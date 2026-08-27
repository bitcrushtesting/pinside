# Pinside

Read a KiCad board and answer one question: **can a bed-of-nails fixture be built against it?**

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
PYTHONPATH=src python3 -m pinside board.kicad_pcb
```

## Use

```bash
pinside board.kicad_pcb                       # table of probes and holes, plus findings
pinside board.kicad_pcb -f csv > probes.csv   # placement data for a spreadsheet or script
pinside board.kicad_pcb -f json               # everything, findings included
pinside board.kicad_pcb -f svg > plan.svg     # 1:1 drill plan; print it and lay it on the board
pinside board.kicad_pcb --mirror x            # the frame for a DUT laid face-down on the probes
```

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

## Tests

```bash
python -m unittest discover -s tests -v
```

The suite builds its own synthetic boards, so it depends on no real project and no KiCad install.

## Why not the KiCad Python API

It only exists inside a KiCad installation, which rules out CI and any machine that just wants to
look at a board file. KiCad's S-expression format is stable enough to read directly, so pinside
parses it and stays a dependency-free script. Writing is a different matter — text edits break
the UUID cross-references KiCad relies on — which is why pinside only ever reads.
