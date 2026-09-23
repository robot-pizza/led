"""START/ESCAPE/END envelope framing.

Every write to the sign -- and every notification back -- is wrapped the
same way (see "Transport & Framing" in led/docs/wire_format.md):

    0x01 [escaped: 2-byte big-endian length + payload] 0x03

Any payload byte in 0x01-0x03 is escaped as 0x02 followed by (byte ^ 0x04),
since those three values are also the START/ESCAPE/END markers.
"""

from __future__ import annotations

START_BYTE = 0x01
ESCAPE_BYTE = 0x02
END_BYTE = 0x03
XOR_MASK = 0x04


class EnvelopeError(ValueError):
    """Raised when a byte sequence isn't a valid envelope frame."""


def escape(data: bytes) -> bytes:
    out = bytearray()
    for byte in data:
        if START_BYTE <= byte <= END_BYTE:
            out.append(ESCAPE_BYTE)
            out.append(byte ^ XOR_MASK)
        else:
            out.append(byte)
    return bytes(out)


def unescape(data: bytes) -> bytes:
    out = bytearray()
    i = 0
    while i < len(data):
        if data[i] == ESCAPE_BYTE and i + 1 < len(data):
            out.append(data[i + 1] ^ XOR_MASK)
            i += 2
        else:
            out.append(data[i])
            i += 1
    return bytes(out)


def encode(payload: bytes) -> bytes:
    """Wrap a payload in the full START/length/escape/END envelope."""
    length_prefixed = len(payload).to_bytes(2, "big") + payload
    return bytes([START_BYTE]) + escape(length_prefixed) + bytes([END_BYTE])


def decode(frame: bytes) -> bytes:
    """Unwrap an envelope frame, returning the inner payload.

    Validates the start/end markers and the declared length, raising
    EnvelopeError if either doesn't check out.
    """
    if len(frame) < 2 or frame[0] != START_BYTE or frame[-1] != END_BYTE:
        raise EnvelopeError(f"not a valid envelope frame: {frame.hex()}")
    length_prefixed = unescape(frame[1:-1])
    if len(length_prefixed) < 2:
        raise EnvelopeError(f"envelope too short to contain a length: {frame.hex()}")
    declared_length = int.from_bytes(length_prefixed[0:2], "big")
    payload = length_prefixed[2:]
    if len(payload) != declared_length:
        raise EnvelopeError(
            f"declared length {declared_length} does not match actual payload length {len(payload)}"
        )
    return payload
