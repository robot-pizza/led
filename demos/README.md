# Demos

Each one shows a single thing the protocol does. They all find the
first sign advertising and drive it, so power a panel on first — and
close any other client, since a sign stops advertising while something
is connected to it.

```bash
uv run python demos/hello.py [WORD ...]
uv run python demos/scroll.py
uv run python demos/clock.py
uv run python demos/timer.py
uv run python demos/display.py
uv run python demos/image.py picture.png
uv run python demos/gif.py animation.gif
```

| | |
|---|---|
| **hello** | Words cycling, as one Animation the sign plays by itself. |
| **scroll** | Scrolling text — and why it needs **two** content items. |
| **clock** | Hands the sign a font and some rectangles; it keeps time alone. |
| **timer** | A count-up and a count-down, and the single engine behind them. |
| **display** | Brightness as a live dimmer, and the four flip modes. |
| **image** | A PNG, scaled to the panel. |
| **gif** | A GIF, uploaded once and looped by the sign. |

Three are worth reading rather than just running, because each encodes
something that was wrong here for a while and cost real time to
correct:

- **scroll.py** — a `TextContent` carries glyph shapes and *no colour*.
  Sent alone it renders nothing, which looked for a long time like a
  broken glyph encoding. The colour is a second content item beside it.
- **display.py** — brightness changes what is already on screen (it was
  documented as baked in at upload time), and flip is a four-value mode
  where `XY` is 1 and the single-axis flips are 2 and 3 (it was
  documented as a boolean, so `X` and `Y` were unreachable).
- **timer.py** — two counters can be displayed but only one can run.
  Starting either freezes the other.

All of them run on a 32x16 panel as well as a 256x32. `clock.py` and
`timer.py` pick a digit font that fits rather than refusing or
overflowing: the manufacturer's 7x16 glyphs where there is room, a
5x7 font where there isn't.

That matters because the sign clips a field positioned off-panel
**silently**. Asking for more than fits produces a plausible-looking
wrong answer, not an error.
