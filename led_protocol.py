"""Everything a caller needs on top of the wire-protocol
library: turning a PIL image into pixel_data bytes, and orchestrating a
program upload (start + chunked data, with retries) over a ConnectionHandler.

coolledux itself deliberately stops at "encode this documented structure" --
it does not generate bitmaps, and its connection handler does one round trip
per send() with no retry/chunking policy (see coolledux/requests.py and
coolledux/connection.py docstrings). Both of those are caller concerns, and
this module is that caller.
"""

from __future__ import annotations

import asyncio
import colorsys
import random
from typing import Optional

from bleak import BleakScanner
from PIL import Image, ImageDraw

import lzss
from connection import ConnectionHandler
from requests import ProgramDataRequest, ProgramStartRequest
from responses import ProgramDataResponse, ProgramStartResponse

# Confirmed timing constants and chunk size, from "Buffering & Rate Limits"
# in led/docs/wire_format.md.
START_ACK_TIMEOUT_S = 5.0
CHUNK_ACK_TIMEOUT_S = 3.0
RETRY_ATTEMPTS = 3
CHUNK_SIZE = 1024


class UploadError(RuntimeError):
    """Raised when a program upload isn't accepted after retrying."""


def rgb444_transfer(value: int) -> int:
    """Non-linear 8-bit -> 4-bit quantization for per-pixel bitmap color
    (Graffiti/Animation/Text-emoji content) -- confirmed from ColorUtils
    .java's rgb444Transfer. Distinct from Clock's flat value/16
    quantization (see coolledux.requests._rgb444_flat)."""
    if value >= 238:
        return 15
    if value <= 30:
        return 0
    return (value - 30) // 15 + 1


def pack_rgb444(r: int, g: int, b: int) -> bytes:
    r4, g4, b4 = rgb444_transfer(r), rgb444_transfer(g), rgb444_transfer(b)
    return bytes([r4, (g4 << 4) | b4])


def image_to_pixel_data(img: Image.Image) -> bytes:
    """Column-major RGB444-packed pixel data for Graffiti/Animation content
    (see "Font & Bitmap Encoding" in led/docs/wire_format.md): outer loop
    over columns, inner loop over rows top-to-bottom."""
    width, height = img.size
    px = img.convert("RGB").load()
    out = bytearray()
    for x in range(width):
        for y in range(height):
            r, g, b = px[x, y]
            out.extend(pack_rgb444(r, g, b))
    return bytes(out)


# Hand-drawn 5x7 monochrome digit font for Clock content's digit-bitmap
# tables (hour_digits/minute_digits/etc.) -- black-and-white glyphs, no
# antialiasing, same reasoning as the pixel font above:
# crisp hard edges read far better on the physical matrix than anything
# font-rendered. Row-major bit strings here for readability; packed into
# the wire's actual column-major MSB-first byte format by digit_glyph_table().
_CLOCK_DIGIT_FONT_5X7 = {
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11111", "00010", "00100", "00010", "00001", "10001", "01110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("00110", "01000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00010", "01100"),
}
_CLOCK_DIGIT_WIDTH = 5
_CLOCK_DIGIT_HEIGHT = 7


def digit_glyph_table() -> bytes:
    """The 10-glyph (0-9) digit-bitmap table Clock content's hour/minute/
    seconds fields expect: monochrome, packed one column at a time,
    top-to-bottom, MSB first (see "Font & Bitmap Encoding" in
    led/docs/wire_format.md) -- bytes_per_column = ceil(height/8) = 1
    byte/column at this font's 7-row height, so each glyph is
    _CLOCK_DIGIT_WIDTH bytes and the whole table is 10x that."""
    out = bytearray()
    for digit in "0123456789":
        rows = _CLOCK_DIGIT_FONT_5X7[digit]
        for col in range(_CLOCK_DIGIT_WIDTH):
            byte = 0
            for row in range(_CLOCK_DIGIT_HEIGHT):
                if rows[row][col] == "1":
                    byte |= 1 << (7 - row)
            out.append(byte)
    return bytes(out)


# The manufacturer's digit-bitmap table and colon glyph for a 16x32
# panel: the bytes a 16-row, 32-column sign is actually sent. 7 cols x
# 16 rows, 2 bytes/col, 14 bytes/digit x 10 digits = 140 bytes, matching
# the style-1/3/4/6 table shape in docs/wire_format.md.
# Confirmed-real, NOT hand-drawn like _CLOCK_DIGIT_FONT_5X7 above.
REAL_DIGIT_GLYPH_TABLE_16X32 = bytes([
    127, 224, 255, 240, 192, 48, 192, 48, 255, 240, 127, 224, 0, 0,
    32, 48, 96, 48, 255, 240, 255, 240, 0, 48, 0, 48, 0, 0,
    96, 240, 225, 240, 195, 48, 198, 48, 252, 48, 120, 48, 0, 0,
    96, 96, 224, 112, 198, 48, 198, 48, 255, 240, 121, 224, 0, 0,
    31, 128, 63, 128, 97, 128, 255, 240, 255, 240, 1, 128, 0, 0,
    252, 96, 252, 112, 204, 48, 204, 48, 207, 240, 199, 224, 0, 0,
    127, 224, 255, 240, 204, 48, 204, 48, 207, 240, 199, 224, 0, 0,
    192, 0, 192, 0, 199, 240, 207, 240, 248, 0, 240, 0, 0, 0,
    123, 224, 255, 240, 198, 48, 198, 48, 255, 240, 123, 224, 0, 0,
    124, 96, 254, 112, 198, 48, 198, 48, 255, 240, 127, 224, 0, 0,
])
assert len(REAL_DIGIT_GLYPH_TABLE_16X32) == 140

# Colon separator glyph for the same 16x32 panel, from the same source
# (str2's default value) -- 2 cols x 16 rows, 2 bytes/col.
REAL_COLON_GLYPH_16X32 = bytes([48, 192, 48, 192])


# The scoreboard's own digit table for a 16x32 panel: the bytes that
# size of panel is sent.
#
# 5 bytes/digit x 10 digits = 50 bytes. At 1 byte/column that's a 5-col x
# 8-row glyph -- deliberately SHORTER than the 16-row TimeCount/Clock font
# above, since a scoreboard has to fit two scores side by side on a panel
# only 32 columns wide.
REAL_SCOREBOARD_DIGIT_TABLE_16X32 = bytes([
    254, 130, 130, 254, 0,
    0, 0, 254, 0, 0,
    158, 146, 146, 242, 0,
    146, 146, 146, 254, 0,
    240, 16, 16, 254, 0,
    242, 146, 146, 158, 0,
    254, 146, 146, 158, 0,
    128, 128, 128, 254, 0,
    254, 146, 146, 254, 0,
    242, 146, 146, 254, 0,
])
assert len(REAL_SCOREBOARD_DIGIT_TABLE_16X32) == 50


async def find_device_by_name(name: str, timeout: float = 10.0) -> Optional[str]:
    device = await BleakScanner.find_device_by_name(name, timeout=timeout)
    return device.address if device else None


async def query_panel_dimensions(device_name: str, timeout: float = 10.0) -> tuple[int, int]:
    """Scans for the sign's BLE advertisement and reads its pixel
    dimensions straight from manufacturer_data -- this is the only way to
    learn panel size; DeviceInfoRequest's reply has no width/height
    fields. Must be called before connecting: the sign stops advertising
    once a GATT connection holds it (see led/docs/wire_format.md's
    "Device Discovery" section). Offsets are relative to bleak's own
    manufacturer_data payload, which already strips the AD header and
    2-byte company ID -- confirmed live: byte 6 = height, bytes 7-8 =
    width (big-endian). Returns (width, height).
    """
    devices = await BleakScanner.discover(timeout=timeout, return_adv=True)
    for _address, (device, adv) in devices.items():
        if device.name != device_name:
            continue
        for payload in adv.manufacturer_data.values():
            if len(payload) >= 9:
                height = payload[6]
                width = (payload[7] << 8) | payload[8]
                return width, height
    raise RuntimeError(f"could not find {device_name!r} or read its dimensions from its advertisement")


# Corner marker colors for build_calibration_pattern(), chosen to be
# mutually distinct, far (in RGB distance) from the full-saturation
# rainbow strip so a stray strip pixel can't be mistaken for a corner,
# and NOT white/gray -- confirmed live that white is a bad choice here:
# it also matches ordinary room background (a monitor, a wall) far more
# strongly than the small in-panel marker, since Calibration.java's
# corner search is a plain color-distance match with no notion of
# "but only within the panel" on its own.
CALIBRATION_MARKER_COLORS = {
    "top_left": (255, 128, 0),
    "top_right": (255, 0, 0),
    "bottom_right": (0, 255, 0),
    "bottom_left": (0, 0, 255),
}


def build_calibration_pattern(width: int, height: int) -> Image.Image:
    """Calibration test image, sized to the sign's own panel: a uniquely
    colored marker block in each corner (so the scanner app can locate
    all 4 corners independently rather than just a bounding box, and
    self-correct orientation as a side effect of always mapping "found
    red" to "top-left" regardless of how the photo itself is rotated --
    see Calibration.java), a 16-hue strip across the middle (exercises
    the panel's color range, used for the app's own color calibration),
    and 3 explicit black/off markers (so "truly off" gets verified
    directly, not just inferred from the surrounding background).

    This layout is shared exactly with Calibration.java's
    CalibrationLayout -- keep both in sync if this changes.
    """
    img = Image.new("RGB", (width, height), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    mw = max(2, width // 6)
    mh = max(2, height // 4)
    draw.rectangle((0, 0, mw - 1, mh - 1), fill=CALIBRATION_MARKER_COLORS["top_left"])
    draw.rectangle((width - mw, 0, width - 1, mh - 1), fill=CALIBRATION_MARKER_COLORS["top_right"])
    draw.rectangle((width - mw, height - mh, width - 1, height - 1), fill=CALIBRATION_MARKER_COLORS["bottom_right"])
    draw.rectangle((0, height - mh, mw - 1, height - 1), fill=CALIBRATION_MARKER_COLORS["bottom_left"])

    strip_y0 = int(height * 0.4)
    strip_y1 = max(strip_y0 + 1, int(height * 0.6))
    for i in range(16):
        hue = i / 16
        r, g, b = (int(c * 255) for c in colorsys.hsv_to_rgb(hue, 1.0, 1.0))
        x0 = int(i * width / 16)
        x1 = int((i + 1) * width / 16)
        draw.rectangle((x0, strip_y0, max(x0, x1 - 1), strip_y1 - 1), fill=(r, g, b))

    off_y0 = int(height * 0.65)
    off_y1 = max(off_y0 + 1, int(height * 0.8))
    ow = max(2, width // 10)
    for frac in (0.25, 0.5, 0.75):
        cx = int(width * frac)
        x0 = max(0, cx - ow // 2)
        x1 = min(width, x0 + ow)
        draw.rectangle((x0, off_y0, max(x0, x1 - 1), off_y1 - 1), fill=(0, 0, 0))

    return img


CORNER_NAMES = ("top_left", "top_right", "bottom_right", "bottom_left")


def build_single_corner_pattern(width: int, height: int, corner: str, color: tuple[int, int, int] = (255, 255, 255)) -> Image.Image:
    """Exactly one LED lit -- the panel's own corner pixel -- everything
    else off. Used for the corner-finding phase of calibration: rather
    than distinguishing 4 differently-colored markers in one photo
    (which needs a hue window narrow enough to keep neighboring marker
    colors from being confused -- fragile under real lighting), each
    corner is found in its own photo where it's the ONLY lit thing on
    the panel. Detection is then a plain "what's the brightest blob"
    search, not a color match at all -- see Calibration.findSingleMarker.

    A single pixel rather than a marker block on purpose: this is only
    ever used for on/off detection, and a lone LED's blob has an
    unambiguous centroid -- no need to guess which extreme of a block's
    bounding box corresponds to the true corner (confirmed live: that
    block+extreme-corner approach produced a visibly wrong warp).
    """
    img = Image.new("RGB", (width, height), (0, 0, 0))
    if corner == "top_left":
        xy = (0, 0)
    elif corner == "top_right":
        xy = (width - 1, 0)
    elif corner == "bottom_right":
        xy = (width - 1, height - 1)
    elif corner == "bottom_left":
        xy = (0, height - 1)
    else:
        raise ValueError(f"unknown corner {corner!r}")
    img.putpixel(xy, color)
    return img


async def _send_start_with_retry(connection: ConnectionHandler, request: ProgramStartRequest) -> ProgramStartResponse:
    last_error: Optional[Exception] = None
    for _ in range(RETRY_ATTEMPTS):
        try:
            response = await connection.send(request, timeout=START_ACK_TIMEOUT_S)
        except asyncio.TimeoutError as e:
            last_error = e
            continue
        if isinstance(response, ProgramStartResponse):
            return response
        last_error = UploadError(f"unexpected response type {type(response).__name__}")
    raise UploadError(f"no ProgramStartResponse after {RETRY_ATTEMPTS} attempts") from last_error


async def _send_chunk_with_retry(connection: ConnectionHandler, request: ProgramDataRequest) -> ProgramDataResponse:
    last_error: Optional[Exception] = None
    for _ in range(RETRY_ATTEMPTS):
        try:
            response = await connection.send(request, timeout=CHUNK_ACK_TIMEOUT_S)
        except asyncio.TimeoutError as e:
            last_error = e
            continue
        if isinstance(response, ProgramDataResponse) and response.accepted:
            return response
        status = response.status if isinstance(response, ProgramDataResponse) else "n/a"
        last_error = UploadError(f"chunk not accepted (status={status})")
    raise UploadError(f"chunk {request.chunk_index} not accepted after {RETRY_ATTEMPTS} attempts") from last_error


def wrap_program_data(contents: list[bytes], nonce: int = 0) -> bytes:
    """Wrap already-encoded content items into the payload a ProgramStart/
    ProgramData upload actually announces and compresses.

    The combined content items aren't sent bare -- they're wrapped in
    [8 reserved 0x00][content item count, 1B][0x00] first. This wrapper is
    what gets CRC32'd and LZSS-compressed, not the raw joined contents.
    Missing it was the reason every earlier upload acked cleanly at the
    chunk level but never actually became the active display: the CRC
    covered different bytes than the firmware expected.

    `nonce` (0-255) overwrites the LAST of the 8 reserved bytes -- see
    upload_program()'s docstring for why. Left at the default 0 by any
    caller that wants the plain documented wrapper (e.g. tests asserting
    exact byte layout).
    """
    combined = b"".join(contents)
    return b"\x00" * 7 + bytes([nonce & 0xFF]) + bytes([len(contents)]) + b"\x00" + combined


async def upload_program(
    connection: ConnectionHandler,
    contents: list[bytes],
    show_count: int = 1,
    index: int = 0,
    count: int = 1,
) -> None:
    """Announce and upload a program (one or more already-encoded content
    items -- e.g. GraffitiContent(...).encode()) via ProgramStart followed
    by chunked ProgramData, retrying each step per the doc's confirmed
    timing constants. Raises UploadError if a step is never accepted.

    Stamps a random nonce into the wrapper's last reserved byte (see
    wrap_program_data) so this call's bytes are essentially never
    byte-identical to a previous upload, even when the visible content
    is (e.g. re-sending the same calibration pattern every run) -- see
    "Known flakiness" in wire_format.md: the firmware's already_stored
    dedup is CRC/cache-window based and documented to false-positive on
    content that collides with something sent recently, and a false
    positive here means the chunk data is never actually sent, leaving
    the sign silently frozen on stale content with no error anywhere.
    Confirmed live this session both ways: trusting already_stored blindly
    reproduced exactly that silent staleness, and the opposite fix (always
    force the chunk send regardless of the reply) turned out to be
    protocol-invalid -- when already_stored is a TRUE positive, the
    firmware isn't expecting data afterward and rejects chunk 0 outright.
    The nonce sidesteps the ambiguity instead of guessing which side of
    it to trust: our own content is never truly a repeat, so
    already_stored should have nothing legitimate to match, and the
    original protocol-correct "skip chunks when it does fire" behavior
    is kept for the rare/genuine case."""
    program_data = wrap_program_data(contents, nonce=random.randint(0, 255))

    # index/count describe *which program* and *how many programs* are in
    # this batch -- distinct from how many content items are combined
    # inside one program (that's the wrapper's own count byte).
    #
    # Defaults to a single program because that's what almost everything
    # wants. To send several, call this once per program with the same
    # `count` and an increasing `index`: confirmed live that uploading
    # index=0,count=2 then index=1,count=2 leaves the sign ROTATING
    # between the two, each shown for a couple of seconds.
    start_request = ProgramStartRequest(
        program_data=program_data, index=index, count=count, show_count=show_count,
    )
    start_response = await _send_start_with_retry(connection, start_request)
    if start_response.already_stored:
        return

    compressed = lzss.compress(program_data)
    chunks = [compressed[i:i + CHUNK_SIZE] for i in range(0, len(compressed), CHUNK_SIZE)] or [b""]
    for index, chunk in enumerate(chunks):
        data_request = ProgramDataRequest(total_length=len(compressed), chunk_index=index, chunk_data=chunk)
        await _send_chunk_with_retry(connection, data_request)
