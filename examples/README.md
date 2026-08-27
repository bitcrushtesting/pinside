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
pinside generate examples/demo-fixture.json --out /tmp/demo-firmware
/tmp/demo-firmware/test/run.sh                       # host tests, no board needed
```

The generated `README.md` carries the channel map as a table, and `openrpc.json`
is the contract an agent reads.
