"""Hash chain for tamper-evident capture.

Each captured raw message contributes one entry to a hash chain. The
chain advances per-day so that compromise of the chain on one day does
not invalidate prior days, and so that operational restart does not
break the chain (we read the prior tip from storage).

A chain entry is:
    entry_hash = SHA-256(prev_hash || sha256_of_message || captured_at_iso)

Concatenation is via newline-separated UTF-8 to keep it human-auditable
when the manifest is exported. We record the manifest one line per
message; verifying the chain is then a single linear scan.

The chain manifest itself lives in MinIO at:
    raw/{yyyy}/{mm}/{dd}/manifest.jsonl

Each line is a JSON object with: sha256, prev_hash, entry_hash,
captured_at, object_key, size_bytes, protocol. This makes the manifest
both parseable and human-readable.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime


GENESIS_PREV_HASH = "0" * 64  # 64 zero hex chars; SHA-256 hex output length


@dataclass(frozen=True)
class ChainEntry:
    """One entry in the daily hash chain."""

    sha256: str           # hash of the raw message bytes
    prev_hash: str        # the entry_hash of the previous record (or GENESIS)
    entry_hash: str       # SHA-256(prev_hash || sha256 || captured_at_iso)
    captured_at: datetime
    object_key: str
    size_bytes: int
    protocol: str

    def to_jsonl(self) -> str:
        """Serialize for the daily manifest (one JSON object per line)."""
        return json.dumps({
            "sha256": self.sha256,
            "prev_hash": self.prev_hash,
            "entry_hash": self.entry_hash,
            "captured_at": self.captured_at.isoformat(),
            "object_key": self.object_key,
            "size_bytes": self.size_bytes,
            "protocol": self.protocol,
        }, separators=(",", ":"))

    @classmethod
    def from_jsonl(cls, line: str) -> ChainEntry:
        d = json.loads(line)
        return cls(
            sha256=d["sha256"],
            prev_hash=d["prev_hash"],
            entry_hash=d["entry_hash"],
            captured_at=datetime.fromisoformat(d["captured_at"]),
            object_key=d["object_key"],
            size_bytes=d["size_bytes"],
            protocol=d["protocol"],
        )


def compute_entry_hash(prev_hash: str, sha256: str, captured_at: datetime) -> str:
    """Compute the entry hash for the chain."""
    payload = f"{prev_hash}\n{sha256}\n{captured_at.isoformat()}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def make_entry(
    *,
    prev_hash: str,
    raw_bytes: bytes,
    captured_at: datetime,
    object_key: str,
    protocol: str,
) -> ChainEntry:
    """Build a new chain entry for the given raw bytes."""
    sha = hashlib.sha256(raw_bytes).hexdigest()
    entry_hash = compute_entry_hash(prev_hash, sha, captured_at)
    return ChainEntry(
        sha256=sha,
        prev_hash=prev_hash,
        entry_hash=entry_hash,
        captured_at=captured_at,
        object_key=object_key,
        size_bytes=len(raw_bytes),
        protocol=protocol,
    )


def verify_chain(entries: list[ChainEntry]) -> tuple[bool, str | None]:
    """Verify the chain. Returns (ok, error_message_or_None).

    Checks both that each entry's hash matches its inputs and that
    prev_hash links correctly to the prior entry. Does not re-fetch the
    raw bytes; that's a separate (more expensive) verification.
    """
    prev = GENESIS_PREV_HASH
    for i, e in enumerate(entries):
        if e.prev_hash != prev:
            return False, f"entry {i}: prev_hash mismatch (expected {prev[:16]}..., got {e.prev_hash[:16]}...)"
        expected = compute_entry_hash(e.prev_hash, e.sha256, e.captured_at)
        if e.entry_hash != expected:
            return False, f"entry {i}: entry_hash mismatch"
        prev = e.entry_hash
    return True, None
