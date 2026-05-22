#!/usr/bin/env python3
"""Verify the hash chain of a captured manifest.

Usage:
    python -m tools.verify_chain <path-to-manifest.jsonl>
"""

import sys
from cto_schema import ChainEntry, verify_chain


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python -m tools.verify_chain <manifest.jsonl>", file=sys.stderr)
        return 2
    path = sys.argv[1]
    with open(path, encoding="utf-8") as f:
        entries = [ChainEntry.from_jsonl(line) for line in f if line.strip()]
    ok, err = verify_chain(entries)
    if ok:
        print(f"CHAIN OK  entries={len(entries)}  tip={entries[-1].entry_hash[:16]}...")
        return 0
    else:
        print(f"CHAIN BROKEN: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())