"""Talking to a sign over Bluetooth directly, whether it's a real panel
or the simulator pretending to be one.

The host finds whatever is advertising the CoolLEDUX service and drives
it with `coolledux`'s own ConnectionHandler. Nothing here knows or cares
which kind of sign answered -- that's the point of the simulator
advertising identically.

Reading a panel back and driving its clock are simulator-only, and
live with the simulator rather than here.
"""

from __future__ import annotations

from typing import Optional

from connection import DiscoveredSign, discover


async def find_sign(timeout: float = 8.0, address: Optional[str] = None) -> DiscoveredSign:
    """The sign to test against: whatever is advertising.

    Real or simulated is not a distinction made here, and deliberately
    so -- the simulator advertises the same service, the same name and
    its size in the same bytes, so a test that works against one works
    against the other unchanged.

    That does mean that with a real panel powered on AND the simulator
    running, the two are genuinely indistinguishable before connecting:
    same name, same service, and the simulator's address is randomised
    by Android so it isn't stable either. Set LED_SIGN_ADDRESS to pin
    one, or pass `address`. Otherwise every sign found is printed and
    the first is used, so at least the ambiguity is visible rather than
    silently resolved.
    """
    import os

    found = await discover(timeout)
    address = address or os.environ.get("LED_SIGN_ADDRESS")
    if address is not None:
        found = [sign for sign in found if sign.address.lower() == address.lower()]
    elif len(found) > 1:
        print(f"{len(found)} signs advertising; using the first. "
              f"Set LED_SIGN_ADDRESS to choose:")
        for sign in found:
            print(f"    {sign.address}  {sign.name or '(no name)'}  {sign.width}x{sign.height}")
    if not found:
        raise RuntimeError(
            "no CoolLEDUX sign is advertising -- power on a real panel, or start the "
            "LED Simulator app, and note that a sign stops advertising while something "
            "else is connected to it"
        )
    return found[0]
