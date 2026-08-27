"""The fixture configuration: what the microcontroller should make of each DUT signal.

A fixture config is a JSON file describing buses, GPIO channels and ADC channels, each tied to a
named DUT signal. It is checked twice before any firmware is written -- once against the target
microcontroller's pin capabilities, and once against the DUT board itself. Both matter:

  * The MCU check catches a pin that cannot carry the function asked of it. On an RP2350, I2C0's
    data line exists only on even-numbered pins, and nothing about "sda: 9" looks wrong until the
    bus stays silent.
  * The DUT check catches a config that has drifted from the board -- a probe named for a signal
    the board no longer has, or a test point the config forgot. Firmware that claims a channel
    the fixture cannot reach is worse than firmware that admits it has none.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import modules, pogo, targets
from .board import Board, read_board, transform
from .checks import ERROR, INFO, WARNING, Finding

IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")

UART_ROLES = ("tx", "rx", "cts", "rts")
I2C_ROLES = ("sda", "scl")
SPI_ROLES = ("rx", "cs", "sck", "tx")

DIRECTIONS = ("input", "output", "open_drain")
PULLS = ("none", "up", "down")
PARITIES = ("none", "even", "odd")
SPI_MODES = (0, 1, 2, 3)
BUS_ROLES = ("master", "monitor")


class ConfigError(Exception):
    """The config could not be read at all -- malformed JSON, missing keys, wrong types."""


# --------------------------------------------------------------------------- model


@dataclass
class Channel:
    """Anything the agent can name and act on: a GPIO, an ADC input, or a bus."""

    name: str
    kind: str  # gpio | adc | uart | i2c | spi
    probe: str = ""  # the DUT signal this lands on
    description: str = ""


@dataclass
class GpioChannel(Channel):
    pin: int = -1
    direction: str = "input"
    pull: str = "none"
    active_low: bool = False
    initial: str = "released"  # released | asserted, for outputs
    kind: str = "gpio"


@dataclass
class AdcChannel(Channel):
    pin: int = -1
    adc: int = -1  # derived from the pin
    divider: float = 1.0  # multiply the measured volts by this
    nominal_v: float | None = None
    tolerance_v: float | None = None
    kind: str = "adc"


@dataclass
class Bus(Channel):
    peripheral: int = 0
    pins: dict[str, int] = field(default_factory=dict)
    probes: dict[str, str] = field(default_factory=dict)
    role: str = "master"
    guard: str = ""  # GPIO channel that must be asserted before driving
    stream: bool = False  # push received data to the host unasked


@dataclass
class UartBus(Bus):
    baud: int = 115200
    data_bits: int = 8
    stop_bits: int = 1
    parity: str = "none"
    kind: str = "uart"


@dataclass
class I2cBus(Bus):
    hz: int = 400_000
    pullups: bool = False
    kind: str = "i2c"


@dataclass
class SpiBus(Bus):
    hz: int = 1_000_000
    mode: int = 0
    kind: str = "spi"


@dataclass
class FixtureConfig:
    name: str
    description: str = ""
    mcu: str = "rp2350b"
    board: str = modules.DEFAULT
    probe: str = pogo.DEFAULT
    mirror: str = "x"
    clock_hz: int = 150_000_000
    usb: dict = field(default_factory=dict)
    dut_board: str = ""
    logic_voltage: float = 3.3
    require_all_test_points: bool = True
    uart: list[UartBus] = field(default_factory=list)
    i2c: list[I2cBus] = field(default_factory=list)
    spi: list[SpiBus] = field(default_factory=list)
    gpio: list[GpioChannel] = field(default_factory=list)
    adc: list[AdcChannel] = field(default_factory=list)
    source: str = ""

    @property
    def target(self) -> targets.Target:
        return targets.get(self.mcu)

    @property
    def module(self) -> modules.Module | None:
        """The carrier board, or None when the chip sits on the fixture board itself."""
        return modules.get(self.board)

    @property
    def probe_part(self) -> pogo.Probe:
        return pogo.get(self.probe)

    @property
    def buses(self) -> list[Bus]:
        return [*self.uart, *self.i2c, *self.spi]

    @property
    def channels(self) -> list[Channel]:
        return [*self.buses, *self.gpio, *self.adc]

    def probe_names(self) -> set[str]:
        names = {g.probe for g in self.gpio if g.probe}
        names |= {a.probe for a in self.adc if a.probe}
        for bus in self.buses:
            names |= {p for p in bus.probes.values() if p}
        return names

    def pin_owners(self) -> dict[int, str]:
        """pin -> "<channel>.<role>", for the duplicate check and for the generated comments."""
        owners: dict[int, str] = {}
        for bus in self.buses:
            for role, pin in bus.pins.items():
                owners.setdefault(pin, f"{bus.name}.{role}")
        for g in self.gpio:
            owners.setdefault(g.pin, g.name)
        for a in self.adc:
            owners.setdefault(a.pin, a.name)
        return owners


# --------------------------------------------------------------------------- loading


def _require(node: dict, key: str, where: str, kind=None, default=None):
    if key not in node:
        if default is not None:
            return default
        raise ConfigError(f"{where}: missing required key {key!r}")
    value = node[key]
    if kind is not None and not isinstance(value, kind):
        want = kind.__name__ if isinstance(kind, type) else "/".join(k.__name__ for k in kind)
        raise ConfigError(f"{where}: {key!r} must be {want}, got {type(value).__name__}")
    return value


def _bus_common(node: dict, where: str, roles: tuple[str, ...]) -> dict:
    pins = _require(node, "pins", where, dict)
    bad = [r for r in pins if r not in roles]
    if bad:
        raise ConfigError(
            f"{where}: unknown pin role(s) {', '.join(sorted(bad))}; "
            f"expected any of {', '.join(roles)}"
        )
    for role, pin in pins.items():
        if not isinstance(pin, int):
            raise ConfigError(f"{where}: pins.{role} must be an integer GPIO number")
    probes = node.get("probes", {})
    if not isinstance(probes, dict):
        raise ConfigError(f"{where}: 'probes' must be an object mapping pin role -> DUT signal")
    return {
        "name": _require(node, "name", where, str),
        "peripheral": node.get("peripheral", 0),
        "pins": dict(pins),
        "probes": {k: str(v) for k, v in probes.items()},
        "role": node.get("role", "master"),
        "guard": node.get("guard", ""),
        "stream": bool(node.get("stream", False)),
        "description": node.get("description", ""),
    }


def load(path: str | Path) -> FixtureConfig:
    text = Path(path).read_text(encoding="utf-8")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as err:
        raise ConfigError(
            f"{path}: invalid JSON at line {err.lineno} column {err.colno}: {err.msg}"
        ) from None
    return from_dict(raw, source=str(path))


def from_dict(raw: dict, source: str = "") -> FixtureConfig:
    if not isinstance(raw, dict):
        raise ConfigError("the config must be a JSON object")

    target_node = raw.get("target", {})
    if not isinstance(target_node, dict):
        raise ConfigError("'target' must be an object")
    dut_node = raw.get("dut", {})
    if not isinstance(dut_node, dict):
        raise ConfigError("'dut' must be an object")
    fixture_node = raw.get("fixture", {})
    if not isinstance(fixture_node, dict):
        raise ConfigError("'fixture' must be an object")

    # A carrier board already determines the chip, so naming both is a chance to disagree.
    # Stating the chip explicitly still wins -- and is then checked against the board.
    board_name = target_node.get("board", modules.DEFAULT)
    try:
        carrier = modules.get(board_name)
    except ValueError:
        carrier = None
    default_mcu = carrier.mcu if carrier else "rp2350b"

    cfg = FixtureConfig(
        name=_require(raw, "name", "config", str),
        description=raw.get("description", ""),
        mcu=target_node.get("mcu", default_mcu),
        board=board_name,
        probe=fixture_node.get("probe", pogo.DEFAULT),
        mirror=fixture_node.get("mirror", "x"),
        clock_hz=int(target_node.get("clock_hz", 150_000_000)),
        usb=dict(target_node.get("usb", {})),
        dut_board=dut_node.get("board", ""),
        logic_voltage=float(dut_node.get("logic_voltage", 3.3)),
        require_all_test_points=bool(dut_node.get("require_all_test_points", True)),
        source=source,
    )

    for i, node in enumerate(raw.get("uart", [])):
        where = f"uart[{i}]"
        common = _bus_common(node, where, UART_ROLES)
        cfg.uart.append(
            UartBus(
                **common,
                baud=int(node.get("baud", 115200)),
                data_bits=int(node.get("data_bits", 8)),
                stop_bits=int(node.get("stop_bits", 1)),
                parity=node.get("parity", "none"),
            )
        )
    for i, node in enumerate(raw.get("i2c", [])):
        where = f"i2c[{i}]"
        common = _bus_common(node, where, I2C_ROLES)
        cfg.i2c.append(
            I2cBus(
                **common, hz=int(node.get("hz", 400_000)), pullups=bool(node.get("pullups", False))
            )
        )
    for i, node in enumerate(raw.get("spi", [])):
        where = f"spi[{i}]"
        node = dict(node)
        # Accept the names everyone actually uses on a schematic.
        if isinstance(node.get("pins"), dict):
            node["pins"] = {targets.SPI_ALIASES.get(k, k): v for k, v in node["pins"].items()}
        if isinstance(node.get("probes"), dict):
            node["probes"] = {targets.SPI_ALIASES.get(k, k): v for k, v in node["probes"].items()}
        common = _bus_common(node, where, SPI_ROLES)
        cfg.spi.append(
            SpiBus(**common, hz=int(node.get("hz", 1_000_000)), mode=int(node.get("mode", 0)))
        )

    for i, node in enumerate(raw.get("gpio", [])):
        where = f"gpio[{i}]"
        cfg.gpio.append(
            GpioChannel(
                name=_require(node, "name", where, str),
                pin=_require(node, "pin", where, int),
                probe=node.get("probe", ""),
                direction=node.get("direction", "input"),
                pull=node.get("pull", "none"),
                active_low=bool(node.get("active_low", False)),
                initial=node.get("initial", "released"),
                description=node.get("description", ""),
            )
        )

    for i, node in enumerate(raw.get("adc", [])):
        where = f"adc[{i}]"
        cfg.adc.append(
            AdcChannel(
                name=_require(node, "name", where, str),
                pin=_require(node, "pin", where, int),
                probe=node.get("probe", ""),
                divider=float(node.get("divider", 1.0)),
                nominal_v=node.get("nominal_v"),
                tolerance_v=node.get("tolerance_v"),
                description=node.get("description", ""),
            )
        )
    return cfg


# --------------------------------------------------------------------------- validation


def _check_names(cfg: FixtureConfig) -> list[Finding]:
    out: list[Finding] = []
    seen: dict[str, str] = {}
    for ch in cfg.channels:
        if not IDENTIFIER.match(ch.name):
            out.append(
                Finding(
                    "PF010",
                    ERROR,
                    f"channel name {ch.name!r} is not usable in C",
                    [ch.name],
                    "use lower_snake_case starting with a letter",
                )
            )
        if ch.name in seen:
            out.append(
                Finding(
                    "PF011",
                    ERROR,
                    f"duplicate channel name {ch.name!r}",
                    [f"{seen[ch.name]} and {ch.kind}"],
                    "the agent addresses channels by name, so they must be unique",
                )
            )
        seen[ch.name] = ch.kind
    return out


def _check_module(cfg: FixtureConfig) -> list[Finding]:
    """A carrier board brings out only some of its chip's pins, and that is easy to forget."""
    try:
        module = cfg.module
    except ValueError as err:
        return [Finding("PF005", ERROR, str(err), [cfg.board])]
    if module is None:
        return []

    out: list[Finding] = []
    if module.mcu != cfg.mcu:
        out.append(
            Finding(
                "PF006",
                ERROR,
                f"board {module.name!r} carries {module.mcu}, but the config says {cfg.mcu}",
                [cfg.board],
                f"set target.mcu to {module.mcu}, or choose a board that carries {cfg.mcu}",
            )
        )
        return out

    claimed = sorted(cfg.pin_owners())
    hidden = module.unexposed(claimed)
    if hidden:
        owners = cfg.pin_owners()
        out.append(
            Finding(
                "PF024",
                ERROR,
                f"{len(hidden)} pins are not brought out on the {module.name}",
                [f"GPIO{g} ({owners[g]})" for g in hidden],
                f"{module.description}. {module.note} "
                f'Use a board that exposes them, or set target.board to "bare" and put the '
                f"{cfg.mcu} on the fixture itself.",
            )
        )

    spare = len(module.gpios) - len(claimed)
    if 0 <= spare < 2 and not hidden:
        out.append(
            Finding(
                "PF025",
                INFO,
                f"the {module.name} has {spare} unused GPIO left",
                [module.name],
                "no room for another probe without changing board",
            )
        )
    return out


def _check_probe(cfg: FixtureConfig) -> list[Finding]:
    try:
        _ = cfg.probe_part
    except ValueError as err:
        return [Finding("PF007", ERROR, str(err), [cfg.probe])]
    return []


def _check_pins(cfg: FixtureConfig, target: targets.Target) -> list[Finding]:
    out: list[Finding] = []
    claims: dict[int, list[str]] = {}

    def claim(pin: int, who: str) -> None:
        claims.setdefault(pin, []).append(who)
        if not target.has_pin(pin):
            out.append(
                Finding(
                    "PF020",
                    ERROR,
                    f"{who} uses GPIO{pin}, which {target.name} does not have",
                    [who],
                    f"{target.name} has GPIO0..GPIO{target.gpio_count - 1}",
                )
            )

    for bus in cfg.buses:
        for role, pin in bus.pins.items():
            who = f"{bus.name}.{role}"
            claim(pin, who)
            if not target.has_pin(pin):
                continue
            lookup = {"uart": target.uart_of, "i2c": target.i2c_of, "spi": target.spi_of}[bus.kind]
            actual = lookup(pin)
            wanted = (bus.peripheral, role)
            if actual != wanted:
                candidates = target.pins_for(bus.kind, bus.peripheral, role)
                out.append(
                    Finding(
                        "PF021",
                        ERROR,
                        f"GPIO{pin} cannot be {bus.kind}{bus.peripheral} {role}",
                        [who],
                        f"on {target.name} it is {bus.kind}{actual[0]} {actual[1]}; "
                        f"{bus.kind}{bus.peripheral} {role} is available on "
                        f"{', '.join(f'GPIO{g}' for g in candidates) or 'no pin'}",
                    )
                )

    for g in cfg.gpio:
        claim(g.pin, g.name)
    for a in cfg.adc:
        claim(a.pin, a.name)
        channel = target.adc_of(a.pin)
        if target.has_pin(a.pin) and channel is None:
            usable = ", ".join(f"GPIO{g}" for g in sorted(target.adc_pins))
            out.append(
                Finding(
                    "PF022",
                    ERROR,
                    f"GPIO{a.pin} has no ADC input",
                    [a.name],
                    f"on {target.name} the ADC reaches {usable}",
                )
            )
        else:
            a.adc = channel if channel is not None else -1

    for pin, owners in sorted(claims.items()):
        if len(owners) > 1:
            out.append(
                Finding(
                    "PF023",
                    ERROR,
                    f"GPIO{pin} is claimed by more than one channel",
                    owners,
                    "one pin cannot serve two signals",
                )
            )
    return out


def _check_settings(cfg: FixtureConfig) -> list[Finding]:
    out: list[Finding] = []
    gpio_names = {g.name for g in cfg.gpio}

    for bus in cfg.buses:
        if bus.role not in BUS_ROLES:
            out.append(
                Finding(
                    "PF030",
                    ERROR,
                    f"{bus.name}: role {bus.role!r} is not recognised",
                    [bus.name],
                    f"expected one of {', '.join(BUS_ROLES)}",
                )
            )
        if bus.guard and bus.guard not in gpio_names:
            out.append(
                Finding(
                    "PF031",
                    ERROR,
                    f"{bus.name}: guard {bus.guard!r} is not a declared gpio channel",
                    [bus.name],
                    "a guard names the GPIO that must be asserted before the fixture "
                    "may drive a bus the DUT also drives",
                )
            )
        missing = [r for r in bus.probes if r not in bus.pins]
        if missing:
            out.append(
                Finding(
                    "PF032",
                    WARNING,
                    f"{bus.name}: probes named for pin roles the bus does not use",
                    [f"{bus.name}.{r}" for r in missing],
                )
            )

    for u in cfg.uart:
        if u.parity not in PARITIES:
            out.append(
                Finding(
                    "PF033",
                    ERROR,
                    f"{u.name}: parity {u.parity!r} is not recognised",
                    [u.name],
                    f"expected one of {', '.join(PARITIES)}",
                )
            )
        if u.baud <= 0:
            out.append(Finding("PF034", ERROR, f"{u.name}: baud must be positive", [u.name]))
    if cfg.mirror not in ("none", "x", "y"):
        out.append(
            Finding(
                "PF008",
                ERROR,
                f"fixture.mirror {cfg.mirror!r} is not recognised",
                [cfg.mirror],
                "expected none, x or y",
            )
        )
    for s in cfg.spi:
        if s.mode not in SPI_MODES:
            out.append(Finding("PF035", ERROR, f"{s.name}: SPI mode {s.mode} is not 0-3", [s.name]))
    for g in cfg.gpio:
        if g.direction not in DIRECTIONS:
            out.append(
                Finding(
                    "PF036",
                    ERROR,
                    f"{g.name}: direction {g.direction!r} is not recognised",
                    [g.name],
                    f"expected one of {', '.join(DIRECTIONS)}",
                )
            )
        if g.pull not in PULLS:
            out.append(
                Finding(
                    "PF037",
                    ERROR,
                    f"{g.name}: pull {g.pull!r} is not recognised",
                    [g.name],
                    f"expected one of {', '.join(PULLS)}",
                )
            )
    for a in cfg.adc:
        if a.divider <= 0:
            out.append(Finding("PF038", ERROR, f"{a.name}: divider must be positive", [a.name]))
    return out


def _check_against_board(cfg: FixtureConfig, board: Board) -> list[Finding]:
    """The half that keeps the firmware honest about the hardware it is talking to."""
    out: list[Finding] = []
    # Ground probes are wired to the fixture's ground plane, not to a channel, so they are not
    # something the config can or should claim.
    available = {t.signal for t in board.test_points if t.signal and not t.is_ground}
    claimed = cfg.probe_names()

    unknown = sorted(claimed - available)
    if unknown:
        out.append(
            Finding(
                "PF040",
                ERROR,
                f"{len(unknown)} probes name signals the DUT does not have",
                unknown,
                "the board has no test point for these, so the fixture cannot reach them",
            )
        )

    unclaimed = sorted(available - claimed)
    if unclaimed:
        severity = ERROR if cfg.require_all_test_points else INFO
        out.append(
            Finding(
                "PF041",
                severity,
                f"{len(unclaimed)} DUT test points are not in the config",
                unclaimed,
                "add a channel for each, or set dut.require_all_test_points to false to accept a "
                "partial fixture",
            )
        )

    for a in cfg.adc:
        if not a.probe:
            continue
        probe = next((t for t in board.test_points if t.signal == a.probe), None)
        if probe and a.nominal_v is not None:
            measured = a.nominal_v / a.divider
            if measured > cfg.logic_voltage:
                out.append(
                    Finding(
                        "PF042",
                        ERROR,
                        f"{a.name}: {a.nominal_v} V through a {a.divider}:1 divider presents "
                        f"{measured:.2f} V to the ADC",
                        [a.name],
                        f"the ADC reference is {cfg.logic_voltage} V; increase the divider",
                    )
                )
    return out


def validate(cfg: FixtureConfig, board: Board | None = None) -> list[Finding]:
    """Everything wrong with this config, worst first. An empty list means it will generate."""
    try:
        target = cfg.target
    except ValueError as err:
        return [Finding("PF001", ERROR, str(err), [cfg.mcu])]

    findings = (
        _check_names(cfg)
        + _check_pins(cfg, target)
        + _check_settings(cfg)
        + _check_module(cfg)
        + _check_probe(cfg)
    )
    if board is not None:
        findings += _check_against_board(cfg, board)
    elif cfg.dut_board:
        findings.append(
            Finding(
                "PF002",
                WARNING,
                "the DUT board was not read, so probe names are unchecked",
                [cfg.dut_board],
                "pass the board so the config can be held against it",
            )
        )

    order = {ERROR: 0, WARNING: 1, INFO: 2}
    return sorted(findings, key=lambda f: (order[f.severity], f.code))


def resolve_board(cfg: FixtureConfig, override: str | None = None) -> Board | None:
    """Read the DUT board the config points at, already in the fixture's own frame.

    The transform is applied here rather than by each caller, because a board carried around
    untransformed has fx/fy of zero -- which does not look wrong, it looks like every probe is
    at the origin, and that is a fixture with all its holes drilled in one place.
    """
    path = override or cfg.dut_board
    if not path:
        return None
    candidate = Path(path)
    if not candidate.is_absolute() and cfg.source:
        candidate = (Path(cfg.source).parent / candidate).resolve()
    if not candidate.exists():
        raise ConfigError(f"DUT board not found: {candidate}")
    return transform(read_board(str(candidate)), origin="outline", mirror=cfg.mirror)
