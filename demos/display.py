"""Brightness and flip, both of which we had wrong for a while.

    python demos/display.py

BRIGHTNESS is a live global dimmer: it changes what is already on
screen, with nothing re-uploaded. It is also a LEVEL, not a percentage:
the usable range is 5 to 255, and a freshly powered panel reports 255.

FLIP takes a MODE, not a flag, and the order is not the obvious one:
0 none, 1 XY, 2 X, 3 Y. XY -- both axes, a 180-degree rotation -- comes
BEFORE the single-axis options, so guessing 1=X, 2=Y, 3=both is wrong
at every value. Sending a bare 0/1 only ever reaches none and XY, which
makes X and Y look like they do the same thing as XY.

The test image is deliberately asymmetric on both axes, because a
symmetric one cannot tell the three flips apart.
"""

import asyncio

from PIL import Image, ImageDraw

from _common import connect
import led_protocol as lp
from requests import FlipMode, GraffitiContent, SetBrightnessRequest, SetFlipRequest


def marker(width: int, height: int) -> Image.Image:
    """Bright in the top-left, a bar down the left, a bar along the top."""
    image = Image.new("RGB", (width, height), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width // 3, 0), fill=(255, 0, 0))
    draw.rectangle((0, 0, 0, height // 2), fill=(0, 128, 255))
    draw.rectangle((1, 1, 3, 3), fill=(255, 255, 255))
    return image


async def main() -> None:
    async with connect() as (connection, sign):
        content = GraffitiContent(
            pixel_data=lp.image_to_pixel_data(marker(sign.width, sign.height)),
            show_width=sign.width, show_height=sign.height,
        ).encode()
        await lp.upload_program(connection, [content])
        await asyncio.sleep(1.0)

        print("brightness, with nothing re-uploaded:")
        for level in (255, 120, 40, 255):
            await connection.send(SetBrightnessRequest(brightness=level))
            print(f"  {level}")
            await asyncio.sleep(1.5)

        print("flip modes:")
        for name, mode in (("none", FlipMode.NONE), ("XY", FlipMode.XY),
                           ("X", FlipMode.X), ("Y", FlipMode.Y)):
            await connection.send(SetFlipRequest(mode=mode))
            print(f"  {name} ({mode})")
            await asyncio.sleep(2.0)

        await connection.send(SetFlipRequest(mode=FlipMode.NONE))

    print("done")


if __name__ == "__main__":
    asyncio.run(main())
