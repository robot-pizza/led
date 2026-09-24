"""A clock the sign keeps by itself.

    python demos/clock.py

Everything else here pushes pixels. This hands the sign a digit font
and four rectangles and then stops talking to it -- the firmware draws
the time and keeps drawing it after the connection closes.

The digits are font_5x9's, the LED departure-board font. HH:MM:SS is
shown where it fits across the panel, HH:MM where it does not.
"""

import asyncio
from datetime import datetime

from _common import connect
import font_5x9
import led_protocol as lp
from requests import ClockContent, SynchronizeTimeRequest

WHITE = 0xFFFFFF

DIGIT_W = font_5x9.WIDTH
DIGIT_H = font_5x9.HEIGHT
# A field's width spans both its digits, and the second digit is drawn
# half that width in -- so a blank column after each digit keeps the
# pair from touching, and keeps it off the colon that follows.
PAIR = (DIGIT_W + 1) * 2
# The colon is font_5x9's one lit column, with a blank column after it
# to match the one each digit pair already ends with.
COLON_W = 2


async def main() -> None:
    async with connect() as (connection, sign):
        # The sign has no idea what time it is until told.
        now = datetime.now()
        await connection.send(SynchronizeTimeRequest(
            year=now.year, month=now.month, day=now.day,
            weekday=now.isoweekday() % 7,
            hour=now.hour, minute=now.minute, second=now.second,
        ))

        # HH:MM:SS, centred, dropping seconds only if they will not
        # fit: a field positioned past the right edge is clipped
        # silently and looks like a broken clock rather than a panel
        # that is too narrow. The last pair's trailing blank column is
        # not counted, so the whole thing centres on what is lit.
        with_seconds = sign.width >= PAIR * 3 + COLON_W * 2 - 1
        span = (PAIR * 3 + COLON_W * 2 if with_seconds else PAIR * 2 + COLON_W) - 1
        left = max(0, (sign.width - span) // 2)
        row = max(0, (sign.height - DIGIT_H) // 2)

        hour = left
        first_colon = hour + PAIR
        minute = first_colon + COLON_W
        second_colon = minute + PAIR
        seconds = second_colon + COLON_W

        content = ClockContent(
            hour_digits=font_5x9.digit_table(),
            colon=font_5x9.colon(COLON_W),
            is_24_hour=True,
            blinking_colon=True,
            num_width=DIGIT_W, num_height=DIGIT_H,
            hour_color=WHITE, hour_start_column=hour, hour_start_row=row,
            hour_width=PAIR, hour_height=DIGIT_H,
            space_hour_color=WHITE, space_hour_start_column=first_colon,
            space_hour_start_row=row, space_hour_width=COLON_W,
            space_hour_height=DIGIT_H,
            minute_color=WHITE, minute_start_column=minute, minute_start_row=row,
            minute_width=PAIR, minute_height=DIGIT_H,
            # Without this the second colon's bitmap is sent empty, and
            # the seconds sit beside the minutes with nothing between.
            show_space_minute_color=with_seconds,
            # A field with zero width isn't drawn, which is how seconds
            # are omitted rather than positioned off-panel.
            space_minute_color=WHITE, space_minute_start_column=second_colon,
            space_minute_start_row=row,
            space_minute_width=COLON_W if with_seconds else 0,
            space_minute_height=DIGIT_H if with_seconds else 0,
            seconds_color=WHITE, seconds_start_column=seconds, seconds_start_row=row,
            seconds_width=PAIR if with_seconds else 0,
            seconds_height=DIGIT_H if with_seconds else 0,
        ).encode()
        await lp.upload_program(connection, [content])

    shown = f"{now:%H:%M:%S}" if with_seconds else f"{now:%H:%M}"
    print(f"clock set to {shown}; the sign keeps it from here")


if __name__ == "__main__":
    asyncio.run(main())
