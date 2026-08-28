# Tasklist

What is planned next for pinside, and what has just been done. Each open item
says why it matters and what "done" looks like, so it can be picked up without
re-deriving the reasoning.

Codes in brackets are the finding namespaces: `PS...` for board checks
(`checks.py`), `PF...` for config checks (`config.py`).

## Done in 0.2.0

The previous version of this file listed seven sections of work. Most of it
landed; the entries below record what came of it, because two items turned into
bug fixes rather than features and that is worth saying out loud.

- **Cut 0.2.0 and publish to PyPI.** `release.yml` now builds, publishes through
  a PyPI trusted publisher, and only then creates the GitHub release. **One
  manual step remains**, and the first tag will fail without it: configure the
  trusted publisher at `pypi.org/manage/project/pinside/settings/publishing`
  with owner `bitcrushtesting`, repository `pinside`, workflow `release.yml`,
  environment `pypi`, and create a `pypi` environment in the repository
  settings.
- **A host client and `pinside probe`.** `pinside.client` speaks the generated
  protocol, refuses a fixture whose config hash disagrees, and keeps pushed
  notifications out of the response stream. Optional extra: `pinside[client]`.
- **Real contract validation.** `tests/test_contract.py` checks `openrpc.json`
  as OpenRPC and against `fixture_core.c`'s own dispatch table, in both
  directions.
- **The CI-invisible paths.** The KiCad tests now run in the `kicad/kicad:10.0`
  container with `PINSIDE_REQUIRE_KICAD=1`, which turns a skip into a failure;
  the generated firmware is cross-compiled against a pinned Pico SDK.
- **Coverage**, at 87% via `scripts/test.sh --coverage`.
- **Baselines and `--json`**, on `check`, `generate` and `project`.
- **New checks**: `PS003`, `PS004`, `PS013`, `PS014`, `PS027`, `PS033`, `PS034`.
- **A ground pour and standoff guidance** on the generated fixture board.
- **Probe provenance**, `rp2354a`, and the HAL boundary scoped in
  CONTRIBUTING.md.
- **The finding-code policy**, written down in CONTRIBUTING.md.

Three bugs surfaced while doing the above, all of which shipped in 0.1.0:

- **`pinside init` did not work at all.** The positional `board` and the carrier
  option `--board` shared an argparse destination. The option is now
  `--carrier`, and `main()` derives its command list from the parser rather than
  a hand-kept set, which is what let a new subcommand be swallowed too.
- **`pinside project --board pico2w` emitted a schematic KiCad would not open.**
  Derived symbols are now flattened against their parent.
- **A board with a cutout was reported as having an unclosed outline.**
  Edge.Cuts holds every edge; the largest ring is the perimeter now.

## 1. What the new checks still cannot see

`PS033` and `PS034` read net names off component pads, which is the only
evidence a `.kicad_pcb` carries. That has limits worth closing.

- [ ] **Read net classes, not just net names.** A board that puts its rails in a
      `Power` net class states the fact that `PS033` currently infers from a
      leading `+`. The class is in the `.kicad_pcb`; nothing reads it yet.
- [ ] **Courtyards, not pad bounding boxes.** `PS024` and `PS027` measure
      against the envelope of a footprint's pads, so a tall part with small pads
      (an electrolytic, a shielded module) reads as smaller than it is. The
      `F.CrtYd` polygon is the real answer and `geometry.py` can already flatten
      one.
- [ ] **Component height.** The fixture-side collision `PS027` checks is planar.
      A probe's clearance actually depends on how far the part stands off the
      board, which is in the 3D model reference and nowhere else useful.
      Probably needs a per-footprint height override in the config.

## 2. Breadth of hardware

- [ ] **A second microcontroller family.** Everything is an RP2350, so one HAL
      covers it. `CONTRIBUTING.md` now scopes what a second one costs: 17
      functions, of which `fx_hal_gpio_release` (must be genuine high-Z) and
      `fx_hal_uart_read` (must not block) are the two that go quietly wrong.
      An ESP32-S3 or an STM32 would say whether `Target` is the right shape.
      Also needs the emitted `CMakeLists.txt` to stop assuming the Pico SDK.
- [ ] **A third carrier board.** Deliberately not done: the only RP2350-family
      module symbols KiCad ships are the Picos, and a header map with nothing to
      check it against is the "validates and does not work" failure
      `CONTRIBUTING.md` warns about. `TestModules` now checks *every* module
      against KiCad's symbol, so adding one is safe as soon as there is a symbol
      to add it against.
- [ ] **Verify the probe catalogue against supplier drawings.** Each entry now
      records its `source` and an empty `verified`, and the generated fixture
      README says in as many words that nobody has checked. Someone with the
      Mill-Max drawings should fill those in. This is the one open item that
      needs paper, not code.

## 3. The fixture board

- [ ] **Thermal relief on the ground pour is guessed.** 0.5 mm gap and bridge
      are reasonable defaults, not measured ones. They should follow the probe:
      a receptacle pressed into a full pour is what makes a worn pin
      unreplaceable, and that is the whole argument for receptacles.
- [ ] **Place the controller somewhere sensible.** It is dropped off the board
      edge for a person to move. Putting it inside the outline, clear of the
      probe field, is a small placement problem with an obvious answer.
- [ ] **Panelisation or a frame outline** for fixtures mounted into an
      off-the-shelf enclosure. Still only worth doing once someone names the
      enclosure.

## 4. The host side

- [ ] **`pinside probe --watch`.** Streaming notifications to stdout as they
      arrive is most of a bench log, and `Fixture.poll` already returns them.
- [ ] **Auto-detect the fixture.** `_sole_port` uses the only port when there is
      exactly one and refuses to guess otherwise. Opening each candidate and
      asking `fixture.info` would do better, at the cost of poking things that
      are not fixtures.
- [ ] **A recorded transcript for the tests.** `FakePort` is a hand-written
      stand-in. Capturing one real session against a flashed board and replaying
      it would pin the client to the firmware's actual bytes rather than to a
      second implementation of what they should be.

## 5. Repository

- [ ] **A `py.typed` that means something.** It ships, and nothing type-checks
      the package. Adding mypy or pyright to `scripts/lint.sh` would make the
      annotations a claim rather than decoration.
- [ ] **`cli.py` is at 70% coverage**, the lowest of anything that matters. The
      command bodies are mostly untested; the logic inside them is not trivial
      any more now that baselines and `--json` run through them.
- [ ] **`scaffold.py` is at 74%.** `pinside init` drafts the config everything
      else is built on, and it went a whole release completely broken. The
      grouping heuristics deserve tests of their own.
