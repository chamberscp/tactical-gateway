"""Tests for UUID v7 and hash chain utilities."""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from cto_schema import (
    GENESIS_PREV_HASH,
    ChainEntry,
    compute_entry_hash,
    make_entry,
    uuid7,
    uuid7_timestamp_ms,
    verify_chain,
)


# ---------------------------------------------------------------------------
# UUID v7
# ---------------------------------------------------------------------------


def test_uuid7_is_version_7() -> None:
    uid = uuid7()
    assert uid.version == 7


def test_uuid7_encodes_recent_timestamp() -> None:
    before_ms = int(time.time() * 1000)
    uid = uuid7()
    after_ms = int(time.time() * 1000)
    ts = uuid7_timestamp_ms(uid)
    # The encoded timestamp should fall within our observation window,
    # with a tiny tolerance for clock granularity.
    assert before_ms - 1 <= ts <= after_ms + 1


def test_uuid7_ids_sort_chronologically() -> None:
    uids = []
    for _ in range(5):
        uids.append(uuid7())
        time.sleep(0.002)  # ensure millisecond ticks
    sorted_uids = sorted(uids)
    assert uids == sorted_uids


def test_uuid7_timestamp_extraction_rejects_other_versions() -> None:
    from uuid import uuid4
    with pytest.raises(ValueError):
        uuid7_timestamp_ms(uuid4())


# ---------------------------------------------------------------------------
# Hash chain
# ---------------------------------------------------------------------------


def test_make_entry_computes_sha_and_chain_hash() -> None:
    captured = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)
    e = make_entry(
        prev_hash=GENESIS_PREV_HASH,
        raw_bytes=b"hello world",
        captured_at=captured,
        object_key="raw/2026/05/20/cot/abc.xml",
        protocol="cot_xml",
    )
    # Known sha256 of "hello world"
    assert e.sha256 == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
    assert e.size_bytes == 11
    assert e.prev_hash == GENESIS_PREV_HASH
    # entry_hash is deterministic given inputs
    expected = compute_entry_hash(GENESIS_PREV_HASH, e.sha256, captured)
    assert e.entry_hash == expected


def test_chain_verifies_when_intact() -> None:
    captured = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)
    e1 = make_entry(prev_hash=GENESIS_PREV_HASH, raw_bytes=b"one",
                    captured_at=captured, object_key="a", protocol="cot_xml")
    e2 = make_entry(prev_hash=e1.entry_hash, raw_bytes=b"two",
                    captured_at=captured, object_key="b", protocol="cot_xml")
    e3 = make_entry(prev_hash=e2.entry_hash, raw_bytes=b"three",
                    captured_at=captured, object_key="c", protocol="cot_xml")

    ok, err = verify_chain([e1, e2, e3])
    assert ok, err


def test_chain_detects_broken_link() -> None:
    captured = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)
    e1 = make_entry(prev_hash=GENESIS_PREV_HASH, raw_bytes=b"one",
                    captured_at=captured, object_key="a", protocol="cot_xml")
    # Tamper: claim a different prev_hash on e2
    e2_bad = ChainEntry(
        sha256=e1.sha256, prev_hash="f" * 64, entry_hash=e1.entry_hash,
        captured_at=captured, object_key="b", size_bytes=3, protocol="cot_xml",
    )
    ok, err = verify_chain([e1, e2_bad])
    assert not ok
    assert err is not None
    assert "prev_hash mismatch" in err


def test_chain_detects_tampered_entry_hash() -> None:
    captured = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)
    e = make_entry(prev_hash=GENESIS_PREV_HASH, raw_bytes=b"x",
                   captured_at=captured, object_key="a", protocol="cot_xml")
    e_bad = ChainEntry(
        sha256=e.sha256, prev_hash=e.prev_hash, entry_hash="0" * 64,
        captured_at=e.captured_at, object_key=e.object_key,
        size_bytes=e.size_bytes, protocol=e.protocol,
    )
    ok, err = verify_chain([e_bad])
    assert not ok
    assert err is not None
    assert "entry_hash mismatch" in err


def test_jsonl_roundtrip() -> None:
    captured = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)
    e = make_entry(prev_hash=GENESIS_PREV_HASH, raw_bytes=b"abc",
                   captured_at=captured, object_key="raw/x", protocol="cot_xml")
    line = e.to_jsonl()
    restored = ChainEntry.from_jsonl(line)
    assert restored == e
