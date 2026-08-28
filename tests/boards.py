"""Synthetic .kicad_pcb files, so the tests do not depend on any real project."""

from __future__ import annotations

import atexit
import itertools
import re
import shutil
import tempfile
from pathlib import Path

# One directory for every board a test run writes. Keeping them together means a failing test
# leaves something inspectable behind, and one atexit hook cleans the lot up.
_SCRATCH = Path(tempfile.mkdtemp(prefix="pinside-tests-"))
_SERIAL = itertools.count()
atexit.register(shutil.rmtree, _SCRATCH, True)


def write(text: str, suffix: str = ".kicad_pcb") -> str:
    """Put text in a temporary file and return its path."""
    path = _SCRATCH / f"case{next(_SERIAL)}{suffix}"
    path.write_text(text, encoding="utf-8")
    return str(path)


def _testpoint(
    ref: str,
    x: float,
    y: float,
    net: str,
    *,
    pad: float = 1.0,
    layer: str = "F.Cu",
    value: str = "TestPoint",
) -> str:
    return f'''
  (footprint "TestPoint:TestPoint_Pad_D1.0mm"
    (layer "{layer}")
    (at {x} {y})
    (property "Reference" "{ref}")
    (property "Value" "{value}")
    (pad "1" smd circle (at 0 0) (size {pad} {pad}) (layers "{layer}" "F.Mask") (net 1 "{net}"))
  )'''


def _hole(ref: str, x: float, y: float, drill: float = 3.5, net: str = "") -> str:
    net_expr = f'(net 2 "{net}")' if net else ""
    return f'''
  (footprint "MountingHole:MountingHole_3.5mm_Pad"
    (layer "F.Cu")
    (at {x} {y})
    (property "Reference" "{ref}")
    (property "Value" "3.5mm")
    (pad "1" thru_hole circle (at 0 0) (size 7 7) (drill {drill})
         (layers "*.Cu" "*.Mask") {net_expr})
  )'''


def _part(ref: str, x: float, y: float, w: float = 4.0, h: float = 2.0) -> str:
    return f'''
  (footprint "Package_SO:SOIC-8"
    (layer "F.Cu")
    (at {x} {y})
    (property "Reference" "{ref}")
    (property "Value" "U")
    (pad "1" smd rect (at 0 0) (size {w} {h}) (layers "F.Cu") (net 3 "SIG"))
  )'''


def _part_with_nets(ref: str, x: float, y: float, nets: list[str]) -> str:
    """A component whose pads carry named nets, for the coverage checks."""
    pads = "\n".join(
        f'    (pad "{i}" smd rect (at {i * 1.5 - 3} 0) (size 0.6 1.2) '
        f'(layers "F.Cu") (net {i + 10} "{net}"))'
        for i, net in enumerate(nets, start=1)
    )
    return f'''
  (footprint "Package_SO:SOIC-8"
    (layer "F.Cu")
    (at {x} {y})
    (property "Reference" "{ref}")
    (property "Value" "U")
{pads}
  )'''


def _wrap(body: str) -> str:
    """Close a board, giving every net its own ordinal and declaring them all.

    The helpers above each write `(net 1 "NAME")`, because a helper cannot know what the rest of
    the board is using. Left that way every net in the board shares ordinal 1, which is a file
    KiCad reads as a single net and re-saves with fifteen test points connected to nothing --
    the defect PS043 exists to catch, and the one the shipped example board had. Numbering
    happens here because here is the only place that sees the whole board.
    """
    names = []
    for _, name in _NET_REF.findall(body):
        if name and name not in names:
            names.append(name)
    ordinals = {name: i for i, name in enumerate(names, start=1)}
    body = _NET_REF.sub(
        lambda m: f'(net {ordinals[m.group(2)]} "{m.group(2)}")' if m.group(2) else m.group(0),
        body,
    )
    table = "".join(f'\n  (net {ordinals[n]} "{n}")' for n in names)
    return f'(kicad_pcb (version 20241229) (generator "pinside-tests"){table}{body}\n)\n'


def without(text: str, *refs: str) -> str:
    """The same board with these footprints removed, by reference.

    Tests used to do this by rebuilding a helper's output and string-replacing it away, which
    only worked while the helper's text was byte-identical to what landed in the board. It is
    not: `_wrap` renumbers the nets, so the reconstructed footprint carries a different ordinal
    and the replace silently matches nothing, leaving the test asserting against a board it did
    not build.
    """
    wanted = {f'(property "Reference" "{ref}")' for ref in refs}
    out = []
    i = 0
    while True:
        start = text.find("\n  (footprint ", i)
        if start == -1:
            out.append(text[i:])
            return "".join(out)
        depth, j = 0, text.index("(", start)
        while True:
            if text[j] == "(":
                depth += 1
            elif text[j] == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        block = text[start : j + 1]
        out.append(text[i:start])
        if not any(marker in block for marker in wanted):
            out.append(block)
        i = j + 1


def sharing_one_net_ordinal(text: str) -> str:
    """Put the whole board back on net 1, the way a hand-written .kicad_pcb tends to be."""
    return _NET_REF.sub(lambda m: f'(net 1 "{m.group(2)}")' if m.group(2) else m.group(0), text)


def on_net_zero(text: str, name: str) -> str:
    """Move one named net onto ordinal 0, KiCad's no-connection net."""
    return re.sub(rf'\(net \d+ "{re.escape(name)}"\)', f'(net 0 "{name}")', text)


_NET_ORDINAL = re.compile(r'\(net \d+ ("[^"]*")\)')

# `(net <ordinal> "NAME")`, with both halves captured.
_NET_REF = re.compile(r'\(net (\d+) "([^"]*)"\)')


def as_kicad10(text: str) -> str:
    """Rewrite a board the way KiCad 10 writes it.

    KiCad 9 and earlier number the nets in the file, `(net 3 "GND")`; KiCad 10 dropped the
    ordinal. Any board helper here can be run through this to get the other form, so both
    branches of ``board._net_of`` are exercised by the same expectations.
    """
    text = text.replace("(version 20241229)", "(version 20260206)")
    return _NET_ORDINAL.sub(r"(net \1)", text)


def rect_outline(x1=0.0, y1=0.0, x2=50.0, y2=40.0, radius=0.0) -> str:
    r = f"(radius {radius})" if radius else ""
    return f"""
  (gr_rect (start {x1} {y1}) (end {x2} {y2}) {r} (layer "Edge.Cuts"))"""


def segment_outline(x1=0.0, y1=0.0, x2=50.0, y2=40.0, gap: bool = False) -> str:
    """Four lines. With gap=True the last one stops short, leaving the edge open."""
    last_x = x1 + 5 if gap else x1
    return f"""
  (gr_line (start {x1} {y1}) (end {x2} {y1}) (layer "Edge.Cuts"))
  (gr_line (start {x2} {y1}) (end {x2} {y2}) (layer "Edge.Cuts"))
  (gr_line (start {x2} {y2}) (end {x1} {y2}) (layer "Edge.Cuts"))
  (gr_line (start {x1} {y2}) (end {last_x} {y1}) (layer "Edge.Cuts"))"""


def slotted() -> str:
    """A perfectly good board with a slot milled through it.

    Two closed shapes on Edge.Cuts: the perimeter and the window. A single-ring walk calls this
    an unclosed outline, which is a hard error on a board a fab would cut without complaint.
    """
    body = rect_outline(0, 0, 50, 40)
    body += rect_outline(20, 15, 30, 25)  # the window
    body += _testpoint("TP1", 10, 10, "/SCL")
    body += _testpoint("TP2", 14, 10, "/SDA")
    body += _testpoint("TP90", 10, 20, "GND", value="GND")
    body += _testpoint("TP91", 14, 20, "GND", value="GND")
    for i, (x, y) in enumerate([(5, 5), (45, 5), (5, 35), (45, 35)], start=1):
        body += _hole(f"H{i}", x, y, net="GND")
    return _wrap(body)


def probe_over_a_slot() -> str:
    """The slotted board with TP2 moved into the middle of the window."""
    return slotted().replace("(at 14 10)", "(at 25 20)", 1)


def panelised() -> str:
    """Two separate board outlines on one Edge.Cuts layer."""
    body = rect_outline(0, 0, 50, 40)
    body += rect_outline(60, 0, 110, 40)
    body += _testpoint("TP1", 10, 10, "/SCL")
    body += _testpoint("TP90", 10, 20, "GND", value="GND")
    body += _testpoint("TP91", 14, 20, "GND", value="GND")
    for i, (x, y) in enumerate([(5, 5), (45, 5), (5, 35), (45, 35)], start=1):
        body += _hole(f"H{i}", x, y, net="GND")
    return _wrap(body)


def unreachable_rails() -> str:
    """Probes on the data lines and on nothing that powers or resets the board."""
    body = rect_outline()
    body += _testpoint("TP1", 10, 10, "/SCL")
    body += _testpoint("TP2", 14, 10, "/SDA")
    body += _testpoint("TP90", 10, 20, "GND", value="GND")
    body += _testpoint("TP91", 14, 20, "GND", value="GND")
    # The rails and the reset line exist on the board, on a component's pads, and nothing
    # probes them.
    body += _part_with_nets("U1", 30, 20, ["+3V3", "+1V8", "/MCU_NRST", "/SCL"])
    for i, (x, y) in enumerate([(5, 5), (45, 5), (5, 35), (45, 35)], start=1):
        body += _hole(f"H{i}", x, y, net="GND")
    return _wrap(body)


def healthy() -> str:
    """A board a fixture can actually be built against."""
    body = rect_outline()
    for i, (x, y, net) in enumerate(
        [(10, 10, "/SCL"), (14, 10, "/SDA"), (18, 10, "/TXD"), (22, 10, "/RXD")], start=1
    ):
        body += _testpoint(f"TP{i}", x, y, net)
    body += _testpoint("TP90", 10, 20, "GND", value="GND")
    body += _testpoint("TP91", 22, 20, "GND", value="GND")
    for i, (x, y) in enumerate([(5, 5), (45, 5), (5, 35), (45, 35)], start=1):
        body += _hole(f"H{i}", x, y, net="GND")
    return _wrap(body)


def unplaced() -> str:
    """Test points still on KiCad's import lattice, outside the outline."""
    body = rect_outline(100, 50, 150, 90)
    nets = ["/SCL", "/SDA", "/TXD", "/RXD", "/CS", "/CLK", "/MISO", "/MOSI"]
    for i, net in enumerate(nets, start=1):
        body += _testpoint(f"TP{i}", 60 + 3.05 * (i % 4), 40 + 3.05 * (i // 4), net)
    for i, (x, y) in enumerate([(105, 55), (145, 55), (105, 85), (145, 85)], start=1):
        body += _hole(f"H{i}", x, y)
    return _wrap(body)


def troubled() -> str:
    """Placed, but with most of the things that make a fixture fail."""
    body = rect_outline(0, 0, 50, 40)
    body += _testpoint("TP1", 10, 10, "/SCL")
    body += _testpoint("TP2", 11.5, 10, "/SDA")  # too close to TP1
    body += _testpoint("TP3", 10, 10, "/DUP")  # stacked on TP1
    body += _testpoint("TP4", 0.8, 20, "/EDGE")  # against the board edge
    body += _testpoint("TP5", 30, 10, "Net-(U2-EN)")  # auto-named net
    body += _testpoint("TP6", 34, 10, "", pad=0.5)  # no net, tiny pad
    body += _testpoint("TP7", 38, 10, "/BOT", layer="B.Cu")  # other side
    body += _testpoint("TP8", 25, 25, "/UNDER")  # under U1
    body += _testpoint("TP9", 5.2, 32, "/NEARHOLE")  # crowding H2
    body += _testpoint("TP10", 40, 30, "/SCL")  # second probe on one net
    body += _part("U1", 25, 25)
    body += _hole("H1", 5, 5, drill=3.5)
    body += _hole("H2", 5, 35, drill=3.0)  # different drill
    return _wrap(body)


def uart_board() -> str:
    """A board with a full UART, a fault line the DUT drives, and a rail to monitor."""
    body = rect_outline()
    for i, (x, y, net) in enumerate(
        [
            (10, 10, "/DUT_TXD"),
            (14, 10, "/DUT_RXD"),
            (18, 10, "/DUT_RTS"),
            (22, 10, "/DUT_CTS"),
            (26, 10, "/PWR_FLT"),
            (30, 10, "/+3.3V"),
        ],
        start=1,
    ):
        body += _testpoint(f"TP{i}", x, y, net)
    body += _testpoint("TP90", 10, 20, "GND", value="GND")
    body += _testpoint("TP91", 22, 20, "GND", value="GND")
    for i, (x, y) in enumerate([(5, 5), (45, 5), (5, 35), (45, 35)], start=1):
        body += _hole(f"H{i}", x, y, net="GND")
    return _wrap(body)
