# Changelog

Notable changes to pinside. The format follows [Keep a Changelog][kac], and the
versions follow [semantic versioning][semver].

A version number covers the tool. The **firmware contract** it generates has its
own compatibility story: a change to the JSON-RPC methods or to the fixture
config schema is a breaking change here, because it makes existing configs or
existing agents wrong.

[kac]: https://keepachangelog.com/en/1.1.0/
[semver]: https://semver.org/spec/v2.0.0.html

## [Unreleased]

## [0.2.0]

The fixture board, the firmware's host side, and the first release published to PyPI.

### Added

- `pinside project` writes a KiCad project for the fixture board: every probe at its DUT test
  point's own coordinates, the DUT's outline and mounting holes, a generated pogo receptacle
  footprint, and a GND pour on both copper layers. Routing is deliberately not generated.
- Carrier boards (`target.board`), defaulting to the **Raspberry Pi Pico 2**. A module exposes
  only some of its chip's pins, and configs are now checked against the board rather than the
  chip (`PF024`). Naming a board determines the chip.
- A spring-pin catalogue (`fixture.probe`), defaulting to the Mill-Max 0985 receptacle. The probe
  sets the minimum spacing, so a finer pin relaxes `PS021` without a second edit. Each entry
  records where its dimensions came from and whether anyone has checked them against the
  supplier's drawing; the generated fixture README says which.
- `fixture.mirror`, defaulting to `x`, because a bed-of-nails takes the DUT face-down.
- **`pinside.client` and `pinside probe`**, an optional extra (`pip install 'pinside[client]'`).
  A JSON-RPC client over USB CDC that refuses a fixture whose config hash disagrees with the
  config it was handed, and keeps pushed notifications out of the response stream. `pinside
  probe` is the bench smoke test: it names every channel and fails on a rail out of range.
- **Baselines.** `--write-baseline` records the findings a board has already been judged on;
  `--baseline` accepts them. Suppression is by code *and* reference, so a new occurrence of an
  accepted code still fails. Available on `check`, `generate` and `project`.
- `--json` on `generate` and `project`, so their findings can be parsed rather than scraped off
  stderr. It reports on the refusal path too.
- New board checks: cutouts and second outlines (`PS003`, `PS004`), probes and mounting holes
  placed over a cutout (`PS013`, `PS014`), a receptacle body fouling a neighbouring component
  (`PS027`), supply rails or reset lines the fixture cannot reach (`PS033`, `PS034`), and net
  numbering that KiCad will not preserve (`PS043`, `PS044`).
- The `rp2354a` target, and `targets.SAME_PINOUT` recording that the stacked-flash parts share
  their plain counterparts' pin map.
- The generated fixture README turns the plate force into hardware: whether a thumbscrew or a
  pneumatic clamp is needed, what each standoff carries, and how long they have to be.
- `scripts/test.sh --coverage`.
- CI now runs the KiCad tests inside the KiCad container and cross-compiles the generated
  firmware against a pinned Pico SDK. Both paths were previously verified only on a developer's
  machine.

### Fixed

- **`pinside init` did not work at all.** The positional `board` and the carrier option
  `--board` shared an argparse destination, so every invocation looked the `.kicad_pcb` path up
  in the module catalogue and refused. The carrier option is now `--carrier`.
- **`pinside project --board pico2w` produced a schematic KiCad could not open.** KiCad writes
  the Pico W as a symbol deriving from the Pico, carrying properties and no pins; copied into a
  schematic's `lib_symbols` that `extends` resolves to nothing. Derived symbols are now
  flattened against their parent, units included.
- **A board with a slot or a cutout was reported as having an unclosed outline** (`PS002`, an
  error). Edge.Cuts holds every edge a board has; the largest ring is now the perimeter and the
  rest are cutouts.
- **`examples/demo-board.kicad_pcb` had a netlist KiCad could not keep.** All sixteen test points
  were written with net ordinal `1` under sixteen different names. Through KiCad 9 a net is
  identified by its ordinal, so KiCad read them as one net and the first save dropped fifteen of
  them to no-net. `pinside check` called the board clean, because it read the name off each pad
  and never compared the ordinals: that is what `PS043` now catches, and the board is renumbered.
  Confirmed in both directions against `kicad-cli pcb export ipcd356`.
- **The generated firmware did not link against the real Pico SDK.** `main.c` calls
  `set_sys_clock_khz`, a `static inline` in `hardware/clocks.h`, and included only
  `pico/stdlib.h`, which does not pull that in. Calling an undeclared function is a warning
  under C11, not an error, so every translation unit compiled and only the linker objected. The
  host tests could not have caught it: they build `fixture_core.c` against the mock HAL and
  never compile `main.c`. Found by the new cross-compile job on its first run. The generated
  project now includes the header, links `hardware_clocks`, and builds with
  `-Werror=implicit-function-declaration`, so the next missing header is a compile error naming
  the function rather than a bare symbol at link time.
- `resolve_board` now applies the fixture transform. It did not, so `fx`/`fy` were zero
  everywhere: not obviously wrong, just every probe at the origin.
- Findings printed by the CLI now go to the current `sys.stderr` rather than whichever stream it
  was at import time.

### Changed

- `PS002` now fires only on segments that genuinely close into nothing, and reports how many.
- `check_placement` distinguishes "never placed" from "placed over a hole", so a probe in a
  cutout no longer reports both.
- The probe body diameter is a limit of its own (`--probe-body`), taken from the chosen probe.

### Note

Generating a project needs KiCad installed, because a schematic embeds a copy of every symbol it
places. `check` and `generate` still need nothing, and `probe` is the only command with a
dependency at all.

## [0.1.0]

First release.

### Geometry and checks

- Read test points, mounting holes and the `Edge.Cuts` outline out of a
  `.kicad_pcb`, with arcs and rounded rectangles flattened and the outline
  chained into a ring.
- Report the probe field as a table, CSV, JSON, or a 1:1 SVG drill plan, in the
  board's own frame or in a mirrored fixture frame.
- 21 checks (`PS001`–`PS052`) covering geometry, probe spacing, obstructions,
  ground return and mounting. Exit status makes them usable as a CI gate.

### Firmware

- `pinside init` drafts a fixture config from a board; `pinside generate` turns
  one into a Pico SDK project with host tests.
- Configs are validated against the target's GPIO function map and against the
  DUT board itself. Nothing is written unless both agree.
- The generated firmware speaks JSON-RPC 2.0 over USB CDC, streams UART traffic
  as unsolicited notifications, and reports a hash of the channel map so an
  agent can tell whether the board matches the config.
- Buses the DUT also masters can be declared with a `guard`: the firmware
  refuses to drive them until the named channel is asserted.

### Targets

- RP2350A, RP2350B and RP2354B.

[Unreleased]: https://github.com/bitcrushtesting/pinside/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/bitcrushtesting/pinside/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/bitcrushtesting/pinside/releases/tag/v0.1.0
