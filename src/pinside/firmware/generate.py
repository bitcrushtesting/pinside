"""Turn a validated fixture config into firmware that matches it.

Two kinds of file come out of here. Most are fixed -- the protocol core, the HAL, the mock, the
test harness -- and are copied from templates so they can be read, compiled and tested on their
own. Only the channel tables and a few identifiers are actually generated, which keeps the
generated surface small enough to review.

Nothing is written until the config validates against both the target microcontroller and the
DUT board. Firmware that claims a channel the fixture cannot reach is worse than no firmware.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .. import __version__
from ..board import Board
from ..checks import ERROR, Finding
from ..config import Bus, FixtureConfig, I2cBus, SpiBus, UartBus, validate

TEMPLATES = Path(__file__).parent / "templates"

PARITY = {"none": "FX_PARITY_NONE", "even": "FX_PARITY_EVEN", "odd": "FX_PARITY_ODD"}
DIRECTION = {"input": "FX_DIR_INPUT", "output": "FX_DIR_OUTPUT", "open_drain": "FX_DIR_OPEN_DRAIN"}
PULL = {"none": "FX_PULL_NONE", "up": "FX_PULL_UP", "down": "FX_PULL_DOWN"}
ROLE = {"master": "FX_ROLE_MASTER", "monitor": "FX_ROLE_MONITOR"}

# Copied verbatim; only the config tables and the test roster are generated.
VERBATIM = {
    "fixture_hal.h": "include/fixture_hal.h",
    "fixture_core.h": "include/fixture_core.h",
    "fixture_core.c": "src/fixture_core.c",
    "fixture_hal_rp2350.c": "src/fixture_hal_rp2350.c",
    "main.c": "src/main.c",
    "mock_hal.h": "test/mock_hal.h",
    "mock_hal.c": "test/mock_hal.c",
}


class GenerationError(Exception):
    """The config did not validate, so no firmware was written."""

    def __init__(self, findings: list[Finding]):
        self.findings = findings
        blocking = [f for f in findings if f.severity == ERROR]
        super().__init__(f"{len(blocking)} error(s) in the fixture config")


@dataclass
class Result:
    out_dir: Path
    files: list[Path]
    config_hash: str
    findings: list[Finding]


def config_hash(cfg: FixtureConfig) -> str:
    """A short digest of the channel map: pins, peripherals, directions, probes, guards.

    The agent compares it against fixture.info to know the board in front of it was built from
    the config in front of it -- the most common way a test rig lies to you is by being one
    revision behind. Prose fields and USB strings are deliberately excluded, so rewording a
    description does not invalidate a fixture that is still correct.
    """
    payload = {
        "name": cfg.name, "mcu": cfg.mcu, "clock_hz": cfg.clock_hz,
        "gpio": [(g.name, g.pin, g.direction, g.pull, g.active_low, g.initial, g.probe)
                 for g in cfg.gpio],
        "adc": [(a.name, a.pin, a.adc, a.divider, a.nominal_v, a.tolerance_v, a.probe)
                for a in cfg.adc],
        "uart": [(u.name, u.peripheral, u.baud, u.data_bits, u.stop_bits, u.parity,
                  sorted(u.pins.items()), u.guard, u.stream) for u in cfg.uart],
        "i2c": [(b.name, b.peripheral, b.hz, b.pullups, sorted(b.pins.items()), b.guard)
                for b in cfg.i2c],
        "spi": [(s.name, s.peripheral, s.hz, s.mode, s.role, sorted(s.pins.items()), s.guard)
                for s in cfg.spi],
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()[:12]


# --------------------------------------------------------------------------- C emitters


def c_string(value: str | None) -> str:
    if not value:
        return '""'
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def _guard_index(cfg: FixtureConfig, bus: Bus) -> int:
    if not bus.guard:
        return -1
    return next(i for i, g in enumerate(cfg.gpio) if g.name == bus.guard)


def _probe_summary(bus: Bus) -> str:
    return " ".join(f"{role}={bus.probes[role]}" for role in sorted(bus.probes) if bus.probes[role])


def _pin(bus: Bus, role: str) -> str:
    return str(bus.pins[role]) if role in bus.pins else "FX_PIN_NONE"


def _milli(value: float) -> int:
    return int(round(value * 1000))


def _table(ctype: str, name: str, rows: list[str]) -> list[str]:
    """One channel table plus its count.

    C has no zero-length array, so a config with (say) no SPI bus still needs something to link
    against. A single zeroed element with a count of 0 does that without the rest of the firmware
    having to special-case an absent table.
    """
    if not rows:
        return ["const " + ctype + " " + name + "[1] = {{0}};  /* none configured */",
                "const size_t " + name + "_count = 0;", ""]
    return ["const " + ctype + " " + name + "[] = {", *rows, "};",
            "const size_t " + name + "_count = " + str(len(rows)) + ";", ""]


def emit_config_c(cfg: FixtureConfig, digest: str, board_path: str) -> str:
    lines = [
        "/* The channel tables. Generated by pinside from the fixture config -- do not edit.",
        " *",
        f" * fixture:    {cfg.name}",
        f" * target:     {cfg.mcu}",
        f" * DUT board:  {board_path or '(none given)'}",
        f" * config:     {digest}",
        " */",
        "",
        '#include "fixture_config.h"',
        "",
        '#include "fixture_hal.h"',
        "",
        f"const char fx_fixture_name[] = {c_string(cfg.name)};",
        f"const char fx_fixture_version[] = {c_string(__version__)};",
        f"const char fx_target_mcu[] = {c_string(cfg.mcu)};",
        f"const char fx_dut_board[] = {c_string(board_path)};",
        f"const char fx_config_hash[] = {c_string(digest)};",
        f"const uint32_t fx_clock_hz = {cfg.clock_hz}u;",
        "",
    ]

    lines += _table("fx_gpio_desc", "fx_gpio", [
        f"    {{{c_string(g.name)}, {c_string(g.probe)}, {c_string(g.description)}, "
        f"{g.pin}, {DIRECTION[g.direction]}, {PULL[g.pull]}, "
        f"{'true' if g.active_low else 'false'}, "
        f"{'true' if g.initial == 'asserted' else 'false'}}},"
        for g in cfg.gpio])

    lines += _table("fx_adc_desc", "fx_adc", [
        f"    {{{c_string(a.name)}, {c_string(a.probe)}, {c_string(a.description)}, "
        f"{a.pin}, {a.adc}, {_milli(a.divider)}, "
        f"{_milli(a.nominal_v) if a.nominal_v is not None else -1}, "
        f"{_milli(a.tolerance_v) if a.tolerance_v is not None else 0}}},"
        for a in cfg.adc])

    lines += _table("fx_uart_desc", "fx_uart", [
        f"    {{{c_string(u.name)}, {c_string(u.description)}, "
        f"{c_string(_probe_summary(u))}, {u.peripheral}, {u.baud}u, "
        f"{u.data_bits}, {u.stop_bits}, {PARITY[u.parity]}, "
        f"{'true' if u.stream else 'false'}, {_guard_index(cfg, u)}, "
        f"{_pin(u, 'tx')}, {_pin(u, 'rx')}, {_pin(u, 'cts')}, {_pin(u, 'rts')}}},"
        for u in cfg.uart])

    lines += _table("fx_i2c_desc", "fx_i2c", [
        f"    {{{c_string(b.name)}, {c_string(b.description)}, "
        f"{c_string(_probe_summary(b))}, {b.peripheral}, {b.hz}u, "
        f"{'true' if b.pullups else 'false'}, {_guard_index(cfg, b)}, "
        f"{_pin(b, 'sda')}, {_pin(b, 'scl')}}},"
        for b in cfg.i2c])

    lines += _table("fx_spi_desc", "fx_spi", [
        f"    {{{c_string(s.name)}, {c_string(s.description)}, "
        f"{c_string(_probe_summary(s))}, {s.peripheral}, {s.hz}u, {s.mode}, "
        f"{ROLE[s.role]}, {_guard_index(cfg, s)}, "
        f"{_pin(s, 'rx')}, {_pin(s, 'cs')}, {_pin(s, 'sck')}, {_pin(s, 'tx')}}},"
        for s in cfg.spi])

    return "\n".join(lines) + "\n"


def emit_roster_assertions(cfg: FixtureConfig) -> str:
    """Assertions naming the exact channels this config declares, so drift is a test failure."""
    lines = [
        f"  check(fx_gpio_count == {len(cfg.gpio)}, "
        f'"the config declares {len(cfg.gpio)} gpio channels");',
        f"  check(fx_adc_count == {len(cfg.adc)}, "
        f'"the config declares {len(cfg.adc)} adc channels");',
        f"  check(fx_uart_count == {len(cfg.uart)}, "
        f'"the config declares {len(cfg.uart)} uart buses");',
        f"  check(fx_i2c_count == {len(cfg.i2c)}, "
        f'"the config declares {len(cfg.i2c)} i2c buses");',
        f"  check(fx_spi_count == {len(cfg.spi)}, "
        f'"the config declares {len(cfg.spi)} spi buses");',
    ]
    for i, g in enumerate(cfg.gpio):
        lines.append(f'  check(strcmp(fx_gpio[{i}].name, {c_string(g.name)}) == 0 && '
                     f'fx_gpio[{i}].pin == {g.pin}, '
                     f'"{g.name} is on GPIO{g.pin}");')
    for i, a in enumerate(cfg.adc):
        lines.append(f'  check(strcmp(fx_adc[{i}].name, {c_string(a.name)}) == 0 && '
                     f'fx_adc[{i}].adc == {a.adc}, '
                     f'"{a.name} reads ADC{a.adc}");')
    for kind, items in (("uart", cfg.uart), ("i2c", cfg.i2c), ("spi", cfg.spi)):
        for i, b in enumerate(items):
            lines.append(f'  check(strcmp(fx_{kind}[{i}].name, {c_string(b.name)}) == 0 && '
                         f'fx_{kind}[{i}].index == {b.peripheral}, '
                         f'"{b.name} is {kind}{b.peripheral}");')
    return "\n".join(lines)


# --------------------------------------------------------------------------- other outputs


def emit_openrpc(cfg: FixtureConfig, digest: str) -> dict:
    """The agent-facing contract, in the same OpenRPC shape the rest of the family uses."""

    def method(name, summary, params, result, description=""):
        return {"name": name, "summary": summary, "description": description,
                "params": params, "result": {"name": "result", "schema": result}}

    def param(name, schema, required=True, description=""):
        return {"name": name, "required": required, "description": description, "schema": schema}

    STR = {"type": "string"}
    INT = {"type": "integer"}
    BOOL = {"type": "boolean"}
    HEX = {"type": "string", "pattern": "^([0-9a-fA-F]{2})*$"}

    channels = [{"name": ch.name, "kind": ch.kind, "probe": getattr(ch, "probe", "")}
                for ch in cfg.channels]

    return {
        "openrpc": "1.2.6",
        "info": {
            "title": f"{cfg.name} fixture",
            "version": __version__,
            "description":
                f"{cfg.description or 'Bed-of-nails fixture'}\n\n"
                f"JSON-RPC 2.0 over USB CDC, one request per line. The fixture also sends "
                f"unsolicited notifications: fixture.ready once at start-up, and uart.data for "
                f"every bus configured to stream.\n\n"
                f"Config hash {digest}. Compare it against fixture.info before trusting a run.",
        },
        "x-pinside": {"config_hash": digest, "mcu": cfg.mcu, "dut_board": cfg.dut_board,
                      "channels": channels},
        "methods": [
            method("fixture.info", "Identify the fixture",
                   [], {"type": "object"},
                   "Returns the fixture name, firmware version, target, DUT board and the "
                   "config hash the firmware was generated from."),
            method("fixture.channels", "List every channel and the DUT signal it probes",
                   [], {"type": "array", "items": {"type": "object"}}),
            method("gpio.read", "Read one GPIO channel",
                   [param("channel", STR)], {"type": "object"},
                   "Reports both the electrical level and whether that means asserted, which "
                   "differ on an active-low line."),
            method("gpio.write", "Drive or release one GPIO channel",
                   [param("channel", STR), param("assert", BOOL)], {"type": "object"},
                   "An open-drain channel is released to high-Z rather than driven high, so the "
                   "fixture never fights a pull-up on the DUT."),
            method("gpio.snapshot", "Read every GPIO channel at once",
                   [], {"type": "array", "items": {"type": "object"}}),
            method("adc.read", "Read one analogue channel",
                   [param("channel", STR)], {"type": "object"},
                   "millivolts is at the DUT, with the fixture's divider ratio already applied."),
            method("adc.snapshot", "Read every analogue channel at once",
                   [], {"type": "array", "items": {"type": "object"}}),
            method("uart.write", "Transmit on a UART channel",
                   [param("channel", STR), param("hex", HEX)], {"type": "object"}),
            method("uart.read", "Drain a UART channel's receive buffer",
                   [param("channel", STR), param("max", INT, required=False)],
                   {"type": "object"}),
            method("uart.configure", "Change a UART channel's baud rate",
                   [param("channel", STR), param("baud", INT)], {"type": "object"}),
            method("i2c.scan", "Find the devices answering on an I2C channel",
                   [param("channel", STR)], {"type": "object"},
                   "Probes 0x08-0x77; the addresses either side are reserved."),
            method("i2c.write", "Write to an I2C device",
                   [param("channel", STR), param("address", INT), param("hex", HEX)],
                   {"type": "object"}),
            method("i2c.read", "Read from an I2C device",
                   [param("channel", STR), param("address", INT), param("length", INT)],
                   {"type": "object"}),
            method("spi.transfer", "Clock bytes out of and into a SPI channel",
                   [param("channel", STR), param("hex", HEX)], {"type": "object"}),
        ],
        "x-notifications": [
            {"name": "fixture.ready",
             "summary": "Sent once when the fixture has configured its pins"},
            {"name": "uart.data",
             "summary": "Bytes received on a streaming UART channel, pushed without being asked"},
        ],
    }


def emit_cmakelists(cfg: FixtureConfig) -> str:
    board = "pico2" if cfg.mcu.startswith(("rp2350", "rp2354")) else "pico"
    platform = "rp2350" if cfg.mcu.startswith(("rp2350", "rp2354")) else "rp2040"
    usb = cfg.usb or {}
    naming = ""
    if usb.get("product"):
        naming += f'pico_set_program_name(${{PROJECT_NAME}} "{usb["product"]}")\n'
    if usb.get("manufacturer"):
        naming += (f'pico_set_program_description(${{PROJECT_NAME}} '
                   f'"{usb["manufacturer"]} bed-of-nails fixture")\n')

    return f"""# Generated by pinside from the {cfg.name} fixture config -- do not edit.
#
#   cmake -B build -DPICO_SDK_PATH=/path/to/pico-sdk
#   cmake --build build
#
# The host tests need none of this: see test/run.sh.

cmake_minimum_required(VERSION 3.13)

set(PICO_BOARD {board} CACHE STRING "Board type")
set(PICO_PLATFORM {platform} CACHE STRING "Platform")

if(DEFINED ENV{{PICO_SDK_PATH}} AND NOT DEFINED PICO_SDK_PATH)
    set(PICO_SDK_PATH $ENV{{PICO_SDK_PATH}})
endif()
if(NOT PICO_SDK_PATH)
    message(FATAL_ERROR "Set PICO_SDK_PATH, on the command line or in the environment.")
endif()
# The SDK ships its own importer; vendoring a copy only lets it go stale.
include(${{PICO_SDK_PATH}}/external/pico_sdk_import.cmake)

project({cfg.name.replace('-', '_')} C CXX ASM)
set(CMAKE_C_STANDARD 11)

pico_sdk_init()

add_executable(${{PROJECT_NAME}}
    src/main.c
    src/fixture_core.c
    src/fixture_config.c
    src/fixture_hal_rp2350.c
)

target_include_directories(${{PROJECT_NAME}} PRIVATE include)

target_link_libraries(${{PROJECT_NAME}}
    pico_stdlib
    hardware_adc
    hardware_gpio
    hardware_i2c
    hardware_spi
    hardware_uart
)

target_compile_options(${{PROJECT_NAME}} PRIVATE -Wall -Wextra)

# The protocol owns the CDC line; nothing else may write to it.
pico_enable_stdio_usb(${{PROJECT_NAME}} 1)
pico_enable_stdio_uart(${{PROJECT_NAME}} 0)
{naming}
pico_add_extra_outputs(${{PROJECT_NAME}})
"""


def emit_run_sh(cfg: FixtureConfig) -> str:
    return f"""#!/usr/bin/env bash
# Host tests for the {cfg.name} fixture firmware. No board, no SDK, no USB.
#
# The protocol core is deliberately free of SDK headers so it can be built against the mock HAL
# and exercised here, which is the only place the JSON layer and the channel tables get checked
# before they reach hardware.

set -euo pipefail

here="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
root="$(cd "$here/.." && pwd)"
out="${{TMPDIR:-/tmp}}/{cfg.name}-host-tests"

mkdir -p "$out"

"${{CC:-cc}}" -std=c11 -Wall -Wextra -Werror -g -fsanitize=address,undefined \\
    -I "$root/include" -I "$here" \\
    -o "$out/test_core" \\
    "$here/test_core.c" \\
    "$here/mock_hal.c" \\
    "$root/src/fixture_core.c" \\
    "$root/src/fixture_config.c"

"$out/test_core" "$@"
"""


def emit_readme(cfg: FixtureConfig, digest: str, board_path: str) -> str:
    def bus_rows(items, extra):
        return "\n".join(
            f"| `{b.name}` | {b.kind}{b.peripheral} | "
            f"{', '.join(f'{r}=GPIO{p}' for r, p in sorted(b.pins.items()))} | "
            f"{_probe_summary(b) or '-'} | {extra(b)} |" for b in items)

    return f"""# {cfg.name} firmware

{cfg.description or 'Bed-of-nails fixture firmware.'}

Generated by pinside {__version__} from `{Path(cfg.source).name if cfg.source else 'the fixture config'}`,
checked against `{board_path or '(no board given)'}`.

**Config hash `{digest}`.** `fixture.info` reports it. If it does not match the config you are
holding, the board in front of you was built from something else.

## Build

```bash
cmake -B build -DPICO_SDK_PATH=/path/to/pico-sdk
cmake --build build
```

Hold BOOTSEL, plug the fixture in, and copy `build/{cfg.name.replace('-', '_')}.uf2` onto it.

## Test, without hardware

```bash
test/run.sh
```

Builds the protocol core and the generated channel tables against a mock HAL, with the address
and undefined-behaviour sanitizers on. This is where a mis-scaled ADC reading or a guard that
does not hold gets caught; on the bench both look like a hardware fault.

## Talking to it

JSON-RPC 2.0 over USB CDC, one request per line, one response per line.

```bash
printf '{{"jsonrpc":"2.0","id":1,"method":"fixture.channels"}}\\n' > /dev/tty.usbmodemXXXX
```

`openrpc.json` is the full contract. Start with `fixture.info` and `fixture.channels`: between
them they say which DUT signal every channel lands on, so an agent needs no other map.

Two notifications arrive unasked: `fixture.ready` once at start-up, and `uart.data` for every
channel configured to stream.

## Channels

### GPIO

| Channel | Pin | Direction | Probes | Notes |
|---|---|---|---|---|
{chr(10).join(f"| `{g.name}` | GPIO{g.pin} | {g.direction}{', active low' if g.active_low else ''} | {g.probe or '-'} | {g.description or ''} |" for g in cfg.gpio) or '| _none_ | | | | |'}

### Analogue

| Channel | Pin | ADC | Divider | Probes | Expected |
|---|---|---|---|---|---|
{chr(10).join(f"| `{a.name}` | GPIO{a.pin} | ADC{a.adc} | {a.divider}:1 | {a.probe or '-'} | {f'{a.nominal_v} V' if a.nominal_v is not None else '-'} |" for a in cfg.adc) or '| _none_ | | | | | |'}

### Buses

| Channel | Peripheral | Pins | Probes | Settings |
|---|---|---|---|---|
{bus_rows(cfg.uart, lambda b: f"{b.baud} baud{', streaming' if b.stream else ''}"
          + (f", guarded by `{b.guard}`" if b.guard else "")) or ''}
{bus_rows(cfg.i2c, lambda b: f"{b.hz} Hz" + (f", guarded by `{b.guard}`" if b.guard else "")) or ''}
{bus_rows(cfg.spi, lambda b: f"{b.hz} Hz, mode {b.mode}, {b.role}"
          + (f", guarded by `{b.guard}`" if b.guard else "")) or ''}

## Guards

A bus whose lines the DUT also drives is declared with a `guard`: the fixture refuses to master
it until that GPIO channel is asserted. It is an interlock, not a substitute for the series
resistors on the fixture board -- but it stops the ordinary mistake, which is an agent probing
an Ethernet bus while the DUT's own controller is mid-transaction.

## Regenerating

```bash
pinside generate {Path(cfg.source).name if cfg.source else 'fixture.json'} --out .
```

Everything here is overwritten. Change the config, not the firmware.
"""


# --------------------------------------------------------------------------- driver


def generate(cfg: FixtureConfig, board: Board | None, out_dir: str | Path,
             force: bool = False) -> Result:
    findings = validate(cfg, board)
    if any(f.severity == ERROR for f in findings):
        raise GenerationError(findings)

    out = Path(out_dir)
    if out.exists() and any(out.iterdir()) and not force:
        stamp = out / "src" / "fixture_config.c"
        if not stamp.exists():
            raise GenerationError([Finding(
                "PF003", ERROR, f"{out} is not empty and was not generated by pinside",
                [str(out)], "pass --force to write into it anyway")])

    digest = config_hash(cfg)
    board_path = cfg.dut_board
    written: list[Path] = []

    def write(relative: str, text: str, executable: bool = False) -> None:
        path = out / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if executable:
            path.chmod(0o755)
        written.append(path)

    substitutions = {
        "@@CONFIG_SOURCE@@": Path(cfg.source).name if cfg.source else "the fixture config",
        "@@DUT_BOARD@@": board_path or "(no board given)",
        "@@PINSIDE_VERSION@@": __version__,
        "@@GENERATED_AT@@": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "@@NAME_MAX@@": str(max(32, max((len(c.name) for c in cfg.channels), default=0) + 1)),
        "@@GPIO_COUNT@@": str(len(cfg.gpio)),
        "@@ADC_COUNT@@": str(len(cfg.adc)),
        "@@UART_COUNT@@": str(len(cfg.uart)),
        "@@I2C_COUNT@@": str(len(cfg.i2c)),
        "@@SPI_COUNT@@": str(len(cfg.spi)),
    }

    header = (TEMPLATES / "fixture_config.h").read_text(encoding="utf-8")
    for marker, value in substitutions.items():
        header = header.replace(marker, value)
    write("include/fixture_config.h", header)

    for name, destination in VERBATIM.items():
        write(destination, (TEMPLATES / name).read_text(encoding="utf-8"))

    test = (TEMPLATES / "test_core.c").read_text(encoding="utf-8")
    test = test.replace("@@ROSTER_ASSERTIONS@@", emit_roster_assertions(cfg))
    write("test/test_core.c", test)

    write("src/fixture_config.c", emit_config_c(cfg, digest, board_path))
    write("CMakeLists.txt", emit_cmakelists(cfg))
    write("test/run.sh", emit_run_sh(cfg), executable=True)
    write("openrpc.json", json.dumps(emit_openrpc(cfg, digest), indent=2) + "\n")
    write("README.md", emit_readme(cfg, digest, board_path))

    return Result(out_dir=out, files=written, config_hash=digest, findings=findings)
