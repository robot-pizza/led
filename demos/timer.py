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

The digit font is chosen to fit half the panel: font_5x9's where there
is room, and a smaller 5x7 font on a 16-row panel, whose 8-row halves
are one row short of it.

Unlike a Clock, a TimeCount item has no blinking-colon flag, so these
colons stay lit.
"""

import asyncio
import time

from _common import connect
import font_5x9
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
# the top row.
SMALL_COLON = bytes([0x24, 0x24])


def font_for(rows: int):
    """(glyphs, colon, digit width, digit height, pair width, colon
    width) fitting `rows` rows.

    A pair's width spans both its digits, the second drawn half that
    width in. font_5x9 gets a blank column after each digit so the two
    don't touch; the 5x7 font's own glyphs already leave one.

    A colon bitmap must be exactly as wide as its field: the sign draws
    the field's full width, reading past a short bitmap into whatever
    bytes come next."""
    if rows >= font_5x9.HEIGHT:
        return (font_5x9.digit_table(), font_5x9.colon(2),
                font_5x9.WIDTH, font_5x9.HEIGHT, (font_5x9.WIDTH + 1) * 2, 2)
    return lp.digit_glyph_table(), SMALL_COLON, 5, 7, 10, 2


def counter(mode: int, row: int, colour: int, left: int, font) -> bytes:
    """MM:SS at `left`, occupying `row` down."""
    glyphs, colon, digit_w, digit_h, pair, colon_w = font
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
        glyphs, colon, digit_w, digit_h, pair, colon_w = font
        left = max(0, (sign.width - (pair * 2 + colon_w)) // 2)
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
