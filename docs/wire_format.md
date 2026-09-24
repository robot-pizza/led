# CoolLEDUX Wire Format

The BLE protocol used by CoolLEDUX/CoolLED1248 LED matrix signs, established by capturing real traffic and confirming it against live hardware.

## Overview

The sign is a BLE GATT peripheral with one write characteristic. Every display update — text, a bitmap, an animation, a live clock — is sent as a **program**: a self-contained, whole-panel content upload. There are no per-pixel or delta update commands anywhere in the protocol.

Two architectural facts drive everything below:

- **Upload once, render forever.** Scrolling text, ticking clocks, and running countdowns are rendered *by the sign's own firmware* after a single upload — confirmed by watching a Clock program advance across real wall-clock minutes with zero further BLE traffic, and by a Countdown capture showing exactly one ~10KB upload followed by silence.
- **Everything is data, never code.** Digit glyphs, fonts and icons are pre-rendered bitmap tables the sender holds and uploads verbatim. The firmware has a small, fixed set of built-in display modes (scroll, static, clock, countdown, ...); a client never pushes logic, only content + parameters.

## Transport & Framing

All content is serialized as a flat list of hex-string bytes, then wrapped in three nested layers before it goes over BLE: a start/end/escape **envelope**, a **start packet** announcing an upload, and one or more **data packets** carrying the (LZSS-compressed) payload in chunks.

### Envelope bytes

Every BLE write — start packets and data packets alike — is wrapped the same way (`SendDataUtils.getSendDataWithInfo`):

- `0x01` START
- `len_hi` (2B length, big-endian)
- `len_lo`
- `… escaped payload …`
- `0x03` END

Before framing, any payload byte in the range `0x01`–`0x03` is escaped as `0x02` followed by `(byte XOR 0x04)` — because those three values are also the START/ESCAPE/END markers and would otherwise be ambiguous on the wire.

| Byte | Meaning |
|---|---|
| 0x01 | START_BYTE |
| 0x02 | ESCAPE_BYTE |
| 0x03 | END_BYTE |
| 0x04 | XOR_MASK (applied to the escaped byte) |

###     Start packet

Announces an incoming program before any data is sent, so the firmware can verify it against a CRC32 once fully received:

- `0x02` marker
- `CRC32` (4B, BE)
- `Length` (4B, BE)
- `Index` (1B)
- `Count` (1B)
- `ShowCount` (1B)

`Length` is the size of the **uncompressed** program content, in bytes. `Index`/`Count` are counts (unitless — which program / how many) addressing one program within a multi-program batch upload; `ShowCount` is a repeat count, in play-throughs (observed as `1` in every capture).

> **[CONFIRMED] What the show modes actually do**
>
> Every mode was run against the real panel with one small item and the travel of its lit centroid measured over four reads. Four behave exactly as named, moving along the named axis while holding still on the other:
>
> | Mode | Observed |
> |---|---|
> | STATIC | no movement at all (x and y both fixed) |
> | LEFT_CONTINUE | x 27.8 → 15.8, y fixed |
> | RIGHT_CONTINUE | x 1.8 → 13.8, y fixed |
> | UP_MODE | y 11.4 → 2.0, x fixed |
> | DOWN_MODE | y 1.5 → 3.4, x fixed (starts off-panel, scrolls in) |
> | SHINING_MODE | blinks — alternates between nothing lit and the full item |
> | LEFT_PAN_MODE / RIGHT_PAN_MODE | travelled the same way as the matching CONTINUE mode; no difference visible from a centroid |
> | LEFT_RIGHT_MODE | travelled leftward, with noticeably fewer cells lit |
> | RIGHT_COVER_MODE | lit-cell count rose and fell (11→9→7→11) without the centroid travelling — consistent with a reveal/cover wipe |
> | ACCUMULATE_MODE, LEFT_COVER_MODE | nothing lit across the whole observation window |
> | PICTURE_MODE | near-blank, one or two cells flickering |

      ````   ```` 
The last two rows are an observation over a few seconds, *not* a conclusion that those modes are broken: several modes begin with the content off-panel and scroll it in, so a short window can legitimately catch nothing. They may simply have a longer cycle than was watched.

> **[CONFIRMED] Confirmed via capture**
>
> A real Countdown-timer start packet decoded to `CRC32=0x0628874C, Length=194, Index=0, Count=1, ShowCount=1` — exactly matching this layout, byte for byte, after unescaping.
>

### Data packet (chunks)

The uncompressed content is LZSS-compressed, then split into chunks (Android chunks by 1024 hex-strings at a time) and sent as:

- `0x03` marker
- `0x00`
- `TotalLength` (4B, BE, bytes)
- `ChunkIndex` (2B, BE, 0-based count)
- `ChunkLength` (2B, BE, bytes)
- `… chunk data …`
- `Checksum` (1B)

The receiver acks each chunk before the next is sent; a client should retry a chunk (observed as up to 3 attempts) on an ack timeout.

> **[BUG] Known flakiness**
>
> The firmware occasionally reports a genuinely new upload as `"already stored"` (a duplicate-detection false positive) and silently skips it, especially at higher send rates — see Responses & Status Codes for the exact status code involved.
>

## Responses & Status Codes

The device replies to commands on the notify characteristic, each reply starting with the same marker byte as the command it answers. The reply's own shape is **specific to that marker** — there's no single uniform status enum shared across every command type, as the two most common replies show.

### Program start reply (marker 0x02)

- `0x02`
- `response` (1B)

| Value | Meaning |
|---|---|
| 0 | send the program data, starting from packet 0 |
| 1 | device already has this content — do not send data, treat as complete |

  This is a plain binary flag, not a 4-way success/error/timeout/invalid enum — value `1` is a normal, designed outcome ("I already have this"), not an error condition being misread as one.

### Data packet reply (marker 0x03)

- `0x03`
- `reserved` (1B, 0x00)
- `chunk index` (2B)
- `status` (1B)

The reserved `0x00` right after the marker mirrors the request's own `[0x03][0x00][…]` shape. It was missing from this table originally — added after decoding live replies byte-for-byte, where treating the reply as 3 bytes read the chunk index and status one byte early.

| Value | Meaning |
|---|---|
| 0 | chunk accepted — send the next one, or finish if this was the last |
| 1 | send error — retry |
| 2 | device error — retry |
| 3 | data error — retry |
| anything else | unknown error — retry |

> **[BUG]     The "already stored" flakiness is a firmware dedup issue, not an ambiguous status code**
>
> Since a program-start reply of `1` is a clean, purpose-built "already have it" signal (not an overloaded error code), a false positive here means the firmware's own duplicate-detection is what's misfiring under load — matching content it hasn't actually already stored, most likely via a CRC or cache-window comparison that collides or expires incorrectly at higher send rates — rather than a client misinterpreting an ambiguous reply. This was directly observed: in a high-frequency send test, nearly every batch after the first got a `1` reply and was silently skipped, for content that was demonstrably not a duplicate.
>

### Other command replies (marker matches the command)

| Marker | Command | Reply |
|---|---|---|
| 0x04 | Set brightness | [0x04][brightness echoed back] — always reported success |
| 0x05 | Power on/off | [0x05][0=off / 1=on] |
| 0x0C | Set mirror/flip | [0x0C][flip state echoed back] — always reported success |
| 0x0D | Verify password | [0x0D][0=correct / else=incorrect] |
| 0x0E | Set password | [0x0E][0=success / else=failure] |
| 0x1F | Device info query | see Device Info Query |

> **[CONFIRMED] ```` ```` ```` ```` ````  Flip takes a MODE, not a flag, and the order is not the obvious one**
>
> `0x0C` carries one of four values, in exactly this order, confirmed on the physical panel:
>
> | value | effect |
> |---|---|
> | 0 | no flip |
> | 1 | XY — both axes, i.e. a 180° rotation |
> | 2 | X — mirrored left-to-right |
> | 3 | Y — mirrored top-to-bottom |

   
XY comes *before* the two single-axis flips, so the natural guess — 1=X, 2=Y, 3=both — is wrong at every value.
 A bare 0/1 only ever reaches "none" and "XY". That is worth stating because of how it fails: choosing X or Y then appears to do the same thing as XY, which looks like a sign that cannot tell its axes apart, when in fact nothing was sending 2 or 3.

The replies below are undocumented — every shape here comes from firing the command at a real sign and reading what came back:

| Marker | Command | Reply |
|---|---|---|
| 0x09 | Synchronize time | [0x09][status 1B] — 0x00 observed |
| 0x0A | Set timer-switch schedule | [0x0A][status 1B] — 0x00 observed (with an empty schedule) |
| 0x0B | Query timer-switch status | [0x0B][0x00] — a single byte, on a device with no schedule ever set |
| 0x0F | Countdown control | [0x0F][sub-command echoed][7 more bytes] for query/start-stop, [1 more byte] for reset |
| 0x10 | Stopwatch control | same sub-command-echo shape as countdown |
| 0x11 | Scoreboard control | same shape; query returns 12 bytes after the echo |

> **[BUG] `````` `````` ```` ``````   Echoed sub-command confirmed, field layout not**
>
> For countdown/stopwatch/scoreboard the leading echoed sub-command byte is solid, but what sits in the bytes after it isn't. A countdown query fired two seconds into a *running* countdown read back all zeros after the echo — which rules out the obvious "remaining time" reading. `coolledux` therefore exposes those trailing bytes as `raw` rather than naming fields it hasn't verified. The single-status-byte replies (`0x09`, `0x0A`) are read as `0=success` by analogy with the confirmed `SetPassword`/`ProgramData` replies, not because a nonzero value was ever observed.
>

Two commands in the catalog are deliberately **never exercised**, live or in tests: **set password** (`0x0E`) writes a new unlock PIN into the sign's own non-volatile config — get it wrong and the sign is locked behind a PIN nobody knows, with no documented reset path — and **start OTA update** (`0xFE`) streams a replacement firmware image, which a wrong or truncated payload would brick. Both are encodable by `coolledux` for completeness; neither has a confirmed-live reply for that reason.

> **[BUG] The CRC32 in ProgramStart is not CRC-32, and the sign doesn't check it**
>
> The protocol's own checksum uses polynomial `0x04C11DB7` **non-reflected**, initialised to `0xFFFFFFFF`, with four table lookups per input byte. That is not what `zlib.crc32` computes, and it is not what `crc.py` computes either — ours is plain zlib CRC32, with a unit test asserting as much.

Both are accepted by the real panel. Our client has driven it for an entire project with the "wrong" value, and other clients drive it with theirs; content from both displays. So the firmware does *not* enforce this field, which contradicts what this document previously claimed — that a mismatch is silently discarded after reassembly. That claim was marked confirmed and was not.

Found by pointing another client at a simulated panel: the simulator validated the CRC the honest way, rejected a genuine upload, and the disagreement was the discovery. Driving the real sign with our own client could never have surfaced it, because both sides were only ever checked against themselves.
>
> **[CONFIRMED] What a client sends on connecting**
>
> Captured from a client talking to a simulated panel, in order:
>
> | Frame | Meaning |
> |---|---|
> | 0D + nonce + digits | VerifyPassword — it authenticates on every connect |
> | 1F | DeviceInfo |
> | 09 1a 09 17 03 09 29 16 | SynchronizeTime — sets the clock unprompted, here to 2026-09-23 09:41:22 |
> | FD | OTA/firmware version query |
> | 02 ... | ProgramStart, then chunked ProgramData |

**    
None of this is required to drive a sign — this project's own client does none of it — but it is what the sign is used to receiving.

> **[BUG] A frame may be split across several BLE writes**
>
> The envelope describes where a frame starts and ends, but nothing said a single write must contain a whole one. Large frames are split across writes, so a receiver has to buffer and reassemble: a frame runs from an unescaped `0x01` to an unescaped `0x03`, and since every `0x01`/`0x02`/`0x03` inside the payload is escaped, a bare END byte always terminates.

This project's client never exercised it, because its frames happen to fit in one write. Anything receiving frames needs to handle it; anything sending them can keep writing whole frames.
>

### Timing constants (confirmed live)

| Constant | Value |
|---|---|
| Program-start ack timeout | 5.0s |
| Per-chunk ack timeout | 3.0s |
| Poll interval while waiting | 50ms |
| Retry attempts per program | 3 |

A stalled upload consistently goes unacknowledged for ~5.0s before a retry is warranted.

## Other Commands

Beyond program uploads, a flat catalog of standalone control commands exists — each just `[marker][payload]`, enveloped the same way as everything else, with no start-packet/CRC/chunking dance:

| Marker | Command | Payload |
|---|---|---|
| 0x04 | Set brightness | brightness (1B) |
| 0x05 | Power on/off | 0=off, 1=on (1B) |
| 0x09 | Synchronize time | year-2000, month, day, weekday(1-7, Mon=1), hour, minute, second — 1B each |
| 0x0A | Set timer-switch schedule | item count (1B) + 6B per entry — see below |
| 0x0B | Query timer-switch status | none |
| 0x0C | Set mirror/flip | mode (1B): 0=none, 1=XY, 2=X, 3=Y |
| 0x0D | Verify password | random nonce (1B) + password digits XORed against the nonce + XOR checksum (see below) |
| 0x0E | Set password | same encoding as verify |
| 0x0F | Countdown control | sub-command (1B): 01=query status, 02=reset (+ hour/minute/second, 1B each), 03=start/stop (+ 0/1, 1B) |
| 0x10 | Stopwatch control | sub-command (1B): 01=query status, 02=reset, 03=start/stop (+ 0/1, 1B) |
| 0x11 | Scoreboard control | sub-command (1B): 01=query, 02=set scores (host/visitor current, 2B each; host/visitor total, 1B each), 03=set time (minute/second, 1B each, + count-down flag), 04=start/stop |
| 0x1F | Device info query | none — see Device Info Query |
| 0xFD | Query OTA/firmware version | none — bare marker |
| 0xFE | Start OTA firmware update | CRC32 (4B) + length (4B) + the update data itself, chunked like a program upload |

> **[CONFIRMED]         ```````` ```````` ``````````    Timer-switch entries are 6 bytes, and they really do switch the sign**
>
> Each scheduled entry:
>

- `enabled` (1B, 0/1)
- `hour` (1B, 0-23)
- `minute` (1B, 0-59)
- `weekdays` (1B, bitmask)
- `turns display on` (1B, 0=off/1=on)
- `reserved` (1B, 0)

> Weekday bits: Mon=1, Tue=2, Wed=4, Thu=8, Fri=16, Sat=32, Sun=64. Zero is how the app encodes "never" (no repeat) — there is no separate one-shot flag. An entry carries one time and one action, so on-in-the-morning/off-at-night is *two* entries; four is the usual cap though nothing in the format enforces one, and every edit resends the whole list since there's no incremental update. Sending zero items clears the schedule.
 **Confirmed live, on screen.** Schedules are evaluated against the device's own clock, so setting that clock to just before a scheduled time makes the switch land within seconds instead of waiting for a real minute boundary. With the device set to 11:59:45 and one entry of `01 0C 00 08 00 00` (enabled, 12:00, Thursday, turn off), a fully lit panel went dark on schedule.

That also settles the **query reply (`0x0B`)**, which is undocumented: it is `[item count][6-byte entries...]`, in the same layout. The query above returned `01 01 0C 00 08 00 00` — a count of 1 followed by that exact entry. The bare `0x00` seen from a device with no schedule had been read as a status byte; it is a count of zero.
>
> **[CONFIRMED] Password encoding**
>
> Both password commands XOR every digit against a random one-byte nonce (sent as the first payload byte), then append a running XOR checksum of everything sent so far. Never transmitted in the clear.
>
> **[CONFIRMED] How the vendor app uses the PIN**
>
> Confirmed from source, not on hardware.
>
> - **Six digits, default `000000`.** The app refuses any other length when setting one.
> - **The app keeps the PIN, not the sign's reply.** The sign never reports its PIN; the app stores the last one that worked, per device MAC address, and assumes `000000` for a sign it hasn't seen. A fresh sign letting the app in on that assumption is the only evidence of the factory default.
> - **It verifies on every connect** to a CoolLEDUX sign, with the stored PIN. On a wrong-PIN reply it retries, three times in all, then asks the user to type the PIN. A PIN typed in and accepted replaces the stored one.
> - **Setting a PIN** sends `0x0E` with the new six digits; on a `0` reply the app stores the new PIN.
> - **"Password on/off" is an app setting.** The switch in the app's settings changes only whether the app verifies on connect, and only for some other sign models. Nothing is sent to the sign, and a CoolLEDUX sign is verified whatever it says.
>
> **[BUG] Open question: what a PIN protects**
>
> Unknown. Our client never sends a verify and the sign obeys it, but it has only ever driven signs still on `000000`. Whether a sign with a PIN set refuses content or settings from a client that never verified — which is what would make a PIN protect anything — has not been tested, and can't be without setting one. With no known way to reset a forgotten PIN, that experiment waits for a sign it's acceptable to lose, or for the simulator's answer to be replaced by a real one.
>
> **[CONFIRMED] Brightness is a live global dimmer, and an 8-bit level**
>
> `0x04` changes what is **already on screen**, with nothing re-uploaded. Confirmed by eye on the physical panel, dragging a brightness slider.

The value is a **level, not a percentage**: the app's slider runs **5 to 255**, and a freshly powered panel reports 255 in its DeviceInfo reply.
 This page previously asserted the opposite — that brightness was baked into a program at upload time and left existing content alone — on the strength of one camera reading: white uploaded at brightness 100 measured RGB444 `(13,13,14)`, and dropping to 5 without re-uploading still measured `(12,13,14)`. That was a measurement artifact, not a property of the sign. White clips: with the camera's exposure locked and the panel near saturation, real dimming barely moves the top of the range. Measure brightness against a mid-level target, never against white.
>

Reply structures for time sync, timer-switch, countdown, stopwatch, scoreboard and OTA are undocumented — request only.

## Device Discovery

Signs advertise their pixel dimensions (and a few other fields) directly in their BLE advertisement, so a client can read them without connecting first. Offsets below are relative to the full raw scan record — the complete advertisement blob as the app's own code (`DeviceManager.java`) indexes it: every concatenated AD structure (flags, local name, service UUIDs, manufacturer data, ...) as one byte array, not just an isolated manufacturer-data payload.

| Byte offset | Field | Size | Notes |
|---|---|---|---|
| 9–10 | Device ID | 2B | read in reverse order — byte 10 first, then byte 9 |
| 17 | Panel height (rows) | 1B |  |
| 18–19 | Panel width (columns) | 2B, BE | byte 18 is the high byte — widths above 255 are representable |
| 20 | Device color type | 1B | enum, no named constants known — see behavioral note below |
| 21 | Device (firmware?) version | 1B | not confirmed |

````  ````   This held consistently across every CoolLEDX-family device tested.

> **[CONFIRMED] Confirmed live**
>
> Re-scanned the real sign (address `01:00:00:6D:7F:C6`) after closing the client holding it, so the sign would resume advertising (it stops while anything holds a connection). `bleak`'s `manufacturer_data` already strips the AD-structure header and 2-byte company ID before handing back the payload, so its offsets are 8 lower than the full-scan-record table above — but the field *widths and values* line up exactly:
>
> | Payload offset | Byte | Field |
> |---|---|---|
> | 0 | e0 | unknown |
> | 1 | 3e | unknown |
> | 2 | 06 | unknown |
> | 3 | 00 | unknown |
> | 4 | 00 | unknown |
> | 5 | 01 | unknown |
> | 6 | 10 | panel height (rows) = 16 |
> | 7–8 | 00 20 | panel width (columns, BE) = 32 |
> | 9 | 03 | device color type = 3 (see behavioral note below) |
> | 10 | 07 | device/firmware version |

```` ```` ```` ```` ```` ```` ```` `````` ```` ```` No ASCII meaning in the unknown bytes (only `0x3e` = `'>'` falls in printable range, and it's isolated). Not compared against anything in `DeviceManager.java` either — every `scanRecord` reference there is one of the five getters above (`getDeviceId`/`getDeviceRow`/`getDeviceColumn`/`getDeviceColorTye`/`getDeviceVersion`) or a debug log, never a comparison; device filtering happens upstream via the BLE scan's service-UUID filter (`fff0`), not by inspecting scan record bytes.

> **[CONFIRMED] Device color type — behavioral, not named**
>
> No named constants for this enum are known — but its effect is traceable:
>
```
if (DeviceManager.COOL_LED_DEVICE_COLOR_TYPE == 1) {
    // reads drawItem.colors (a per-pixel color LIST) and derives
    // drawItem.color from it via a device-specific TextEmojiManager
} else if (DeviceManager.COOL_LED_DEVICE_COLOR_TYPE != 2) {
    int i2 = DeviceManager.COOL_LED_DEVICE_COLOR_TYPE;  // dead code, no-op
}
```

> `color_type == 1` is the only value that does anything here: it switches to a per-pixel multi-color path. Every other value — including `2`, and our sign's own `3` — falls into a no-op branch, meaning the app just uses whatever single `drawItem.color` was already set elsewhere. Our sign's `color_type=3` is therefore confirmed *not* on the multi-color path, consistent with the single-solid-color-per-frame model `GraffitiContent`/`AnimationContent` already assume.
>

## Device Info Query

Sign capabilities that vary by hardware model — including how many program "zones" it can hold at once — aren't fixed protocol constants; they're queried live from the connected device.

Request (enveloped, no other bytes):

- `0x1F`

Reply — 9 fields, 1 byte each, in this order:

| Field | Notes |
|---|---|
| switchState | power on/off |
| brightNess | current brightness level |
| flip | display flip/mirror state |
| localMicSupported | whether this unit has an onboard mic for local (non-phone) audio-reactive mode |
| localMicOnOff | onboard mic enabled state |
| localMicMode | onboard mic visualization mode (enum) |
| showDeviceId |  |
| maxProgramNumber | the device's program/zone capacity — how many combinePrograms items it can hold loaded at once |
| remoteEnable |  |

       ````  Not independently captured against a live device — not yet cross-checked byte-for-byte against a real reply.

## Program Structure

A `CoolleduxProgram` carries a list, `combinePrograms`, of one or more content items. Concatenating every item's serialized bytes back-to-back is only part of it — that concatenation is itself wrapped once more before it becomes the `ProgramStart`/`ProgramData` payload (`SendDataUtils.getDataWithProgram`):

```
result = [0x00] * 8            // 8 reserved bytes, always zero
result += [contentItemCount]   // 1B -- combinePrograms.size(), NOT program index/count
result += [0x00]               // 1 more reserved byte
for item in program.combinePrograms:
    result += getDataForCombineProgram(item)
```

> **[CONFIRMED] Confirmed**
>
> This wrapper is what gets CRC32'd (for `ProgramStart`) and LZSS-compressed/chunked (for `ProgramData`) -- not the bare concatenated content. The wire framing itself doesn't depend on it: chunks ack normally either way. But the firmware's CRC check runs only after reassembling and decompressing every chunk, with no reply of its own -- a mismatch there discards the upload silently, with nothing in any prior response indicating failure. Note `ProgramStart`'s own `index`/`count` fields are a separate thing from this wrapper's content-item count: `index`/`count` describe which program and how many programs are in this batch (almost always `index=0, count=1`), while the wrapper's count byte is `combinePrograms.size()` for that one program.
>

This is also how split-screen layouts work — e.g. a Clock in the top half and scrolling Text in the bottom half is *two* combine-program items in one list, each with its own position/size fields (below). No separate "zone" concept exists; positioning alone determines where each item renders.

> **[CONFIRMED] Confirmed live, on screen**
>
> Two Graffiti items in a single upload — a solid block at `startColumn=0` and vertical stripes at `startColumn=16` — rendered side by side simultaneously on the panel, each confined to its own region. Deliberately different patterns, so neither a single full-panel solid fill nor a single full-panel striped fill could have produced the result.
>

## Common Content Header

Every content type shares the same opening shape before its type-specific fields begin:

| Field | Size | Notes |
|---|---|---|
| content type marker | 1B | Text glyphs=0x01, Graffiti=0x02, Animation=0x03, Text auto-colour=0x05 (below), Text custom colour=0x06 (below), Clock=0x07, Date=0x09, Countdown/Stopwatch=0x0A, Scoreboard=0x0B (unrelated to the Java-side getType() ints used for dispatch, which use a different numbering) |
| reserved | 7B | always zero in every capture |
| layerType | 1B | enum flag (unitless), not a position — usually 0 or 1 |
| startColumn | 2B | region's left edge, in pixels |
| startRow | 2B | region's top edge, in pixels |
| showWidth | 2B | region width, in pixels |
| showHeight | 2B | region height, in pixels |

```````````````````` ``````     After this header, the whole thing is wrapped once more: `[4-byte total length][header + type-specific fields]`, where the length includes its own 4 bytes.

> **[BUG] The position fields aren't actually universal**
>
> Graffiti, Animation and Text carry the full header above. **Clock, Countdown/Stopwatch and Scoreboard stop after `layerType`** — they have no `startColumn`/`startRow`/`showWidth`/`showHeight` at all, because each of their sub-fields (hour, minute, seconds, host score…) carries its own position and size instead. Rhythm isn't a content item in the first place. Encoding a Clock with the full header shifts every field after it by eight bytes and renders garbage.
>

## Content Types

### Graffiti (static bitmap)

| Field | Size | Units |
|---|---|---|
| mode | 1B | enum code (unitless) |
| speed | 1B | raw byte, 0-255 (no direct physical unit — see the Text speed note below, same field family) |
| stayTime | 1B | seconds |
| pixel data length | 4B | bytes |
| pixel data | N bytes | RGB444-packed pixel columns, see Font & Bitmap Encoding |

###    Animation

A list of Graffiti-style frames plus a per-frame delay list. Frame content is separately RGB444-packed per pixel column (see Font & Bitmap Encoding).

### Text

| Field | Size | Notes |
|---|---|---|
| mode | 1B | enum code (unitless) — see TextShowMode table below |
| speed | 1B | raw byte, 0-255 (no direct physical unit) — see empirical mapping below |
| stayTime | 1B | seconds |
| moveSpace | 2B | pixels — gap before scrolling text loops |
| item count | 2B | count of text/emoji items (unitless) |
| total width | 4B | pixel-columns — sum of all item widths |
| items… | var | each: width (1B, pixel-columns) + type (1B enum, 0=text/1=emoji) + item data — see the callout below on what each type actually accepts |

| Mode | Value |
|---|---|
| STATIC | 1 |
| LEFT_CONTINUE | 2 |
| RIGHT_CONTINUE | 3 |
| UP_MODE | 4 |
| DOWN_MODE | 5 |
| ACCUMULATE_MODE | 6 |
| PICTURE_MODE | 7 |
| SHINING_MODE | 8 |
| LEFT_PAN_MODE | 9 |
| RIGHT_PAN_MODE | 10 |
| LEFT_COVER_MODE | 11 |
| RIGHT_COVER_MODE | 12 |
| LEFT_RIGHT_MODE | 13 |

> **[CONFIRMED] ````                 Confirmed via capture**
>
> `moveSpace` is sent completely literally: the app UI's "Moving interval: 32" produced wire byte `0x0020` (32) with no transform. `speed` is *not* literal: the UI's "Display speed: 41" produced wire byte `0xF6` (246). `246 = 41 × 6` held for this one data point — treat as an approximate mapping, not a proven formula, until checked against more values. Higher byte values scroll faster; low values (e.g. 20) are clearly, visibly slower.
>
> **[CONFIRMED] A text program is TWO content items, and that is why text never worked**
>
> Text is not one item. The glyphs go in a **TextContent (`0x01`)**, and the colours go in a *separate* item beside it in the same program — either **TextCustomColor (`0x06`)**, one RGB444 colour per character, or **TextAutoColor (`0x05`)**.

Sent on its own, TextContent renders *nothing*. That is exactly what was observed when type-0 glyph items were first tried here, and it was written up as "the type-0 data layout is unconfirmed, possibly needs a field we don't send". Half right: the missing thing was not a field but a whole second content item. **The glyph encoding was correct all along** — column-major, 2 bytes per column at 16 rows, MSB first, exactly as Font & Bitmap Encoding says.

Captured from "123" going to a simulated panel: a colour item (laid out below) followed by a glyph item carrying three characters of widths 4, 8 and 7, summing to the declared total of 19. Decoding the first one column-major draws the stem and serif of a `1`.
 **Emoji items (type 1)** remain what they were: RGB444 pixel data, two bytes per pixel, carrying their own colour — which is why that path worked without any of this.
>
> **[CONFIRMED] Scroll geometry: one cycle is the text plus `moveSpace`, and the panel width never enters into it**
>
> `moveSpace` is the gap from the end of one pass to the start of the next, measured in columns. A cycle is therefore `totalWidth + moveSpace` — **not** the panel width, and there is no separate "scroll across, then pause" phase.

`moveSpace` is conventionally set to the panel width, which is what makes this look seamless: the next copy reaches the far edge exactly as the last one leaves. Confirmed on the real sign with a 23-column message on a 32-column panel at `moveSpace` 32 — *"the moment the e leaves the left side of the screen, the next animation brings in the left column of the o"*.
 Content narrower than the panel is not a special case. It is tempting to wrap short content over the panel width so the screen is never empty; the sign does not, and doing so keeps a short message permanently on screen chasing its own tail, which is a visibly different animation.
>
> **[CONFIRMED] Scroll rate, and why the wire byte is not the app's slider**
>
> Timed on hardware: a 23-column message at wire `speed` **215** took **4.3s** from appearing at the right edge to vanishing at the left of a single 32-column panel. That is 32 + 23 - 1 = 54 columns of travel, so about **12.6 columns/second**, or 0.0584 columns/second per unit of the speed byte.

That 215 came from setting the app's own speed control to **10**. The wire byte is not the slider value and is not even ordered the same way — a separate capture at the app's default sent 246. Anything reasoning from the app's numbers rather than the captured byte will be wrong.

This supersedes an earlier figure fitted to "about 4 columns in 0.6s at speed 60", which put the same traverse at 2.26s, roughly 1.9x too fast. A 0.6-second window read off a camera is a thin basis for a rate. Note the two points do not lie on a line through the origin, so proportionality rests on the one good measurement; a second timing at a very different speed byte would settle it.
>
> **[CONFIRMED] A program can hold several text zones, each with its own window**
>
> The app's split templates send **four** content items in one program, not two: a colour item and a glyph item for each zone. Captured from the top/bottom split on a 32x16 panel — two zones at `startRow` 0 and 8, both `showHeight` 8, scrolling opposite ways (`LEFT_CONTINUE` above, `RIGHT_CONTINUE` below).

Two consequences worth stating plainly, because both are easy to get wrong when only full-panel text has ever been seen:
>
> - **Glyph stride comes from the item's own `showHeight`, not the panel's height.** An 8-row band packs **one** byte per glyph column; a 16-row band packs two. The arithmetic proves it: three characters 8, 8 and 7 columns wide arrived in a 57-byte body, and 28 + 3×2 + 23×1 = 57, where two bytes per column would need 80.
> - **Each zone is clipped to its own window** (`startColumn`, `startRow`, `showWidth`, `showHeight`), and scrolls within it. Content leaving one zone must not appear in another.

> Colour items pair with the glyph item sharing their window, not simply with the nearest one — nothing in the format fixes the order, and taking the last colour item for every glyph item silently gives one zone the other's colours.

The two item types place that window at offsets differing by **one**, because `TextContent` carries a `layerType` byte the colour items do not. Reading both the same way yields a `showHeight` of 8192 instead of 8.
>
> **[CONFIRMED] TextCustomColor (`0x06`) — one colour per character**
>
```
06 00 00 00 00 00 | 00 20 | 00 00 | 00 00 | 00 20 | 00 10 | 02 | f6 | 03 | 00 | 00 03 | 00 13 | 04 08 07 | 0f00 0f00 0f00
marker  5 reserved  moveSpace startCol startRow showW   showH  mode speed stay  pad  count   totalW   widths     colours
```

> `count` is the number of CHARACTERS: one width byte and one two-byte RGB444 colour each. Confirmed byte for byte against a capture of "123"; our encoder reproduces it exactly.

Note `moveSpace` comes **before** the position fields here, unlike every other content type.
>
> **[NOTE] TextAutoColor (`0x05`) — a palette the firmware animates**
>
> The alternative to `0x06`, and a program carries one or the other, never both.
>
```
05 00 00 00 00 00 00 00 | 00 00 | 00 00 | 00 20 | 00 10 | 01 | e6 | 00 | 00 | 00 56 | 0f00 0f20 ...
marker    7 reserved       startCol startRow showW   showH  patt speed var  pad  BYTES    palette
```

> The count here is a BYTE count, not a colour count. 43 colours announce as `0x56` = 86. The identically placed, identically sized field in the `0x06` item beside it counts characters. The palettes are counted one hex string per byte, which is where the difference comes from — nothing about the wire suggests it.

There are fourteen choices, labelled with nothing but the numbers 1–14. They map onto a `(pattern, variant)` pair and one of only **two** palettes — eleven of the fourteen named tables are byte-identical copies of the rainbow:
>
> | type | pattern | variant | palette |
> |---|---|---|---|
> | 1, 2 | 1 | 0, 1 | rainbow |
> | 3 | 2 | 0 | rainbow |
> | 4, 5 | 3 | 2, 3 | rainbow |
> | 6, 7 | 4 | 4, 5 | rainbow |
> | 8 | 5 | 0 | primaries |
> | 9, 10 | 6 | 0, 1 | primaries |
> | 11, 12 | 7 | 0, 1 | rainbow |
> | 13, 14 | 8 | 0, 1 | rainbow |

Note the variants do not restart at 0 for every pattern. That irregularity is in the format itself, not in our reading of it — see the `if`/`else` chain, and is reproduced rather than tidied.
 **rainbow**: 43 entries, a full hue sweep at full saturation, first and last both `f00` so a sweep over it closes seamlessly.
 **primaries**: 6 entries — `f00 ff0 0f0 0ff 00f f0f`.
 What a pattern actually does on the panel is not known. It is firmware behaviour, and nothing documents it — the app just sends a number. The encoding above is fully determined by that source and is pinned by tests; the rendering is not. Settling it means photographing a real sign and comparing all fourteen.

The `speed` byte is the palette's own, separate from the TextContent's scroll speed. The app's default is 230.

> **[CONFIRMED] Items render on a text line height, not the full panel**
>
> An `emoji` item (type `1`, raw RGB444 pixel data) is the practical way to exercise this path from a codebase that doesn't ship the firmware's font: type `0` expects glyphs already rendered against that font, type `1` takes a bitmap directly. Doing so revealed a layout quirk — a 16-row item on a 16-row panel rendered across only the **top 13 rows**, so the firmware appears to lay items out against its own text line height rather than against `showHeight`. Observed live; not something the app's source states.
>
> **[CONFIRMED] stayTime, showTime and frame duration all confirmed on screen**
>
> These three had been sent as defaults and never verified to do anything; an implementation ignoring them entirely would have looked identical. All three are now confirmed against a camera, and the unit is what the tables claim:
>
> - **`stayTime` (seconds)** governs how long each program is held when several rotate. Two programs at `stayTime=1` alternated on nearly every read; the same pair at `8` held for 7 consecutive reads, about 8.4 seconds.
> - **Clock's `showTime` (seconds)** plays the same role for a Clock program: held 1–2 reads at `2`, 6–8 reads at `10`.
> - **Animation's frame duration** sets the frame pace: at 10s per frame the panel sat on one frame for 7 consecutive reads, at 300ms it never held more than 4.

> Note these are only observable once more than one program is uploaded (see Program Structure) — with a single program there is nothing to rotate to, so `stayTime` has no visible effect at all.

A measurement caveat worth repeating: a camera read takes about a second, so anything timed near or below that *aliases*. Counting transitions gave 2/4/3 for 100ms/1000ms/3000ms animation frames, which is noise. Measuring the longest HOLD instead separates cleanly.
>

### Date

Marker `0x09`. Never sent or observed here — listed because it exists and was missing from this table entirely, not because its layout is known.

### Clock

Marker `0x07`. After the common header:

| Field | Size | Notes |
|---|---|---|
| time-format flags | 1B | packed enum (unitless), from is24HourShowMode + isSpaceShing (blinking colon): 00=12h/no-blink, 01=24h/no-blink, 02=12h/blink, 03=24h/blink |
| showTime | 2B | seconds — how long the Clock is held when several programs rotate (confirmed live, see below) |
| numHeight, numWidth | 2B + 2B | pixels — the size of one rendered digit. Not a scale multiplier (see below) |
| hour digit bitmap table | 2B len (bytes) + N bytes | 10 concatenated glyphs (0–9); see Font & Bitmap Encoding for per-glyph pixel dimensions |
| hourColor + position/size | 2B + 2B×4 | color RGB444-packed (see below); position/size in pixels, same as the common header |
| spaceHourColor + position/size | 2B + 2B×4 | the hour:minute separator's color/position, in pixels |
| colon/separator bitmap | 2B len (bytes) + N bytes | spaceName |
| minuteColor + position/size | 2B + 2B×4 | pixels |
| spaceMinuteColor + position/size | 2B + 2B×4 | pixels |
| second colon bitmap | 2B len (bytes), +N bytes or 0 | reuses the same separator bitmap if showSpaceMinuteColor, else empty |
| secondsColor + position/size | 2B + 2B×4 | pixels |
| ampmColor + position/size | 2B + 2B×4 | pixels |
| AM/PM glyph bitmap | 2B len (bytes), +N bytes or 0 | only populated for specific panel sizes + styleIndex==7 |

> **[CONFIRMED] ````  ````          Confirmed from source**
>
> Clock/Countdown color fields (`ColorUtils.getColorDataWithColor`) are **2 bytes**, RGB444-packed: `byte0 = 0x0R, byte1 = 0xGB`, each channel quantized as flat `value / 16` (0-255 → 0-15). This is a *different* quantization curve than the Text/emoji color path (`getColorDataWithColorWithRGB444Transfer`), which uses non-linear breakpoints (≥238→15, ≤30→0, else `(value-30)/15 + 1`) — same 2-byte shape, different rounding behavior.
>
> **[CONFIRMED] Two different widths, easy to conflate**
>
> `numWidth`/`numHeight` and the per-field `width`/`height` are *not* the same measurement, and getting them mixed up renders a sparse, garbled clock rather than an obviously broken one:
>
> - `numWidth`/`numHeight` size **one digit**.
> - A field's own `width` (e.g. `hourWidth`) spans **both of that field's digits** — the full two-character cell, roughly `2 × numWidth` plus whatever spacing the firmware adds.

> Confirmed live: an hour field sized to one digit's width renders only part of the hour. The same pairing applies to the Countdown/Stopwatch and Scoreboard fields, which reuse this shape.
>

### Countdown / Stopwatch

> **[CONFIRMED] One counter runs at a time — the two share a single engine**
>
> A program can hold a countdown item and a stopwatch item at once, each with its own field rectangles, and both render with their own value. Only one of them advances. Starting either freezes the other where it stands; the frozen one keeps its number and resumes from it when restarted.

Confirmed on the real 32x16 panel with a count-up on the top half and a count-down on the bottom: starting the countdown stopped the stopwatch, and starting the stopwatch stopped the countdown. The control commands ack either way, so the reply tells you nothing — the only evidence is that the pixels stop changing.
 **To show two moving counters anyway**, give the engine to one and drive the other from the host: resend its reset (`0x0F`/`0x10` sub-command `0x02`) once a second with the next value. Confirmed working on the real sign — the display follows each reset while the other counter keeps ticking on its own.
>

Content-type marker **`0x0A`**. `CoolleduxTimeCountProgramContent` mirrors Clock's layout almost field-for-field (`timeCountMode` selects countdown vs. stopwatch), with the same hour/minute/second color+position+bitmap structure — including the `numWidth`-vs-field-`width` pairing described under Clock.

> **[CONFIRMED] Confirmed live, on screen**
>
> Both modes were watched ticking on a real panel: upload a TimeCount program, then drive it with the standalone `0x0F`/`0x10` control commands. Only the fields you actually give a position to are rendered — a 90-second countdown displayed as hour:minute looks *frozen*, because the only field changing (seconds) has nowhere to draw. That's a content-layout mistake, not a firmware fault, and it costs an hour if you assume otherwise.
>

A real capture of a 5-minute countdown's start showed:

```
marker=0x02  crc32=0x0628874C  length=194  index=0  count=1  showCount=1
```

...followed by an LZSS-compressed chunk that decompressed to exactly 194 bytes — matching the announced length — and then **no further BLE traffic for the rest of the capture window**, confirming the countdown ticks purely in firmware.

> **[BUG] Open question**
>
> The digit-bitmap-table math for Clock (worked out below) only accounts for 40–140 bytes per style on a 16×32 panel — far short of the ~10KB seen in one Countdown capture. Either TimeCount uses a larger/different digit encoding than Clock, or that capture included more than just this table. Not yet resolved.
>

### Scoreboard

Content-type marker **`0x0B`**. Structurally the largest content type: three independent digit tables (current score, total score, match time) each followed by their own coloured fields.  After `[marker][7 reserved zeros][layerType][0x00]` — note it carries *no* `startColumn`/`startRow`/`showWidth`/`showHeight`, same as Clock:

| Field | Size | Notes |
|---|---|---|
| scoreNumHeight, scoreNumWidth | 2B + 2B | pixels, one score digit |
| score digit table | 2B len + N bytes | 10 glyphs, as everywhere else |
| scoreHost field | 2B + 2B×4 | colour + column/row/width/height |
| scoreVisit field | 2B + 2B×4 | colour + column/row/width/height |
| scoreTotalNumHeight, …Width | 2B + 2B | pixels, one total-score digit |
| total-score digit table | 2B len + N bytes | 10 glyphs |
| scoreTotalHost, scoreTotalVisit | 2×(2B + 2B×4) | colour + position/size each |
| timeNumHeight, timeNumWidth | 2B + 2B | pixels, one clock digit |
| time digit table | 2B len + N bytes | 10 glyphs |
| minute, spaceMinute | 2×(2B + 2B×4) | colour + position/size each |
| colon bitmap | 2B len + N bytes | the minute/second separator |
| seconds field | 2B + 2B×4 | colour + position/size |

````   ```` ```` ```` ````  Every "field" above is the same repeated 10-byte shape: `[colour 2B][startColumn 2B][startRow 2B][width 2B][height 2B]`, with the colour using the flat `value/16` quantization (not the Text transfer curve), and `width` spanning both of that field's digits.

> **[BUG] Displayed score doesn't follow `SetScore` on this panel**
>
> A scoreboard program renders correctly on the 16×32 sign — two-digit host and visitor scores are legible on screen — but the `0x11` sub-command `02` (set scores) does not change what's displayed, even though a subsequent query shows the firmware's internal score state *did* update. There is no standard 16×32 scoreboard layout at all, so the most likely reading is that live score updates are wired only to panel geometries that app supports; two-digit scores are about all that fits on 16×32 regardless. Unresolved, and possibly not resolvable without a larger panel.
>

### Rhythm (audio-reactive)

Structurally different from every other content type: it's not a `combinePrograms` content item at all, and carries none of the common header's position/size fields — see below.

Mode select, sent once before playback starts:

- `0x06` marker
- `type` (1B, enum)

Bar data, sent repeatedly and continuously while music plays:

- `0x01` marker
- `type` (1B, enum)
- `bar 1` (1B, pixels)
- `bar 2` (1B, pixels)
- `…`
- `bar 8` (1B, pixels)

Both are wrapped in the standard START/escape/END envelope, same as everything else, but sent as standalone commands rather than as part of a program upload — there's no start-packet/CRC32/chunking dance for these, each message is just sent directly.

| Field | Size | Notes |
|---|---|---|
| marker | 1B | 0x06 for mode-select, 0x01 for a bar-data update |
| type | 1B | enum (unitless) — selects the visual style; same field in both messages |
| bar 1–8 | 1B each | pixels — one bar's height, clamped to the panel's row count |

> **[CONFIRMED] ````  No area/zone fields**
>
> Every other content type carries `startColumn`/`startRow`/`showWidth`/`showHeight` and is composed via the `combinePrograms` list (see Program Structure). Rhythm has neither — it's always exactly 8 bars, driving what appears to be the whole panel (or a fixed region baked into that device model's firmware), not a positionable sub-region like Graffiti/Text/Clock.
>
> **[BUG] Not supported by this hardware**
>
> The 16×32 sign used throughout this document reports `localMicSupported = 0` in its device info, and a live `SetRhythmType` (`0x06`) drew **no reply at all** — not an error reply, silence. That's consistent with the feature simply not existing in this unit's firmware rather than with a wire-format mistake, so the encoding above remains unverified against real hardware and rhythm replies fall through to a raw/unparsed response.
>

## Font & Bitmap Encoding

### Column byte-packing

Monochrome glyph/icon data is packed one column at a time, top-to-bottom, MSB first:

```
bytes_per_column = ceil(panel_height_in_pixels / 8)
```

At the sign's native 16-pixel row height, that's **2 bytes per column**. A glyph N pixel-columns wide is `N × 2` bytes.

### Digit tables are always 10 glyphs

Every Clock digit-bitmap table holds exactly ten glyphs (0–9), concatenated. Verified across every style on a 16×32 panel — all table sizes divide evenly by 10:

| Style | Total bytes | Bytes/digit | Implied glyph size |
|---|---|---|---|
| 1, 3, 4, 6 | 140 | 14 | 7 cols × 16 rows (2B/col) |
| 2, 5 | 60 | 6 | 3 cols × 16 rows (2B/col) |
| 7, 8 | 50 | 5 | 5 cols × 8 rows (1B/col) — shorter glyph |
| 9 | 40 | 4 | 4 cols × 8 rows (1B/col) |

Decoding style 9's digit "0" (`248, 136, 248, 0`) as a 4-col×8-row bitmap does render a recognizable box/"0" shape, confirming styles 7–9 use a shorter glyph height rather than fewer digits.

### speed and moveSpace need real values

> **[BUG] Zero values break scrolling**
>
> Leaving `speed` at `0` or `moveSpace` at `0` produces a sign that flips between two static pages instead of scrolling smoothly — both fields need real non-zero values (see the confirmed mapping under Text above) for continuous scrolling to work at all.
>
> **[CONFIRMED] Confirmed working font size**
>
> Font size **16** matches this panel's native 16-pixel row height exactly, needing no glyph height conversion at all, and is confirmed rendering correctly against the real firmware.
>

## Buffering & Rate Limits

Since every update is a whole-panel upload with real per-upload latency, an animated progress bar can't just push a new frame the instant it changes. These practical constraints shape how a client should drive the sign:

### Upload latency dominates at this scale

A single small (32×16) frame upload typically completes in 150–600ms round-trip (start-packet negotiation + one or two data chunks + acks). That fixed per-upload cost, not raw bandwidth, is the real bottleneck — so batching several frames into one Animation-type upload is far more efficient than sending one static Graffiti frame per update.

### Batching with a recycled frame for seamless handoff

Send new frames a few at a time per upload (2 worked well in testing), timed to arrive *one frame-duration before* the currently-playing batch finishes, with the previous batch's last-displayed frame prepended to the new batch:

```
frames_to_send = [recycled_last_frame] + new_frames   # recycled frame replays first
upload(frames_to_send, frame_duration_ms)
sleep(len(frames_to_send) * frame_duration - frame_duration - upload_time)
```

Without the recycled frame, the moment a new batch takes over there's a visible jump backward (the animation "loops back" to its first frame for one tick before continuing). Prepending the last real frame hides the seam.

### The ambiguous status code gets worse at higher send rates

> **[BUG] Empirical finding**
>
> Pushing updates faster doesn't just risk falling behind on bandwidth — it sharply increases how often the `RESPONSE_ERROR` ("already stored") ambiguity described above fires against genuinely new content. At roughly 10fps (a fresh, different frame every ~100ms), **nearly every batch after the first got silently dropped**. At ~2.7fps, only the very last batch (racing the final static "endcap" frame) occasionally hit it.
>

Because of this, a rate cap independent of the requested duration is worth enforcing outright:

```
max_fps = 32 / 12  # ~2.67 fps, empirically the fastest reliable rate on a 32-wide panel
steps = min(native_pixel_steps, round(total_duration_seconds * max_fps))
```

For a short countdown, this means fewer, coarser fill-steps rather than a fast-but-unreliable full-resolution sweep. For a long countdown, the cap never binds — `steps` is already capped by the panel's native pixel width.

### The endcap

Animation programs loop by default. Without an explicit final static frame, the completed 100% bar (or the last few seconds of a countdown) would loop forever instead of holding. A client should always finish with one plain static Graffiti frame at the final state after the last animation batch.
