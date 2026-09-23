# led

A Python implementation of the CoolLEDUX / CoolLED1248
Bluetooth LE wire protocol, the tooling built on it, and a byte-level
reference for the protocol itself.

Everything is at the top level — there are no packages. `pytest.ini`
puts the repository root on the path so the modules import as top-level
names.

## Layout

- `docs/wire_format.md` — the protocol reference: byte layouts,
  response codes, timing constants, and an explicit distinction
  throughout between what has been confirmed against real hardware and
  what is still inferred. The most valuable artifact here, and the thing
  most worth keeping honest.
- The protocol itself, and nothing else: `envelope.py` (START/ESCAPE/END
  framing), `lzss.py`, `crc.py`, `requests.py`, `responses.py`, and
  `connection.py`, whose `ConnectionHandler` does exactly one round
  trip. Deliberately stops at "encode or decode this documented
  structure" — no bitmap rendering, no retry policy, no fonts.
- On top of that: `led_protocol.py` (bitmap→pixel conversion, chunked
  uploads with retries, a hand-drawn pixel font), `ble_sign.py`
  (`find_sign`).
- `demos/` — one script per thing the protocol does. `_common.py` holds
  the find-and-connect the rest would repeat.
- `tests/` — pure encode/decode, no hardware, under a second:
  `uv run python -m pytest tests`. `tests/data/` holds real
  captures taken off the air, which is what makes several of these
  worth more than a restatement of our own assumptions.

Every command except three has been driven against a real sign and
watched on screen. The exceptions: **rhythm** (`0x01`/`0x06`) — the
32x16 unit reports `localMicSupported=0` and never replies, though the
256x32 panel reports `1`, so it may be testable there; **set password**
(`0x0E`) and **OTA update** (`0xFE`) — deliberately never executed,
because a wrong PIN locks the sign with no known reset and a wrong
firmware image bricks it. Encode them if you like; don't send them.

## What lives elsewhere

A separate private repository holds the two phone apps (a simulator that
makes a phone behave as a sign, and an observer that reads a real panel
through its camera) and the hardware-backed test suite. Those tests
import this repository's modules; the dependency runs one way only, and
nothing here imports anything there.

## Working conventions this project has settled on

- Protocol facts belong in `docs/wire_format.md`, not in comments that
  narrate testing/debugging history. Comments should explain *why* (a
  confirmed quirk, a rejected alternative), not restate what the code
  does.
- When something is confirmed only empirically against the real hardware
  (a rotation offset, a firmware size limit, a threshold value), say so
  in a comment and how it was confirmed — don't state it as if it were a
  documented spec fact. And when a later observation overturns it, say
  that too, in place.
- Measure against something that can actually show the effect. One
  brightness claim rested on photographing **white**, which clips: with
  the camera's exposure locked and the panel near saturation, real
  dimming barely moves the top of the range. A mid-level target would
  have shown it immediately.
- The sign clips a field positioned off-panel **silently**. Asking for
  more than fits produces a plausible-looking wrong answer rather than an
  error, so anything laying out fields should size itself to the panel it
  found.
- A command is "working" when it has been seen on the panel *and* has a
  test. Neither alone counts: an ack proves the sign parsed the frame,
  not that it drew anything, and a one-off script proves nothing
  tomorrow.
