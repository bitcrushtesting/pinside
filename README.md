# Pinside

Read a KiCad board and answer three questions: **can a bed-of-nails fixture be built against
it?**, **what does that fixture board look like?**, and **what firmware does it run?**

A pogo-pin fixture is three lists taken from the device under test — where the test pads are,
where it can be bolted down, and how big it is. All three are already in the `.kicad_pcb`, so
copying them by hand is how a fixture ends up 0.3 mm out with nobody able to say why. Pinside
reads them instead, and then checks the things that only turn up after the fixture comes back
from the fab.

The board file is **only ever read**. Pinside never writes KiCad sources.

## Install

```bash
pip install .
```

No dependencies; Python 3.10+. It also runs straight from a checkout:

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
pinside project fixture.json --out fixture-board/   # a KiCad project for the fixture
pinside generate fixture.json --out firmware/       # firmware that matches the board
```

`pinside board.kicad_pcb` with no subcommand still means `check`.

Exit status is `0` clean, `1` warnings under `--strict`, `2` errors, `3` bad usage — so it drops
into CI as a gate on the board, not just as a report.

### Coordinates

KiCad board space has +X right, +Y **down**, origin at the page corner. `--origin outline`
(the default) shifts everything so the outline's top-left corner is `(0, 0)`, which is what you
want when the fixture is drawn as its own board. `--mirror x` additionally flips X: that is the
transform for a DUT laid face-down onto upward-pointing probes. Getting it wrong yields a
perfect mirror image of the fixture you need, which is not obvious until the pins miss.

Every row carries both frames — `dut_x/dut_y` as the board stores them, `fix_x/fix_y` after the
transform — so you can always check one against the other.

### Tuning the limits

The checks are measured against real hardware, and the defaults describe a Mill-Max 0985
receptacle. Change them when your probes differ:

```bash
pinside board.kicad_pcb --probe-pitch 1.9 --edge-clearance 1.5 --min-pad 0.7
```

`--ignore PS041,PS042` silences findings you have already decided about.

## What it checks

| Code | Severity | What it means |
|---|---|---|
| PS001 | error | No `Edge.Cuts` outline: the board size is unknown |
| PS002 | error | The outline does not close into one ring — unfillable, unmillable |
| PS010 | error | Test points sit outside the outline, so they were never placed |
| PS011 | error | Mounting holes sit outside the outline |
| PS012 | warning | The probes lie on a uniform lattice — KiCad's import spread, not a layout |
| PS020 | error | Two test points share a position |
| PS021 | error | Two probes are closer than the receptacle pitch; the bodies collide |
| PS022 | warning | A probe is up against the board edge, where the fixture wall lives |
| PS023 | warning | A probe crowds a mounting hole, where the standoff lives |
| PS024 | error | A probe lands inside another footprint — it would strike the component |
| PS025 | warning | A test pad is too small for a spring tip plus placement tolerance |
| PS026 | warning | Test points on both sides; one plate cannot reach them all |
| PS030 | error | No ground test point — there is no return path to measure against |
| PS031 | warning | Only one ground probe |
| PS032 | info | Plated mounting holes carry no net; grounding them is a free return path |
| PS040 | warning | A test point has no net — it probes nothing |
| PS041 | info | A test point is on a KiCad auto-named net; label it in the schematic |
| PS042 | info | Two probes on one signal net — a wasted fixture channel |
| PS050 | warning | Fewer mounting holes than are needed to locate the board |
| PS051 | info | Mounting holes have differing drill sizes |
| PS052 | info | Probes lie outside the mounting-hole span, where the plate cantilevers |

PS010 and PS012 are the two that matter most in practice: both mean *the layout is not finished*,
and any fixture cut from those coordinates is scrap.

## The fixture board

`pinside project` writes a KiCad project for the fixture itself. The part it generates is the
part that has to be exact:

- **Every probe at its DUT test point's own coordinates**, carried through the fixture transform.
- **The outline and mounting holes**, taken from the DUT, so the two boards bolt together.
- **A pogo receptacle footprint**, generated to the chosen probe's dimensions, with the project's
  `fp-lib-table` already pointing at it.

Routing is not generated. A ratsnest and an accurate drill plan are the useful part; guessing
trace paths is not, and an autorouter or a person does it better.

```bash
pinside project fixture.json --out fixture-board/
```

It refuses to lay out a fixture against a DUT that has not been laid out itself — unplaced test
points would put every hole in the wrong place, and nothing about the output would look wrong
until the boards came back:

```
pinside: 3 error(s); no project was written
pinside: error: PS010 30 of 30 test points sit outside the board outline ...
```

### The default board

The fixture is built around a **Raspberry Pi Pico 2** unless told otherwise. Soldering a module
onto a carrier costs one part and no support circuitry — no crystal, no flash, no USB connector,
no regulator — and it unplugs when a probe shorts something.

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

Naming a board is enough — the chip follows from it. A fixture needing more than 26 channels has
to carry the chip itself, which is what `bare` is for.

### The default probe

`millmax_0985`: a Mill-Max 0985 receptacle with an 0900 spring pin, on a 2.54 mm pitch. The
receptacle is what makes a fixture maintainable — a worn pin pulls out and a new one goes in.

| Probe | Hole | Pad | Minimum pitch |
|---|---|---|---|
| `millmax_0985` (default) | 1.37 mm | 2.29 mm | 2.54 mm |
| `millmax_0906` | 1.02 mm | 1.70 mm | 1.91 mm |
| `soldered_1mm` | 1.02 mm | 1.60 mm | 2.00 mm |

The probe sets the spacing limit, so choosing a finer one relaxes the `PS021` check without a
second edit.

Dimensions are what pinside builds to — check them against your supplier's drawing before
ordering.

### Mirroring

`fixture.mirror` defaults to `x`, because a bed-of-nails takes the DUT **face-down** onto
upward-pointing pins. Get it wrong and the board is a perfect mirror image of the one you need.
Before ordering, print `pinside check <dut> -f svg` at 1:1 and lay the real board on it.

### What KiCad does next

Open the project and run **Update PCB from Schematic**. The resistors and the controller arrive
from KiCad's own libraries with correct pads and nets, and the probes stay exactly where pinside
put them — KiCad matches footprints by UUID, and pinside derives those from the config rather
than randomising them, so regenerating an unchanged config produces a byte-identical project.

`pinside project` needs KiCad installed, because a schematic embeds a copy of every symbol it
places and the only honest source for those is a KiCad library. `check` and `generate` do not.

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

**Guards.** A bus the DUT also masters — an Ethernet SPI, a shared panel bus — is declared with
`"guard": "esp_en"`. The firmware then refuses to drive it until that channel is asserted:

```
-> {"jsonrpc":"2.0","id":3,"method":"spi.transfer","params":{"channel":"eth_spi","hex":"0f00"}}
<- {"jsonrpc":"2.0","id":3,"error":{"code":-32003,"message":"guard not asserted","data":"esp_en"}}
```

It is an interlock, not a substitute for series resistors — but it stops the ordinary mistake,
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

### The config hash

`fixture.info` reports a digest of the channel map. Compare it against the config before trusting
a run — a test rig one revision behind is the most common way a bench lies to you. Rewording a
description does not change it; moving a pin does.

### Config findings

| Code | What it means |
|---|---|
| PF001 | Unknown target microcontroller |
| PF002 | The DUT board was not read, so probe names went unchecked |
| PF010, PF011 | A channel name C cannot use, or two channels with one name |
| PF020 | A pin the target does not have |
| PF021 | A pin that cannot carry the role asked of it, with the pins that can |
| PF022 | An ADC channel on a pin with no converter |
| PF023 | One pin claimed by two channels |
| PF030-PF038 | An unrecognised role, guard, parity, direction, pull, or SPI mode |
| PF005-PF007 | An unknown board or probe, or a chip the board does not carry |
| PF008 | An unrecognised `fixture.mirror` |
| PF024 | A pin the carrier board does not bring out |
| PF025 | The board has almost no GPIO left |
| PF040 | A probe naming a signal the board does not have |
| PF041 | A test point with no channel |
| PF042 | A divider that would present more than the ADC reference |

`pinside project` additionally reports the board findings (`PS...`) and refuses on any error.

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
install. One test goes further and compiles the generated firmware, then runs *its* tests — the
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
    cli.py          check | init | generate | project
    kicad/          the KiCad project emitter
    firmware/       the emitter, and the C templates it emits
```

[CONTRIBUTING.md](CONTRIBUTING.md) has the rules that are not obvious from the code, and
[CHANGELOG.md](CHANGELOG.md) records what changed.

## Why not the KiCad Python API

It only exists inside a KiCad installation, which rules out CI and any machine that just wants to
look at a board file. KiCad's S-expression format is stable enough to read directly, so pinside
parses it and stays a dependency-free script. Writing is a different matter — text edits break
the UUID cross-references KiCad relies on — which is why pinside only ever reads.
