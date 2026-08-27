"""Synthetic .kicad_pcb files, so the tests do not depend on any real project."""

from __future__ import annotations

import atexit
import itertools
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


def _wrap(body: str) -> str:
    return f'(kicad_pcb (version 20241229) (generator "pinside-tests"){body}\n)\n'


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
