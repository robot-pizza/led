"""The BLE transport: owns the link and does exactly one thing, send a
request and return its decoded response.

No retry loop, no chunking orchestration, no timing/rate-limit policy --
those are caller-level concerns (a ProgramData upload is many `send()`
calls in a row, driven by the caller). This class's only job is one
round trip: envelope-encode, write, await the next notification,
unescape, dispatch on marker byte, return.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

from bleak import BleakClient

import envelope
from requests import Request
from responses import Response, dispatch

# CoolLEDUX GATT service/characteristic UUIDs. A single characteristic
# is used for both writes and notifications, not separate ones.
SERVICE_UUID = "0000fff0-0000-1000-8000-00805f9b34fb"
CHARACTERISTIC_UUID = "0000fff1-0000-1000-8000-00805f9b34fb"

DEFAULT_TIMEOUT_S = 5.0


class ConnectionHandler:
    def __init__(self, address: str, timeout: float = DEFAULT_TIMEOUT_S) -> None:
        self._address = address
        self._timeout = timeout
        self._client: Optional[BleakClient] = None
        self._response_queue: "asyncio.Queue[bytes]" = asyncio.Queue()
        self._inbound = bytearray()

    async def connect(self) -> None:
        self._client = BleakClient(self._address)
        await self._client.connect()
        await self._client.start_notify(CHARACTERISTIC_UUID, self._on_notify)

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.stop_notify(CHARACTERISTIC_UUID)
            await self._client.disconnect()
            self._client = None

    def _on_notify(self, _handle: int, data: bytearray) -> None:
        """Reassemble notifications into whole envelope frames.

        A reply can exceed the MTU just as a request can, so one
        notification is not necessarily one frame. Scanning for the END
        byte is exact rather than a heuristic: the payload is escaped,
        so an unescaped 0x01 or 0x03 can only be the frame's own
        markers.
        """
        self._inbound.extend(data)
        while True:
            end = self._inbound.find(envelope.END_BYTE)
            if end < 0:
                return
            frame = bytes(self._inbound[: end + 1])
            del self._inbound[: end + 1]
            start = frame.find(envelope.START_BYTE)
            if start >= 0:
                self._response_queue.put_nowait(frame[start:])

    async def _write_frame(self, frame: bytes) -> None:
        """Write one envelope frame, split across as many GATT writes as
        the link's MTU requires.

        A frame is not a packet. The envelope's own START/END bytes are
        what delimit a request, and the sign reassembles across writes
        -- which it has to, since a program chunk is far larger than any
        MTU this link negotiates.

        Splitting is not optional on some panels. The 256x32 unit offers
        the characteristic as write-without-response ONLY, so a write
        over MTU-3 is rejected outright by the stack ("the parameter is
        incorrect", no bytes sent). The 32x16 unit accepted a whole
        1KB frame in one call, which is why this went unnoticed: with
        one panel to test against, "write the frame" looked like it was
        the same thing as "send the frame".
        """
        limit = max(20, getattr(self._client, "mtu_size", 23) - 3)
        for offset in range(0, len(frame), limit):
            await self._client.write_gatt_char(
                CHARACTERISTIC_UUID, frame[offset:offset + limit]
            )

    async def send(self, request: Request, timeout: Optional[float] = None) -> Response:
        """Envelope-encode `request`, write it, await the next
        notification, and return its decoded response. One round trip.

        `timeout` overrides the instance default for this call only --
        different requests warrant different plain read timeouts (a
        program-start ack and a per-chunk ack are documented as 5.0s and
        3.0s respectively), but that's the only timing knob this method
        exposes; retry policy stays a caller concern.
        """
        if self._client is None:
            raise RuntimeError("not connected -- call connect() first")

        while not self._response_queue.empty():
            self._response_queue.get_nowait()
        self._inbound.clear()

        frame = envelope.encode(request.encode())
        await self._write_frame(frame)

        raw = await asyncio.wait_for(self._response_queue.get(), timeout=timeout if timeout is not None else self._timeout)
        payload = envelope.decode(raw)
        return dispatch(payload)

    async def __aenter__(self) -> "ConnectionHandler":
        await self.connect()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.disconnect()


async def discover(timeout: float = 6.0) -> list["DiscoveredSign"]:
    """Every CoolLEDUX sign currently advertising, real or simulated.

    Filters on the service UUID rather than a name or address, which is
    what makes a simulated panel indistinguishable here: it advertises
    the same service and carries the panel size in the same place.

    Panel dimensions come from the manufacturer payload. bleak strips
    the AD-structure header and the 2-byte company ID before handing it
    over, so byte 6 is the height and bytes 7-8 the width, big-endian.
    """
    from bleak import BleakScanner

    found: dict[str, DiscoveredSign] = {}
    devices = await BleakScanner.discover(timeout=timeout, return_adv=True)
    for address, (device, advertisement) in devices.items():
        uuids = {u.lower() for u in (advertisement.service_uuids or ())}
        if SERVICE_UUID.lower() not in uuids:
            continue
        width = height = 0
        for payload in (advertisement.manufacturer_data or {}).values():
            if payload is not None and len(payload) >= 9:
                height = payload[6]
                width = (payload[7] << 8) | payload[8]
                break
        found[address] = DiscoveredSign(
            address=address, name=device.name or advertisement.local_name or "",
            width=width, height=height,
        )
    return list(found.values())


@dataclass(frozen=True)
class DiscoveredSign:
    address: str
    name: str
    width: int
    height: int
