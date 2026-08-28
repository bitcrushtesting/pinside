"""Talking to a fixture from the host.

`pinside generate` emits firmware that speaks JSON-RPC 2.0 over USB CDC and ships an
`openrpc.json` describing it. Until this module existed nothing on the host spoke that protocol,
so everyone who used a fixture wrote the same serial client by hand, and the config-hash check
the README recommends was a comparison somebody did by eye.

Two things here are not conveniences:

**The hash check is on by default.** `connect()` reads `fixture.info` and refuses to hand back a
client whose `config_hash` disagrees with the config it was given. A rig one revision behind is
the most common way a bench lies to you, and it lies quietly: every call succeeds, against the
wrong pin.

**Notifications are not responses.** A streaming UART pushes `uart.data` between replies, so a
client that reads one line per request eventually returns a log line as the answer to an ADC
read. Every line is classified before anything waits on it, and notifications go to a queue.

pyserial is an optional dependency (`pip install pinside[client]`). The core stays
dependency-free; only this module needs it, and it says so when it is missing.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .config import FixtureConfig
from .firmware.generate import config_hash

DEFAULT_BAUD = 115200
DEFAULT_TIMEOUT = 5.0

# How long to wait for `fixture.ready` before assuming the board was already running. A fixture
# announces itself once at start-up, and opening the port does not always reset it.
READY_GRACE = 2.0


class FixtureError(Exception):
    """The fixture could not be reached, or answered something that is not a valid response."""


class RpcError(FixtureError):
    """The fixture understood the call and refused it."""

    def __init__(self, code: int, message: str, data: Any = None):
        self.code = code
        self.message = message
        self.data = data
        detail = f" ({data})" if data is not None else ""
        super().__init__(f"{message}{detail} [{code}]")


class ConfigMismatchError(FixtureError):
    """The fixture is running firmware generated from a different config.

    This is the failure the config hash exists to catch. Every subsequent call would succeed and
    reach the wrong pin, so it is raised rather than warned about.
    """

    def __init__(self, expected: str, found: str, name: str = ""):
        self.expected = expected
        self.found = found
        super().__init__(
            f"the fixture on this port reports config {found!r}, but the config given here "
            f"hashes to {expected!r}"
            + (f" ({name})" if name else "")
            + ". Regenerate and reflash, or pass check_hash=False if you meant to."
        )


@dataclass
class Notification:
    """Something the fixture said without being asked."""

    method: str
    params: dict
    received: float = field(default_factory=time.monotonic)

    @property
    def channel(self) -> str:
        return self.params.get("channel", "")

    @property
    def data(self) -> bytes:
        """The payload of a `uart.data` notification, decoded from its hex."""
        return bytes.fromhex(self.params.get("hex", ""))


def _require_serial():
    try:
        import serial
    except ImportError:
        raise FixtureError(
            "talking to a fixture needs pyserial, which pinside does not install by default. "
            "Install it with: pip install 'pinside[client]'"
        ) from None
    return serial


def ports() -> list[tuple[str, str]]:
    """Every serial port the machine can see, as (device, description).

    Not filtered to fixtures: a fixture identifies itself by answering `fixture.info`, not by its
    USB descriptor, and guessing from vendor IDs would hide the board somebody actually plugged in.
    """
    serial = _require_serial()
    from serial.tools import list_ports

    del serial
    return [(p.device, p.description or "") for p in list_ports.comports()]


class Fixture:
    """One open connection to a fixture. Use `connect()` rather than constructing this."""

    def __init__(self, port, timeout: float = DEFAULT_TIMEOUT):
        self._port = port
        self._timeout = timeout
        self._next_id = 1
        self._lock = threading.Lock()
        self.notifications: deque[Notification] = deque(maxlen=1000)
        self.info: dict = {}

    # ---------------------------------------------------------------- plumbing

    def _read_line(self, deadline: float) -> dict:
        """One JSON object off the wire, or a timeout."""
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FixtureError(f"the fixture did not answer within {self._timeout:g}s")
            self._port.timeout = remaining
            raw = self._port.readline()
            if not raw:
                continue
            text = raw.decode("utf-8", "replace").strip()
            if not text:
                continue
            try:
                message = json.loads(text)
            except json.JSONDecodeError:
                # Not fatal. A board that was mid-line when the port opened, or one printing
                # boot chatter before the protocol starts, produces exactly this.
                continue
            if isinstance(message, dict):
                return message

    def _pump(self, deadline: float, want_id: int | None) -> dict:
        """Read until the response with this id arrives, filing notifications on the way.

        A response is classified the way JSON-RPC 2.0 defines one: it carries `result` or
        `error` and never `method`. Matching on the id alone is not enough. Anything that echoes
        what it was sent -- a loopback, a terminal in local-echo, a half-configured USB gadget --
        sends back a message with the right id and no result, and a client keyed on the id calls
        that the answer and reports a successful read of nothing.
        """
        while True:
            message = self._read_line(deadline)
            if "method" in message:
                if "id" not in message:
                    self.notifications.append(
                        Notification(message["method"], message.get("params") or {})
                    )
                # A request coming the other way, or our own echo. Neither is a response.
                continue
            if "result" not in message and "error" not in message:
                continue  # neither a response nor a notification: not part of the protocol
            if want_id is None or message.get("id") == want_id:
                return message
            # A response to a call that already timed out. Dropping it keeps the stream in
            # step; keeping it would answer the next call with the previous one's result.

    def call(self, method: str, timeout: float | None = None, **params) -> Any:
        """Make one JSON-RPC call and return its result, raising RpcError on refusal."""
        with self._lock:
            request = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
            if params:
                request["params"] = params
            self._next_id += 1

            self._port.write((json.dumps(request) + "\n").encode("utf-8"))
            self._port.flush()

            deadline = time.monotonic() + (timeout or self._timeout)
            response = self._pump(deadline, request["id"])

        if "error" in response:
            err = response["error"]
            raise RpcError(err.get("code", 0), err.get("message", "?"), err.get("data"))
        return response.get("result")

    def notify(self, method: str, **params) -> None:
        """Send a request with no id, expecting no reply."""
        with self._lock:
            request = {"jsonrpc": "2.0", "method": method}
            if params:
                request["params"] = params
            self._port.write((json.dumps(request) + "\n").encode("utf-8"))
            self._port.flush()

    def poll(self, seconds: float = 0.0) -> list[Notification]:
        """Collect notifications the fixture pushed, waiting up to `seconds` for more."""
        deadline = time.monotonic() + seconds
        while seconds and time.monotonic() < deadline:
            try:
                message = self._read_line(deadline)
            except FixtureError:
                break
            if "method" in message and "id" not in message:
                self.notifications.append(
                    Notification(message["method"], message.get("params") or {})
                )
        drained = list(self.notifications)
        self.notifications.clear()
        return drained

    def close(self) -> None:
        with self._lock:
            self._port.close()

    def __enter__(self) -> Fixture:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------------------------------------------------------------- the protocol

    def channels(self) -> list[dict]:
        """Every channel, with the DUT signal it lands on. The map an agent navigates by."""
        return self.call("fixture.channels")

    def gpio_read(self, channel: str) -> dict:
        return self.call("gpio.read", channel=channel)

    def gpio_write(self, channel: str, value: bool) -> dict:
        return self.call("gpio.write", channel=channel, value=bool(value))

    def gpio_snapshot(self) -> list[dict]:
        return self.call("gpio.snapshot")

    def adc_read(self, channel: str) -> dict:
        return self.call("adc.read", channel=channel)

    def adc_snapshot(self) -> list[dict]:
        return self.call("adc.snapshot")

    def uart_write(self, channel: str, data: bytes) -> dict:
        return self.call("uart.write", channel=channel, hex=bytes(data).hex())

    def uart_read(self, channel: str) -> bytes:
        result = self.call("uart.read", channel=channel)
        return bytes.fromhex(result.get("hex", "") if isinstance(result, dict) else "")

    def i2c_scan(self, channel: str) -> list[int]:
        result = self.call("i2c.scan", channel=channel)
        return result.get("addresses", []) if isinstance(result, dict) else result

    def i2c_write(self, channel: str, address: int, data: bytes) -> dict:
        return self.call("i2c.write", channel=channel, address=address, hex=bytes(data).hex())

    def i2c_read(self, channel: str, address: int, length: int) -> bytes:
        result = self.call("i2c.read", channel=channel, address=address, length=length)
        return bytes.fromhex(result.get("hex", "") if isinstance(result, dict) else "")

    def spi_transfer(self, channel: str, data: bytes) -> bytes:
        result = self.call("spi.transfer", channel=channel, hex=bytes(data).hex())
        return bytes.fromhex(result.get("hex", "") if isinstance(result, dict) else "")


def connect(
    device: str,
    config: FixtureConfig | None = None,
    *,
    baud: int = DEFAULT_BAUD,
    timeout: float = DEFAULT_TIMEOUT,
    check_hash: bool = True,
    opener: Callable[..., Any] | None = None,
) -> Fixture:
    """Open a port, identify what is on it, and refuse a fixture that is not the one expected.

    `opener` exists so the tests can drive a fake port; leave it alone in real use.
    """
    if opener is None:
        serial = _require_serial()
        opener = serial.serial_for_url

    try:
        port = opener(device, baudrate=baud, timeout=timeout)
    except Exception as err:  # pyserial raises several unrelated types here
        raise FixtureError(f"cannot open {device}: {err}") from None

    fixture = Fixture(port, timeout=timeout)
    try:
        fixture.info = fixture.call("fixture.info")
    except FixtureError:
        fixture.close()
        raise

    if check_hash:
        if config is None:
            raise FixtureError(
                "check_hash needs a config to check against; pass one, or check_hash=False"
            )
        expected = config_hash(config)
        found = (fixture.info or {}).get("config_hash", "")
        if found != expected:
            fixture.close()
            raise ConfigMismatchError(expected, found, config.name)
    return fixture
