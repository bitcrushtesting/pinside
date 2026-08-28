# Contributing to pinside

## Getting set up

There is no setup step. The scripts install what they need:

```bash
scripts/test.sh            # the test suite
scripts/lint.sh            # ruff, plus clang-format on the firmware templates
scripts/lint.sh --fix      # apply what can be applied
```

`scripts/lint.sh` creates `.venv-tools/` and installs the ruff version pinned in
`pyproject.toml`, so CI and your machine run the same one. `clang-format` is the
exception: it comes from a toolchain rather than from pip, and the script tells
you how to get it rather than guessing which one you want.

## Layout

```
src/pinside/
    sexpr.py        a tolerant reader for KiCad's S-expressions
    geometry.py     outlines: arcs and rounded rects flattened, segments chained
    board.py        what is read out of a .kicad_pcb
    checks.py       everything that can be wrong with a board (PS...)
    report.py       table, CSV, JSON, SVG
    targets.py      what each microcontroller can do with each pin
    config.py       the fixture config, and everything wrong with one (PF...)
    scaffold.py     drafting a config from a board
    modules.py      carrier boards, and which pins they bring out
    pogo.py         spring-pin probes and the holes they need
    cli.py          check | init | generate | project
    kicad/          the KiCad project emitter
    firmware/
        generate.py the emitter
        templates/  the C. Only fixture_config.c is really generated.
tests/              synthetic boards; no real project, no KiCad, no SDK
examples/           a small board and config that pass everything
```

## The rules that matter

**Existing KiCad files are read, never modified.** Text edits break the UUID
cross-references KiCad keeps between a symbol, its instance data and the footprint on the board.
`pinside project` writes *new* projects, which is a different job and a safe one: every
identifier is derived from the config, so the result is internally consistent and regenerating
an unchanged config is byte-identical.

**Symbol definitions are copied verbatim, never re-serialised.** KiCad's format distinguishes a
bare token from a quoted string (`(type default)` and `(shape line)` are not strings) and a
parse throws that away. Round-tripping a symbol produces a file KiCad silently refuses to open.

**A finding must be actionable.** Each one says what is wrong, which references
it applies to, and what to do. "GPIO9 cannot be i2c0 sda" is only useful
because it goes on to name the pins that can.

**A finding code is a public interface.** People put them in `--ignore` lists,
in baseline files, and in CI configuration that nobody revisits. So:

- **Codes are appended, never renumbered.** Pick the next free number in the
  right family (`PS0[0-5]x` by subject, `PF0xx` by stage). Gaps are fine;
  reordering is not.
- **A withdrawn code is retired, not reused.** Delete the check, leave the
  number burnt, and say so in `CHANGELOG.md`. Reusing it silently repoints
  every existing `--ignore PS0xx` at a different question, and the people
  affected are the ones who will never read the release notes.
- **Widening a code needs a new one.** If a check starts reporting a case it
  did not before, that is a new finding: somebody accepted the old meaning.
  Narrowing it, or improving its wording or its detail, is not.
- **Every code must appear in the README's tables.** `tests/test_docs.py`
  enforces this in both directions, so an undocumented code fails CI and so
  does a documented code nothing emits.

**The generated firmware is tested, not just generated.** `fixture_core.c` has no
SDK headers precisely so it can be built against `mock_hal.c` on a host. A change
to the protocol needs a test in `templates/test_core.c`, and that suite runs as
part of `scripts/test.sh`.

**Templates are formatted C.** They are the product, so `clang-format` runs over
them. Placeholders are identifiers (`PINSIDE__NAME_MAX`) rather than a
punctuation sigil, because a formatter turns `@@NAME_MAX@@` into
`@ @NAME_MAX @ @` and generates a file that will not compile. The generator
refuses to write any file with a placeholder left in it.

## Adding a check

1. Write it in `checks.py` as a function taking `(board, limits)` and returning
   a list of `Finding`, and add it to `CHECKS`.
2. Give it the next free code, and a severity: `ERROR` for something that makes
   a fixture unbuildable or unsafe, `WARNING` for unreliable, `INFO` for worth
   knowing.
3. Add a board to `tests/boards.py` that triggers it, and a test that the clean
   board does *not* trigger it. A check that fires on `boards.healthy()` is a
   false positive by definition.
4. Add the row to the table in `README.md`.

## Adding a target

`targets.py` holds pin capability, not a datasheet. Add a `Target`, and check
the function map against the vendor's own table. `tests/test_firmware.py` has
spot checks for the RP2350 that exist because getting this wrong produces a
config that validates and does not work. `tests/test_kicad.py` checks the GPIO
count and the ADC map a second time against KiCad's own symbol library, which
is an independent transcription of the same pinout; give a new target an entry
in `TestTargetsAgainstKiCad.SYMBOLS` so it gets that check too.

A part in the same family as one already here costs almost nothing: the RP2354s
are the RP2350s with flash in the package, so they share a pin map, and
`targets.SAME_PINOUT` records that rather than keeping two copies to drift.

### A target from another vendor

Every target so far is an RP2350, so one HAL covers them all. A part from
another vendor needs a second implementation beside `fixture_hal_rp2350.c`, and
that is the work worth scoping before starting.

`fixture_hal.h` is the whole contract: **17 functions in 56 lines**, and
`fixture_hal_rp2350.c` implements them in 114. Nothing above it knows what chip
it is on. Grouped by what they need from the silicon:

| Group | Functions | What it takes |
|---|---|---|
| Console | `fx_hal_write`, `fx_hal_millis` | A byte sink and a millisecond counter. USB CDC on the RP2350; a UART is fine. |
| GPIO | `configure`, `get`, `set`, `release` | Direction, pull, and a real high-Z for open-drain. `release` must actually float the pin, not drive it high. |
| ADC | `read`, `full_scale`, `reference_mv` | Raw counts, plus the two numbers that turn them into volts. Parts differ in both; do not hardcode 12 bits. |
| UART | `configure`, `write`, `read` | Non-blocking `read`: it is polled from `fx_core_poll` for streaming, and blocking there stalls every other channel. |
| I2C | `write`, `read` | Return a negative value on NAK. The scan depends on telling a NAK from a bus error. |
| SPI | `configure`, `transfer` | Mode and speed, and a full-duplex transfer. |

Two of these are where an ill-fitting port shows up:

- **`fx_hal_gpio_release` must be high-Z.** A HAL that drives high instead makes
  every `open_drain` channel a short across whatever the DUT pulls up. Nothing
  in the core can detect that; it just contends.
- **`fx_hal_uart_read` must not block.** `fx_core_poll` calls it for every
  streaming bus on every pass.

`mock_hal.c` is a complete implementation in 121 lines and is the thing to read
first: it is what the host tests run against, so it defines the behaviour a real
HAL has to match. A new HAL should compile against the same `test_core.c`.

The generator also needs the new HAL wired into the emitted `CMakeLists.txt`,
which currently assumes the Pico SDK.

## Before opening a pull request

```bash
scripts/lint.sh && scripts/test.sh
```

The KiCad-facing tests skip themselves without KiCad installed, so **CI does not cover them**:
run them locally before changing anything under `kicad/`. They were verified against KiCad 10.0.3.

Both run in CI along with an end-to-end job that installs the wheel and
generates firmware from `examples/`. If you changed anything under
`firmware/templates/`, say in the pull request whether the generated project
still builds against the Pico SDK; CI compiles the host tests, but it has no
SDK and cannot build the firmware itself.

## Versioning

The version in `pyproject.toml` and `src/pinside/__init__.py` is the tool's. A
change to the JSON-RPC methods or the fixture config schema is breaking, because
it invalidates configs and agents that already exist. Record it under
`## [Unreleased]` in `CHANGELOG.md`; a `v*` tag builds the release and quotes
that section, and fails if the tag and `pyproject.toml` disagree.
