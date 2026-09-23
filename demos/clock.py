"""A clock the sign keeps by itself.

    python demos/clock.py

Everything else here pushes pixels. This hands the sign a digit font
and four rectangles and then stops talking to it -- the firmware draws
the time and keeps drawing it after the connection closes.

The glyphs are the manufacturer's own 7x16 digit table rather than
anything drawn here, so the clock looks the way the sign's own does --
falling back to a smaller 5x7 font when HH:MM:SS will not fit across
the panel at that size.
"""

import asyncio
from datetime import datetime

from _common import connect
import led_protocol as lp
from requests import ClockContent, SynchronizeTimeRequest

WHITE = 0xFFFFFF

# A colon for the small font: one column, lit at rows 2 and 5, MSB is
# the top row. One rather than two because at 5-wide digits, HH:MM:SS
# plus two 2-column colons is 34 on a 32-column panel -- a column
# either side is exactly what has to go.
SMALL_COLON = bytes([0x24])


def font_for(width: int):
    """The largest of our two digit fonts that fits HH:MM:SS across
    `width`, as (glyphs, colon, digit width, digit height, colon width)."""
    big = (lp.REAL_DIGIT_GLYPH_TABLE_16X32, lp.REAL_COLON_GLYPH_16X32, 7, 16, 3)
    small = (lp.digit_glyph_table(), SMALL_COLON, 5, 7, 1)
    for font in (big, small):
        _, _, digit_w, _, colon_w = font
        if digit_w * 6 + colon_w * 2 <= width:
            return font
    return small


async def main() -> None:
    async with connect() as (connection, sign):
        # The sign has no idea what time it is until told.
        now = datetime.now()
        await connection.send(SynchronizeTimeRequest(
            year=now.year, month=now.month, day=now.day,
            weekday=now.isoweekday() % 7,
            hour=now.hour, minute=now.minute, second=now.second,
        ))

        # HH:MM:SS, centred. Each field's width spans BOTH its digits.
        #
        # The font is chosen to fit: at 7 columns a digit the whole
        # thing wants 48, so a 32-wide panel gets the 5x7 font instead.
        # Seconds are only dropped if even that will not fit, because a
        # field positioned past the right edge is clipped silently and
        # looks like a broken clock rather than a panel that is too
        # narrow.
        glyphs, colon, digit_w, digit_h, colon_w = font_for(sign.width)
        pair = digit_w * 2
        with_seconds = sign.width >= pair * 3 + colon_w * 2
        span = pair * 3 + colon_w * 2 if with_seconds else pair * 2 + colon_w
        left = max(0, (sign.width - span) // 2)
        row = max(0, (sign.height - digit_h) // 2)

        hour = left
        first_colon = hour + pair
        minute = first_colon + colon_w
        second_colon = minute + pair
        seconds = second_colon + colon_w

        content = ClockContent(
            hour_digits=glyphs,
            colon=colon,
            is_24_hour=True,
            num_width=digit_w, num_height=digit_h,
            hour_color=WHITE, hour_start_column=hour, hour_start_row=row,
            hour_width=pair, hour_height=digit_h,
            space_hour_color=WHITE, space_hour_start_column=first_colon,
            space_hour_start_row=row, space_hour_width=colon_w,
            space_hour_height=digit_h,
            minute_color=WHITE, minute_start_column=minute, minute_start_row=row,
            minute_width=pair, minute_height=digit_h,
            # A field with zero width isn't drawn, which is how seconds
            # are omitted rather than positioned off-panel.
            space_minute_color=WHITE, space_minute_start_column=second_colon,
            space_minute_start_row=row,
            space_minute_width=colon_w if with_seconds else 0,
            space_minute_height=digit_h if with_seconds else 0,
            seconds_color=WHITE, seconds_start_column=seconds, seconds_start_row=row,
            seconds_width=pair if with_seconds else 0,
            seconds_height=digit_h if with_seconds else 0,
        ).encode()
        await lp.upload_program(connection, [content])

    shown = f"{now:%H:%M:%S}" if with_seconds else f"{now:%H:%M}"
    print(f"clock set to {shown}; the sign keeps it from here"
          + f" ({digit_w}x{digit_h} digits)")


if __name__ == "__main__":
    asyncio.run(main())
