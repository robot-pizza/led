"""The three lines every demo would otherwise repeat.

Each demo is meant to show one thing about the protocol, so finding a
sign and opening a connection belongs here rather than at the top of
six files.
"""

import sys
from contextlib import asynccontextmanager
from pathlib import Path

# The modules live at the repository root, a level up from here.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ble_sign import find_sign
from connection import ConnectionHandler
from requests import SetBrightnessRequest, SetPowerRequest


@asynccontextmanager
async def connect(brightness: int = 255):
    """Yields (connection, sign) for the first sign advertising.

    Powers it on and sets brightness, because a demo that appears to do
    nothing because the panel was left dark or dimmed is worse than no
    demo. Note a sign stops advertising while something is connected to
    it, so close other clients first.
    """
    sign = await find_sign()
    print(f"found {sign.address}  {sign.width}x{sign.height}  {sign.name or '(unnamed)'}")
    async with ConnectionHandler(sign.address) as connection:
        await connection.send(SetPowerRequest(is_on=True))
        await connection.send(SetBrightnessRequest(brightness=brightness))
        yield connection, sign
