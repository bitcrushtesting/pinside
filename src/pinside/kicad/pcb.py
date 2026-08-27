"""Building the fixture board.

This is the half of a fixture that has to be exact. A schematic can be redrawn; a probe drilled
0.3 mm off misses its pad and there is nothing to do but order another board. So the placement
here is not laid out by pinside at all -- it is the DUT's own test-point coordinates, read out of
its board file and carried through the fixture transform. The outline and the mounting holes come
from the same place, which is what makes the two boards bolt together.

What is *not* generated is routing. A ratsnest and an accurate drill plan are the useful part;
guessing at trace paths is not, and a human or an autorouter does it better.
"""

from __future__ import annotations

from ..board import Board
from ..config import FixtureConfig
from ..pogo import Probe
from .footprint import mounting_hole_shape, pogo_shape
from .schematic import channel_slots
from .write import Node, Raw, at, document, effects, num, uid, uid_node

PCB_VERSION = "20260206"

_LAYERS = [
    (0, "F.Cu", "signal", None),
    (2, "B.Cu", "signal", None),
    (9, "F.Adhes", "user", "F.Adhesive"),
    (11, "B.Adhes", "user", "B.Adhesive"),
    (13, "F.Paste", "user", None),
    (15, "B.Paste", "user", None),
    (5, "F.SilkS", "user", "F.Silkscreen"),
    (7, "B.SilkS", "user", "B.Silkscreen"),
    (1, "F.Mask", "user", None),
    (3, "B.Mask", "user", None),
    (17, "Dwgs.User", "user", "User.Drawings"),
    (19, "Cmts.User", "user", "User.Comments"),
    (21, "Eco1.User", "user", "User.Eco1"),
    (23, "Eco2.User", "user", "User.Eco2"),
    (25, "Edge.Cuts", "user", None),
    (27, "Margin", "user", None),
    (31, "F.CrtYd", "user", "F.Courtyard"),
    (29, "B.CrtYd", "user", "B.Courtyard"),
    (35, "F.Fab", "user", None),
    (33, "B.Fab", "user", None),
]


class Layout:
    def __init__(self, config: FixtureConfig):
        self.config = config
        self.project = config.name
        self.items: list[Node] = []
        self.nets: dict[str, int] = {"": 0}

    def net(self, name: str) -> int:
        if name not in self.nets:
            self.nets[name] = len(self.nets)
        return self.nets[name]

    def outline(self, segments) -> None:
        for index, (start, end) in enumerate(segments):
            self.items.append(
                Node(
                    "gr_line",
                    Node("start", num(start[0]), num(start[1])),
                    Node("end", num(end[0]), num(end[1])),
                    Node("stroke", Node("width", num(0.1)), Node("type", Raw("default"))),
                    Node("layer", "Edge.Cuts"),
                    uid_node(self.project, f"edge.{index}"),
                )
            )

    def text(
        self, message: str, x: float, y: float, size: float = 1.5, layer: str = "F.SilkS"
    ) -> None:
        self.items.append(
            Node(
                "gr_text",
                message,
                at(x, y, 0),
                Node("layer", layer),
                uid_node(self.project, f"pcbtext.{x}.{y}.{message[:12]}"),
                effects(size, justify="left"),
            )
        )

    def place(
        self,
        shape,
        reference: str,
        value: str,
        x: float,
        y: float,
        net_name: str,
        symbol_uuid: str | None = None,
    ) -> None:
        """Put one generated part on the board, on its net."""
        self.items.append(
            shape.placed(reference, value, x, y, (self.net(net_name), net_name), symbol_uuid)
        )

    def render(self) -> str:
        layers = Node("layers")
        for index, name, kind, alias in _LAYERS:
            node = Node(str(index), Raw(str(index)), name, Raw(kind))
            node.tag = str(index)
            node.items = [name, Raw(kind)] + ([alias] if alias else [])
            layers.add(node)

        root = Node(
            "kicad_pcb",
            Node("version", Raw(PCB_VERSION)),
            Node("generator", "pinside"),
            Node("generator_version", "10.0"),
            Node("general", Node("thickness", num(1.6)), Node("legacy_teardrops", Raw("no"))),
            Node("paper", "A3"),
            layers,
            Node(
                "setup",
                Node("pad_to_mask_clearance", num(0)),
                Node("allow_soldermask_bridges_in_footprints", Raw("no")),
                Node(
                    "pcbplotparams",
                    Node("layerselection", Raw("0x00000000_00000000_55555555_5755f5ff")),
                    Node(
                        "plot_on_all_layers_selection", Raw("0x00000000_00000000_00000000_00000000")
                    ),
                    Node("disableapertmacros", Raw("no")),
                    Node("usegerberextensions", Raw("no")),
                    Node("usegerberattributes", Raw("yes")),
                    Node("usegerberadvancedattributes", Raw("yes")),
                    Node("creategerberjobfile", Raw("yes")),
                    Node("dashed_line_dash_ratio", num(12)),
                    Node("dashed_line_gap_ratio", num(3)),
                    Node("svgprecision", Raw("4")),
                    Node("plotframeref", Raw("no")),
                    Node("mode", Raw("1")),
                    Node("useauxorigin", Raw("no")),
                    Node("dxfpolygonmode", Raw("yes")),
                    Node("dxfimperialunits", Raw("yes")),
                    Node("dxfusepcbnewfont", Raw("yes")),
                    Node("psnegative", Raw("no")),
                    Node("psa4output", Raw("no")),
                    Node("plot_black_and_white", Raw("yes")),
                    Node("sketchpadsonfab", Raw("no")),
                    Node("plotpadnumbers", Raw("no")),
                    Node("hidednponfab", Raw("no")),
                    Node("sketchdnponfab", Raw("yes")),
                    Node("crossoutdnponfab", Raw("yes")),
                    Node("subtractmaskfromsilk", Raw("no")),
                    Node("outputformat", Raw("1")),
                    Node("mirror", Raw("no")),
                    Node("drillshape", Raw("1")),
                    Node("scaleselection", Raw("1")),
                    Node("outputdirectory", ""),
                ),
            ),
        )
        for name, number in sorted(self.nets.items(), key=lambda kv: kv[1]):
            root.add(Node("net", Raw(str(number)), name))
        for item in self.items:
            root.add(item)
        root.add(Node("embedded_fonts", Raw("no")))
        return document(root)


def build(config: FixtureConfig, board: Board | None, probe: Probe) -> str:
    """Place the probe field, the mounting holes and the outline in the fixture frame."""
    layout = Layout(config)
    layout.net("GND")

    if board is not None:
        layout.outline(_fixture_segments(board))
        signal_to_probe = {t.signal: t for t in board.test_points if t.signal}

        shape = pogo_shape(probe, config.name)
        placed = 0
        for slot in channel_slots(config):
            test_point = signal_to_probe.get(slot.dut_signal)
            if test_point is None:
                continue
            layout.place(
                shape,
                slot.test_point,
                slot.dut_signal,
                test_point.fx,
                test_point.fy,
                slot.net,
                uid(config.name, f"sym.{slot.test_point}"),
            )
            placed += 1

        for hole in board.mounting_holes:
            hole_shape = mounting_hole_shape(
                hole.drill or 3.2, hole.pad_diameter or 6.0, config.name
            )
            layout.place(hole_shape, hole.ref, f"{hole.drill or 3.2:g}mm", hole.fx, hole.fy, "GND")

        box = board.outline.bbox
        if box:
            layout.text(f"{config.name}", 2, -6, 2.5)
            layout.text(
                f"probes {placed}  |  {probe.receptacle}  |  DUT {config.dut_board}", 2, -2, 1.2
            )
    return layout.render()


def _probe_map(config: FixtureConfig) -> dict[str, str]:
    """channel-or-bus-role name -> the DUT signal it probes."""
    mapping: dict[str, str] = {}
    for channel in [*config.gpio, *config.adc]:
        if channel.probe:
            mapping[channel.name] = channel.probe
    for bus in config.buses:
        for role, signal in bus.probes.items():
            if signal:
                mapping[f"{bus.name}_{role}"] = signal
    return mapping


def _fixture_segments(board: Board):
    """The DUT outline carried into the fixture frame, so the two boards line up."""
    frame = board.frame or {}
    ox, oy = frame.get("offset", [0.0, 0.0])
    mirror = frame.get("mirror", "none")
    box = board.outline.bbox
    width = box.width if box else 0.0
    height = box.height if box else 0.0

    def move(point):
        x, y = point[0] - ox, point[1] - oy
        if mirror == "x":
            x = width - x
        elif mirror == "y":
            y = height - y
        return round(x, 4), round(y, 4)

    return [(move(a), move(b)) for a, b in board.outline.segments]
