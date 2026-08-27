"""Carrier modules: the ready-made boards a fixture can be built around.

A fixture does not have to carry a bare microcontroller. Soldering a Pico onto a carrier costs
one part and no support circuitry -- no crystal, no flash, no USB connector, no regulator -- and
the module can be unplugged and replaced when a probe shorts something. For a fixture, which is
a tool rather than a product, that is usually the right trade.

The catch is that a module exposes only some of its chip's pins. A Pico 2 carries an RP2350A with
30 GPIO and brings 26 of them to the header, so a config that fits the chip can still not fit the
board. That is worth catching in the tool rather than in the layout.

Every pinout here is taken from KiCad's own symbol library, which is the same authority the
generated schematic will use -- so the numbering in a generated project cannot disagree with the
numbering these checks were made against.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import targets


@dataclass(frozen=True)
class Module:
    """A carrier board, and which of its chip's pins reach the outside world."""

    name: str
    description: str
    mcu: str
    symbol: str
    footprint: str
    # GPIO number -> header pin number. The keys are the whole story: a GPIO absent from this
    # map exists on the chip but not on the board.
    header: dict[int, int]
    power_pins: dict[str, list[int]] = field(default_factory=dict)
    # Header pins carrying something other than a GPIO or a rail, by pin number.
    special: dict[int, str] = field(default_factory=dict)
    width_mm: float = 21.0
    length_mm: float = 51.0
    note: str = ""

    @property
    def target(self) -> targets.Target:
        return targets.get(self.mcu)

    @property
    def gpios(self) -> set[int]:
        return set(self.header)

    def exposes(self, gpio: int) -> bool:
        return gpio in self.header

    def pin_of(self, gpio: int) -> int | None:
        return self.header.get(gpio)

    def adc_gpios(self) -> dict[int, int]:
        """The ADC-capable GPIOs this module actually brings out."""
        return {g: c for g, c in self.target.adc_pins.items() if self.exposes(g)}

    def unexposed(self, gpios: list[int]) -> list[int]:
        return sorted({g for g in gpios if not self.exposes(g)})


def _pico_header() -> dict[int, int]:
    """The 40-pin Pico header, GPIO number -> physical pin.

    Read out of KiCad's MCU_Module:RaspberryPi_Pico symbol. GPIO23, 24, 25 and 29 are used on the
    module itself (regulator mode, VBUS sense, LED, VSYS sense) and never reach the header.
    """
    header = {}
    # GPIO0..GPIO22, interrupted by a ground pin every fifth position.
    pin = 1
    for gpio in range(23):
        while pin in (3, 8, 13, 18, 23, 28):  # GND
            pin += 1
        header[gpio] = pin
        pin += 1
    # The analogue trio sits past RUN on the other side of the board.
    header[26], header[27], header[28] = 31, 32, 34
    return header


_PICO_POWER = {"GND": [3, 8, 13, 18, 23, 28, 38], "3V3": [36], "VSYS": [39], "VBUS": [40]}
_PICO_SPECIAL = {30: "RUN", 33: "AGND", 35: "ADC_VREF", 37: "3V3_EN"}

_PICO_NOTE = (
    "GPIO23/24/25/29 are consumed by the module (regulator mode, VBUS sense, LED, VSYS sense) "
    "and are not on the header."
)

MODULES: dict[str, Module] = {
    "pico2": Module(
        name="pico2",
        description="Raspberry Pi Pico 2 (RP2350A), 26 GPIO on a 40-pin 0.1in header",
        mcu="rp2350a",
        symbol="MCU_Module:RaspberryPi_Pico",
        footprint="Module:RaspberryPi_Pico_Common_THT",
        header=_pico_header(),
        power_pins=_PICO_POWER,
        special=_PICO_SPECIAL,
        note=_PICO_NOTE,
    ),
    "pico2w": Module(
        name="pico2w",
        description="Raspberry Pi Pico 2 W (RP2350A + wireless), same header as the Pico 2",
        mcu="rp2350a",
        symbol="MCU_Module:RaspberryPi_Pico_W",
        footprint="Module:RaspberryPi_Pico_Common_THT",
        header=_pico_header(),
        power_pins=_PICO_POWER,
        special=_PICO_SPECIAL,
        note=_PICO_NOTE,
    ),
}

# A fixture large enough to need more than 26 channels has to carry the chip itself. Naming that
# here keeps the "no module" case a deliberate choice rather than an omission.
BARE = "bare"

DEFAULT = "pico2"


def get(name: str) -> Module | None:
    """The named module, or None for a bare chip on the fixture board."""
    if not name or name.lower() == BARE:
        return None
    try:
        return MODULES[name.lower()]
    except KeyError:
        known = ", ".join([*sorted(MODULES), BARE])
        raise ValueError(f"unknown module {name!r}; known modules: {known}") from None
