"""Play a GIF on the sign.

    python demos/gif.py animation.gif

Every frame is uploaded as one Animation program and the sign cycles
them itself, so this exits and the GIF keeps playing.

Two things the sign will not do for you:

FRAME TIMING. Animation carries one duration per frame, and this reads
each frame's own delay out of the GIF. But the whole thing has to fit
in one upload, so a long or large GIF gets its frames sampled down --
see MAX_FRAMES.

TRANSPARENCY AND DISPOSAL. GIF frames are often partial updates over
what came before. Each frame is composited onto the last so that what
goes to the sign is a complete picture, which is all Animation can
carry.
"""

import asyncio
import sys
from pathlib import Path

from PIL import Image, ImageSequence

from _common import connect
import led_protocol as lp
from requests import AnimationContent

# The whole animation is one program, and a program that is too large
# is rejected outright. 24 frames of a 256x32 panel is already 384KB
# before compression; past this the demo stops being a demo.
MAX_FRAMES = 24
DEFAULT_FRAME_MS = 100


def frames(path: Path, width: int, height: int) -> tuple[list[Image.Image], list[int]]:
    source = Image.open(path)
    images: list[Image.Image] = []
    delays: list[int] = []

    canvas = Image.new("RGBA", source.size, (0, 0, 0, 255))
    for frame in ImageSequence.Iterator(source):
        # Composite onto what came before: GIF frames are frequently
        # partial updates, and Animation can only carry whole pictures.
        canvas = canvas.copy()
        rgba = frame.convert("RGBA")
        canvas.paste(rgba, (0, 0), rgba)

        flat = Image.new("RGB", canvas.size, (0, 0, 0))
        flat.paste(canvas, mask=canvas.getchannel("A"))

        scale = min(width / flat.width, height / flat.height)
        size = (max(1, round(flat.width * scale)), max(1, round(flat.height * scale)))
        panel = Image.new("RGB", (width, height), (0, 0, 0))
        panel.paste(flat.resize(size, Image.NEAREST),
                    ((width - size[0]) // 2, (height - size[1]) // 2))

        images.append(panel)
        delays.append(int(frame.info.get("duration", DEFAULT_FRAME_MS)) or DEFAULT_FRAME_MS)

    if len(images) > MAX_FRAMES:
        step = len(images) / MAX_FRAMES
        picked = [round(i * step) for i in range(MAX_FRAMES)]
        # Keep the total duration right even though frames were dropped.
        images = [images[i] for i in picked]
        delays = [sum(delays) // MAX_FRAMES] * MAX_FRAMES
        print(f"sampled down to {MAX_FRAMES} frames")

    return images, delays


async def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python demos/gif.py <file.gif>")
        return
    path = Path(sys.argv[1])
    if not path.is_file():
        print(f"no such file: {path}")
        return

    async with connect() as (connection, sign):
        images, delays = frames(path, sign.width, sign.height)
        content = AnimationContent(
            frames=[lp.image_to_pixel_data(image) for image in images],
            show_width=sign.width, show_height=sign.height,
            frame_duration_ms=delays[0],
            delays=delays,
        ).encode()
        await lp.upload_program(connection, [content])

    print(f"playing {path.name}: {len(images)} frames, {sum(delays) / 1000:.1f}s a loop")


if __name__ == "__main__":
    asyncio.run(main())
