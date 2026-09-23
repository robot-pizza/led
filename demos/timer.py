"""A count-up and a count-down, and the limit that stops both running.

    python demos/timer.py

Two TimeCount items in one program, one per half of the panel, each
with its own field rectangles and its own value. Both render.

Only ONE of them advances. Starting either freezes the other where it
stands -- there is a single timer engine behind the two command
markers. Confirmed on real hardware: starting the countdown stopped the
stopwatch, and vice versa.

So the stopwatch gets the engine, and the countdown is walked down from
here, one reset per second. That is the way to show two moving counters
at once, and it also works.

The digit font is chosen to fit half the panel: the manufacturer's own
7x16 glyphs where there is room, and a smaller 5x7 font on a 16-row
panel, where two 16-tall counters would not fit at all.
"""

import asyncio
import time

from _common import connect
import led_protocol as lp
from requests import (
    CountdownResetRequest,
    CountdownStartStopRequest,
    StopwatchResetRequest,
    StopwatchStartStopRequest,
    TimeCountContent,
)

COUNTDOWN_FROM = 30
GREEN, RED = 0x00FF00, 0xFF0000

STOPWATCH, COUNTDOWN = 1, 0

# A colon for the small font: two columns, lit at rows 2 and 5, MSB is
# the top row. The manufacturer ships one only at 16 rows.
SMALL_COLON = bytes([0x24, 0x24])


def font_for(rows: int):
    """(glyphs, colon, width, height) fitting `rows` rows."""
    if rows >= 16:
        return lp.REAL_DIGIT_GLYPH_TABLE_16X32, lp.REAL_COLON_GLYPH_16X32, 7, 16
    return lp.digit_glyph_table(), SMALL_COLON, 5, 7


def counter(mode: int, row: int, colour: int, left: int, font) -> bytes:
    """MM:SS at `left`, occupying `row` down."""
    glyphs, colon, digit_w, digit_h = font
    pair, colon_w = digit_w * 2, max(2, digit_w // 2)
    return TimeCountContent(
        hour_digits=glyphs,
        colon=colon,
        time_count_mode=mode,
        num_width=digit_w, num_height=digit_h,
        minute_color=colour, minute_start_column=left, minute_start_row=row,
        minute_width=pair, minute_height=digit_h,
        space_minute_color=colour, space_minute_start_column=left + pair,
        space_minute_start_row=row, space_minute_width=colon_w,
        space_minute_height=digit_h,
        seconds_color=colour, seconds_start_column=left + pair + colon_w,
        seconds_start_row=row, seconds_width=pair, seconds_height=digit_h,
    ).encode()


async def main() -> None:
    async with connect(brightness=160) as (connection, sign):
        half = sign.height // 2
        font = font_for(half)
        glyphs, colon, digit_w, digit_h = font
        colon_w = max(2, digit_w // 2)
        left = max(0, (sign.width - (digit_w * 4 + colon_w)) // 2)
        # Centred within each half rather than jammed to its top edge.
        offset = (half - digit_h) // 2
        print(f"{digit_w}x{digit_h} digits, two halves of {half} rows")
        await lp.upload_program(connection, [
            counter(STOPWATCH, offset, GREEN, left, font),
            counter(COUNTDOWN, half + offset, RED, left, font),
        ])
        await asyncio.sleep(1.0)

        await connection.send(StopwatchResetRequest())
        await connection.send(StopwatchStartStopRequest(is_start=True))
        print("stopwatch running on the top half")

        # The countdown cannot also run, so drive it by hand.
        print(f"walking the countdown down from {COUNTDOWN_FROM}s")
        tick = time.monotonic()
        for remaining in range(COUNTDOWN_FROM, -1, -1):
            await connection.send(CountdownResetRequest(
                hour=0, minute=remaining // 60, seconds=remaining % 60))
            tick += 1.0
            await asyncio.sleep(max(0.0, tick - time.monotonic()))

        await connection.send(CountdownStartStopRequest(is_start=False))

    print("done")


if __name__ == "__main__":
    asyncio.run(main())
