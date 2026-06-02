# Phase 2c — Chat Ingest & Cross-Protocol Bridging

**Status:** scoped, not started
**Builds on:** Phase 2a (opstore + query API), Phase 2b-1 (multi-format ingest pattern)
**Tag on completion:** `phase2c`

---

## 1. Purpose

Two things, deliberately combined:

1. **Bridge XMPP and IRC chat** so an operator on Openfire/JChat (XMPP)
   and an operator on MAKO (IRC) can hold a conversation in what feels
   like one room.
2. **Persist every chat message as a CTO** so the operational store and
   query API treat chat the same as any other tactical object —
   searchable by room, sender, time, content; auditable via the
   tamper-evident hash chain; later available for RAG.

The bridging itself is done by a proven open-source tool sitting
alongside the gateway, not custom code. The gateway owns the
persistence path.

The user need that drove this:

> "We use Openfire/JChat (XMPP). Coalition partners and other services
> use MAKO (IRC). We need to talk to each other, *and* we need a
> defensible record of what was said."

## 2. Architecture summary

```
   MAKO users  <----IRC---->  [Bridge: biboumi or matterbridge]  <----XMPP---->  Openfire / JChat users
                                            |
                                            |  (gateway joins as a passive XMPP client; also as
                                            |   a passive IRC client if D2 = both-sides)
                                            v
                                  Gateway chat listener(s)
                                            |
                                            v
                              Raw capture + hash chain (MinIO)
                                            |
                                            v
                              chat_message normalizer  -->  CTO
                                            |
                                            v
                                         NATS bus
                                            |
                                            v
                                     Opstore (PostgreSQL)
                                            |
                                            v
                                       Query API
```

The bridge handles real-time relay. The gateway handles capture,
normalization, persistence, audit, and search. Neither does the other's
job.

## 3. Scope

### 3.1 Bridge (selected, configured — not built)

- Evaluate **biboumi** vs **matterbridge** against:
  - IRC SASL + TLS support for classified IRC servers
  - XMPP component vs. client mode
  - Attachment handling (XEP-0363 ↔ DCC or out-of-band link)
  - Operational maturity in air-gapped deployment
- Document the choice in ADR-0013 and stand it up next to the gateway
  in the compose stack. Configuration only; no code changes to the
  bridge itself.

### 3.2 CTO schema additions

- New `object_class` value: **`chat_message`**.
- New CTO fields (or `attributes` keys, depending on which is cleaner —
  decided in build):
  - `room` (string, e.g. `tac1@conference.openfire.local` or `#tac1`)
  - `room_canonical` (a single canonical room identifier that maps
    both sides of the bridge to one logical room — see D1)
  - `sender_jid` / `sender_nick` (whichever the source side provides)
  - `sender_canonical` (one identity string per human, see D3)
  - `body` (text, UTF-8)
  - `thread_id` (optional, XMPP threading or in-message reply markers)
  - `edit_of` (optional, message-id of edited original)
  - `delete_of` (optional, message-id of retracted message)
  - `attachments[]` (list of `{filename, sha256, content_type, size}`
    referencing files stored in MinIO under `raw/chat/...`)
- Geometry is optional and usually null. XMPP geolocation extensions
  (XEP-0080) when present populate it; otherwise `geometry = null`.
- Alembic migration: `0005_add_chat_message`.

### 3.3 Chat listener(s)

- **XMPP client.** A bot account on Openfire that joins configured
  MUC rooms (`tac1@conference`, etc.). Library: `slixmpp` (mature,
  async, MUC and MAM support).
- **IRC client (conditional on D2).** A bot account on the IRC server
  that joins the same set of bridged channels. Library: `bottom` or
  `irc3` (both async, mature).
- Each listener:
  - Receives messages, presence events, joins/parts, edits, deletes,
    attachment notifications.
  - Hands raw bytes (the original XML stanza for XMPP, the raw IRC
    line for IRC) to the existing capture writer — so the hash chain
    covers chat too, by the same mechanism as CoT/KMZ/OVL.
  - Normalizes to a `chat_message` CTO and publishes to NATS.
- Listeners run as part of the existing gateway service or as their own
  service — decision in build, but the pattern from KMZ/OVL (folder
  watcher *inside* gateway) suggests keeping them inside.

### 3.4 Normalizer

- `XMPP stanza  →  chat_message CTO`
- `IRC line     →  chat_message CTO`
- Both routes produce identical CTO shapes so downstream consumers
  don't care which side the message originated on.
- Sender canonicalization runs here (see D3).
- Room canonicalization runs here (see D1).
- Deduplication: when both listeners are active (D2 = both-sides), the
  same message arrives twice — once natively, once as a bridge relay.
  The normalizer deduplicates by `(room_canonical, sender_canonical,
  body, timestamp ± 2s)` and keeps the *native* version (the one not
  routed through the bridge bot), so attribution is clean.

### 3.5 Query API extensions

- `GET /cto?object_class=chat_message&room=<canonical>&time_from=...&time_to=...`
- `GET /cto?object_class=chat_message&sender=<canonical>`
- Full-text search on `body` (Postgres `tsvector` column added in the
  migration; trigram index for substring search).
- Existing spatial, temporal, and pagination semantics still apply.

### 3.6 Out of scope (explicit)

- **Live message egress (CTO → chat).** The gateway does not *post*
  into chat rooms. The bridge handles relay; the gateway captures. If
  a use case appears later (alerting, bot commands), that's its own
  phase.
- **Custom bridging.** No gateway code participates in real-time
  message relay. Biboumi/matterbridge does that.
- **End-to-end encrypted chat (OMEMO).** Out of scope for v1. If a
  classified deployment uses OMEMO, the bot account would need to be a
  participant in each session — solvable but not in this phase.
- **Direct messages (1:1 chats).** v1 captures MUC / channel traffic
  only. DMs raise different audit and consent questions.
- **Voice / video / presence-only events.** Presence may be logged
  separately for ops awareness but is not modeled as CTOs.

## 4. Decisions to lock in before code

### D1 — Room canonicalization

When MAKO `#tac1` and Openfire `tac1@conference.openfire.local` are
bridged to the same logical room, what do CTOs store as the room
identifier?

**Proposed:** a separate `room_canonical` field set by configuration
(the bridge config already maps the two; we mirror that mapping). The
native room identifier from whichever side the message came in on is
also stored, so we never lose attribution of *where* a message was
literally said.

Confirm or override.

### D2 — One-side or both-side listening

The gateway can:

- **(a) Listen on XMPP only.** Sees everything (bridge relays IRC
  side into XMPP). Easiest. Downside: IRC-side messages appear as
  "said by bridge-bot, originally from <user>" — attribution is
  derived, not direct.
- **(b) Listen on both sides.** Cleanest attribution. Requires the
  deduplication logic in §3.4. More moving parts.

**Proposed: (b), both-side.** The attribution and audit value justify
the extra listener, and dedup is straightforward. The single-side
option remains a fallback if the IRC client side proves operationally
painful.

Confirm or override.

### D3 — Identity canonicalization

`chuck@xmpp.local` and `chuck` on IRC are presumably the same person,
but the system can't know that without help.

**Proposed:** a static `identity_map.yaml` in the deploy directory:

```yaml
- canonical: chuck.chambers
  xmpp_jid: chuck@xmpp.openfire.local
  irc_nicks: [chuck, chuck_, chuck|away]
- canonical: smith.j
  xmpp_jid: jsmith@xmpp.openfire.local
  irc_nicks: [jsmith, jsmith_mako]
```

Messages from unmapped identities still ingest, but with
`sender_canonical = null` and a `provenance` note flagging it. An
operator can update the map and rerun a backfill if needed.

Confirm or override (a database-backed map is the alternative — more
flexible, more moving parts).

### D4 — Retention

Chat is high-volume compared to overlays and tracks. Default Postgres
retention?

**Proposed:** indefinite for v1; document the volume after a week of
operation; add a retention policy in a follow-on phase if needed. Raw
captures in MinIO keep their existing daily-chain structure.

Confirm or override.

### D5 — Attachments

When someone shares a file in chat, do we:

- **(a) Reference only.** Store a pointer; the file lives wherever the
  chat system put it. Lighter, but breaks if the chat server reaps.
- **(b) Mirror to MinIO.** Pull a copy through the capture writer the
  same as any other raw ingest. Heavier but durable and chain-covered.

**Proposed: (b).** Consistency with the rest of the audit story.

Confirm or override.

## 5. Deliverables

1. ADR-0013 — chat bridging tool selection (biboumi vs matterbridge).
2. ADR-0014 — chat-as-CTO schema and dedup model.
3. `deploy/migrations/versions/0005_add_chat_message.py`
4. `services/gateway/chat/listener_xmpp.py`
5. `services/gateway/chat/listener_irc.py` (if D2 = both-side)
6. `services/gateway/chat/normalizer.py`
7. Bridge container in `docker-compose.dev.yml` with config files.
8. Integration tests:
   - `test_xmpp_capture_and_normalize.py`
   - `test_irc_capture_and_normalize.py` (if D2)
   - `test_cross_protocol_dedup.py` (if D2)
   - `test_chat_query_api.py`
9. `PHASE2C_README.md` — operator-facing how-to-use.

## 6. Risks

- **Bot account access.** XMPP bot needs a real Openfire account with
  permission to join the rooms. IRC bot the same. In a classified
  deployment this is a coordination task, not a code task.
- **Volume.** Chat traffic can spike during ops. Capture writer was
  designed for tactical-data rates; chat should still be well within
  capacity, but worth measuring early.
- **OMEMO / encryption.** If any rooms become E2E-encrypted, the bot
  is locked out. Mitigation: document this constraint clearly in the
  README; treat OMEMO support as a future phase, not a v1 problem.
- **Bridge edge cases.** Netsplits, presence flapping, attachment
  proxy failures, nick collisions across protocols. Most of these are
  bridge problems, not gateway problems — but the gateway has to
  handle the resulting odd inputs (duplicated messages, missing
  attribution) without crashing. The dedup logic and the
  unmapped-sender path cover the common cases.
- **MAKO IRC dialect specifics.** "IRC" is a family; MAKO's flavor may
  have proprietary extensions. The IRC client library will need
  validation against the real server. Mitigation: keep the IRC
  listener isolated and easy to patch.

## 7. Definition of done

- Bridge runs and successfully relays messages both directions between
  an XMPP test room and an IRC test channel in the dev stack.
- Both listeners (or one, depending on D2) capture every message that
  flows through the rooms. Hash chain extends correctly.
- Every captured message appears as a `chat_message` CTO in opstore
  within ~2s, with sender, room, body, and (where applicable)
  attachment references.
- Cross-protocol dedup works: a single human utterance produces a
  single CTO, attributed to the native side.
- Query API serves `GET /cto?object_class=chat_message&...` with
  room, sender, time, and full-text filters.
- ADRs 0013 and 0014 merged; `PHASE2C_README.md` written.
- Tagged `phase2c`.

## 8. Estimated shape (not a commitment)

- Schema migration + normalizer: small.
- Listeners (XMPP, optionally IRC): medium — most of the cost is
  handling the protocol quirks, not the happy path.
- Bridge selection and standup: small in code, real in coordination
  time.
- Dedup logic + tests: small.
- Query API extensions: small.

Net: smaller than 2b-1 in pure code volume; comparable in calendar
time because of the bridge selection and the bot-account coordination.

## 9. How this fits into the bigger picture

After Phase 2c, the gateway covers:

- **Tactical state:** tracks (CoT), graphics (KMZ, OVL).
- **Tactical conversation:** chat (XMPP, IRC).

That's a meaningful completeness milestone. Phase 4 (Document RAG)
then gets to ingest chat history alongside documents — *"what did TF
Alpha say about the bridge between 0800 and 1100?"* becomes a real
question the system can answer with citations.
