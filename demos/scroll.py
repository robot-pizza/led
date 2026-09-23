"""Scrolling text the way the sign actually does it: TWO content items.

    python demos/scroll.py

This is the demo worth reading. A TextContent (0x01) carries glyph
SHAPES and no colour anywhere in it -- sent on its own it renders
nothing at all, which is exactly what this project saw for a long time
while the glyph encoding took the blame. The colour arrives beside it
in the same program, as a TextCustomColor (0x06) item holding one
colour per character.

See "A text program is TWO content items" in docs/wire_format.md.

The glyphs below are lifted byte-for-byte out of a capture of "123"
going to a panel, because nothing here ships a font: column-major, two
bytes per column at 16 rows, MSB is the top row. That is also why this
demo says 123 rather than something friendlier.
"""

import asyncio

from _common import connect
import led_protocol as lp
from requests import TextContent, TextCustomColorContent

# (width, glyph) per character, from that capture.
DIGITS = [
    (4, bytes.fromhex("100030007ffc0000")),
    (8, bytes.fromhex("301c402440444084410442043c040000")),
    (7, bytes.fromhex("2008400441044104410441043ef8")),
]

# The glyphs are 16 rows tall. That is a property of the DATA, not of
# the panel: showHeight sets the stride the sign decodes them at, so it
# must say 16 even on a 32-row panel.
ROWS = 16

COLOURS = [0xFF0000, 0x00FF00, 0x0088FF]

LEFT_CONTINUE = 2


async def main() -> None:
    async with connect() as (connection, sign):
        glyphs = TextContent(
            items=[(width, 0, data) for width, data in DIGITS],
            show_width=sign.width,
            show_height=ROWS,
            mode=LEFT_CONTINUE,
            stay_time=3,
        ).encode()

        # Without this item the panel stays black. That is the whole
        # point of the demo.
        colours = TextCustomColorContent(
            widths=[width for width, _ in DIGITS],
            colors=COLOURS,
            show_width=sign.width,
            show_height=ROWS,
            mode=LEFT_CONTINUE,
            stay_time=3,
        ).encode()

        await lp.upload_program(connection, [colours, glyphs])

    print("scrolling 123, one colour per character")


if __name__ == "__main__":
    asyncio.run(main())
