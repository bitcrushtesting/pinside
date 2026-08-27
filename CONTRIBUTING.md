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
    cli.py          check | init | generate
    firmware/
        generate.py the emitter
        templates/  the C. Only fixture_config.c is really generated.
tests/              synthetic boards; no real project, no KiCad, no SDK
examples/           a small board and config that pass everything
```

## The rules that matter

**KiCad files are read, never written.** Text edits break the UUID
cross-references KiCad keeps, and a corrupted board is expensive. If pinside ever
needs to write one, it goes through KiCad's own tooling.

**A finding must be actionable.** Each one says what is wrong, which references
it applies to, and what to do — "GPIO9 cannot be i2c0 sda" is only useful
because it goes on to name the pins that can. New codes are appended, never
renumbered: people put them in `--ignore` lists.

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
the function map against the vendor's own table — `tests/test_firmware.py` has
spot checks for the RP2350 that exist because getting this wrong produces a
config that validates and does not work.

## Before opening a pull request

```bash
scripts/lint.sh && scripts/test.sh
```

Both run in CI along with an end-to-end job that installs the wheel and
generates firmware from `examples/`. If you changed anything under
`firmware/templates/`, say in the pull request whether the generated project
still builds against the Pico SDK — CI compiles the host tests, but it has no
SDK and cannot build the firmware itself.

## Versioning

The version in `pyproject.toml` and `src/pinside/__init__.py` is the tool's. A
change to the JSON-RPC methods or the fixture config schema is breaking, because
it invalidates configs and agents that already exist. Record it under
`## [Unreleased]` in `CHANGELOG.md`; a `v*` tag builds the release and quotes
that section, and fails if the tag and `pyproject.toml` disagree.
