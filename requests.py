"""Every request and program-content structure in the wire protocol.

Each class's `encode()` returns the raw [marker][payload] bytes for that
request -- unenveloped. The connection handler applies the START/ESCAPE/END
envelope uniformly for every request, so individual classes don't need to
know about it.

Field names and shapes mirror led/docs/wire_format.md directly (snake_case
of the doc's own field names). This module encodes the documented structure;
it does not render bitmaps or fonts -- callers supply already-prepared
pixel/glyph bytes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Optional


def _u8(value: int) -> bytes:
    return bytes([value & 0xFF])


def _u16(value: int) -> bytes:
    return (value & 0xFFFF).to_bytes(2, "big")


def _u32(value: int) -> bytes:
    return (value & 0xFFFFFFFF).to_bytes(4, "big")


def _length_prefixed(inner: bytes) -> bytes:
    """[4-byte total length][inner], where the length includes its own 4 bytes."""
    return _u32(4 + len(inner)) + inner


class Request:
    """Base for every request. `encode()` returns [marker][payload], unenveloped."""

    def encode(self) -> bytes:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Program content items (composed inside a ProgramStart/ProgramData payload)
# ---------------------------------------------------------------------------


class ProgramContent(Request):
    """Shared header for Graffiti/Text/Clock/TimeCount content items:

    [content_type marker (1B)][7 reserved bytes, zero]
    [layer_type (1B)][start_column (2B)][start_row (2B)]
    [show_width (2B)][show_height (2B)]

    Animation does NOT use this shape (see AnimationContent) -- it has its
    own two-byte marker+submarker prefix instead of marker+7-zeros.
    """

    CONTENT_TYPE: ClassVar[int]

    def _header(self, layer_type: int, start_column: int, start_row: int, show_width: int, show_height: int) -> bytes:
        return (
            _u8(self.CONTENT_TYPE)
            + b"\x00" * 7
            + _u8(layer_type)
            + _u16(start_column)
            + _u16(start_row)
            + _u16(show_width)
            + _u16(show_height)
        )

    def _body(self) -> bytes:
        raise NotImplementedError

    def encode(self) -> bytes:
        return _length_prefixed(self._body())


@dataclass
class GraffitiContent(ProgramContent):
    """Static bitmap. Marker 0x02. pixel_data is already RGB444-packed
    (see "Font & Bitmap Encoding" in the doc) -- this class does not
    render it."""

    CONTENT_TYPE: ClassVar[int] = 0x02

    pixel_data: bytes
    layer_type: int = 1
    start_column: int = 0
    start_row: int = 0
    show_width: int = 0
    show_height: int = 0
    mode: int = 1
    speed: int = 0
    stay_time: int = 0

    def _body(self) -> bytes:
        header = self._header(self.layer_type, self.start_column, self.start_row, self.show_width, self.show_height)
        return (
            header
            + _u8(self.mode)
            + _u8(self.speed)
            + _u8(self.stay_time)
            + _u32(len(self.pixel_data))
            + self.pixel_data
        )


@dataclass
class TextContent(ProgramContent):
    """Scrolling/static text. Marker 0x01.

    `items` is a list of (width_columns, item_type, data) tuples, where
    item_type is 0 (text glyph data) or 1 (emoji/image RGB444 data) -- see
    "Text" in the doc. This class does not render glyphs from a font;
    callers supply already-rendered per-item data.

    speed and move_space default to the confirmed real-app values (0xF6 /
    32) rather than 0 -- see the doc's "Confirmed via capture" note: zero
    values make the sign flip between two static pages instead of
    scrolling smoothly.
    """

    CONTENT_TYPE: ClassVar[int] = 0x01

    items: list[tuple[int, int, bytes]]
    layer_type: int = 1
    start_column: int = 0
    start_row: int = 0
    show_width: int = 0
    show_height: int = 0
    mode: int = 2  # LEFT_CONTINUE -- see TextShowMode
    speed: int = 0xF6
    stay_time: int = 0
    move_space: int = 32

    def _body(self) -> bytes:
        header = self._header(self.layer_type, self.start_column, self.start_row, self.show_width, self.show_height)
        total_width = sum(width for width, _type, _data in self.items)
        items_bytes = b"".join(
            _u8(width) + _u8(item_type) + data for width, item_type, data in self.items
        )
        return (
            header
            + _u8(self.mode)
            + _u8(self.speed)
            + _u8(self.stay_time)
            + _u16(self.move_space)
            + _u16(len(self.items))
            + _u32(total_width)
            + items_bytes
        )


# The two palettes auto-colour text draws from. Fourteen numbered
# choices, but only two distinct palettes: eleven of them are copies of
# the rainbow. What actually distinguishes the fourteen choices is the
# pattern/variant pair below, not the colours.
#
# Packed 0xRGB, one nibble per channel, the same shape _rgb444_flat
# produces.
_AUTO_COLOR_RAINBOW: tuple[int, ...] = (
    0xF00, 0xF20, 0xF40, 0xF60, 0xF80, 0xFA0, 0xFC0, 0xFF0, 0xCF0,
    0xAF0, 0x8F0, 0x6F0, 0x4F0, 0x2F0, 0x0F0, 0x0F2, 0x0F4, 0x0F6,
    0x0F8, 0x0FA, 0x0FC, 0x0FF, 0x0CF, 0x0AF, 0x08F, 0x06F, 0x04F,
    0x02F, 0x00F, 0x20F, 0x40F, 0x60F, 0x80F, 0xA0F, 0xC0F, 0xF0F,
    0xF0C, 0xF0A, 0xF08, 0xF06, 0xF04, 0xF02, 0xF00
)

_AUTO_COLOR_PRIMARIES: tuple[int, ...] = (
    0xF00, 0xFF0, 0x0F0, 0x0FF, 0x00F, 0xF0F
)

# auto_color_type (1..14, unlabelled) -> (pattern, variant, palette).
# The variants do not run 0,1 per pattern the way you would expect;
# that irregularity is in the format, reproduced rather than tidied.
_AUTO_COLOR_TYPES: dict[int, tuple[int, int, tuple[int, ...]]] = {
    1: (1, 0, _AUTO_COLOR_RAINBOW),
    2: (1, 1, _AUTO_COLOR_RAINBOW),
    3: (2, 0, _AUTO_COLOR_RAINBOW),
    4: (3, 2, _AUTO_COLOR_RAINBOW),
    5: (3, 3, _AUTO_COLOR_RAINBOW),
    6: (4, 4, _AUTO_COLOR_RAINBOW),
    7: (4, 5, _AUTO_COLOR_RAINBOW),
    8: (5, 0, _AUTO_COLOR_PRIMARIES),
    9: (6, 0, _AUTO_COLOR_PRIMARIES),
    10: (6, 1, _AUTO_COLOR_PRIMARIES),
    11: (7, 0, _AUTO_COLOR_RAINBOW),
    12: (7, 1, _AUTO_COLOR_RAINBOW),
    13: (8, 0, _AUTO_COLOR_RAINBOW),
    14: (8, 1, _AUTO_COLOR_RAINBOW),
}


@dataclass
class TextAutoColorContent(ProgramContent):
    """The firmware-animated alternative to TextCustomColorContent.
    Marker 0x05.

    The other half of a text program (see TextCustomColorContent): a
    TextContent (0x01) carries the glyphs and no colour at all, and one
    of these two items carries the colour. Exactly one: a program never
    holds both.

    Where TextCustomColorContent names a colour per character, this
    hands the sign a palette and a pattern number and lets the firmware
    animate it. `auto_color_type` is the app's own 1..14, which its UI
    presents as fourteen unnamed swatches.

    NOT confirmed against hardware: what each pattern actually does on
    the panel is firmware behaviour, and nothing documents it.

    One field to be careful with: the count before the palette is a
    BYTE count, not a colour count. The
    corresponding field in TextCustomColorContent counts characters.
    Same position, same width, different unit.
    """

    CONTENT_TYPE: ClassVar[int] = 0x05

    auto_color_type: int = 1
    speed: int = 230  # the app's own default (mColorfulSpeed 229, sent +1)
    start_column: int = 0
    start_row: int = 0
    show_width: int = 0
    show_height: int = 0

    @property
    def palette(self) -> tuple[int, ...]:
        """The packed 0xRGB colours this type sends."""
        return _AUTO_COLOR_TYPES[self.auto_color_type][2]

    def _body(self) -> bytes:
        if self.auto_color_type not in _AUTO_COLOR_TYPES:
            raise ValueError(
                f"auto_color_type must be 1-14, not {self.auto_color_type}"
            )
        pattern, variant, palette = _AUTO_COLOR_TYPES[self.auto_color_type]
        palette_bytes = b"".join(_u16(color) for color in palette)
        return (
            _u8(self.CONTENT_TYPE)
            + b"\x00" * 7
            + _u16(self.start_column)
            + _u16(self.start_row)
            + _u16(self.show_width)
            + _u16(self.show_height)
            + _u8(pattern)
            + _u8(self.speed)
            + _u8(variant)
            + b"\x00"
            + _u16(len(palette_bytes))
            + palette_bytes
        )

@dataclass
class TextCustomColorContent(ProgramContent):
    """Per-character colours for a text program. Marker 0x06.

    Text is not one content item but two, and this is the half that was
    missing. A text program carries a TextContent (0x01) with the glyph
    data AND a colour item alongside it -- either this one, or
    TextAutoColor (0x05). Sent on its own, TextContent renders nothing
    at all: confirmed live, and it is exactly why type-0 glyph items
    appeared not to work and got documented as an unconfirmed encoding.
    The glyphs were always right; they just had no colour.

    Field order verified byte for byte against a capture of "123"
    going to a panel -- note this item
    carries moveSpace BEFORE the position fields, unlike every other
    content type.

    `widths` is one byte per character and must sum to `total_width`;
    `colors` is one 0xRRGGBB per character.
    """

    CONTENT_TYPE: ClassVar[int] = 0x06

    widths: list[int]
    colors: list[int]
    move_space: int = 32
    start_column: int = 0
    start_row: int = 0
    show_width: int = 0
    show_height: int = 0
    mode: int = 2
    speed: int = 0xF6
    stay_time: int = 3

    def _body(self) -> bytes:
        return (
            _u8(self.CONTENT_TYPE)
            + b"\x00" * 5
            + _u16(self.move_space)
            + _u16(self.start_column)
            + _u16(self.start_row)
            + _u16(self.show_width)
            + _u16(self.show_height)
            + _u8(self.mode)
            + _u8(self.speed)
            + _u8(self.stay_time)
            + b"\x00"
            + _u16(len(self.widths))
            + _u16(sum(self.widths))
            + bytes(self.widths)
            + b"".join(_rgb444_transfer(color) for color in self.colors)
        )


class TextShowMode:
    """TextContent.mode values."""

    STATIC = 1
    LEFT_CONTINUE = 2
    RIGHT_CONTINUE = 3
    UP_MODE = 4
    DOWN_MODE = 5
    ACCUMULATE_MODE = 6
    PICTURE_MODE = 7
    SHINING_MODE = 8
    LEFT_PAN_MODE = 9
    RIGHT_PAN_MODE = 10
    LEFT_COVER_MODE = 11
    RIGHT_COVER_MODE = 12
    LEFT_RIGHT_MODE = 13


@dataclass
class AnimationContent(Request):
    """A sequence of Graffiti-style frames. Marker 0x03.

    Does NOT use ProgramContent's shared header -- the real protocol's
    Animation header is [0x03][0x01][6 reserved bytes], not the standard
    [marker][7 reserved bytes] shape every other content type uses.

    `frames` is a list of already-RGB444-packed per-frame pixel data.
    `delays`, if given, is a per-frame duration in the same unit as
    `frame_duration_ms` (one entry per frame); otherwise `frame_duration_ms`
    is repeated for every frame, matching the real app's own fallback.
    """

    CONTENT_TYPE: ClassVar[int] = 0x03

    frames: list[bytes]
    layer_type: int = 1
    start_column: int = 0
    start_row: int = 0
    show_width: int = 0
    show_height: int = 0
    frame_duration_ms: int = 100
    delays: Optional[list[int]] = None

    def encode(self) -> bytes:
        header = (
            _u8(self.CONTENT_TYPE)
            + _u8(0x01)
            + b"\x00" * 6
            + _u8(self.layer_type)
            + _u16(self.start_column)
            + _u16(self.start_row)
            + _u16(self.show_width)
            + _u16(self.show_height)
            + _u8(0x00)  # always zero in every capture; purpose unconfirmed
        )
        delays = self.delays if self.delays is not None else [self.frame_duration_ms] * len(self.frames)
        body = (
            header
            + _u16(len(self.frames))
            + b"".join(_u16(d) for d in delays)
            + b"".join(self.frames)
        )
        return _length_prefixed(body)


def _rgb444_transfer(color: int) -> bytes:
    """The Text/emoji colour path: 2 bytes, [0x0R][0xGB], with each
    channel through a non-linear transfer curve -- >=238 maps to 15,
    <=30 to 0, otherwise (v-30)/15 + 1.

    Distinct from _rgb444_flat below, which Clock, Countdown and
    Scoreboard use: same two-byte shape, different rounding. Getting
    them the wrong way round shifts colours subtly rather than
    obviously, which is the sort of bug that survives a long time.
    """
    def level(value: int) -> int:
        if value >= 238:
            return 15
        if value <= 30:
            return 0
        return (value - 30) // 15 + 1

    red = level((color >> 16) & 0xFF)
    green = level((color >> 8) & 0xFF)
    blue = level(color & 0xFF)
    return bytes([red & 0x0F, ((green & 0x0F) << 4) | (blue & 0x0F)])


def _rgb444_flat(color: int) -> bytes:
    """2-byte RGB444 pack used by Clock/Countdown color fields
    (ColorUtils.getColorDataWithColor): flat value/16 per channel,
    byte0=0x0R, byte1=0xGB. Different quantization than the Text/emoji
    color path -- see the doc's "Confirmed from source" note."""
    r = (color >> 16) & 0xFF
    g = (color >> 8) & 0xFF
    b = color & 0xFF
    return bytes([r // 16, ((g // 16) << 4) | (b // 16)])


@dataclass
class ClockContent(ProgramContent):
    """Live clock display. Marker 0x07.

    Bitmap tables (hour_digits, colon, second_colon, ampm_glyph) are
    already-rendered bytes the caller supplies -- see "Font & Bitmap
    Encoding" for the confirmed 10-glyph digit-table shape. Colors are
    plain 0xRRGGBB ints, packed here via the confirmed Clock/Countdown
    RGB444 quantization.

    Does NOT use ProgramContent's shared _header(): Clock's header is only
    [marker][7 reserved zeros][layerType] -- no startColumn/startRow/
    showWidth/showHeight. Position is expressed per-field instead
    (hour_start_column, minute_start_row, etc. below).
    """

    CONTENT_TYPE: ClassVar[int] = 0x07

    hour_digits: bytes  # 10 concatenated glyphs (0-9)
    layer_type: int = 1
    is_24_hour: bool = True
    blinking_colon: bool = False
    show_time: int = 10
    num_height: int = 1
    num_width: int = 1
    hour_color: int = 0xFFFFFF
    hour_start_column: int = 0
    hour_start_row: int = 0
    hour_width: int = 0
    hour_height: int = 0
    space_hour_color: int = 0xFFFFFF
    space_hour_start_column: int = 0
    space_hour_start_row: int = 0
    space_hour_width: int = 0
    space_hour_height: int = 0
    colon: bytes = b""
    minute_color: int = 0xFFFFFF
    minute_start_column: int = 0
    minute_start_row: int = 0
    minute_width: int = 0
    minute_height: int = 0
    space_minute_color: int = 0xFFFFFF
    space_minute_start_column: int = 0
    space_minute_start_row: int = 0
    space_minute_width: int = 0
    space_minute_height: int = 0
    show_space_minute_color: bool = False
    seconds_color: int = 0xFFFFFF
    seconds_start_column: int = 0
    seconds_start_row: int = 0
    seconds_width: int = 0
    seconds_height: int = 0
    ampm_color: int = 0xFFFFFF
    ampm_start_column: int = 0
    ampm_start_row: int = 0
    ampm_width: int = 0
    ampm_height: int = 0
    ampm_glyph: bytes = b""

    def _time_format_flags(self) -> int:
        # 00=12h/no-blink, 01=24h/no-blink, 02=12h/blink, 03=24h/blink
        if self.is_24_hour:
            return 0x03 if self.blinking_colon else 0x01
        return 0x02 if self.blinking_colon else 0x00

    def _body(self) -> bytes:
        header = _u8(self.CONTENT_TYPE) + b"\x00" * 7 + _u8(self.layer_type)
        second_colon = self.colon if self.show_space_minute_color else b""
        return (
            header
            + _u8(self._time_format_flags())
            + _u16(self.show_time)
            + _u16(self.num_height)
            + _u16(self.num_width)
            + _u16(len(self.hour_digits))
            + self.hour_digits
            + _rgb444_flat(self.hour_color)
            + _u16(self.hour_start_column)
            + _u16(self.hour_start_row)
            + _u16(self.hour_width)
            + _u16(self.hour_height)
            + _rgb444_flat(self.space_hour_color)
            + _u16(self.space_hour_start_column)
            + _u16(self.space_hour_start_row)
            + _u16(self.space_hour_width)
            + _u16(self.space_hour_height)
            + _u16(len(self.colon))
            + self.colon
            + _rgb444_flat(self.minute_color)
            + _u16(self.minute_start_column)
            + _u16(self.minute_start_row)
            + _u16(self.minute_width)
            + _u16(self.minute_height)
            + _rgb444_flat(self.space_minute_color)
            + _u16(self.space_minute_start_column)
            + _u16(self.space_minute_start_row)
            + _u16(self.space_minute_width)
            + _u16(self.space_minute_height)
            + _u16(len(second_colon))
            + second_colon
            + _rgb444_flat(self.seconds_color)
            + _u16(self.seconds_start_column)
            + _u16(self.seconds_start_row)
            + _u16(self.seconds_width)
            + _u16(self.seconds_height)
            + _rgb444_flat(self.ampm_color)
            + _u16(self.ampm_start_column)
            + _u16(self.ampm_start_row)
            + _u16(self.ampm_width)
            + _u16(self.ampm_height)
            + _u16(len(self.ampm_glyph))
            + self.ampm_glyph
        )


@dataclass
class TimeCountContent(ProgramContent):
    """Countdown/stopwatch display. Marker 0x0A. Mirrors ClockContent's
    layout closely (see that class's doc) but confirmed as its OWN
    distinct shape rather than assumed from Clock: no
    is_24_hour/blinking_colon/show_time/ampm fields at all, and
    `time_count_mode` sits right after layer_type instead. `hour_digits`
    is followed by hour+space_hour (colon) fields, THEN minute fields,
    THEN its own colon, THEN seconds fields -- each of the two colon slots
    gets its own copy of `colon` (the real app sends the identical colon
    bitmap twice, once between hour/minute and again between
    minute/seconds, not shared by reference on the wire).

    Does NOT use ProgramContent's shared _header() -- same reasoning as
    Clock: no startColumn/startRow/showWidth/showHeight, position is
    per-field. `time_count_mode`: 0=countdown, 1=stopwatch (from
    DeviceManager.CoolleduxTimeCountProgramContent.timeCountMode's
    default of 1 alongside CountdownControl/StopwatchControl both using
    this same content type, distinguished only by this field)."""

    CONTENT_TYPE: ClassVar[int] = 0x0A

    hour_digits: bytes
    layer_type: int = 1
    time_count_mode: int = 0
    num_height: int = 16
    num_width: int = 7
    hour_color: int = 0xFFFFFF
    hour_start_column: int = 0
    hour_start_row: int = 0
    hour_width: int = 0
    hour_height: int = 0
    space_hour_color: int = 0xFFFFFF
    space_hour_start_column: int = 0
    space_hour_start_row: int = 0
    space_hour_width: int = 0
    space_hour_height: int = 0
    colon: bytes = b""
    minute_color: int = 0xFFFFFF
    minute_start_column: int = 0
    minute_start_row: int = 0
    minute_width: int = 0
    minute_height: int = 0
    space_minute_color: int = 0xFFFFFF
    space_minute_start_column: int = 0
    space_minute_start_row: int = 0
    space_minute_width: int = 0
    space_minute_height: int = 0
    seconds_color: int = 0xFFFFFF
    seconds_start_column: int = 0
    seconds_start_row: int = 0
    seconds_width: int = 0
    seconds_height: int = 0

    def _body(self) -> bytes:
        header = _u8(self.CONTENT_TYPE) + b"\x00" * 7 + _u8(self.layer_type)
        return (
            header
            + _u8(self.time_count_mode)
            + _u16(self.num_height)
            + _u16(self.num_width)
            + _u16(len(self.hour_digits))
            + self.hour_digits
            + _rgb444_flat(self.hour_color)
            + _u16(self.hour_start_column)
            + _u16(self.hour_start_row)
            + _u16(self.hour_width)
            + _u16(self.hour_height)
            + _rgb444_flat(self.space_hour_color)
            + _u16(self.space_hour_start_column)
            + _u16(self.space_hour_start_row)
            + _u16(self.space_hour_width)
            + _u16(self.space_hour_height)
            + _u16(len(self.colon))
            + self.colon
            + _rgb444_flat(self.minute_color)
            + _u16(self.minute_start_column)
            + _u16(self.minute_start_row)
            + _u16(self.minute_width)
            + _u16(self.minute_height)
            + _rgb444_flat(self.space_minute_color)
            + _u16(self.space_minute_start_column)
            + _u16(self.space_minute_start_row)
            + _u16(self.space_minute_width)
            + _u16(self.space_minute_height)
            + _u16(len(self.colon))
            + self.colon
            + _rgb444_flat(self.seconds_color)
            + _u16(self.seconds_start_column)
            + _u16(self.seconds_start_row)
            + _u16(self.seconds_width)
            + _u16(self.seconds_height)
        )


@dataclass
class ScoreboardContent(ProgramContent):
    """Scoreboard display. Marker 0x0B.

    Layout confirmed byte-for-byte against a reference encoder.

    Three independent digit tables, each preceded by its own num
    height/width pair: scores, total-scores, and the clock. The real app
    sends a different (or empty) table per panel size -- on a 16x32 panel
    only the SCORE table is non-empty; the total-score, time and colon
    tables are all sent as length-0. Leave those fields empty and their
    widths/heights at 0 to match.

    Header differs from Clock/TimeCount: after the usual marker + 7 zero
    bytes + layerType there's ONE MORE reserved zero byte, where
    TimeCount puts timeCountMode. Field groups are (color 2B, startColumn,
    startRow, width, height) -- note width BEFORE height on the wire, the
    reverse of the order the fields are usually declared in.

    Note there is no standard scoreboard layout for a 16x32 panel --
    the smallest one in common use is 16x64 -- though a 16x32 score font
    exists and the firmware supports it. Callers pick their own
    positions."""

    CONTENT_TYPE: ClassVar[int] = 0x0B

    score_digits: bytes
    layer_type: int = 0
    score_num_height: int = 8
    score_num_width: int = 5
    score_host_color: int = 0xFFFFFF
    score_host_start_column: int = 0
    score_host_start_row: int = 0
    score_host_width: int = 0
    score_host_height: int = 0
    score_visit_color: int = 0xFFFFFF
    score_visit_start_column: int = 0
    score_visit_start_row: int = 0
    score_visit_width: int = 0
    score_visit_height: int = 0
    score_total_num_height: int = 0
    score_total_num_width: int = 0
    score_total_digits: bytes = b""
    score_total_host_color: int = 0xFFFFFF
    score_total_host_start_column: int = 0
    score_total_host_start_row: int = 0
    score_total_host_width: int = 0
    score_total_host_height: int = 0
    score_total_visit_color: int = 0xFFFFFF
    score_total_visit_start_column: int = 0
    score_total_visit_start_row: int = 0
    score_total_visit_width: int = 0
    score_total_visit_height: int = 0
    time_num_height: int = 0
    time_num_width: int = 0
    time_digits: bytes = b""
    minute_color: int = 0xFFFFFF
    minute_start_column: int = 0
    minute_start_row: int = 0
    minute_width: int = 0
    minute_height: int = 0
    space_minute_color: int = 0xFFFFFF
    space_minute_start_column: int = 0
    space_minute_start_row: int = 0
    space_minute_width: int = 0
    space_minute_height: int = 0
    colon: bytes = b""
    seconds_color: int = 0xFFFFFF
    seconds_start_column: int = 0
    seconds_start_row: int = 0
    seconds_width: int = 0
    seconds_height: int = 0

    @staticmethod
    def _field(color: int, start_column: int, start_row: int, width: int, height: int) -> bytes:
        return (
            _rgb444_flat(color)
            + _u16(start_column)
            + _u16(start_row)
            + _u16(width)
            + _u16(height)
        )

    def _body(self) -> bytes:
        header = _u8(self.CONTENT_TYPE) + b"\x00" * 7 + _u8(self.layer_type) + b"\x00"
        return (
            header
            + _u16(self.score_num_height)
            + _u16(self.score_num_width)
            + _u16(len(self.score_digits))
            + self.score_digits
            + self._field(self.score_host_color, self.score_host_start_column,
                          self.score_host_start_row, self.score_host_width, self.score_host_height)
            + self._field(self.score_visit_color, self.score_visit_start_column,
                          self.score_visit_start_row, self.score_visit_width, self.score_visit_height)
            + _u16(self.score_total_num_height)
            + _u16(self.score_total_num_width)
            + _u16(len(self.score_total_digits))
            + self.score_total_digits
            + self._field(self.score_total_host_color, self.score_total_host_start_column,
                          self.score_total_host_start_row, self.score_total_host_width,
                          self.score_total_host_height)
            + self._field(self.score_total_visit_color, self.score_total_visit_start_column,
                          self.score_total_visit_start_row, self.score_total_visit_width,
                          self.score_total_visit_height)
            + _u16(self.time_num_height)
            + _u16(self.time_num_width)
            + _u16(len(self.time_digits))
            + self.time_digits
            + self._field(self.minute_color, self.minute_start_column, self.minute_start_row,
                          self.minute_width, self.minute_height)
            + self._field(self.space_minute_color, self.space_minute_start_column,
                          self.space_minute_start_row, self.space_minute_width,
                          self.space_minute_height)
            + _u16(len(self.colon))
            + self.colon
            + self._field(self.seconds_color, self.seconds_start_column, self.seconds_start_row,
                          self.seconds_width, self.seconds_height)
        )


# ---------------------------------------------------------------------------
# Top-level standalone commands (marker + payload, no length-prefix wrapper)
# ---------------------------------------------------------------------------


@dataclass
class ProgramStartRequest(Request):
    """Announces an incoming program upload. Marker 0x02."""

    program_data: bytes  # the UNCOMPRESSED content this announces
    index: int = 0
    count: int = 1
    show_count: int = 1

    def encode(self) -> bytes:
        from crc import crc32

        return (
            _u8(0x02)
            + _u32(crc32(self.program_data))
            + _u32(len(self.program_data))
            + _u8(self.index)
            + _u8(self.count)
            + _u8(self.show_count)
        )


@dataclass
class ProgramDataRequest(Request):
    """One chunk of an LZSS-compressed program upload. Marker 0x03.

    The checksum is a running XOR over everything *after* the marker byte
    (0x00 + TotalLength + ChunkIndex + ChunkLength + chunk data) -- the
    marker itself is added afterward by getSendDataWithInfo and is not
    part of the XOR.
    """

    total_length: int  # total compressed length across all chunks, in bytes
    chunk_index: int
    chunk_data: bytes

    def encode(self) -> bytes:
        body = (
            _u8(0x00)
            + _u32(self.total_length)
            + _u16(self.chunk_index)
            + _u16(len(self.chunk_data))
            + self.chunk_data
        )
        checksum = 0
        for byte in body:
            checksum ^= byte
        return _u8(0x03) + body + _u8(checksum)


@dataclass
class SetBrightnessRequest(Request):
    brightness: int

    def encode(self) -> bytes:
        return _u8(0x04) + _u8(self.brightness)


@dataclass
class SetPowerRequest(Request):
    is_on: bool

    def encode(self) -> bytes:
        return _u8(0x05) + _u8(1 if self.is_on else 0)


@dataclass
class SynchronizeTimeRequest(Request):
    year: int  # full year, e.g. 2026 -- encoded as year-2000
    month: int
    day: int
    weekday: int  # 1-7, Monday=1
    hour: int
    minute: int
    second: int

    def encode(self) -> bytes:
        return (
            _u8(0x09)
            + _u8(self.year - 2000)
            + _u8(self.month)
            + _u8(self.day)
            + _u8(self.weekday)
            + _u8(self.hour)
            + _u8(self.minute)
            + _u8(self.second)
        )


@dataclass
class TimerSwitchItem:
    """One scheduled entry for SetTimerSwitchRequest: six bytes, in the
    order below.

    An entry carries ONE time and ONE action, so turning the sign on in
    the morning and off at night is two entries, not one. Four entries
    is the usual cap, though nothing in the format enforces one, and the
    whole list is always resent since there is no incremental edit.
    Sending no items at all clears the schedule.

    `weekdays` is a bitmask: Mon=1, Tue=2, Wed=4, Thu=8, Fri=16, Sat=32,
    Sun=64. Zero is how the app encodes "never" (no repeat) -- there's
    no separate one-shot flag.

    Schedules are evaluated against the device's own clock, so
    SynchronizeTimeRequest should be sent first or the times mean
    nothing.
    """

    hour: int
    minute: int
    turns_display_on: bool
    weekdays: int = 0
    enabled: bool = True

    MONDAY: ClassVar[int] = 1
    TUESDAY: ClassVar[int] = 2
    WEDNESDAY: ClassVar[int] = 4
    THURSDAY: ClassVar[int] = 8
    FRIDAY: ClassVar[int] = 16
    SATURDAY: ClassVar[int] = 32
    SUNDAY: ClassVar[int] = 64

    def encode(self) -> bytes:
        return bytes([
            1 if self.enabled else 0,
            self.hour,
            self.minute,
            self.weekdays,
            1 if self.turns_display_on else 0,
            0,  # fixed reserved byte, always zero
        ])


@dataclass
class SetTimerSwitchRequest(Request):
    items: list[TimerSwitchItem] = field(default_factory=list)

    def encode(self) -> bytes:
        return _u8(0x0A) + _u8(len(self.items)) + b"".join(item.encode() for item in self.items)


@dataclass
class QueryTimerSwitchRequest(Request):
    def encode(self) -> bytes:
        return _u8(0x0B)


class FlipMode:
    """SetFlip values, in this order: no flip, XY, X, Y.

    Note XY is 1 and comes BEFORE the single-axis options -- the
    obvious guess, that 1 and 2 are the single axes and 3 is both, is
    wrong."""

    NONE = 0
    XY = 1      # both axes, i.e. a 180-degree rotation
    X = 2       # mirrored left-to-right
    Y = 3       # mirrored top-to-bottom


@dataclass
class SetFlipRequest(Request):
    """Marker 0x0C. The payload is a MODE, not a flag.

    It takes an int, and there are four choices. Sending a bare 0/1
    only ever reaches `none` and `XY`,
    which is why X and Y appeared to do the same thing as XY: nothing
    was sending 2 or 3 at all.

    `is_flipped` is kept because it reads naturally for the common
    case and because callers already use it; it selects XY, the
    180-degree rotation that was the only flip confirmed on hardware.
    Pass `mode` to reach the single-axis options.
    """

    is_flipped: bool = False
    mode: Optional[int] = None

    def encode(self) -> bytes:
        mode = self.mode
        if mode is None:
            mode = FlipMode.XY if self.is_flipped else FlipMode.NONE
        return _u8(0x0C) + _u8(mode)


def _encode_password(marker: int, password: str, nonce: int) -> bytes:
    """Shared XOR-nonce encoding for VerifyPassword/SetPassword -- see the
    doc's "Password encoding" note. Every digit is XORed against a random
    one-byte nonce, then a running XOR checksum of everything sent so far
    is appended."""
    payload = [marker, nonce]
    for char in password:
        payload.append(int(char, 16) ^ nonce)
    checksum = 0
    for byte in payload[1:]:
        checksum ^= byte
    payload.append(checksum)
    return bytes(payload)


@dataclass
class VerifyPasswordRequest(Request):
    password: str
    nonce: int  # caller supplies (usually random.randint(0, 255)) for testability

    def encode(self) -> bytes:
        return _encode_password(0x0D, self.password, self.nonce)


@dataclass
class SetPasswordRequest(Request):
    password: str
    nonce: int

    def encode(self) -> bytes:
        return _encode_password(0x0E, self.password, self.nonce)


@dataclass
class CountdownQueryRequest(Request):
    def encode(self) -> bytes:
        return _u8(0x0F) + _u8(0x01)


@dataclass
class CountdownResetRequest(Request):
    hour: int
    minute: int
    seconds: int

    def encode(self) -> bytes:
        return _u8(0x0F) + _u8(0x02) + _u8(self.hour) + _u8(self.minute) + _u8(self.seconds)


@dataclass
class CountdownStartStopRequest(Request):
    is_start: bool

    def encode(self) -> bytes:
        return _u8(0x0F) + _u8(0x03) + _u8(1 if self.is_start else 0)


@dataclass
class StopwatchQueryRequest(Request):
    def encode(self) -> bytes:
        return _u8(0x10) + _u8(0x01)


@dataclass
class StopwatchResetRequest(Request):
    def encode(self) -> bytes:
        return _u8(0x10) + _u8(0x02)


@dataclass
class StopwatchStartStopRequest(Request):
    is_start: bool

    def encode(self) -> bytes:
        return _u8(0x10) + _u8(0x03) + _u8(1 if self.is_start else 0)


@dataclass
class ScoreboardQueryRequest(Request):
    def encode(self) -> bytes:
        return _u8(0x11) + _u8(0x01)


@dataclass
class ScoreboardSetScoreRequest(Request):
    host_score: int
    visitor_score: int
    host_total_score: int
    visitor_total_score: int

    def encode(self) -> bytes:
        return (
            _u8(0x11)
            + _u8(0x02)
            + _u16(self.host_score)
            + _u16(self.visitor_score)
            + _u8(self.host_total_score)
            + _u8(self.visitor_total_score)
        )


@dataclass
class ScoreboardSetTimeRequest(Request):
    minute: int
    seconds: int
    is_count_down: bool

    def encode(self) -> bytes:
        return (
            _u8(0x11)
            + _u8(0x03)
            + _u8(self.minute)
            + _u8(self.seconds)
            + _u8(1 if self.is_count_down else 0)
        )


@dataclass
class ScoreboardStartStopRequest(Request):
    is_start: bool

    def encode(self) -> bytes:
        return _u8(0x11) + _u8(0x04) + _u8(1 if self.is_start else 0)


@dataclass
class SetRhythmTypeRequest(Request):
    """Selects the Rhythm visual style, sent once before playback starts."""

    rhythm_type: int

    def encode(self) -> bytes:
        return _u8(0x06) + _u8(self.rhythm_type)


@dataclass
class RhythmDataRequest(Request):
    """One frame of live bar-height data, sent continuously while music
    plays. Always exactly 8 bars; each is a pixel height clamped to the
    panel's row count -- see "Rhythm" in the doc."""

    rhythm_type: int
    bars: list[int]  # exactly 8 values

    def encode(self) -> bytes:
        if len(self.bars) != 8:
            raise ValueError(f"Rhythm data must have exactly 8 bars, got {len(self.bars)}")
        return _u8(0x01) + _u8(self.rhythm_type) + b"".join(_u8(b) for b in self.bars)


@dataclass
class DeviceInfoRequest(Request):
    def encode(self) -> bytes:
        return _u8(0x1F)


@dataclass
class StartOTAUpdateRequest(Request):
    update_data: bytes  # the uncompressed firmware image

    def encode(self) -> bytes:
        from crc import crc32

        return _u8(0xFE) + _u32(crc32(self.update_data)) + _u32(len(self.update_data))
