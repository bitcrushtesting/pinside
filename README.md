# Pinside

[![CI](https://github.com/bitcrushtesting/pinside/actions/workflows/ci.yml/badge.svg)](https://github.com/bitcrushtesting/pinside/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/pinside)](https://pypi.org/project/pinside/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Dependencies: none](https://img.shields.io/badge/dependencies-none-brightgreen)](pyproject.toml)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

Read a KiCad board and answer three questions: **can a bed-of-nails fixture be built against
it?**, **what does that fixture board look like?**, and **what firmware does it run?**

A pogo-pin fixture is three lists taken from the device under test: where the test pads are,
where it can be bolted down, and how big it is. All three are already in the `.kicad_pcb`, so
copying them by hand is how a fixture ends up 0.3 mm out with nobody able to say why. Pinside
reads them instead, and then checks the things that only turn up after the fixture comes back
from the fab.

The board file is **only ever read**. Pinside never writes KiCad sources.

## Install

```bash
pip install pinside
```

No dependencies; Python 3.10+. Add `pinside[client]` for the one command that needs pyserial:

```bash
pip install 'pinside[client]'     # ... if you want `pinside probe` as well
```

It also runs straight from a checkout, with nothing installed:

```bash
PYTHONPATH=src python3 -m pinside check board.kicad_pcb
```

## Try it

[`examples/`](examples/) has a small board that passes every check, and the
fixture config drafted from it:

```bash
pinside check examples/demo-board.kicad_pcb           # a board with nothing wrong with it
pinside project examples/demo-fixture.json --out /tmp/demo-board
pinside generate examples/demo-fixture.json --out /tmp/demo-firmware
/tmp/demo-firmware/test/run.sh                        # host tests, no board needed
```

## Use

```bash
pinside check board.kicad_pcb                       # probes, holes and findings
pinside check board.kicad_pcb -f csv > probes.csv   # placement data for a spreadsheet or script
pinside check board.kicad_pcb -f json               # everything, findings included
pinside check board.kicad_pcb -f svg > plan.svg     # 1:1 drill plan; print and lay it on the board
pinside check board.kicad_pcb --mirror x            # the frame for a face-down DUT

pinside init board.kicad_pcb -o fixture.json        # draft a config covering every test point
pinside init board.kicad_pcb --carrier bare        # ... for a fixture carrying the chip itself
pinside project fixture.json --out fixture-board/   # a KiCad project for the fixture
pinside generate fixture.json --out firmware/       # firmware that matches the board

pinside probe fixture.json                          # talk to a flashed fixture and check it
```

`pinside board.kicad_pcb` with no subcommand still means `check`.

`probe` is the only command that needs anything installed beyond the standard library:
`pip install 'pinside[client]'` for pyserial.

Exit status is `0` clean, `1` warnings under `--strict`, `2` errors, `3` bad usage, so it drops
into CI as a gate on the board, not just as a report.

### Coordinates

KiCad board space has +X right, +Y **down**, origin at the page corner. `--origin outline`
(the default) shifts everything so the outline's top-left corner is `(0, 0)`, which is what you
want when the fixture is drawn as its own board. `--mirror x` additionally flips X: that is the
transform for a DUT laid face-down onto upward-pointing probes. Getting it wrong yields a
perfect mirror image of the fixture you need, which is not obvious until the pins miss.

Every row carries both frames: `dut_x/dut_y` as the board stores them, and `fix_x/fix_y` after
the transform, so you can always check one against the other.

### Tuning the limits

The checks are measured against real hardware, and the defaults describe a Mill-Max 0985
receptacle. Change them when your probes differ:

```bash
pinside board.kicad_pcb --probe-pitch 1.9 --probe-body 1.27 --edge-clearance 1.5 --min-pad 0.7
```

`--ignore PS041,PS042` silences findings you have already decided about.

### Baselines

`--ignore` is per-invocation and global: it silences a code on every board, forever. That is the
wrong shape for the usual situation, which is a board with two findings somebody has looked at
and accepted and one that has not happened yet.

```bash
pinside check board.kicad_pcb --write-baseline pinside-baseline.json
pinside check board.kicad_pcb --baseline pinside-baseline.json   # exit 0 for the accepted ones
```

A baseline records each accepted finding by code **and by reference**. Accepting `PS041 on TP5`
says nothing about `PS041 on TP9`, so a new occurrence still fails while the old one stays quiet.
That is what makes the file safe to commit: it cannot absorb a finding nobody has seen. Every
entry is written with an empty `note`; fill them in before committing, because a suppression
whose reason nobody wrote down is indistinguishable from a mistake six months later.

`generate` and `project` take `--baseline` too, and both accept `--json` to put their findings on
stdout as a machine-readable object rather than prose on stderr:

```bash
pinside generate fixture.json --out firmware/ --json | jq '.errors'
```

## What it checks

| Code | Severity | What it means |
|---|---|---|
| PS001 | error | No `Edge.Cuts` outline: the board size is unknown |
| PS002 | error | Edge.Cuts segments that close into nothing: unfillable, unmillable |
| PS003 | info | The outline has internal cutouts; they are holes, not a broken edge |
| PS004 | warning | Closed Edge.Cuts shapes outside the board: a panel, or a leftover |
| PS010 | error | Test points sit outside the outline, so they were never placed |
| PS011 | error | Mounting holes sit outside the outline |
| PS012 | warning | The probes lie on a uniform lattice: KiCad's import spread, not a layout |
| PS013 | error | A test point sits over a cutout, where there is no board |
| PS014 | error | A mounting hole sits over a cutout, so there is nothing to bolt to |
| PS020 | error | Two test points share a position |
| PS021 | error | Two probes are closer than the receptacle pitch; the bodies collide |
| PS022 | warning | A probe is up against the board edge, where the fixture wall lives |
| PS023 | warning | A probe crowds a mounting hole, where the standoff lives |
| PS024 | error | A probe lands inside another footprint, so it would strike the component |
| PS025 | warning | A test pad is too small for a spring tip plus placement tolerance |
| PS026 | warning | Test points on both sides; one plate cannot reach them all |
| PS027 | warning | The tip clears a component but the receptacle body does not |
| PS030 | error | No ground test point: there is no return path to measure against |
| PS031 | warning | Only one ground probe |
| PS032 | info | Plated mounting holes carry no net; grounding them is a free return path |
| PS033 | warning | A supply rail on the board has no test point, so nothing proves it came up |
| PS034 | info | A reset or strap line has no test point: readable, but not resettable |
| PS040 | warning | A test point has no net, so it probes nothing |
| PS041 | info | A test point is on a KiCad auto-named net; label it in the schematic |
| PS042 | info | Two probes on one signal net: a wasted fixture channel |
| PS043 | error | One net number with several names: KiCad will merge them and drop the rest |
| PS044 | error | A named net on number 0, KiCad's no-connection net, so it probes nothing |
| PS050 | warning | Fewer mounting holes than are needed to locate the board |
| PS051 | info | Mounting holes have differing drill sizes |
| PS052 | info | Probes lie outside the mounting-hole span, where the plate cantilevers |

PS010 and PS012 are the two that matter most in practice: both mean *the layout is not finished*,
and any fixture cut from those coordinates is scrap.

`PS043` and `PS044` are about net *numbering* rather than geometry. Through KiCad 9 a net is
identified by its ordinal and the name beside it is only a label, so two names on one number are
one net as far as KiCad is concerned: it keeps the first and drops the rest to no-net on the next
save. The file stays well-formed and nothing warns anyone. pinside's own example board had
exactly this, and pinside called it clean for two releases. Both checks are verified against
`kicad-cli pcb export ipcd356`, which is KiCad's own answer to what a board's netlist is.

## The fixture board

`pinside project` writes a KiCad project for the fixture itself. The part it generates is the
part that has to be exact:

- **Every probe at its DUT test point's own coordinates**, carried through the fixture transform.
- **The outline and mounting holes**, taken from the DUT, so the two boards bolt together.
- **A pogo receptacle footprint**, generated to the chosen probe's dimensions, with the project's
  `fp-lib-table` already pointing at it.
- **A GND pour** on both copper layers, held back from the board edge. On a fixture that pour is
  most of the return path, and unlike a trace it is one fixed shape rather than a guess.

Routing is not generated. A ratsnest and an accurate drill plan are the useful part; guessing
trace paths is not, and an autorouter or a person does it better.

The generated `README.md` turns the plate force into hardware: how many newtons the probes add
up to, whether that needs a clamp or a thumbscrew, what each standoff carries, and how long the
standoffs have to be to leave the probes their travel.

```bash
pinside project fixture.json --out fixture-board/
```

It refuses to lay out a fixture against a DUT that has not been laid out itself. Unplaced test
points would put every hole in the wrong place, and nothing about the output would look wrong
until the boards came back:

```
pinside: 3 error(s); no project was written
pinside: error: PS010 30 of 30 test points sit outside the board outline ...
```

### The default board

The fixture is built around a **Raspberry Pi Pico 2** unless told otherwise. Soldering a module
onto a carrier costs one part and no support circuitry (no crystal, no flash, no USB connector,
no regulator), and it unplugs when a probe shorts something.

A module exposes only some of its chip's pins, and pinside checks against the board rather than
the chip:

```
error: PF024 1 pins are not brought out on the pico2 [GPIO25 (led)] -- GPIO23/24/25/29 are
       consumed by the module ... or set target.board to "bare" and put the rp2350a on the
       fixture itself.
```

| Board | Chip | GPIO on the header |
|---|---|---|
| `pico2` (default) | RP2350A | 26 |
| `pico2w` | RP2350A + wireless | 26 |
| `bare` | whatever `target.mcu` says | all of them |

Naming a board is enough; the chip follows from it. A fixture needing more than 26 channels has
to carry the chip itself, which is what `bare` is for.

### The targets

`target.mcu` is what `bare` needs, and what a carrier board resolves to. `targets.py` holds pin
capability rather than a datasheet: how many GPIO the part has, which of them reach an ADC, and
which peripheral function each one can carry.

| `target.mcu` | Package | GPIO | ADC inputs |
|---|---|---|---|
| `rp2350b` (the `init` default) | QFN-80 | 48 | 8, on GPIO40-47 |
| `rp2350a` | QFN-60 | 30 | 4, on GPIO26-29 |
| `rp2354b` | QFN-80, 2 MB stacked flash | 48 | 8, on GPIO40-47 |
| `rp2354a` | QFN-60, 2 MB stacked flash | 30 | 4, on GPIO26-29 |

The stacked-flash parts are the plain ones with flash in the package, so they share a pin map;
KiCad's own library says the same thing, and the tests check pinside's map against it.

All four are the RP2350 family, so one HAL covers them. Adding a part from another vendor means
a second HAL beside `fixture_hal_rp2350.c`. That contract is 17 functions in 56 lines and
[CONTRIBUTING.md](CONTRIBUTING.md#a-target-from-another-vendor) scopes what implementing it
involves.

### The default probe

`millmax_0985`: a Mill-Max 0985 receptacle with an 0900 spring pin, on a 2.54 mm pitch. The
receptacle is what makes a fixture maintainable: a worn pin pulls out and a new one goes in.

| Probe | Hole | Pad | Minimum pitch |
|---|---|---|---|
| `millmax_0985` (default) | 1.37 mm | 2.29 mm | 2.54 mm |
| `millmax_0906` | 1.02 mm | 1.70 mm | 1.91 mm |
| `soldered_1mm` | 1.02 mm | 1.60 mm | 2.00 mm |

The probe sets the spacing limit, so choosing a finer one relaxes the `PS021` check without a
second edit.

Dimensions are what pinside builds to, so check them against your supplier's drawing before
ordering. Each entry records where its numbers came from and whether anyone has done that check;
none has yet, and the generated fixture README says so where somebody about to order a board
will see it.

### Mirroring

`fixture.mirror` defaults to `x`, because a bed-of-nails takes the DUT **face-down** onto
upward-pointing pins. Get it wrong and the board is a perfect mirror image of the one you need.
Before ordering, print `pinside check <dut> -f svg` at 1:1 and lay the real board on it.

### What KiCad does next

Open the project and run **Update PCB from Schematic**. The resistors and the controller arrive
from KiCad's own libraries with correct pads and nets, and the probes stay exactly where pinside
put them: KiCad matches footprints by UUID, and pinside derives those from the config rather
than randomising them, so regenerating an unchanged config produces a byte-identical project.

`pinside project` needs KiCad installed, because a schematic embeds a copy of every symbol it
places and the only honest source for those is a KiCad library. `check` and `generate` do not.

### KiCad versions

| | Format |
|---|---|
| Board files `check` and `generate` read | tested against `20241229` (KiCad 8/9) and the KiCad 10 net record |
| Files `project` writes | `.kicad_pcb` `20260206`, `.kicad_sch` `20260306` (KiCad 10) |
| `project` output opened and checked in | KiCad 10.0.3 |

The reader is deliberately tolerant: it walks the S-expressions looking for the tags it cares
about and ignores everything else, so a file from a version it has never seen still reads, and a
newer version that adds tags does not break it. The one format change it handles explicitly is
the net record, which was `(net <ordinal> "NAME")` through KiCad 9 and is `(net "NAME")` in 10.
Both forms are covered by tests.

The writer emits one version and does not negotiate. Opening a generated project in an older
KiCad will not work.

## Firmware

A fixture is only half a tool: something has to drive those probes and hand what it sees to
whoever is running the test. `pinside generate` writes that firmware from a JSON config, and
refuses to write anything until the config agrees with two separate realities.

**The microcontroller.** Pin roles are checked against the target's function map, so a bus that
cannot work is a message rather than a silent dead line:

```
error: PF021 GPIO9 cannot be i2c0 sda [uext.sda] -- on rp2350b it is i2c0 scl;
       i2c0 sda is available on GPIO0, GPIO4, GPIO8, GPIO12, ...
```

**The board.** Every `probe` in the config must name a real test point, and by default every test
point must reach a channel. A config that has drifted from the board fails before it becomes
firmware that lies about what it can reach.

### The config

```json
{
  "name": "cuarto500-fixture",
  "target": { "mcu": "rp2350b", "clock_hz": 150000000 },
  "dut": { "board": "../board.kicad_pcb", "require_all_test_points": true },

  "uart": [{ "name": "dut_uart", "peripheral": 0, "baud": 115200, "stream": true,
             "pins":   { "tx": 0,        "rx": 1,        "cts": 2,       "rts": 3 },
             "probes": { "tx": "DUT_RXD", "rx": "DUT_TXD", "cts": "DUT_RTS", "rts": "DUT_CTS" } }],

  "spi":  [{ "name": "eth_spi", "peripheral": 1, "hz": 8000000, "guard": "esp_en",
             "pins":   { "miso": 12, "cs": 13, "sclk": 14, "mosi": 15 },
             "probes": { "miso": "ETH_MISO", "cs": "ETH_CS",
                         "sclk": "ETH_CLK", "mosi": "ETH_MOSI" } }],

  "gpio": [{ "name": "esp_en", "pin": 24, "probe": "ESP_EN",
             "direction": "open_drain", "active_low": true, "initial": "released" }],

  "adc":  [{ "name": "dut_3v3", "pin": 40, "probe": "+3.3V",
             "divider": 2.0, "nominal_v": 3.3, "tolerance_v": 0.15 }]
}
```

Three things in there are worth knowing about.

**Directions invert.** A net called `DUT_TXD` is an output *of the DUT*, so it lands on the
fixture's **rx**. Wire it to `tx` because both are called TXD and nothing works, with no error to
explain why. `pinside init` gets this right when it drafts a config.

**Guards.** A bus the DUT also masters (an Ethernet SPI, a shared panel bus) is declared with
`"guard": "esp_en"`. The firmware then refuses to drive it until that channel is asserted:

```
-> {"jsonrpc":"2.0","id":3,"method":"spi.transfer","params":{"channel":"eth_spi","hex":"0f00"}}
<- {"jsonrpc":"2.0","id":3,"error":{"code":-32003,"message":"guard not asserted","data":"esp_en"}}
```

It is an interlock, not a substitute for series resistors, but it stops the ordinary mistake,
which is probing a bus while the DUT's own controller is mid-transaction.

**Open drain means released, not driven high.** An `open_drain` channel only ever pulls towards
its asserted rail; releasing it goes high-Z. That is what keeps a fixture off a reset line the
DUT already pulls up.

### What comes out

```
CMakeLists.txt              Pico SDK project
include/fixture_config.h    channel tables, counts, identifiers
include/fixture_core.h      the protocol layer
include/fixture_hal.h       what the core needs from hardware
src/fixture_config.c        generated: every channel, its pin, its DUT signal
src/fixture_core.c          JSON-RPC parse, dispatch, format, notifications
src/fixture_hal_rp2350.c    the only file that knows about the SDK
src/main.c
test/{run.sh,test_core.c,mock_hal.c}   host tests, no board required
openrpc.json                the agent-facing contract
README.md                   the channel map, as a table
```

Only `fixture_config.c` and a block of test assertions are really generated; the rest is copied
from templates that can be read and compiled on their own. That keeps the generated surface small
enough to review.

### Talking to the fixture

JSON-RPC 2.0 over USB CDC, one request per line. `fixture.info` and `fixture.channels` together
tell an agent which DUT signal every channel lands on, so it needs no other map:

```
<- {"jsonrpc":"2.0","method":"fixture.ready","params":{"fixture":"cuarto500-fixture",
                                                       "config_hash":"a5dce260c05e"}}
-> {"jsonrpc":"2.0","id":2,"method":"adc.snapshot"}
<- {"jsonrpc":"2.0","id":2,"result":[{"channel":"dut_3v3","probe":"+3.3V",
                                      "millivolts":3298,"raw":2047,"in_range":true}]}
```

Anything on a bus marked `"stream": true` is pushed without being asked, so a log line reaches the
agent as the DUT emits it:

```
<- {"jsonrpc":"2.0","method":"uart.data","params":{"channel":"dut_uart",
                                                   "hex":"626f6f74206f6b0d0a","t_ms":4210}}
```

Methods: `fixture.info`, `fixture.channels`, `gpio.read`, `gpio.write`, `gpio.snapshot`,
`adc.read`, `adc.snapshot`, `uart.write`, `uart.read`, `uart.configure`, `i2c.scan`, `i2c.write`,
`i2c.read`, `spi.transfer`. Full schemas in the generated `openrpc.json`.

That contract is checked against the firmware rather than merely emitted: the test suite reads
the dispatch table out of `fixture_core.c` and fails if the two disagree in either direction, so
a method the document promises and the firmware does not answer cannot ship.

### The config hash

`fixture.info` reports a digest of the channel map. Compare it against the config before trusting
a run: a test rig one revision behind is the most common way a bench lies to you. Rewording a
description does not change it; moving a pin does.

You do not have to compare it by eye. `pinside.client` does it on connect, and refuses.

## Talking to a fixture from the host

```bash
pip install 'pinside[client]'                 # pyserial; the core stays dependency-free
pinside probe fixture.json                    # is this the right fixture, and is it wired up
pinside probe fixture.json --port /dev/ttyACM0 --json
```

`pinside probe` opens the port, checks the firmware's config hash against the config you handed
it, prints every channel with the DUT signal it lands on, and exits non-zero if any monitored
rail is out of range. It is the answer to "is the thing on my bench the thing I think it is".

The same client is a library:

```python
from pinside.client import connect
from pinside.config import load

config = load("fixture.json")
with connect("/dev/ttyACM0", config) as fixture:  # raises if the hash disagrees
    fixture.gpio_write("dut_reset", True)
    print(fixture.adc_snapshot())
    for message in fixture.poll(seconds=2):
        print(message.channel, message.data)
```

Two things it does that a hand-rolled client usually does not:

**It refuses a fixture that does not match.** `connect()` compares `fixture.info`'s hash against
the config before returning, and raises `ConfigMismatchError` rather than warning. Pass
`check_hash=False` if you mean to, and say why.

**It keeps notifications out of the response stream.** A streaming UART pushes `uart.data`
between replies. Reading one line per request means eventually returning a log line as the answer
to an ADC read; every line is classified before anything waits on it, and pushed messages go to
`fixture.poll()`.

### Config findings

| Code | What it means |
|---|---|
| PF001 | Unknown target microcontroller |
| PF002 | The DUT board was not read, so probe names went unchecked |
| PF003 | The output directory is not empty and pinside did not write it |
| PF004 | A template placeholder survived into a generated file |
| PF005, PF006, PF007 | An unknown board or probe, or a chip the board does not carry |
| PF008 | An unrecognised `fixture.mirror` |
| PF010, PF011 | A channel name C cannot use, or two channels with one name |
| PF020 | A pin the target does not have |
| PF021 | A pin that cannot carry the role asked of it, with the pins that can |
| PF022 | An ADC channel on a pin with no converter |
| PF023 | One pin claimed by two channels |
| PF024 | A pin the carrier board does not bring out |
| PF025 | The board has almost no GPIO left |
| PF030-PF038 | An unrecognised role, guard, parity, direction, pull, or SPI mode |
| PF040 | A probe naming a signal the board does not have |
| PF041 | A test point with no channel |
| PF042 | A divider that would present more than the ADC reference |

`pinside project` additionally reports the board findings (`PS...`) and refuses on any error. It
has three of its own, for the things that stop a project being written at all:

| Code | What it means |
|---|---|
| PK001 | No DUT board, so there are no coordinates to lay a fixture out from |
| PK002 | The output directory is not empty and pinside did not write it |
| PK003 | KiCad's symbol libraries were not found, so a schematic cannot be built |

## As a library

```python
from pinside import read_board, transform, run

board = transform(read_board("board.kicad_pcb"), mirror="x")
for probe in board.test_points:
    print(probe.ref, probe.signal, probe.fx, probe.fy)

for finding in run(board):
    print(finding.code, finding.severity, finding.summary)
```

`Limits` carries the physical numbers; pass your own to `run(board, Limits(probe_pitch=1.9))`.

## Development

```bash
scripts/test.sh            # the test suite
scripts/lint.sh            # ruff, plus clang-format on the firmware templates
scripts/lint.sh --fix      # apply what can be applied
```

There is no setup step: `scripts/lint.sh` installs the ruff version pinned in `pyproject.toml`
into `.venv-tools/`, so CI and your machine run the same one.

The test suite builds its own synthetic boards, so it depends on no real project and no KiCad
install. One test goes further and compiles the generated firmware, then runs *its* tests, the
only check that the C templates and the generated tables actually agree. It skips if there is no
compiler. Another generates a KiCad project and runs KiCad's own ERC and DRC over it; it skips
without KiCad, so **that path is verified locally rather than in CI**.

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
    client.py       talking to a flashed fixture over USB CDC
    cli.py          check | init | generate | project | probe
    kicad/          the KiCad project emitter
    firmware/       the emitter, and the C templates it emits
```

[CONTRIBUTING.md](CONTRIBUTING.md) has the rules that are not obvious from the code,
[CHANGELOG.md](CHANGELOG.md) records what changed, and [TASKLIST.md](TASKLIST.md) is what is
planned next.

## Why not the KiCad Python API

It only exists inside a KiCad installation, which rules out CI and any machine that just wants to
look at a board file. KiCad's S-expression format is stable enough to read directly, so pinside
parses it and stays a dependency-free script. Writing is a different matter: text edits break
the UUID cross-references KiCad relies on, which is why pinside only ever reads.
