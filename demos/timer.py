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

The digits are font_5x9's, 7 rows tall, so both counters fit even a
16-row panel's 8-row halves.

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

DIGIT_W = font_5x9.WIDTH
DIGIT_H = font_5x9.DIGIT_HEIGHT
# A field's width spans both its digits, the second drawn half that
# width in -- so a blank column after each digit keeps the two apart.
PAIR = (DIGIT_W + 1) * 2
# A colon bitmap must be exactly as wide as its field: the sign draws
# the field's full width, reading past a short bitmap into whatever
# bytes come next.
COLON_W = 2


def counter(mode: int, row: int, colour: int, left: int) -> bytes:
    """MM:SS at `left`, occupying `row` down."""
    return TimeCountContent(
        hour_digits=font_5x9.digit_table(),
        colon=font_5x9.colon(COLON_W),
        time_count_mode=mode,
        num_width=DIGIT_W, num_height=DIGIT_H,
        minute_color=colour, minute_start_column=left, minute_start_row=row,
        minute_width=PAIR, minute_height=DIGIT_H,
        space_minute_color=colour, space_minute_start_column=left + PAIR,
        space_minute_start_row=row, space_minute_width=COLON_W,
        space_minute_height=DIGIT_H,
        seconds_color=colour, seconds_start_column=left + PAIR + COLON_W,
        seconds_start_row=row, seconds_width=PAIR, seconds_height=DIGIT_H,
    ).encode()


async def main() -> None:
    async with connect(brightness=160) as (connection, sign):
        half = sign.height // 2
        # The last pair's trailing blank column isn't counted, so the
        # counter centres on what is lit.
        left = max(0, (sign.width - (PAIR * 2 + COLON_W - 1)) // 2)
        # Centred within each half rather than jammed to its top edge.
        offset = (half - DIGIT_H) // 2
        print(f"{DIGIT_W}x{DIGIT_H} digits, two halves of {half} rows")
        await lp.upload_program(connection, [
            counter(STOPWATCH, offset, GREEN, left),
            counter(COUNTDOWN, half + offset, RED, left),
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
