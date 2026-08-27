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

### Added

- `pinside project` writes a KiCad project for the fixture board: every probe at its DUT test
  point's own coordinates, the DUT's outline and mounting holes, and a generated pogo receptacle
  footprint. Routing is deliberately not generated.
- Carrier boards (`target.board`), defaulting to the **Raspberry Pi Pico 2**. A module exposes
  only some of its chip's pins, and configs are now checked against the board rather than the
  chip (`PF024`). Naming a board determines the chip.
- A spring-pin catalogue (`fixture.probe`), defaulting to the Mill-Max 0985 receptacle. The probe
  sets the minimum spacing, so a finer pin relaxes `PS021` without a second edit.
- `fixture.mirror`, defaulting to `x` — a bed-of-nails takes the DUT face-down.

### Fixed

- `resolve_board` now applies the fixture transform. It did not, so `fx`/`fy` were zero
  everywhere: not obviously wrong, just every probe at the origin.

### Note

Generating a project needs KiCad installed, because a schematic embeds a copy of every symbol it
places. `check` and `generate` still need nothing.

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

[Unreleased]: https://github.com/bitcrushtesting/pinside/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/bitcrushtesting/pinside/releases/tag/v0.1.0
