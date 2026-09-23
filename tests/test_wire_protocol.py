"""Verification tests from the approved plan, items 1-4 (item 5, the live
DeviceInfoQuery round trip, needs real hardware and isn't run here).

Note on item 3 (LZSS round-trip against the real captured countdown
chunk): the actual captured bytes were never saved to disk this session,
only their metadata (CRC32=0x0628874C, length=194) in wire_format.md.
Without the raw chunk we can't replay that specific capture, so this
file's LZSS check is a self-consistency round-trip on synthetic data
instead. Same caveat applies to the CRC32 check.
"""

from __future__ import annotations

import random

import pytest

import envelope
import lzss
from pathlib import Path

from crc import crc32, protocol_crc32
from requests import (
    AnimationContent,
    TextAutoColorContent,
    TextCustomColorContent,
    ClockContent,
    CountdownQueryRequest,
    CountdownResetRequest,
    CountdownStartStopRequest,
    DeviceInfoRequest,
    FlipMode,
    GraffitiContent,
    ProgramDataRequest,
    ProgramStartRequest,
    SetBrightnessRequest,
    SetFlipRequest,
    QueryTimerSwitchRequest,
    ScoreboardContent,
    ScoreboardQueryRequest,
    ScoreboardSetScoreRequest,
    ScoreboardStartStopRequest,
    SetPowerRequest,
    SetTimerSwitchRequest,
    StopwatchQueryRequest,
    StopwatchResetRequest,
    StopwatchStartStopRequest,
    SynchronizeTimeRequest,
    TextShowMode,
    TimeCountContent,
    TimerSwitchItem,
    TextContent,
    VerifyPasswordRequest,
)
from responses import (
    BrightnessResponse,
    CountdownControlResponse,
    DeviceInfoResponse,
    FlipResponse,
    PowerResponse,
    ProgramDataResponse,
    ProgramStartResponse,
    QueryTimerSwitchResponse,
    RawResponse,
    ScoreboardControlResponse,
    SetPasswordResponse,
    SetTimerSwitchResponse,
    StopwatchControlResponse,
    SynchronizeTimeResponse,
    VerifyPasswordResponse,
    dispatch,
)


def test_envelope_round_trip_with_marker_bytes_in_payload() -> None:
    payload = bytes([0x00, 0x01, 0x02, 0x03, 0xFF, 0x01, 0x03, 0x02])
    frame = envelope.encode(payload)
    assert frame[0] == envelope.START_BYTE
    assert frame[-1] == envelope.END_BYTE
    assert envelope.decode(frame) == payload


def test_envelope_round_trip_empty_and_random_payloads() -> None:
    assert envelope.decode(envelope.encode(b"")) == b""
    rng = random.Random(0)
    for _ in range(20):
        payload = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 200)))
        assert envelope.decode(envelope.encode(payload)) == payload


def test_crc32_is_zlib_but_the_protocol_s_own_crc_is_not() -> None:
    """What we send, versus what the protocol's own client computes.

    These are different algorithms, which was only established by
    watching another client upload to a simulated panel: it announced
    0x6fa5f0a1 where zlib gives 0xc53f6b1b. The sign accepts both, so it
    plainly doesn't check -- but the distinction is real and this pins
    it, because crc.py used to claim the two were the same thing.
    """
    import zlib

    data = b"the quick brown fox jumps over the lazy dog"
    assert crc32(data) == zlib.crc32(data)
    assert protocol_crc32(data) != crc32(data)
    # Empty input is definitional: the initial value, untouched.
    assert protocol_crc32(b"") == 0xFFFFFFFF
    # Regression guards, not external reference values -- no standard
    # names this algorithm, so there is nothing published to check
    # against. The real validation is the live one recorded in crc.py:
    # a captured upload announced 0x6fa5f0a1 and this produced it.
    assert protocol_crc32(bytes(1)) == 0xC704DD7B
    assert protocol_crc32(b"123456789") == 0x1556F485


def test_lzss_decompresses_a_real_upload() -> None:
    """Our LZSS against bytes a foreign compressor produced.

    Every other check of it is a round trip through our own compressor,
    which cannot catch a shared misunderstanding. This is an animation
    captured off the air, and it is the first genuinely independent
    input the decompressor has ever had.

    Also exercises the program wrapper: 8 reserved zeros, an item count,
    one more reserved byte, then each item as a 4-byte length (including
    itself) followed by its body.
    """
    fixture = Path(__file__).parent / "data" / "capture_animation.bin"
    compressed = fixture.read_bytes()
    program = lzss.decompress(compressed)

    assert len(compressed) == 4257
    assert len(program) == 9268
    assert program[:8] == bytes(8)
    assert program[8] == 1  # one content item

    item_length = int.from_bytes(program[10:14], "big")
    assert item_length == 9258
    assert program[14] == 0x03  # AnimationContent
    assert 10 + item_length == len(program)


def test_lzss_round_trip_arbitrary_data() -> None:
    rng = random.Random(1)
    samples = [
        b"",
        b"a",
        b"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        bytes(range(256)) * 4,
        bytes(rng.randrange(256) for _ in range(2000)),
    ]
    for data in samples:
        assert lzss.decompress(lzss.compress(data)) == data


def test_lzss_round_trip_highly_repetitive_data_exercises_overlapping_matches() -> None:
    data = b"ABCD" * 300
    compressed = lzss.compress(data)
    assert len(compressed) < len(data)
    assert lzss.decompress(compressed) == data


def test_program_start_request_encoding() -> None:
    content = b"\x00" * 194
    req = ProgramStartRequest(program_data=content, index=0, count=1, show_count=1)
    encoded = req.encode()
    assert encoded[0] == 0x02
    assert int.from_bytes(encoded[1:5], "big") == crc32(content)
    assert int.from_bytes(encoded[5:9], "big") == 194
    assert encoded[9] == 0
    assert encoded[10] == 1
    assert encoded[11] == 1


def test_program_data_request_checksum_is_running_xor() -> None:
    req = ProgramDataRequest(total_length=100, chunk_index=0, chunk_data=b"\x01\x02\x03")
    encoded = req.encode()
    assert encoded[0] == 0x03
    assert encoded[1] == 0x00
    assert int.from_bytes(encoded[2:6], "big") == 100
    assert int.from_bytes(encoded[6:8], "big") == 0
    assert int.from_bytes(encoded[8:10], "big") == 3
    assert encoded[10:13] == b"\x01\x02\x03"
    # Checksum excludes the marker byte (encoded[0]): the XOR runs over
    # the payload before the marker is prepended by the framing.
    expected_checksum = 0
    for byte in encoded[1:-1]:
        expected_checksum ^= byte
    assert encoded[-1] == expected_checksum


def test_set_brightness_request() -> None:
    assert SetBrightnessRequest(brightness=200).encode() == bytes([0x04, 200])


def test_set_power_request() -> None:
    assert SetPowerRequest(is_on=True).encode() == bytes([0x05, 1])
    assert SetPowerRequest(is_on=False).encode() == bytes([0x05, 0])


def test_set_flip_request() -> None:
    assert SetFlipRequest(is_flipped=True).encode() == bytes([0x0C, 1])
    assert SetFlipRequest(is_flipped=False).encode() == bytes([0x0C, 0])


def test_set_flip_carries_a_mode_not_a_flag() -> None:
    """0x0C takes one of four values, and the order is not the obvious one.

    The four entries come in this order: no flip 0, XY 1, X 2, Y 3. So
    XY -- both axes, a 180 degree rotation -- comes BEFORE the two
    single-axis flips, and guessing 1=X, 2=Y, 3=both would be wrong at
    every value.

    Sending a bare 0/1 only ever reaches `none` and `XY`, which is why
    picking X or Y in the app appeared to do the same thing as XY:
    nothing was distinguishing them.
    """
    assert FlipMode.NONE == 0 and FlipMode.XY == 1
    assert FlipMode.X == 2 and FlipMode.Y == 3
    for mode in (FlipMode.NONE, FlipMode.XY, FlipMode.X, FlipMode.Y):
        assert SetFlipRequest(mode=mode).encode() == bytes([0x0C, mode])
    # The flag is a shorthand for XY, the only flip confirmed on
    # hardware, not for "some flip or other".
    assert SetFlipRequest(is_flipped=True).encode() == SetFlipRequest(mode=FlipMode.XY).encode()


def test_synchronize_time_request() -> None:
    req = SynchronizeTimeRequest(year=2026, month=9, day=17, weekday=4, hour=13, minute=5, second=30)
    assert req.encode() == bytes([0x09, 26, 9, 17, 4, 13, 5, 30])


def test_device_info_request_is_bare_marker() -> None:
    assert DeviceInfoRequest().encode() == bytes([0x1F])


def test_verify_password_request_xor_and_checksum() -> None:
    req = VerifyPasswordRequest(password="12", nonce=0x10)
    encoded = req.encode()
    assert encoded[0] == 0x0D
    assert encoded[1] == 0x10
    assert encoded[2] == (0x1 ^ 0x10)
    assert encoded[3] == (0x2 ^ 0x10)
    checksum = 0
    for byte in encoded[1:-1]:
        checksum ^= byte
    assert encoded[-1] == checksum


def test_graffiti_content_marker_and_header() -> None:
    pixel_data = b"\xAB" * 32
    content = GraffitiContent(pixel_data=pixel_data, show_width=16, show_height=16)
    body = content._body()
    assert body[0] == 0x02
    assert body[1:8] == b"\x00" * 7
    header_len = 8 + 1 + 2 + 2 + 2 + 2
    assert body[8] == 1  # layer_type
    encoded = content.encode()
    total_length = int.from_bytes(encoded[0:4], "big")
    assert total_length == 4 + len(body)


def test_animation_content_has_distinct_header_shape() -> None:
    frames = [b"\x11" * 4, b"\x22" * 4]
    content = AnimationContent(frames=frames, show_width=4, show_height=1, frame_duration_ms=50)
    encoded = content.encode()
    body = encoded[4:]
    assert body[0] == 0x03
    assert body[1] == 0x01  # literal sub-byte, unique to Animation
    assert body[2:8] == b"\x00" * 6
    # layer_type, start_column(2), start_row(2), show_width(2), show_height(2), then the
    # mystery "00" byte, then frame count (2B)
    offset = 8 + 1 + 2 + 2 + 2 + 2
    assert body[offset] == 0x00
    frame_count = int.from_bytes(body[offset + 1: offset + 3], "big")
    assert frame_count == 2


def test_text_content_marker_and_move_space_defaults() -> None:
    content = TextContent(items=[(8, 0, b"\x00" * 4)], show_width=8, show_height=16)
    body = content._body()
    assert body[0] == 0x01
    assert body[1:8] == b"\x00" * 7
    encoded = content.encode()
    assert int.from_bytes(encoded[0:4], "big") == 4 + len(body)


def test_clock_content_header_has_no_position_fields() -> None:
    # Clock's header is [marker][7 zeros][layerType] only, unlike
    # Graffiti/Text/Animation's showWidth/showHeight/startColumn/startRow.
    content = ClockContent(hour_digits=b"\x00" * 50, layer_type=1)
    body = content._body()
    assert body[0] == 0x07
    assert body[1:8] == b"\x00" * 7
    assert body[8] == 1  # layer_type
    # time-format flag comes immediately after layerType, no position fields
    assert body[9] == 0x01  # 24h, no blink (default)


def test_program_start_response_is_binary_flag_not_enum() -> None:
    assert ProgramStartResponse.decode(b"\x00").already_stored is False
    assert ProgramStartResponse.decode(b"\x01").already_stored is True


def test_program_data_response_four_way_status() -> None:
    # data is [reserved 0x00][chunk_index 2B][status 1B] -- confirmed live,
    # the doc's original 3-byte table was missing the reserved byte.
    resp = ProgramDataResponse.decode(bytes([0x00, 0x00, 0x05, 0x02]))
    assert resp.chunk_index == 5
    assert resp.status == 2
    assert resp.accepted is False
    assert ProgramDataResponse.decode(bytes([0x00, 0x00, 0x00, 0x00])).accepted is True


def test_brightness_and_power_and_flip_responses() -> None:
    assert BrightnessResponse.decode(b"\xC8").brightness == 200
    assert PowerResponse.decode(b"\x01").is_on is True
    assert FlipResponse.decode(b"\x00").is_flipped is False


def test_verify_password_response() -> None:
    assert VerifyPasswordResponse.decode(b"\x00").correct is True
    assert VerifyPasswordResponse.decode(b"\x01").correct is False


def test_device_info_response_nine_fields_in_order() -> None:
    payload = bytes([1, 80, 0, 1, 1, 2, 1, 4, 0])
    resp = DeviceInfoResponse.decode(payload)
    assert resp.switch_state == 1
    assert resp.brightness == 80
    assert resp.flip == 0
    assert resp.local_mic_supported == 1
    assert resp.local_mic_on_off == 1
    assert resp.local_mic_mode == 2
    assert resp.show_device_id == 1
    assert resp.max_program_number == 4
    assert resp.remote_enable == 0


def test_dispatch_routes_by_marker_and_falls_back_to_raw() -> None:
    frame_payload = bytes([0x04, 0x64])
    resp = dispatch(frame_payload)
    assert isinstance(resp, BrightnessResponse)
    assert resp.brightness == 0x64

    # 0x06 (SetRhythmType) has no confirmed reply shape -- a live attempt
    # got no reply at all (see responses.py's module doc), consistent
    # with this device not supporting the feature at all.
    unconfirmed_marker_payload = bytes([0x06, 0x01, 0x02, 0x03])
    resp = dispatch(unconfirmed_marker_payload)
    assert isinstance(resp, RawResponse)
    assert resp.marker == 0x06
    assert resp.payload == bytes([0x01, 0x02, 0x03])


# --- Classes added while verifying the rest of the protocol against real
# hardware. These are pure encode/decode shape checks: the live
# behaviour they describe is covered by led/tests, which needs a sign.


def test_countdown_and_stopwatch_requests_share_a_subcommand_shape() -> None:
    assert CountdownQueryRequest().encode() == bytes([0x0F, 0x01])
    assert CountdownResetRequest(hour=0, minute=1, seconds=30).encode() == bytes([0x0F, 0x02, 0, 1, 30])
    assert CountdownStartStopRequest(is_start=True).encode() == bytes([0x0F, 0x03, 1])
    assert CountdownStartStopRequest(is_start=False).encode() == bytes([0x0F, 0x03, 0])
    # Stopwatch is the same shape under its own marker, and its reset
    # takes no time because it always restarts from zero.
    assert StopwatchQueryRequest().encode() == bytes([0x10, 0x01])
    assert StopwatchResetRequest().encode() == bytes([0x10, 0x02])
    assert StopwatchStartStopRequest(is_start=True).encode() == bytes([0x10, 0x03, 1])


def test_scoreboard_requests() -> None:
    assert ScoreboardQueryRequest().encode() == bytes([0x11, 0x01])
    # Current scores are 2 bytes each, totals only 1 -- easy to get
    # backwards, and the reply is unparsed, so nothing would catch it live.
    assert ScoreboardSetScoreRequest(
        host_score=12, visitor_score=7, host_total_score=3, visitor_total_score=2,
    ).encode() == bytes([0x11, 0x02, 0, 12, 0, 7, 3, 2])
    assert ScoreboardStartStopRequest(is_start=True).encode() == bytes([0x11, 0x04, 1])


def test_timer_switch_item_is_six_bytes_in_order() -> None:
    # enabled, hour, minute, weekday bitmask, turns-display-on, reserved.
    item = TimerSwitchItem(
        hour=12, minute=0, turns_display_on=False, weekdays=TimerSwitchItem.THURSDAY)
    assert item.encode() == bytes([0x01, 0x0C, 0x00, 0x08, 0x00, 0x00])
    # This exact entry was confirmed live: with the device clock set to
    # just before 12:00 on a Thursday, a lit panel switched itself off.

    weekdays = TimerSwitchItem.MONDAY | TimerSwitchItem.WEDNESDAY | TimerSwitchItem.SUNDAY
    every_other_day = TimerSwitchItem(
        hour=7, minute=30, turns_display_on=True, weekdays=weekdays, enabled=False)
    assert every_other_day.encode() == bytes([0x00, 0x07, 0x1E, 1 | 4 | 64, 0x01, 0x00])

    # "Never" (no repeat) is a zero mask, not a separate flag.
    assert TimerSwitchItem(hour=1, minute=2, turns_display_on=True).encode()[3] == 0


def test_set_timer_switch_request_counts_its_items() -> None:
    first = TimerSwitchItem(hour=9, minute=0, turns_display_on=True)
    second = TimerSwitchItem(hour=18, minute=0, turns_display_on=False)
    assert SetTimerSwitchRequest(items=[first, second]).encode() == (
        bytes([0x0A, 2]) + first.encode() + second.encode()
    )
    # No items is how the schedule is cleared, not an empty-list no-op.
    assert SetTimerSwitchRequest(items=[]).encode() == bytes([0x0A, 0])
    assert QueryTimerSwitchRequest().encode() == bytes([0x0B])


def test_query_timer_switch_response_is_a_count_then_entries() -> None:
    # Confirmed live by setting one entry and reading it back; this is
    # the exact reply that came off the sign.
    resp = QueryTimerSwitchResponse.decode(bytes([0x01, 0x01, 0x0C, 0x00, 0x08, 0x00, 0x00]))
    assert resp.item_count == 1
    assert resp.items == (bytes([0x01, 0x0C, 0x00, 0x08, 0x00, 0x00]),)

    # A device with no schedule replies with a bare zero. That had been
    # read as a status byte; it is a count.
    empty = QueryTimerSwitchResponse.decode(bytes([0x00]))
    assert empty.item_count == 0 and empty.items == ()


def test_single_status_byte_responses_follow_the_zero_is_success_convention() -> None:
    assert SynchronizeTimeResponse.decode(b"\x00").success is True
    assert SynchronizeTimeResponse.decode(b"\x01").success is False
    assert SetTimerSwitchResponse.decode(b"\x00").success is True
    assert SetPasswordResponse.decode(b"\x00").success is True


def test_subcommand_echo_responses_keep_their_tail_raw() -> None:
    # The leading echoed sub-command is confirmed; what follows isn't, so
    # it stays `raw` rather than being given field names (a live query 2s
    # into a running countdown read back all zeros, which rules out the
    # obvious "remaining time" reading).
    resp = CountdownControlResponse.decode(bytes([0x01, 0, 0, 0, 0, 0, 0, 0]))
    assert resp.sub_command == 0x01
    assert resp.raw == bytes(7)
    assert StopwatchControlResponse.decode(bytes([0x03, 0x01])).sub_command == 0x03
    assert ScoreboardControlResponse.decode(bytes([0x02, 0xFF])).raw == b"\xFF"


def test_time_count_content_marker_and_header() -> None:
    # Marker 0x0A, and -- like Clock -- no position fields in the header.
    content = TimeCountContent(hour_digits=b"\x00" * 50, layer_type=1)
    body = content._body()
    assert body[0] == 0x0A
    assert body[1:8] == b"\x00" * 7
    assert body[8] == 1  # layer_type
    encoded = content.encode()
    assert int.from_bytes(encoded[0:4], "big") == 4 + len(body)


def test_scoreboard_content_marker_and_header() -> None:
    content = ScoreboardContent(
        score_digits=b"\x00" * 50, score_total_digits=b"\x00" * 50,
        time_digits=b"\x00" * 50, colon=b"\x00" * 4,
    )
    body = content._body()
    assert body[0] == 0x0B
    assert body[1:8] == b"\x00" * 7
    encoded = content.encode()
    assert int.from_bytes(encoded[0:4], "big") == 4 + len(body)


def test_text_show_mode_values_are_stable() -> None:
    assert TextShowMode.STATIC == 1
    assert TextShowMode.LEFT_CONTINUE == 2
    assert TextShowMode.RIGHT_CONTINUE == 3
    assert TextShowMode.UP_MODE == 4
    assert TextShowMode.DOWN_MODE == 5
    # Confirmed on a real panel: these four travel along the axis they
    # name, holding still on the other (see led/tests).


def test_text_custom_color_content_reproduces_a_captured_item() -> None:
    """Our encoder against captured bytes, byte for byte.

    Captured off the air from "123" going to a simulated panel. Every
    other encoder test here checks our output against our
    own reading of the spec; this checks it against the thing the spec
    describes.

    Also pins the field order, which is genuinely irregular: this item
    carries moveSpace BEFORE the position fields, unlike every other
    content type.
    """
    fixture = Path(__file__).parent / "data" / "capture_text_123.bin"
    program = lzss.decompress(fixture.read_bytes())

    assert program[8] == 2, "a text program is TWO items: colours and glyphs"
    item_length = int.from_bytes(program[10:14], "big")
    captured = program[10:10 + item_length]
    assert captured[4] == 0x06

    ours = TextCustomColorContent(
        widths=[4, 8, 7], colors=[0xFF0000] * 3,
        move_space=32, show_width=32, show_height=16,
        mode=2, speed=0xF6, stay_time=3,
    ).encode()
    assert ours == captured


def test_a_text_program_pairs_glyphs_with_a_colour_item() -> None:
    """The second item is TextContent (0x01), carrying type-0 glyphs.

    This is why text never rendered for us: TextContent alone displays
    nothing, because nothing gives its characters a colour. The glyph
    encoding was correct all along -- decoding the first glyph below
    column-major, 2 bytes per column, MSB first, draws a "1".
    """
    fixture = Path(__file__).parent / "data" / "capture_text_123.bin"
    program = lzss.decompress(fixture.read_bytes())

    offset = 10 + int.from_bytes(program[10:14], "big")
    length = int.from_bytes(program[offset:offset + 4], "big")
    text_item = program[offset + 4:offset + length]

    assert text_item[0] == 0x01
    assert int.from_bytes(text_item[13:15], "big") == 32   # showWidth
    assert int.from_bytes(text_item[15:17], "big") == 16   # showHeight
    assert int.from_bytes(text_item[22:24], "big") == 3    # three characters
    assert int.from_bytes(text_item[24:28], "big") == 19   # total width

    # [width][type][glyph], type 0 = font glyph, 2 bytes per column.
    assert text_item[28] == 4 and text_item[29] == 0
    first_glyph = text_item[30:38]
    columns = [
        (first_glyph[i] << 8) | first_glyph[i + 1] for i in range(0, len(first_glyph), 2)
    ]
    lit_rows = [
        [row for row in range(16) if column & (1 << (15 - row))] for column in columns
    ]
    assert lit_rows[0] == [3]                    # serif
    assert lit_rows[1] == [2, 3]
    assert lit_rows[2] == list(range(1, 14))     # the stem of a "1"
    assert lit_rows[3] == []


def test_text_auto_color_content_matches_the_documented_layout() -> None:
    """Every byte here is pinned to the documented layout.

    Type 1 is pattern 1, variant 0, rainbow palette. Not confirmed
    against hardware -- what the pattern does on the panel is firmware
    behaviour nothing documents -- but the ENCODING is fully
    determined, so it can be pinned.
    """
    encoded = TextAutoColorContent(
        auto_color_type=1, speed=230, show_width=32, show_height=16
    ).encode()

    assert int.from_bytes(encoded[:4], "big") == len(encoded)
    body = encoded[4:]
    assert body[0] == 0x05
    assert body[1:8] == bytes(7)
    assert int.from_bytes(body[8:10], "big") == 0      # startColumn
    assert int.from_bytes(body[10:12], "big") == 0     # startRow
    assert int.from_bytes(body[12:14], "big") == 32    # showWidth
    assert int.from_bytes(body[14:16], "big") == 16    # showHeight
    assert body[16] == 1                                # pattern
    assert body[17] == 230                              # speed
    assert body[18] == 0                                # variant
    assert body[19] == 0


def test_the_auto_colour_count_is_bytes_where_custom_colour_counts_characters() -> None:
    """Two sibling items, same field position and width, different unit.

    The palettes are counted one hex string per BYTE, so 43 colours
    announce as 86. The
    0x06 item's equivalent field counts characters. Getting these the
    same way round reads twice as many colours as exist, half of them
    off the end of the item -- which is exactly the bug this pins.
    """
    auto = TextAutoColorContent(auto_color_type=1).encode()[4:]
    declared = int.from_bytes(auto[20:22], "big")
    palette = auto[22:]
    assert declared == len(palette) == 86
    assert len(palette) // 2 == 43

    custom = TextCustomColorContent(widths=[4, 8, 7], colors=[0xFF0000] * 3).encode()[4:]
    assert int.from_bytes(custom[20:22], "big") == 3  # characters, not bytes


def test_every_auto_colour_type_encodes_and_only_two_palettes_exist() -> None:
    """Fourteen choices in the app, two distinct palettes behind them.

    Worth pinning because it says where the fourteen actually differ:
    in the pattern/variant pair, not in the colours. Eleven of the
    fourteen tables are byte-identical copies of the rainbow.
    """
    palettes = set()
    patterns = set()
    for auto_color_type in range(1, 15):
        body = TextAutoColorContent(auto_color_type=auto_color_type).encode()[4:]
        palettes.add(body[22:])
        patterns.add((body[16], body[18]))

    assert len(palettes) == 2
    assert sorted(len(p) // 2 for p in palettes) == [6, 43]
    assert len(patterns) == 14, "the fourteen types must differ somewhere"

    with pytest.raises(ValueError):
        TextAutoColorContent(auto_color_type=15).encode()


def test_the_rainbow_palette_is_a_closed_hue_loop() -> None:
    """First and last entries are the same colour, which is what makes
    a sweep over it seamless -- and the reason the simulator models it
    as a cycle. 43 entries, full-saturation hues."""
    rainbow = TextAutoColorContent(auto_color_type=1).palette
    assert len(rainbow) == 43
    assert rainbow[0] == rainbow[-1] == 0xF00
    for color in rainbow:
        channels = ((color >> 8) & 0xF, (color >> 4) & 0xF, color & 0xF)
        assert max(channels) == 15, f"{color:03x} is not fully saturated"


def test_a_split_zone_program_is_two_text_zones_in_one_program() -> None:
    """Captured from a top/bottom split template.

    Four items, not two: a colour item and a glyph item for EACH zone.
    Both zones are 8 rows of the 16-row panel, at startRow 0 and 8, and
    they scroll opposite ways -- which is the whole point of the
    template and the thing a single-zone reading of the format gets
    wrong.
    """
    fixture = Path(__file__).parent / "data" / "capture_text_split_zones.bin"
    program = lzss.decompress(fixture.read_bytes())

    assert program[8] == 4

    items, offset = [], 10
    for _ in range(4):
        length = int.from_bytes(program[offset:offset + 4], "big")
        items.append(program[offset + 4:offset + length])
        offset += length

    assert [item[0] for item in items] == [0x06, 0x01, 0x06, 0x01]

    # The two item types put their window at offsets that differ by
    # ONE, because TextContent carries a layerType byte the colour
    # item doesn't. Reading both the same way is an easy mistake and
    # gives a showHeight of 8192 instead of 8.
    top_colours, top_glyphs, bottom_colours, bottom_glyphs = items
    for colours, glyphs, start_row, mode in (
        (top_colours, top_glyphs, 0, 2),        # LEFT_CONTINUE
        (bottom_colours, bottom_glyphs, 8, 3),  # RIGHT_CONTINUE
    ):
        assert int.from_bytes(colours[10:12], "big") == start_row
        assert int.from_bytes(colours[12:14], "big") == 32  # showWidth
        assert int.from_bytes(colours[14:16], "big") == 8   # showHeight
        assert colours[16] == mode

        assert glyphs[11:13] == colours[10:12]
        assert glyphs[13:15] == colours[12:14]
        assert glyphs[15:17] == colours[14:16]
        assert glyphs[17] == mode

    assert top_glyphs[17] != bottom_glyphs[17], "the two zones must scroll opposite ways"


def test_glyph_stride_comes_from_the_items_own_height_not_the_panels() -> None:
    """An 8-row band packs ONE byte per glyph column, not two.

    The arithmetic is the proof, and it is why this matters: each zone
    declares three characters 8, 8 and 7 columns wide. At one byte per
    column the item body is 28 + 3*2 + 23*1 = 57 bytes, which is what
    arrived. At two -- the stride a 16-row panel would imply -- it
    would be 80. Reading a split program with the panel's stride walks
    off the end of the first character and mis-decodes everything
    after it.
    """
    fixture = Path(__file__).parent / "data" / "capture_text_split_zones.bin"
    program = lzss.decompress(fixture.read_bytes())

    offset = 10 + int.from_bytes(program[10:14], "big")
    length = int.from_bytes(program[offset:offset + 4], "big")
    glyphs = program[offset + 4:offset + length]

    rows = int.from_bytes(glyphs[15:17], "big")  # past layerType
    assert rows == 8
    bytes_per_column = (rows + 7) // 8
    assert bytes_per_column == 1

    count = int.from_bytes(glyphs[22:24], "big")
    total_width = int.from_bytes(glyphs[24:28], "big")
    assert (count, total_width) == (3, 23)

    widths, at = [], 28
    for _ in range(count):
        width, item_type = glyphs[at], glyphs[at + 1]
        assert item_type == 0, "a font glyph, so it needs the colour item beside it"
        widths.append(width)
        at += 2 + width * bytes_per_column
    assert widths == [8, 8, 7]
    assert at == len(glyphs), f"consumed {at} of {len(glyphs)} bytes"
