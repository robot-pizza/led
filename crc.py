"""The checksum announced in a ProgramStart request.

There are two functions here because there are, in practice, two
answers, and the difference was only settled by watching another client
upload to a simulated panel.

`protocol_crc32` is what the protocol's own clients compute: polynomial
0x04C11DB7 applied NON-reflected, initialised to 0xFFFFFFFF, four table
lookups per input byte. It is not CRC-32 as the name is normally
understood, and no standard library computes it.

`crc32` is plain zlib CRC-32, which is what this library has always
sent.

The sign accepts BOTH. This client has driven a real panel through an
entire project with the zlib value, and another client drives the same
panel with its own; content from both displays. So the firmware does not
enforce this field. That contradicts what the documentation asserted for
a long time -- that a mismatch is discarded silently after reassembly --
which was marked confirmed and never actually was.

Verified live, from an upload captured off the air: announced
0x6fa5f0a1 for an 8242-byte program, where protocol_crc32 gives
0x6fa5f0a1 and zlib gives 0xc53f6b1b.

This module's own history is worth keeping in view: it used to claim it
had been "checked against a real captured value (CRC32=0x0628874C)".
The bytes behind that capture were never saved, so nothing was ever
checked against it -- the tests said as much while crc.py claimed
otherwise.
"""

from __future__ import annotations

import zlib


def crc32(data: bytes) -> int:
    """Plain zlib CRC-32. What this library sends, and what the sign
    accepts, despite not being what the protocol's own client computes."""
    return zlib.crc32(data) & 0xFFFFFFFF


def _build_table() -> list[int]:
    table = []
    for index in range(256):
        value = index << 24
        for _ in range(8):
            if value & 0x80000000:
                value = ((value << 1) ^ 0x04C11DB7) & 0xFFFFFFFF
            else:
                value = (value << 1) & 0xFFFFFFFF
        table.append(value)
    return table


_TABLE = _build_table()


def protocol_crc32(data: bytes) -> int:
    """The value other clients put in ProgramStart.

    Note the four lookups per input byte -- a byte is XORed into the low
    end and then shifted out through four rounds, rather than the single
    round a conventional table-driven CRC does. That oddity is
    faithful to the format; reproducing it any more sensibly produces a
    different number.
    """
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte
        for _ in range(4):
            crc = ((crc << 8) & 0xFFFFFFFF) ^ _TABLE[(crc >> 24) & 0xFF]
    return crc & 0xFFFFFFFF
