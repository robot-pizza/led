"""Put a PNG on the sign.

    python demos/image.py picture.png

Scaled to fit the panel, keeping its aspect ratio, centred on black.
Nearest-neighbour rather than a smooth filter: the panel has sixteen
levels per channel and one LED per pixel, so interpolated edges become
banding rather than blur. Hard pixels survive the trip better.

Transparency is flattened onto black, since the sign has no notion of
it -- an unlit LED is the only black there is.
"""

import asyncio
import sys
from pathlib import Path

from PIL import Image

from _common import connect
import led_protocol as lp
from requests import GraffitiContent


def fit(path: Path, width: int, height: int) -> Image.Image:
    source = Image.open(path)
    if source.mode in ("RGBA", "LA", "P"):
        source = source.convert("RGBA")
        flattened = Image.new("RGB", source.size, (0, 0, 0))
        flattened.paste(source, mask=source.getchannel("A"))
        source = flattened
    else:
        source = source.convert("RGB")

    scale = min(width / source.width, height / source.height)
    size = (max(1, round(source.width * scale)), max(1, round(source.height * scale)))
    source = source.resize(size, Image.NEAREST)

    panel = Image.new("RGB", (width, height), (0, 0, 0))
    panel.paste(source, ((width - size[0]) // 2, (height - size[1]) // 2))
    return panel


async def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python demos/image.py <file.png>")
        return
    path = Path(sys.argv[1])
    if not path.is_file():
        print(f"no such file: {path}")
        return

    async with connect() as (connection, sign):
        image = fit(path, sign.width, sign.height)
        content = GraffitiContent(
            pixel_data=lp.image_to_pixel_data(image),
            show_width=sign.width, show_height=sign.height,
        ).encode()
        await lp.upload_program(connection, [content])

    print(f"showing {path.name} at {sign.width}x{sign.height}")


if __name__ == "__main__":
    asyncio.run(main())
