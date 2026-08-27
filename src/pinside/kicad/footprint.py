"""The footprints pinside emits, and the single description both copies are built from.

A KiCad board carries its own copy of every footprint it places, and checks that copy against
the library on every DRC run. So the two have to be built from one description -- assembling
them separately means they drift, and every board check reports a mismatch that is really just
two spellings of the same part.

pinside emits its own mounting hole rather than using KiCad's for the same reason the probes are
generated: the DUT decides the drill, and KiCad's holes are named for standard screw sizes. A
3.5 mm hole against a library that offers 3.2 mm is a mismatch on every check.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..pogo import Probe
from .write import Node, Raw, at, document, effects, num, uid_node


@dataclass
class Shape:
    """One round through-hole part, described once and rendered two ways."""

    name: str
    descr: str
    tags: str
    drill_mm: float
    pad_mm: float
    radius: float
    project: str
    extra_attr: list[str] = field(default_factory=list)

    # -- the pieces both copies share -------------------------------------

    def graphics(self) -> list[Node]:
        return [
            Node(
                "property",
                "Reference",
                "REF**",
                at(0, -self.radius - 1.2, 0),
                Node("layer", "F.SilkS"),
                uid_node(self.project, f"fp.{self.name}.ref"),
                effects(1.0),
            ),
            Node(
                "property",
                "Value",
                self.name,
                at(0, self.radius + 1.2, 0),
                Node("layer", "F.Fab"),
                uid_node(self.project, f"fp.{self.name}.val"),
                effects(1.0),
            ),
            Node(
                "fp_circle",
                Node("center", num(0), num(0)),
                Node("end", num(self.radius + 0.15), num(0)),
                Node("stroke", Node("width", num(0.12)), Node("type", Raw("solid"))),
                Node("fill", Raw("no")),
                Node("layer", "F.SilkS"),
                uid_node(self.project, f"fp.{self.name}.silk"),
            ),
            Node(
                "fp_circle",
                Node("center", num(0), num(0)),
                Node("end", num(self.radius + 0.25), num(0)),
                Node("stroke", Node("width", num(0.05)), Node("type", Raw("default"))),
                Node("fill", Raw("no")),
                Node("layer", "F.CrtYd"),
                uid_node(self.project, f"fp.{self.name}.crtyd"),
            ),
        ]

    def pad(self, net: tuple[int, str] | None = None) -> Node:
        node = Node(
            "pad",
            "1",
            Raw("thru_hole"),
            Raw("circle"),
            at(0, 0),
            Node("size", num(self.pad_mm), num(self.pad_mm)),
            Node("drill", num(self.drill_mm)),
            Node("layers", Raw('"*.Cu"'), Raw('"*.Mask"')),
            Node("remove_unused_layers", Raw("no")),
        )
        if net is not None:
            node.add(Node("net", Raw(str(net[0])), net[1]))
        node.add(uid_node(self.project, f"fp.{self.name}.pad1"))
        return node

    # -- the two renderings ------------------------------------------------

    def library(self) -> str:
        """The standalone .kicad_mod."""
        root = Node(
            "footprint",
            self.name,
            Node("version", Raw("20260206")),
            Node("generator", "pinside"),
            Node("generator_version", "10.0"),
            Node("layer", "F.Cu"),
            Node("descr", self.descr),
            Node("tags", self.tags),
            Node("attr", Raw("through_hole"), *[Raw(a) for a in self.extra_attr]),
            *self.graphics(),
            self.pad(),
        )
        return document(root)

    def placed(
        self,
        reference: str,
        value: str,
        x: float,
        y: float,
        net: tuple[int, str],
        symbol_uuid: str | None,
    ) -> Node:
        """The copy that goes on the board, at a position and on a net."""
        graphics = self.graphics()
        # items are [name, value, (at ...), ...]; the text is index 1.
        graphics[0].items[1] = reference
        graphics[1].items[1] = value

        node = Node(
            "footprint",
            f"pinside:{self.name}",
            Node("layer", "F.Cu"),
            uid_node(self.project, f"fp.{reference}"),
            at(x, y),
            Node("descr", self.descr),
            Node("tags", self.tags),
            Node("attr", Raw("through_hole"), *[Raw(a) for a in self.extra_attr]),
        )
        if symbol_uuid:
            node.add(Node("path", f"/{symbol_uuid}"))
        for item in graphics:
            node.add(item)
        node.add(self.pad(net))
        return node


def pogo_shape(probe: Probe, project: str) -> Shape:
    return Shape(
        name=probe.footprint_name,
        descr=f"{probe.description}. {probe.summary()}. Mounting: {probe.mounting}.",
        tags="pogo spring pin test fixture bed-of-nails",
        drill_mm=probe.drill_mm,
        pad_mm=probe.pad_mm,
        radius=probe.body_dia_mm / 2,
        project=project,
    )


def mounting_hole_name(drill: float) -> str:
    return f"MountingHole_{drill:g}mm"


def mounting_hole_shape(drill: float, pad_dia: float, project: str) -> Shape:
    return Shape(
        name=mounting_hole_name(drill),
        descr=f"Mounting hole, {drill:g} mm drill, taken from the DUT",
        tags="mounting hole fixture",
        drill_mm=drill,
        pad_mm=pad_dia,
        radius=max(pad_dia, drill) / 2,
        project=project,
        extra_attr=["exclude_from_pos_files", "exclude_from_bom"],
    )
