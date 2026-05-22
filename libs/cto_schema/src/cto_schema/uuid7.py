"""UUID v7 generation (time-ordered UUIDs).

UUID v7 encodes a Unix millisecond timestamp in the high bits, followed by
random data. This gives us globally unique IDs that sort chronologically,
which is useful for time-series databases and for human inspection of
sequences of events.

Python 3.11's stdlib does not include uuid.uuid7 (added in 3.13+). We
implement it here to keep the dependency surface small and the behavior
explicit.

Spec: RFC 9562 section 5.7.
"""

from __future__ import annotations

import os
import time
from uuid import UUID


def uuid7() -> UUID:
    """Generate a new UUID v7.

    Layout (128 bits, big-endian):
      48 bits: Unix timestamp in milliseconds
       4 bits: version (0b0111)
      12 bits: random
       2 bits: variant (0b10)
      62 bits: random
    """
    ts_ms = int(time.time() * 1000) & 0xFFFFFFFFFFFF  # 48 bits

    rand_a = int.from_bytes(os.urandom(2), "big") & 0x0FFF  # 12 bits
    rand_b = int.from_bytes(os.urandom(8), "big") & 0x3FFFFFFFFFFFFFFF  # 62 bits

    version = 0x7
    variant = 0b10

    value = (
        (ts_ms << 80)
        | (version << 76)
        | (rand_a << 64)
        | (variant << 62)
        | rand_b
    )
    return UUID(int=value)


def uuid7_timestamp_ms(uid: UUID) -> int:
    """Extract the millisecond timestamp from a UUID v7.

    Raises ValueError if the UUID is not version 7.
    """
    if uid.version != 7:
        raise ValueError(f"not a UUID v7: version is {uid.version}")
    return uid.int >> 80
