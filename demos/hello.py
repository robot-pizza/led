"""Cycle some words on the first sign we can find.

    python demos/hello.py
    python demos/hello.py HELLO FROM LED

Find a sign, render each word as a full-panel bitmap, push the lot as
one Animation program and let the sign cycle them by itself.

The words are drawn with PIL's built-in bitmap font rather than the
wire protocol's own TextContent. TextContent carries glyph SHAPES and
no colour -- it needs a second content item beside it to render at all
(see "A text program is TWO content items" in docs/wire_format.md) --
and neither it nor this library ships a font to produce those shapes
from. Full-panel bitmaps sidestep the question entirely.
"""

import asyncio
import sys
from pathlib import Path

# The modules live at the repository root, a level up from here.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw

import led_protocol as lp
from ble_sign import find_sign
from connection import ConnectionHandler
from requests import AnimationContent, SetBrightnessRequest, SetPowerRequest

WORDS = ["HELLO", "FROM", "LED"]
COLOUR = (255, 40, 0)
FRAME_MS = 2000


def render(text: str, width: int, height: int) -> Image.Image:
    """`text` centred on a `width` x `height` panel."""
    image = Image.new("RGB", (width, height), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    left, top, right, bottom = draw.textbbox((0, 0), text)
    draw.text(
        ((width - (right - left)) // 2 - left, (height - (bottom - top)) // 2 - top),
        text,
        fill=COLOUR,
    )
    return image


async def main() -> None:
    words = sys.argv[1:] or WORDS

    sign = await find_sign()
    print(f"found {sign.address}  {sign.width}x{sign.height}  {sign.name or '(unnamed)'}")

    async with ConnectionHandler(sign.address) as connection:
        await connection.send(SetPowerRequest(is_on=True))
        await connection.send(SetBrightnessRequest(brightness=255))
        content = AnimationContent(
            frames=[
                lp.image_to_pixel_data(render(word, sign.width, sign.height))
                for word in words
            ],
            show_width=sign.width,
            show_height=sign.height,
            frame_duration_ms=FRAME_MS,
        ).encode()
        await lp.upload_program(connection, [content])

    print(f"cycling {', '.join(words)} at {FRAME_MS / 1000:g}s each")


if __name__ == "__main__":
    asyncio.run(main())
