"""Draft a fixture config from a DUT board, so nobody starts from a blank file.

What comes out is a starting point, not an answer: the signal names decide which bus a probe
joins, and names lie sometimes. But it gets the two things right that are tedious and easy to get
wrong -- legal pin assignments for the target, and the direction inversion between the two sides
of a UART.

That inversion is the usual first bug in a fixture. A net called DUT_TXD is an output *of the
DUT*, so it has to land on the fixture's receiver. Wire it to the fixture's TX because both are
called TXD and nothing works, with no error to explain why.
"""

from __future__ import annotations

import re
from collections import OrderedDict

from . import targets
from .board import Board

# Signal-name suffix -> the fixture pin role that must face it. Longest match wins.
UART_FACING = OrderedDict(
    [
        ("TXD", "rx"),
        ("TX", "rx"),  # the DUT transmits; the fixture listens
        ("RXD", "tx"),
        ("RX", "tx"),
        ("RTS", "cts"),
        ("CTS", "rts"),  # flow control crosses over the same way
    ]
)

SPI_FACING = OrderedDict(
    [
        ("MISO", "rx"),
        ("MOSI", "tx"),
        ("SCLK", "sck"),
        ("CLK", "sck"),
        ("SCK", "sck"),
        ("CS", "cs"),
        ("SS", "cs"),
        ("NSS", "cs"),
    ]
)

I2C_FACING = OrderedDict([("SDA", "sda"), ("SCL", "scl")])

POWER = re.compile(r"^\+?\d|^(VCC|VDD|VBUS|\+?\d?V\d?|VBAT)", re.I)


def _facing(signal: str, table: OrderedDict) -> str | None:
    upper = signal.upper()
    for suffix, role in table.items():
        if upper.endswith("_" + suffix) or upper == suffix:
            return role
    return None


class _Allocator:
    """Hands out pins that the target can actually use for the role being asked for."""

    def __init__(self, target: targets.Target):
        self.target = target
        self.taken: set[int] = set()

    def take(self, kind: str, instance: int, role: str) -> int | None:
        for pin in self.target.pins_for(kind, instance, role):
            if pin not in self.taken and self.target.adc_of(pin) is None:
                self.taken.add(pin)
                return pin
        return None

    def take_plain(self) -> int | None:
        """Any free pin with no analogue duty, for a bare GPIO."""
        for pin in range(self.target.gpio_count):
            if pin not in self.taken and self.target.adc_of(pin) is None:
                self.taken.add(pin)
                return pin
        return None

    def take_adc(self) -> tuple[int, int] | None:
        for pin, channel in sorted(self.target.adc_pins.items()):
            if pin not in self.taken:
                self.taken.add(pin)
                return pin, channel
        return None


def _group_signals(board: Board) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for tp in board.test_points:
        if not tp.signal or tp.is_ground:
            continue
        groups.setdefault(tp.bus, []).append(tp.signal)
    return groups


def _bus_prefix(signals: list[str]) -> str:
    """The common leading token of a group, lowercased -- ETH_CS and ETH_CLK give 'eth'."""
    heads = {s.split("_")[0].lower() for s in signals if "_" in s}
    return heads.pop() if len(heads) == 1 else ""


def scaffold(board: Board, name: str, mcu: str = "rp2350b", board_path: str = "") -> dict:
    target = targets.get(mcu)
    alloc = _Allocator(target)
    groups = _group_signals(board)

    config: dict = {
        "name": name,
        "description": f"Bed-of-nails fixture for {board_path or board.source}",
        "target": {
            "mcu": mcu,
            "clock_hz": target.default_clock_hz,
            "usb": {"product": f"{name} fixture"},
        },
        "dut": {
            "board": board_path or board.source,
            "logic_voltage": 3.3,
            "require_all_test_points": True,
        },
        "uart": [],
        "i2c": [],
        "spi": [],
        "gpio": [],
        "adc": [],
    }

    claimed: set[str] = set()

    def claim(signals: list[str]) -> None:
        claimed.update(signals)

    # --- UARTs. Peripheral instances are handed out in the order the groups appear.
    uart_instance = 0
    for group, signals in sorted(groups.items()):
        if not group.startswith("uart"):
            continue
        pins, probes = {}, {}
        for signal in sorted(signals):
            role = _facing(signal, UART_FACING)
            if role is None:
                continue
            pin = alloc.take("uart", uart_instance, role)
            if pin is None:
                continue
            pins[role] = pin
            probes[role] = signal
        if not pins:
            continue
        prefix = _bus_prefix(signals) or "dut"
        config["uart"].append(
            {
                "name": f"{prefix}_uart",
                "description": f"{prefix.upper()} serial link",
                "peripheral": uart_instance,
                "baud": 115200,
                "pins": pins,
                "probes": probes,
                "stream": True,
            }
        )
        claim([s for s in signals if _facing(s, UART_FACING)])
        uart_instance = (uart_instance + 1) % target.uart_count

    # --- I2C.
    i2c_instance = 0
    for group, signals in sorted(groups.items()):
        if group != "i2c":
            continue
        pins, probes = {}, {}
        for signal in sorted(signals):
            role = _facing(signal, I2C_FACING)
            if role is None:
                continue
            pin = alloc.take("i2c", i2c_instance, role)
            if pin is None:
                continue
            pins[role] = pin
            probes[role] = signal
        if len(pins) < 2:
            continue
        prefix = _bus_prefix(signals) or "dut"
        config["i2c"].append(
            {
                "name": f"{prefix}_i2c",
                "description": f"{prefix.upper()} two-wire bus",
                "peripheral": i2c_instance,
                "hz": 400000,
                "pins": pins,
                "probes": probes,
                "pullups": False,
            }
        )
        claim([s for s in signals if _facing(s, I2C_FACING)])
        i2c_instance = (i2c_instance + 1) % target.i2c_count

    # --- SPI. A group with no clock of its own is not a bus the fixture can master; its lines
    #     become plain GPIO below.
    spi_instance = 0
    for group, signals in sorted(groups.items()):
        if not group.startswith("spi"):
            continue
        roles = {s: _facing(s, SPI_FACING) for s in sorted(signals)}
        if "sck" not in roles.values():
            continue
        pins, probes = {}, {}
        for signal, role in roles.items():
            if role is None or role in pins:
                continue
            pin = alloc.take("spi", spi_instance, role)
            if pin is None:
                continue
            pins[role] = pin
            probes[role] = signal
        if "sck" not in pins:
            continue
        prefix = _bus_prefix(signals) or "dut"
        config["spi"].append(
            {
                "name": f"{prefix}_spi",
                "description": f"{prefix.upper()} SPI bus",
                "peripheral": spi_instance,
                "hz": 1000000,
                "mode": 0,
                "role": "master",
                "pins": pins,
                "probes": probes,
            }
        )
        claim([s for s, r in roles.items() if r and r in probes and probes[r] == s])
        spi_instance = (spi_instance + 1) % target.spi_count

    # --- Everything left over. Rails go to the ADC, the rest to GPIO.
    for tp in board.test_points:
        signal = tp.signal
        if not signal or tp.is_ground or signal in claimed:
            continue
        claimed.add(signal)
        identifier = re.sub(r"[^a-z0-9]+", "_", signal.lower()).strip("_") or "unnamed"
        if not identifier[0].isalpha():
            identifier = "ch_" + identifier

        if POWER.match(signal):
            got = alloc.take_adc()
            if got is None:
                continue
            pin, _adc_channel = got
            volts = 3.3
            match = re.search(r"(\d+)[.,v](\d+)", signal, re.I)
            if match:
                volts = float(f"{match.group(1)}.{match.group(2)}")
            config["adc"].append(
                {
                    "name": identifier,
                    "description": f"{signal} rail, through a 2:1 divider",
                    "pin": pin,
                    "probe": signal,
                    "divider": 2.0,
                    "nominal_v": volts,
                    "tolerance_v": round(volts * 0.05, 3),
                }
            )
            continue

        pin = alloc.take_plain()
        if pin is None:
            continue
        # A fault or status line the DUT drives must stay an input, or the fixture fights it.
        driven_by_dut = bool(re.search(r"(_FLT|_FAULT|_INT|_BUSY|_STAT)$", signal, re.I))
        entry = {
            "name": identifier,
            "description": signal,
            "pin": pin,
            "probe": signal,
            "direction": "input" if driven_by_dut else "open_drain",
        }
        if not driven_by_dut:
            # Open drain plus active-low is the safe default for anything the fixture may pull
            # down -- a reset, an enable, a button. Released means high-Z, not driven high.
            entry["active_low"] = True
            entry["initial"] = "released"
        config["gpio"].append(entry)

    return config
