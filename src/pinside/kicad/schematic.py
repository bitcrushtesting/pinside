"""Building the fixture schematic.

Every connection is made with a net label sitting exactly on a pin, rather than with drawn wires.
For a generated sheet that is the right trade: a wire has to be routed around whatever else is on
the page, and a mis-routed wire is a silent short, whereas a label either lands on the pin or
visibly does not. It also means the sheet stays correct however the layout is rearranged later.

Coordinates need care in two places. A symbol library is drawn Y-up and a schematic is Y-down, so
a pin at library (x, y) lands at schematic (sx + x, sy - y). And a pin's stated angle points from
its connection point *toward* the symbol body, so a label reads outward at angle + 180.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import FixtureConfig
from ..modules import Module
from ..pogo import Probe
from ..sexpr import atom, child, find_all, floats, parse, tokenize
from .library import load_symbol
from .write import Node, Raw, Verbatim, at, document, effects, uid, uid_node

SHEET_VERSION = "20260306"

# Symbols the generated sheet is built from.
SYM_TESTPOINT = "Connector:TestPoint"
SYM_RESISTOR = "Device:R"
SYM_GND = "power:GND"
SYM_3V3 = "power:+3V3"
SYM_FLAG = "power:PWR_FLAG"

GRID = 1.27


@dataclass(frozen=True)
class ChannelSlot:
    """One probed line: what it is called, what it lands on, and its designators.

    Reference designators are assigned here, once, and used by both the schematic and the board.
    KiCad wants a letter prefix and a number -- `TP7`, not `TP_dut_uart_tx` -- or annotation
    fails and the netlist cannot be matched to the layout.
    """

    key: str
    dut_signal: str
    net: str
    test_point: str
    resistor: str
    series: bool


def channel_slots(config: FixtureConfig) -> list[ChannelSlot]:
    """Every probed line, in a stable order, with its designators already decided."""
    slots: list[ChannelSlot] = []
    index = 0

    def add(key: str, dut_signal: str, series: bool) -> None:
        nonlocal index
        index += 1
        slots.append(
            ChannelSlot(
                key=key,
                dut_signal=dut_signal,
                net=f"PROBE_{key.upper()}",
                test_point=f"TP{index}",
                resistor=f"R{index}",
                series=series,
            )
        )

    for channel in config.gpio:
        add(channel.name, channel.probe, True)
    for channel in config.adc:
        # An analogue channel's divider is in the config, so it takes no stock series resistor.
        add(channel.name, channel.probe, False)
    for bus in config.buses:
        for role in sorted(bus.pins):
            add(f"{bus.name}_{role}", bus.probes.get(role, ""), True)
    return slots


def snap(value: float) -> float:
    """KiCad connects on a 1.27 mm grid; anything else is a pin that only looks connected."""
    return round(value / GRID) * GRID


def pin_geometry(definition: str, sx: float, sy: float) -> dict[str, tuple[float, float, float]]:
    """pin number -> (schematic x, schematic y, outward angle) for a symbol placed unrotated."""
    out: dict[str, tuple[float, float, float]] = {}
    for pin in find_all(parse(tokenize(definition)), "pin"):
        number = atom(child(pin, "number"), 1)
        if not number:
            continue
        coords = floats(child(pin, "at"))
        while len(coords) < 3:
            coords.append(0.0)
        px, py, angle = coords[0], coords[1], coords[2]
        out[number] = (sx + px, sy - py, (angle + 180) % 360)
    return out


class Sheet:
    """Accumulates the pieces of one schematic page."""

    def __init__(self, config: FixtureConfig, paper: str = "A2"):
        self.config = config
        self.project = config.name
        self.paper = paper
        self.root_uuid = uid(self.project, "sheet.root")
        self.lib_symbols: dict[str, str] = {}
        self.items: list[Node] = []
        self.refs: dict[str, int] = {}

    # -- placement ---------------------------------------------------------

    def _definition(self, lib_id: str) -> str:
        if lib_id not in self.lib_symbols:
            self.lib_symbols[lib_id] = load_symbol(lib_id)
        return self.lib_symbols[lib_id]

    def place(
        self,
        lib_id: str,
        reference: str,
        value: str,
        x: float,
        y: float,
        footprint: str = "",
        description: str = "",
    ) -> dict[str, tuple[float, float, float]]:
        """Put a symbol on the sheet and report where its pins ended up."""
        definition = self._definition(lib_id)
        x, y = snap(x), snap(y)
        key = f"sym.{reference}"

        symbol = Node(
            "symbol",
            Node("lib_id", lib_id),
            at(x, y, 0),
            Node("unit", Raw("1")),
            Node("exclude_from_sim", Raw("no")),
            Node("in_bom", Raw("yes")),
            Node("on_board", Raw("yes")),
            Node("dnp", Raw("no")),
            uid_node(self.project, key),
            Node("property", "Reference", reference, at(x, y - 12.7, 0), effects(1.27)),
            Node("property", "Value", value, at(x, y + 12.7, 0), effects(1.27)),
            Node("property", "Footprint", footprint, at(x, y, 0), effects(1.27, hide=True)),
            Node("property", "Datasheet", "", at(x, y, 0), effects(1.27, hide=True)),
            Node("property", "Description", description, at(x, y, 0), effects(1.27, hide=True)),
        )
        for number in sorted(pin_geometry(definition, 0, 0)):
            symbol.add(Node("pin", number, uid_node(self.project, f"{key}.pin{number}")))
        symbol.add(
            Node(
                "instances",
                Node(
                    "project",
                    self.project,
                    Node(
                        "path",
                        f"/{self.root_uuid}",
                        Node("reference", reference),
                        Node("unit", Raw("1")),
                    ),
                ),
            )
        )
        self.items.append(symbol)
        return pin_geometry(definition, x, y)

    def label(self, net: str, position: tuple[float, float, float], key: str) -> None:
        """A net label on a pin. Its angle is the pin's outward direction, so it reads away."""
        x, y, outward = position
        # KiCad label angles are 0/90/180/270; anything else is rounded to the nearest.
        angle = min((0, 90, 180, 270), key=lambda a: abs((outward - a + 180) % 360 - 180))
        justify = "right" if angle == 180 else "left"
        self.items.append(
            Node(
                "label",
                net,
                at(snap(x), snap(y), angle),
                Node("fields_autoplaced", Raw("yes")),
                effects(1.27, justify=justify),
                uid_node(self.project, f"label.{key}"),
            )
        )

    def no_connect(self, position: tuple[float, float, float], key: str) -> None:
        """Mark a pin deliberately unused, so ERC reports real omissions rather than spares."""
        x, y, _ = position
        self.items.append(
            Node("no_connect", at(snap(x), snap(y)), uid_node(self.project, f"nc.{key}"))
        )

    def note(self, text: str, x: float, y: float, size: float = 2.0) -> None:
        self.items.append(
            Node(
                "text",
                text,
                Node("exclude_from_sim", Raw("no")),
                at(snap(x), snap(y), 0),
                effects(size, justify="left"),
                uid_node(self.project, f"text.{x}.{y}.{text[:16]}"),
            )
        )

    # -- output ------------------------------------------------------------

    def render(self) -> str:
        lib = Node("lib_symbols")
        for lib_id in sorted(self.lib_symbols):
            lib.add(Verbatim(self.lib_symbols[lib_id]))

        root = Node(
            "kicad_sch",
            Node("version", Raw(SHEET_VERSION)),
            Node("generator", "pinside"),
            Node("generator_version", "10.0"),
            Node("uuid", self.root_uuid),
            Node("paper", self.paper),
            Node(
                "title_block",
                Node("title", f"{self.config.name} test fixture"),
                Node("company", "Generated by pinside"),
                Node("comment", Raw("1"), self.config.description or "Bed-of-nails fixture"),
                Node("comment", Raw("2"), f"DUT: {self.config.dut_board or 'unspecified'}"),
            ),
            lib,
            *self.items,
            Node("sheet_instances", Node("path", "/", Node("page", "1"))),
            Node("embedded_fonts", Raw("no")),
        )
        return document(root)


def build(config: FixtureConfig, module: Module | None, probe: Probe) -> str:
    """Lay out the whole fixture sheet: probes on the left, the controller on the right."""
    channels = config.channels
    rows = max(len(channels), 1)
    per_column = 16
    columns = (rows + per_column - 1) // per_column
    paper = "A2" if columns <= 2 else "A1"

    sheet = Sheet(config, paper)
    sheet.note(f"{config.name} - fixture probes", 25, 20, 3.0)
    sheet.note(f"Probe: {probe.summary()}", 25, 27, 1.6)
    sheet.note("Every connection is made by net label; there are no drawn wires.", 25, 32, 1.6)

    # -- the probe field: one test point and one series resistor per probed line.
    x0, y0, dy, dx = 40.0, 45.0, 20.0, 95.0
    for index, slot in enumerate(channel_slots(config)):
        column, row = divmod(index, per_column)
        _probe_channel(sheet, probe, slot, x0 + column * dx, y0 + row * dy)

    # -- the controller.
    ctrl_x = x0 + columns * dx + 45
    ctrl_y = 90.0
    if module:
        pins = sheet.place(
            module.symbol,
            "U1",
            module.name,
            ctrl_x,
            ctrl_y,
            footprint=module.footprint,
            description=module.description,
        )
        _wire_module(sheet, config, module, pins)
        sheet.note(f"{module.description}", ctrl_x - 25, ctrl_y - 55, 1.6)
        if module.note:
            sheet.note(module.note, ctrl_x - 25, ctrl_y - 50, 1.4)
    else:
        sheet.note(f"Controller: bare {config.mcu} -- not generated.", ctrl_x - 25, ctrl_y, 2.0)
        sheet.note(
            "Add the MCU, its supply, crystal and USB by hand, then wire the FIX_* nets.",
            ctrl_x - 25,
            ctrl_y + 6,
            1.6,
        )

    # -- rails. A module's 3V3 and GND pins are declared power *outputs*, so it already drives
    #    the rails; adding a PWR_FLAG there would be a second source and an ERC conflict. A bare
    #    fixture has nothing driving them yet, so it gets the flags.
    rail_y = ctrl_y + 130
    for offset, (symbol, net) in enumerate(((SYM_GND, "GND"), (SYM_3V3, "+3V3"))):
        pins = sheet.place(symbol, f"#PWR0{offset + 1}", net, ctrl_x - 20 + offset * 20, rail_y)
        for number, position in pins.items():
            sheet.label(net, position, f"rail.{net}.{number}")
        if module is None:
            flag = sheet.place(
                SYM_FLAG, f"#FLG0{offset + 1}", "PWR_FLAG", ctrl_x - 20 + offset * 20, rail_y - 15
            )
            for number, position in flag.items():
                sheet.label(net, position, f"flag.{net}.{number}")

    return sheet.render()


def _probe_channel(sheet: Sheet, probe: Probe, slot: ChannelSlot, x: float, y: float) -> None:
    """A pogo pad, its series resistor, and the two nets that join them."""
    pins = sheet.place(
        SYM_TESTPOINT,
        slot.test_point,
        slot.dut_signal or slot.key,
        x,
        y,
        footprint=f"pinside:{probe.footprint_name}",
        description=f"{slot.key}: pogo probe onto {slot.dut_signal or 'an unnamed DUT signal'}",
    )
    for number, position in pins.items():
        sheet.label(slot.net, position, f"{slot.test_point}.{number}")

    if not slot.series:
        return

    res_pins = sheet.place(
        SYM_RESISTOR,
        slot.resistor,
        "100",
        x + 30,
        y,
        footprint="Resistor_SMD:R_0402_1005Metric",
        description=f"{slot.key}: series protection between the probe and the controller",
    )
    sheet.label(slot.net, res_pins["1"], f"{slot.resistor}.1")
    sheet.label(f"FIX_{slot.key.upper()}", res_pins["2"], f"{slot.resistor}.2")


def _wire_module(
    sheet: Sheet, config: FixtureConfig, module: Module, pins: dict[str, tuple[float, float, float]]
) -> None:
    """Label the module's header pins with the nets the config puts on them."""
    wanted: dict[int, str] = {}
    for bus in config.buses:
        for role, gpio in bus.pins.items():
            wanted[gpio] = f"FIX_{bus.name.upper()}_{role.upper()}"
    for gpio_channel in config.gpio:
        wanted[gpio_channel.pin] = f"FIX_{gpio_channel.name.upper()}"
    for adc_channel in config.adc:
        wanted[adc_channel.pin] = f"PROBE_{adc_channel.name.upper()}"

    used: set[str] = set()
    for gpio, net in sorted(wanted.items()):
        header = module.pin_of(gpio)
        if header is None:
            continue  # reported as a finding elsewhere; the sheet simply leaves it unwired
        position = pins.get(str(header))
        if position:
            sheet.label(net, position, f"U1.{header}")
            used.add(str(header))

    for rail, header_pins in module.power_pins.items():
        net = {"3V3": "+3V3", "GND": "GND"}.get(rail)
        if not net:
            continue
        for header in header_pins:
            position = pins.get(str(header))
            if position:
                sheet.label(net, position, f"U1.rail.{header}")
                used.add(str(header))

    # Everything else on the header is a spare. Flagging it says so deliberately, which keeps
    # ERC about real omissions. Delete a flag when you want the pin.
    for number, position in pins.items():
        if number not in used:
            sheet.no_connect(position, f"U1.{number}")
