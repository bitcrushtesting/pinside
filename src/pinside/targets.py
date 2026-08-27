"""What a microcontroller can actually do with each of its pins.

The point of holding this in the tool is that a fixture config can be checked before a line of
firmware is emitted. "SDA on GPIO9" looks perfectly reasonable in a JSON file and silently does
not work on an RP2350, because I2C0's data line only exists on even-numbered pins. Catching that
here costs nothing; catching it on the bench costs an afternoon.

The RP2350 function map is periodic, which is why it fits in four expressions rather than a
48-row table. It was checked against the Pico SDK's own GPIO function listing:

    GPIO0  F1 SPI0 RX   F2 UART0 TX   F3 I2C0 SDA
    GPIO1  F1 SPI0 CSn  F2 UART0 RX   F3 I2C0 SCL
    GPIO2  F1 SPI0 SCK  F2 UART0 CTS  F3 I2C1 SDA
    GPIO3  F1 SPI0 TX   F2 UART0 RTS  F3 I2C1 SCL
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Sub-function within a group of four, for both SPI and UART.
_SPI_ROLE = {0: "rx", 1: "cs", 2: "sck", 3: "tx"}
_UART_ROLE = {0: "tx", 1: "rx", 2: "cts", 3: "rts"}

# SPI's RX/TX pins are named MISO/MOSI everywhere else, so accept both spellings.
SPI_ALIASES = {"miso": "rx", "mosi": "tx", "sclk": "sck", "clk": "sck", "csn": "cs", "ss": "cs"}


@dataclass(frozen=True)
class Target:
    """One microcontroller variant."""

    name: str
    description: str
    gpio_count: int
    adc_pins: dict[int, int]          # gpio -> ADC channel number
    uart_count: int = 2
    spi_count: int = 2
    i2c_count: int = 2
    pio_count: int = 3
    sdk: str = "pico-sdk"
    default_clock_hz: int = 150_000_000
    reserved: dict[int, str] = field(default_factory=dict)

    # -- pin capability ----------------------------------------------------

    def has_pin(self, gpio: int) -> bool:
        return 0 <= gpio < self.gpio_count

    def uart_of(self, gpio: int) -> tuple[int, str] | None:
        """(instance, role) this pin can serve on a UART, or None."""
        if not self.has_pin(gpio):
            return None
        instance = 0 if (gpio // 4) % 4 in (0, 3) else 1
        return instance, _UART_ROLE[gpio % 4]

    def spi_of(self, gpio: int) -> tuple[int, str] | None:
        if not self.has_pin(gpio):
            return None
        return (gpio // 8) % 2, _SPI_ROLE[gpio % 4]

    def i2c_of(self, gpio: int) -> tuple[int, str] | None:
        if not self.has_pin(gpio):
            return None
        return (gpio // 2) % 2, ("sda" if gpio % 2 == 0 else "scl")

    def adc_of(self, gpio: int) -> int | None:
        return self.adc_pins.get(gpio)

    def pins_for(self, kind: str, instance: int, role: str) -> list[int]:
        """Every pin that could carry this role -- used to make errors actionable."""
        role = SPI_ALIASES.get(role, role) if kind == "spi" else role
        lookup = {"uart": self.uart_of, "spi": self.spi_of, "i2c": self.i2c_of}[kind]
        return [g for g in range(self.gpio_count) if lookup(g) == (instance, role)]


def _rp2350_adc(first_gpio: int, channels: int) -> dict[int, int]:
    return {first_gpio + i: i for i in range(channels)}


TARGETS: dict[str, Target] = {
    "rp2350b": Target(
        name="rp2350b",
        description="Raspberry Pi RP2350B, QFN-80, 48 GPIO, 8 ADC inputs, 3 PIO blocks",
        gpio_count=48,
        adc_pins=_rp2350_adc(40, 8),
    ),
    "rp2350a": Target(
        name="rp2350a",
        description="Raspberry Pi RP2350A, QFN-60, 30 GPIO, 4 ADC inputs, 3 PIO blocks",
        gpio_count=30,
        adc_pins=_rp2350_adc(26, 4),
    ),
    "rp2354b": Target(
        name="rp2354b",
        description="Raspberry Pi RP2354B, QFN-80 with 2 MB stacked flash; RP2350B pinout",
        gpio_count=48,
        adc_pins=_rp2350_adc(40, 8),
    ),
}


def get(name: str) -> Target:
    try:
        return TARGETS[name.lower()]
    except KeyError:
        known = ", ".join(sorted(TARGETS))
        raise ValueError(f"unknown target {name!r}; known targets: {known}") from None
