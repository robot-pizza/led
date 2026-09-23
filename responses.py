"""Every confirmed reply shape in the wire protocol, plus a marker-byte
dispatch table.

Not every request has a documented reply shape. Time sync,
timer-switch, countdown, stopwatch and scoreboard replies were
confirmed live against real hardware instead (see each class's own doc
for what is actually verified vs. still a guess at field layout). Rhythm and OTA remain unconfirmed: this
device's DeviceInfoResponse reports local_mic_supported=0, and a live
SetRhythmType attempt got no reply at all (consistent with the feature
not existing on this hardware, not a wire-format bug) -- OTA was never
attempted live (real firmware data would be required, and a wrong
payload risks bricking the device). Both still fall through to
RawResponse rather than guessing a layout.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar


class Response:
    """Base for every response. `decode(data)` parses the payload that
    follows the marker byte (the marker itself is what `dispatch()` used
    to pick this class, so it is not part of `data`)."""

    MARKER: ClassVar[int]

    @classmethod
    def decode(cls, data: bytes) -> "Response":
        raise NotImplementedError


@dataclass
class ProgramStartResponse(Response):
    """Reply to ProgramStartRequest (0x02).

    This is a plain binary flag, NOT a 4-way status enum: 0 means "send
    the data starting from chunk 0", 1 means "device already has this
    content" -- a designed, non-error outcome (see the doc's "Confirmed
    semantics" note). Non-0/1 values are unexpected.
    """

    MARKER: ClassVar[int] = 0x02

    already_stored: bool

    @classmethod
    def decode(cls, data: bytes) -> "ProgramStartResponse":
        return cls(already_stored=bool(data[0]))


class DataStatus:
    ACCEPTED = 0
    SEND_ERROR = 1
    DEVICE_ERROR = 2
    DATA_ERROR = 3


@dataclass
class ProgramDataResponse(Response):
    """Reply to ProgramDataRequest (0x03). Genuine 4-way enum -- any
    non-zero status should be retried.

    Real wire layout, confirmed against a live device (the doc's original
    [0x03][chunk_index 2B][status 1B] table was missing a byte): there's a
    reserved 0x00 immediately after the marker, mirroring the request's own
    [0x03][0x00][...] shape -- [0x03][0x00][chunk_index 2B][status 1B].
    """

    MARKER: ClassVar[int] = 0x03

    chunk_index: int
    status: int

    @property
    def accepted(self) -> bool:
        return self.status == DataStatus.ACCEPTED

    @classmethod
    def decode(cls, data: bytes) -> "ProgramDataResponse":
        chunk_index = int.from_bytes(data[1:3], "big")
        status = data[3]
        return cls(chunk_index=chunk_index, status=status)


@dataclass
class BrightnessResponse(Response):
    """Reply to SetBrightnessRequest (0x04). Echoes the set value; always
    indicates success."""

    MARKER: ClassVar[int] = 0x04

    brightness: int

    @classmethod
    def decode(cls, data: bytes) -> "BrightnessResponse":
        return cls(brightness=data[0])


@dataclass
class PowerResponse(Response):
    """Reply to SetPowerRequest (0x05)."""

    MARKER: ClassVar[int] = 0x05

    is_on: bool

    @classmethod
    def decode(cls, data: bytes) -> "PowerResponse":
        return cls(is_on=bool(data[0]))


@dataclass
class FlipResponse(Response):
    """Reply to SetFlipRequest (0x0C). Echoes the set value; always
    indicates success."""

    MARKER: ClassVar[int] = 0x0C

    is_flipped: bool

    @classmethod
    def decode(cls, data: bytes) -> "FlipResponse":
        return cls(is_flipped=bool(data[0]))


@dataclass
class VerifyPasswordResponse(Response):
    """Reply to VerifyPasswordRequest (0x0D). 0=correct, else=incorrect."""

    MARKER: ClassVar[int] = 0x0D

    correct: bool

    @classmethod
    def decode(cls, data: bytes) -> "VerifyPasswordResponse":
        return cls(correct=(data[0] == 0))


@dataclass
class SetPasswordResponse(Response):
    """Reply to SetPasswordRequest (0x0E). 0=success, else=failure."""

    MARKER: ClassVar[int] = 0x0E

    success: bool

    @classmethod
    def decode(cls, data: bytes) -> "SetPasswordResponse":
        return cls(success=(data[0] == 0))


@dataclass
class DeviceInfoResponse(Response):
    """Reply to DeviceInfoRequest (0x1F). 9 single-byte fields, in order."""

    MARKER: ClassVar[int] = 0x1F

    switch_state: int
    brightness: int
    flip: int
    local_mic_supported: int
    local_mic_on_off: int
    local_mic_mode: int
    show_device_id: int
    max_program_number: int
    remote_enable: int

    @classmethod
    def decode(cls, data: bytes) -> "DeviceInfoResponse":
        return cls(
            switch_state=data[0],
            brightness=data[1],
            flip=data[2],
            local_mic_supported=data[3],
            local_mic_on_off=data[4],
            local_mic_mode=data[5],
            show_device_id=data[6],
            max_program_number=data[7],
            remote_enable=data[8],
        )


@dataclass
class SynchronizeTimeResponse(Response):
    """Reply to SynchronizeTimeRequest (0x09). Undocumented; confirmed
    live against real hardware: a single status byte, 0x00
    observed. Semantics of a nonzero value aren't confirmed, but every
    other single-status-byte reply in this protocol (ProgramData,
    SetPassword) uses 0=success, so `success` follows that same
    convention rather than introducing a different one un-evidenced."""

    MARKER: ClassVar[int] = 0x09

    status: int

    @property
    def success(self) -> bool:
        return self.status == 0

    @classmethod
    def decode(cls, data: bytes) -> "SynchronizeTimeResponse":
        return cls(status=data[0])


@dataclass
class SetTimerSwitchResponse(Response):
    """Reply to SetTimerSwitchRequest (0x0A). Undocumented; confirmed
    live with an empty schedule (items=[]): a single status
    byte, 0x00. Same 0=success convention as the other single-status-byte
    replies in this protocol -- see SynchronizeTimeResponse's doc."""

    MARKER: ClassVar[int] = 0x0A

    status: int

    @property
    def success(self) -> bool:
        return self.status == 0

    @classmethod
    def decode(cls, data: bytes) -> "SetTimerSwitchResponse":
        return cls(status=data[0])


@dataclass
class QueryTimerSwitchResponse(Response):
    """Reply to QueryTimerSwitchRequest (0x0B): item count, then that
    many 6-byte entries in SetTimerSwitchRequest's own item layout.

    Undocumented, so this was confirmed live instead, by setting a
    schedule and reading
    it back. Sending one item as 01 0c 00 08 00 00 returned
    01 01 0c 00 08 00 00: a count of 1 followed by that exact entry.
    With no schedule set it returns a bare 0x00, which had previously
    been read as a status byte; it's a count of zero."""

    MARKER: ClassVar[int] = 0x0B

    raw: bytes
    item_count: int = 0
    items: tuple[bytes, ...] = ()

    ITEM_SIZE: ClassVar[int] = 6

    @classmethod
    def decode(cls, data: bytes) -> "QueryTimerSwitchResponse":
        if not data:
            return cls(raw=data)
        count = data[0]
        body = data[1:]
        items = tuple(
            body[i:i + cls.ITEM_SIZE]
            for i in range(0, min(len(body), count * cls.ITEM_SIZE), cls.ITEM_SIZE)
        )
        return cls(raw=data, item_count=count, items=items)


@dataclass
class CountdownControlResponse(Response):
    """Reply to CountdownQuery/Reset/StartStopRequest (0x0F).
    Undocumented; confirmed live: always starts with the sub-command byte
    echoed back (0x01 query, 0x02 reset, 0x03 start/stop), followed by
    more bytes that vary in count by sub-command (7 more for query/
    start-stop, 1 more for reset). Field-by-field layout within those
    remaining bytes (e.g. where hour/minute/second/running-flag actually
    sit) isn't confirmed -- a live query 2s into a running countdown
    still read back all zeros after the echo byte, which doesn't match a
    simple "remaining time" guess, so that decoding is left as `raw`
    rather than asserting field names not actually verified."""

    MARKER: ClassVar[int] = 0x0F

    sub_command: int
    raw: bytes

    @classmethod
    def decode(cls, data: bytes) -> "CountdownControlResponse":
        return cls(sub_command=data[0], raw=data[1:])


@dataclass
class StopwatchControlResponse(Response):
    """Reply to StopwatchQuery/Reset/StartStopRequest (0x10). Same
    sub-command-echo shape as CountdownControlResponse -- see its doc."""

    MARKER: ClassVar[int] = 0x10

    sub_command: int
    raw: bytes

    @classmethod
    def decode(cls, data: bytes) -> "StopwatchControlResponse":
        return cls(sub_command=data[0], raw=data[1:])


@dataclass
class ScoreboardControlResponse(Response):
    """Reply to ScoreboardQuery/SetScore/SetTime/StartStopRequest (0x11).
    Same sub-command-echo shape as CountdownControlResponse -- see its
    doc. Query (sub-command 0x01) returned 12 bytes after the echo,
    plausibly host/visitor current score (2B each) + host/visitor total
    (1B each) + set-time fields + running flag per the request shapes
    that write those same fields, but not confirmed field-by-field."""

    MARKER: ClassVar[int] = 0x11

    sub_command: int
    raw: bytes

    @classmethod
    def decode(cls, data: bytes) -> "ScoreboardControlResponse":
        return cls(sub_command=data[0], raw=data[1:])


@dataclass
class RawResponse(Response):
    """Fallback for any marker byte without a confirmed reply shape:
    timer-switch, countdown, stopwatch, scoreboard, rhythm and OTA
    replies have no documented shape."""

    marker: int
    payload: bytes

    @classmethod
    def decode(cls, data: bytes) -> "RawResponse":
        raise NotImplementedError("RawResponse is constructed directly by dispatch(), not decode()")


_DISPATCH: dict[int, type[Response]] = {
    cls.MARKER: cls
    for cls in (
        ProgramStartResponse,
        ProgramDataResponse,
        BrightnessResponse,
        PowerResponse,
        FlipResponse,
        VerifyPasswordResponse,
        SetPasswordResponse,
        DeviceInfoResponse,
        SynchronizeTimeResponse,
        SetTimerSwitchResponse,
        QueryTimerSwitchResponse,
        CountdownControlResponse,
        StopwatchControlResponse,
        ScoreboardControlResponse,
    )
}


def dispatch(frame_payload: bytes) -> Response:
    """Decode an unescaped, unenveloped notification payload: marker byte
    followed by that response's fields."""
    marker, data = frame_payload[0], frame_payload[1:]
    cls = _DISPATCH.get(marker)
    if cls is None:
        return RawResponse(marker=marker, payload=data)
    return cls.decode(data)
