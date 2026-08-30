"""What pinside reads out of a .kicad_pcb: probes, mounting holes, the outline, and obstacles."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import geometry as g
from .sexpr import atom, child, find_all, floats, load

# Reference prefixes that name a thing even when the footprint library does not.
TEST_POINT_REF = re.compile(r"^TP[A-Z]*\d+$", re.I)
MOUNTING_HOLE_REF = re.compile(r"^(H|MH|MK)\d+$", re.I)

GROUND_NET = re.compile(r"^(/)?(GND|GNDA|GNDD|AGND|DGND|VSS|0)$", re.I)
AUTO_NET = re.compile(r"^(Net-\(|unconnected-)")

# A supply rail. KiCad's own power symbols produce most of these names, and the leading + is
# the convention for a numbered rail (+3V3, +5V, +1V8).
POWER_NET = re.compile(r"^\+|^(VCC|VDD|VBUS|VBAT|VIN|VSYS|AVDD|DVDD)([_A-Z0-9]*)$", re.I)

# The lines that decide whether a fixture can put the DUT into a known state. A fixture that
# reaches every data bus and none of these can read a board it cannot reset, which is the
# difference between a test rig and a monitor.
CONTROL_NET = re.compile(
    r"(^|/|_)(N?RE?SET|NRST|RST|BOOT\d*|BOOTSEL|PROG|EN|ENABLE|PWR_?EN|SHDN|WAKE)$", re.I
)

# Which bus a probed signal belongs to, decided by its name. First match wins.
#
# Grouping matters beyond tidy reporting: a fixture wants each bus on contiguous,
# peripheral-capable pins, and the scaffolder cannot suggest any without knowing which lines
# travel together. Names are the only evidence a board file carries about that.
_BUS_RULES = [
    ("ground", GROUND_NET),
    ("i2c", re.compile(r"(^|_)(SCL|SDA)$", re.I)),
    ("power", re.compile(r"^\+?\d|^(VCC|VDD|VBUS|VBAT|\+3V3|\+3\.3V|\+5V|\+1V\d)$", re.I)),
    ("uart", re.compile(r"(^|_)(TXD|RXD|TX|RX|RTS|CTS)$", re.I)),
    ("spi", re.compile(r"(^|_)(MISO|MOSI|SCLK|SCK|CLK|CS|SS|NSS)$", re.I)),
]


def classify(signal: str) -> str:
    """Group a signal by name: i2c, uart, spi, power, ground, or control.

    Signals sharing a leading token are kept apart by that token elsewhere, so ETH_CLK and
    DISP_CS both classify as "spi" here and separate into ``spi_eth`` and ``spi_disp`` by prefix.
    """
    bare = signal.rsplit("/", 1)[-1]
    for name, rule in _BUS_RULES:
        if rule.search(bare):
            prefix = bare.split("_")[0].lower() if "_" in bare else ""
            if name in ("spi", "uart") and prefix:
                return f"{name}_{prefix}"
            return name
    return "control"


_EDGE_TAGS = ("gr_line", "gr_rect", "gr_arc", "gr_circle", "gr_poly", "gr_curve")


@dataclass
class Pad:
    number: str
    type: str
    shape: str
    size: tuple[float, float] | None
    drill: float | None
    layers: list[str]
    net: str
    x: float  # absolute, footprint rotation applied
    y: float
    net_ordinal: int | None = None  # None in the KiCad 10 form, which has no ordinals

    @property
    def max_dimension(self) -> float:
        return max(self.size) if self.size else 0.0

    @property
    def min_dimension(self) -> float:
        return min(self.size) if self.size else 0.0


@dataclass
class Footprint:
    ref: str
    value: str
    library: str
    x: float
    y: float
    rotation: float
    side: str
    pads: list[Pad] = field(default_factory=list)

    @property
    def bbox(self) -> g.BBox | None:
        if not self.pads:
            return None
        xs, ys = [], []
        for pad in self.pads:
            half_w = (pad.size[0] / 2) if pad.size else 0.0
            half_h = (pad.size[1] / 2) if pad.size else 0.0
            xs += [pad.x - half_w, pad.x + half_w]
            ys += [pad.y - half_h, pad.y + half_h]
        return g.BBox(min(xs), min(ys), max(xs), max(ys))


@dataclass
class TestPoint:
    ref: str
    value: str
    signal: str
    net: str
    x: float
    y: float
    side: str
    pad: Pad | None

    fx: float = 0.0  # position in the fixture frame, filled in by transform()
    fy: float = 0.0

    @property
    def bus(self) -> str:
        """i2c, uart_<prefix>, spi_<prefix>, power, ground, or control."""
        return classify(self.signal)

    @property
    def anonymous_net(self) -> bool:
        return not self.net or bool(AUTO_NET.match(self.net))

    @property
    def is_ground(self) -> bool:
        return bool(GROUND_NET.match(self.net or "")) or bool(GROUND_NET.match(self.value or ""))


@dataclass
class MountingHole:
    ref: str
    x: float
    y: float
    drill: float | None
    pad_diameter: float | None
    plated: bool
    net: str

    fx: float = 0.0
    fy: float = 0.0


@dataclass
class Board:
    source: str
    outline: g.Outline
    test_points: list[TestPoint]
    mounting_holes: list[MountingHole]
    obstacles: list[Footprint]  # every other placed footprint, for collision checks
    frame: dict = field(default_factory=dict)

    # ordinal -> every name the file gives it, from the net table and from every pad. Empty for
    # a KiCad 10 board, which has no ordinals to disagree about.
    net_ordinals: dict[int, set[str]] = field(default_factory=dict)

    # The stackup thickness the board file declares, mm. None when it declares none, which is
    # what a hand-written or minimal file does: how stiff the DUT is then has to be assumed.
    thickness: float | None = None

    @property
    def nets(self) -> set[str]:
        """Every named net the board file mentions, from any pad on any footprint.

        A .kicad_pcb has no net list of its own worth reading -- the names live on the pads --
        so this is assembled rather than parsed. Auto-named nets are left out: they carry no
        intent, so their absence from the probe list says nothing.
        """
        found = {pad.net for fp in self.obstacles for pad in fp.pads}
        found |= {t.net for t in self.test_points}
        found |= {h.net for h in self.mounting_holes}
        return {n for n in found if n and not AUTO_NET.match(n.rsplit("/", 1)[-1])}

    @property
    def probed_nets(self) -> set[str]:
        return {t.net for t in self.test_points if t.net}


def _net_ref(node) -> tuple[int | None, str]:
    """The (ordinal, name) a `(net ...)` expression carries.

    KiCad <= 9 writes `(net <ordinal> "NAME")` and keys the net by the *ordinal*: the name is a
    label. KiCad 10 writes `(net "NAME")` and the name is the identity. A zone writes `(net 3)`
    with no name at all. All three shapes turn up, so the ordinal is read rather than skipped:
    two names sharing one ordinal is a board that loses nets the moment KiCad opens it, and
    nothing else in the file gives that away.
    """
    if node is None:
        return None, ""
    if len(node) > 2 and isinstance(node[2], str):
        try:
            return int(node[1]), node[2]
        except (TypeError, ValueError):
            return None, node[2]
    token = atom(node, 1)
    try:
        return int(token), ""  # a zone's bare (net 3)
    except ValueError:
        return None, token  # KiCad 10's (net "NAME")


def _net_of(pad_node) -> str:
    return _net_ref(child(pad_node, "net"))[1]


def _property(footprint, name: str) -> str:
    for prop in find_all(footprint, "property"):
        if len(prop) > 2 and prop[1] == name and isinstance(prop[2], str):
            return prop[2]
    return ""


def _at(node) -> tuple[float, float, float]:
    vals = floats(child(node, "at"))
    vals += [0.0] * (3 - len(vals))
    return vals[0], vals[1], vals[2]


def _read_pad(node, fx: float, fy: float, rot: float) -> Pad:
    px, py, _ = _at(node)
    dx, dy = g.rotate(px, py, rot)
    size = floats(child(node, "size"))
    drill_node = child(node, "drill")
    drill_vals = floats(drill_node) if drill_node is not None else []
    layers = child(node, "layers")
    ordinal, net_name = _net_ref(child(node, "net"))
    return Pad(
        number=atom(node, 1, "?"),
        type=atom(node, 2, "?"),
        shape=atom(node, 3, "?"),
        size=(size[0], size[1]) if len(size) >= 2 else None,
        drill=max(drill_vals) if drill_vals else None,
        layers=[t for t in (layers[1:] if layers else []) if isinstance(t, str)],
        net=net_name,
        net_ordinal=ordinal,
        x=fx + dx,
        y=fy + dy,
    )


def _read_footprint(node) -> Footprint:
    fx, fy, rot = _at(node)
    return Footprint(
        ref=_property(node, "Reference") or "?",
        value=_property(node, "Value"),
        library=node[1] if len(node) > 1 and isinstance(node[1], str) else "",
        x=fx,
        y=fy,
        rotation=rot,
        side="bottom" if atom(child(node, "layer"), 1).startswith("B.") else "top",
        pads=[_read_pad(p, fx, fy, rot) for p in find_all(node, "pad")],
    )


def signal_name(net: str, value: str) -> str:
    """The human name of the probed signal.

    KiCad auto-names any net that was never given a label -- Net-(U2-EN) and friends. Those say
    nothing, so fall back to the test point's Value field, which is where the schematic records
    the intent (a test point valued POWER_IN_EN is on the power-enable line, whatever KiCad
    decided to call the net).
    """
    bare = net.rsplit("/", 1)[-1]
    if (not bare or AUTO_NET.match(bare)) and value and value.lower() != "testpoint":
        return value
    return bare


def is_test_point(fp: Footprint) -> bool:
    return "TestPoint" in fp.library or bool(TEST_POINT_REF.match(fp.ref))


def is_mounting_hole(fp: Footprint) -> bool:
    return "MountingHole" in fp.library or bool(MOUNTING_HOLE_REF.match(fp.ref))


def read_outline(tree) -> g.Outline:
    outline = g.Outline()
    for tag in _EDGE_TAGS:
        for node in find_all(tree, tag):
            if atom(child(node, "layer"), 1) != "Edge.Cuts":
                continue
            kind = tag[3:]
            start = floats(child(node, "start"))
            end = floats(child(node, "end"))
            mid = floats(child(node, "mid"))
            center = floats(child(node, "center"))
            radius = floats(child(node, "radius"))
            pts_node = child(node, "pts")
            poly = [
                tuple(floats(p)[:2])
                for p in (pts_node or [])
                if isinstance(p, list) and p and p[0] == "xy"
            ]

            shape = {"kind": kind}
            points: list[g.Point] = []
            if kind == "rect" and len(start) >= 2 and len(end) >= 2:
                r = radius[0] if radius else 0.0
                shape.update(start=start[:2], end=end[:2], radius=r)
                points = g.rounded_rect_points(tuple(start[:2]), tuple(end[:2]), r)
            elif kind == "line" and len(start) >= 2 and len(end) >= 2:
                shape.update(start=start[:2], end=end[:2])
                points = [tuple(start[:2]), tuple(end[:2])]
            elif kind == "arc" and len(start) >= 2 and len(mid) >= 2 and len(end) >= 2:
                shape.update(start=start[:2], mid=mid[:2], end=end[:2])
                points = g.arc_points(tuple(start[:2]), tuple(mid[:2]), tuple(end[:2]))
            elif kind == "circle" and len(center) >= 2 and len(end) >= 2:
                shape.update(center=center[:2], end=end[:2])
                points = g.circle_points(tuple(center[:2]), tuple(end[:2]))
            elif kind in ("poly", "curve") and poly:
                shape.update(points=[list(p) for p in poly])
                points = list(poly)
                if kind == "poly" and points[0] != points[-1]:
                    points.append(points[0])
            if not points:
                continue
            outline.shapes.append(shape)
            outline.segments.extend(g.polyline_segments(points))

    rings, outline.open_segments = g.chain_rings(outline.segments)
    outline.ring, outline.cutouts, outline.islands = g.resolve_rings(rings)
    return outline


def read_net_ordinals(tree) -> dict[int, set[str]]:
    """Every net ordinal in the file, with every name attached to it.

    Both the board's own `(net N "NAME")` table and the copy on each pad, because they can
    disagree with each other and that disagreement is itself the defect.
    """
    ordinals: dict[int, set[str]] = {}
    for node in find_all(tree, "net"):
        ordinal, name = _net_ref(node)
        if ordinal is not None and name:
            ordinals.setdefault(ordinal, set()).add(name)
    return ordinals


def read_thickness(tree) -> float | None:
    """The board's own stackup thickness, from `(general (thickness 1.6))`.

    It is the one number in the file that says how stiff the DUT is, and stiffness is what
    decides whether the probes bow it. A board that does not declare one is not an error: the
    force check falls back to an assumption and says that it did.
    """
    for general in find_all(tree, "general"):
        values = floats(child(general, "thickness"))
        if values and values[0] > 0:
            return values[0]
    return None


def read_board(path: str) -> Board:
    tree = load(path)
    outline = read_outline(tree)

    test_points: list[TestPoint] = []
    holes: list[MountingHole] = []
    obstacles: list[Footprint] = []

    for node in find_all(tree, "footprint"):
        fp = _read_footprint(node)
        if is_test_point(fp):
            pad = fp.pads[0] if fp.pads else None
            net = pad.net if pad else ""
            test_points.append(
                TestPoint(
                    ref=fp.ref,
                    value=fp.value,
                    signal=signal_name(net, fp.value),
                    net=net,
                    x=pad.x if pad else fp.x,
                    y=pad.y if pad else fp.y,
                    side=fp.side,
                    pad=pad,
                )
            )
        elif is_mounting_hole(fp):
            drills = [p.drill for p in fp.pads if p.drill]
            holes.append(
                MountingHole(
                    ref=fp.ref,
                    x=fp.x,
                    y=fp.y,
                    drill=max(drills) if drills else None,
                    pad_diameter=max((p.max_dimension for p in fp.pads), default=None),
                    plated=bool(drills),
                    net=next((p.net for p in fp.pads if p.net), ""),
                )
            )
        else:
            obstacles.append(fp)

    def order(ref: str) -> tuple[str, int]:
        """TP9 before TP10: sort on the prefix, then the trailing number as a number."""
        digits = re.search(r"\d+$", ref)
        return re.sub(r"\d+$", "", ref), int(digits.group()) if digits else 0

    test_points.sort(key=lambda t: order(t.ref))
    holes.sort(key=lambda h: order(h.ref))
    return Board(
        source=path,
        outline=outline,
        test_points=test_points,
        mounting_holes=holes,
        obstacles=obstacles,
        net_ordinals=read_net_ordinals(tree),
        thickness=read_thickness(tree),
    )


def transform(board: Board, origin: str = "outline", mirror: str = "none") -> Board:
    """Fill in each item's fixture-frame coordinates.

    'outline' puts (0,0) at the outline's top-left corner, which is what you want when the
    fixture is drawn as its own board. mirror='x' additionally flips X, the transform for a DUT
    laid face-down onto upward-pointing probes -- get this wrong and the fixture is a perfect
    mirror image of the one you need.
    """
    box = board.outline.bbox
    # A board with no outline is a finding (PS001), not a reason to refuse to look at it -- the
    # net and pitch checks still say something useful. Fall back to raw page coordinates.
    resolved = origin if (origin == "page" or box) else "page"
    ox, oy = (box.min_x, box.min_y) if (resolved == "outline" and box) else (0.0, 0.0)
    width = box.width if box else 0.0
    height = box.height if box else 0.0

    for item in [*board.test_points, *board.mounting_holes]:
        x, y = item.x - ox, item.y - oy
        if mirror == "x" and box:
            x = width - x
        elif mirror == "y" and box:
            y = height - y
        item.fx, item.fy = round(x, 4), round(y, 4)

    board.frame = {
        "origin": resolved,
        "requested_origin": origin,
        "mirror": mirror if box else "none",
        "offset": [ox, oy],
    }
    return board
