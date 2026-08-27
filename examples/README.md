# Examples

A worked example, small enough to read and complete enough to run.

`demo-board.kicad_pcb` is a synthetic 50 × 40 mm board with one of each kind of
bus, two control lines, a rail to monitor, two ground probes and four mounting
holes. It is deliberately clean: every check passes, which makes it the baseline
to compare a real board's findings against.

```bash
pinside check examples/demo-board.kicad_pcb          # a board with nothing wrong with it
pinside check examples/demo-board.kicad_pcb -f svg > plan.svg
```

`demo-fixture.json` is the config `pinside init` drafts from that board, with one
edit: the SPI bus is given `"guard": "dut_reset"`, because on a real board that
bus belongs to the DUT's own controller and the fixture may only master it while
the DUT is held in reset. That edit is the part a person has to make — the
grouping and the pin assignment come out of `init` already correct.

```bash
pinside init examples/demo-board.kicad_pcb           # the draft, before that edit
pinside project examples/demo-fixture.json --out /tmp/demo-board
pinside generate examples/demo-fixture.json --out /tmp/demo-firmware
/tmp/demo-firmware/test/run.sh                       # host tests, no board needed
```

`pinside project` writes the fixture's own KiCad project: 14 probes, each at its DUT test point's
mirrored coordinates, plus the outline and the four mounting holes. Open it and run **Update PCB
from Schematic** to bring in the resistors and the Pico 2; the probes stay where they are.

The config targets a **Raspberry Pi Pico 2**, which is the default. Its 26 header GPIO are ample
for these 14 channels — the Cuarto 500's 34 need a bare RP2350B, which is what `"board": "bare"`
is for.

The generated `README.md` carries the channel map as a table, and `openrpc.json`
is the contract an agent reads.
