"""LZSS compression for program content.

Program content always travels LZSS-compressed inside a ProgramData
request (see "Data packet (chunks)" in docs/wire_format.md) -- this is
part of the wire format itself, not a rendering concern, so it lives
here rather than being left to callers.

The window/lookahead/threshold constants and the bitstream layout are
the wire format, not implementation detail, and are not ours to choose.
See the comment in compress() for the overlapping-match case.
"""

from __future__ import annotations

WINDOW_SIZE = 512
LOOKAHEAD_SIZE = 18
THRESHOLD = 2


def compress(data: bytes) -> bytes:
    if not data:
        return b""

    buffer = bytearray(WINDOW_SIZE)
    history_len = 0
    r = WINDOW_SIZE - LOOKAHEAD_SIZE

    result = bytearray()
    code_buf = bytearray(17)
    code_buf_ptr = 1
    flags = 0
    flag_mask = 1

    pos = 0
    while pos < len(data):
        max_match = min(LOOKAHEAD_SIZE, len(data) - pos)
        match_len = 0
        match_pos = 0

        if history_len:
            max_search = min(history_len, WINDOW_SIZE)
            for dist in range(1, max_search + 1):
                idx = (r - dist) & (WINDOW_SIZE - 1)
                length = 0
                while length < max_match:
                    # For an overlapping match (length >= dist), the byte at this
                    # offset hasn't been written into `buffer` yet -- it's still
                    # zero-initialized filler, not the self-referential value the
                    # decoder's interleaved copy loop will actually produce. Compare
                    # against `data` directly instead, which is always correct: for
                    # length < dist it's the same historical byte `buffer` would hold,
                    # and for length >= dist it's exactly the earlier part of this
                    # same match (true self-reference), matching decode's semantics.
                    buf_byte = data[pos + length - dist]
                    cur_byte = data[pos + length]
                    if buf_byte != cur_byte:
                        break
                    length += 1
                if length > match_len:
                    match_len = length
                    match_pos = idx
                    if match_len == max_match:
                        break

        if match_len > THRESHOLD:
            code_buf[code_buf_ptr] = match_pos & 0xFF
            code_buf[code_buf_ptr + 1] = ((match_pos >> 4) & 0xF0) | (match_len - 3)
            code_buf_ptr += 2
        else:
            match_len = 1
            flags |= flag_mask
            code_buf[code_buf_ptr] = data[pos]
            code_buf_ptr += 1

        flag_mask <<= 1
        if flag_mask == 0x100:
            code_buf[0] = flags & 0xFF
            result.extend(code_buf[:code_buf_ptr])
            code_buf = bytearray(17)
            code_buf_ptr = 1
            flags = 0
            flag_mask = 1

        for i in range(match_len):
            byte = data[pos + i]
            buffer[r] = byte
            r = (r + 1) & (WINDOW_SIZE - 1)
        history_len = min(history_len + match_len, WINDOW_SIZE)
        pos += match_len

    if code_buf_ptr > 1:
        code_buf[0] = flags & 0xFF
        result.extend(code_buf[:code_buf_ptr])

    return bytes(result)


def decompress(data: bytes) -> bytes:
    if not data:
        return b""

    buffer = bytearray(WINDOW_SIZE)
    r = WINDOW_SIZE - LOOKAHEAD_SIZE
    out = bytearray()

    idx = 0
    flags = 0
    while idx < len(data):
        flags >>= 1
        if flags & 0x100 == 0:
            if idx >= len(data):
                break
            flags = data[idx] | 0xFF00
            idx += 1
        if flags & 1:
            if idx >= len(data):
                break
            byte = data[idx]
            idx += 1
            out.append(byte)
            buffer[r] = byte
            r = (r + 1) & (WINDOW_SIZE - 1)
        else:
            if idx + 1 >= len(data):
                break
            low = data[idx]
            high = data[idx + 1]
            idx += 2
            match_pos = (low | ((high & 0xF0) << 4)) & (WINDOW_SIZE - 1)
            match_len = (high & 0x0F) + 3
            for _ in range(match_len):
                byte = buffer[match_pos]
                match_pos = (match_pos + 1) & (WINDOW_SIZE - 1)
                out.append(byte)
                buffer[r] = byte
                r = (r + 1) & (WINDOW_SIZE - 1)

    return bytes(out)
