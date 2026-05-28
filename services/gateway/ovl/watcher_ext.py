"""Folder-watcher extension for OVL.

Phase 2a added a folder watcher that picks up *.kmz from the inbox, captures,
parses, and publishes. This module registers an *.ovl handler with that same
watcher so dropping an .ovl behaves exactly like dropping a .kmz: picked up
within ~5s, captured to the hash chain, parsed, CTOs published, and the file
moved to the .processed/ subfolder.

Integration: in services/gateway/watcher.py (Phase 2a), the watcher exposes
`register_handler(extension, handler)`. We call it for ".ovl". If the watcher
API differs, this is the single place to adapt.
"""
from __future__ import annotations

from typing import Callable

from ovl.ingest import ingest_ovl_bytes


def register(watcher, *, capture, bus, audit) -> None:
    """Register the .ovl handler on the Phase 2a folder watcher."""

    async def handle_ovl(filename: str, data: bytes) -> dict:
        return await ingest_ovl_bytes(
            data, filename=filename, capture=capture, bus=bus, audit=audit
        )

    watcher.register_handler(".ovl", handle_ovl)


# For an HTTP upload endpoint mirroring POST /ingest/kmz, the gateway's route
# table should add POST /ingest/ovl bound to ingest_ovl_bytes with the same
# 409-on-duplicate + ?force=true semantics already implemented for KMZ in
# Phase 2a. The duplicate-detection logic is filename-based and lives in the
# shared upload handler, so /ingest/ovl reuses it unchanged; only the parser
# dispatch differs by extension.
