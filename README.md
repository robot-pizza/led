# led

A Python implementation of the CoolLEDUX / CoolLED1248
Bluetooth LE wire protocol, and the tooling built on it — plus a
byte-level reference for the protocol itself.

## What's here

- **[`docs/wire_format.md`](docs/wire_format.md)** — the protocol
  reference: byte layouts, response codes, timing constants, and an
  explicit distinction throughout between what has been confirmed
  against real hardware and what is still inferred. This is the part
  worth reading first, and the part most carefully kept honest.
- **The protocol itself and nothing else** — `envelope.py` (framing),
  `lzss.py`, `crc.py`, `requests.py`, `responses.py`, and
  `connection.py`, whose `ConnectionHandler` does one round trip.
  Deliberately stops at "encode or decode this documented structure" —
  no bitmap rendering, no retry policy, no fonts.
- **On top of that** — `led_protocol.py` (bitmap conversion, chunked
  uploads with retries, a pixel font) and `ble_sign.py` (finding a
  sign).
- **[`demos/`](demos/)** — seven short scripts, one per thing the
  protocol does: text, a clock, timers, brightness and flip, a PNG, a
  GIF.

## Using it

```bash
uv sync
uv run python demos/hello.py
```

The demos find whatever sign is advertising, so a panel just needs to
be powered on.

## Tests

```bash
uv run python -m pytest tests
```

## Licence

BSD 3-Clause. See [LICENSE.txt](LICENSE.txt).
