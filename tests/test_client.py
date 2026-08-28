"""The host client, driven against a fake fixture.

No hardware and no pyserial: `connect()` takes an `opener`, so these tests hand it a port that
behaves the way the generated firmware does, including the parts that make a naive client wrong.
The firmware's own behaviour is verified by tests/test_firmware.py compiling and running it; what
is checked here is that the host half copes with it.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "tests"))

from pinside import client
from pinside.cli import main
from pinside.config import load
from pinside.firmware.generate import config_hash

_DEMO = _ROOT / "examples" / "demo-fixture.json"


class FakePort:
    """A serial port that answers like the generated firmware.

    `lines` is a list of extra raw lines to emit before the next response, which is how the
    awkward cases get reproduced: boot chatter, a notification arriving mid-exchange, a partial
    line left over from before the port was opened.
    """

    def __init__(self, hash_="", *, interleave=None, fail_methods=(), name="demo-fixture"):
        self.hash = hash_
        self.name = name
        self.sent: list[dict] = []
        self.pending: list[bytes] = []
        self.interleave = list(interleave or [])
        self.fail_methods = set(fail_methods)
        self.closed = False
        self.timeout = 5.0

    # -- the pyserial surface the client uses --------------------------------

    def write(self, data: bytes) -> int:
        request = json.loads(data.decode())
        self.sent.append(request)
        for extra in self.interleave:
            self.pending.append(extra if isinstance(extra, bytes) else extra.encode())
        self.interleave = []
        self.pending.append(json.dumps(self._respond(request)).encode() + b"\n")
        return len(data)

    def flush(self) -> None:
        pass

    def readline(self) -> bytes:
        return self.pending.pop(0) if self.pending else b""

    def close(self) -> None:
        self.closed = True

    # -- the fixture's side --------------------------------------------------

    def _respond(self, request: dict) -> dict:
        method = request.get("method")
        rid = request.get("id")
        if method in self.fail_methods:
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {"code": -32003, "message": "guard not asserted", "data": "esp_en"},
            }
        results = {
            "fixture.info": {
                "fixture": self.name,
                "version": "0.1.0",
                "config_hash": self.hash,
            },
            "fixture.channels": [
                {"name": "dut_uart", "kind": "uart", "probe": "DUT_TXD"},
                {"name": "dut_3v3", "kind": "adc", "probe": "+3.3V"},
            ],
            "adc.snapshot": [
                {"channel": "dut_3v3", "probe": "+3.3V", "millivolts": 3298, "in_range": True}
            ],
            "gpio.snapshot": [{"channel": "dut_reset", "value": False}],
            "spi.transfer": {"hex": "aabb"},
            "i2c.scan": {"addresses": [0x50]},
            "uart.read": {"hex": "6f6b"},
        }
        return {"jsonrpc": "2.0", "id": rid, "result": results.get(method, {})}


def opener_for(port: FakePort):
    def opener(device, **kwargs):
        return port

    return opener


def cli(args: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        status = main(args)
    return status, out.getvalue(), err.getvalue()


class Handshake(unittest.TestCase):
    def setUp(self):
        self.config = load(str(_DEMO))
        self.hash = config_hash(self.config)

    def test_a_matching_fixture_connects(self):
        port = FakePort(self.hash)
        with client.connect("fake", self.config, opener=opener_for(port)) as fixture:
            self.assertEqual(fixture.info["config_hash"], self.hash)

    def test_a_mismatched_fixture_is_refused(self):
        # The whole reason the hash exists. Every later call would succeed against the wrong pin.
        port = FakePort("deadbeefdead")
        with self.assertRaises(client.ConfigMismatchError) as caught:
            client.connect("fake", self.config, opener=opener_for(port))
        self.assertIn(self.hash, str(caught.exception))
        self.assertIn("deadbeefdead", str(caught.exception))

    def test_a_refused_connection_closes_the_port(self):
        port = FakePort("deadbeefdead")
        with contextlib.suppress(client.ConfigMismatchError):
            client.connect("fake", self.config, opener=opener_for(port))
        self.assertTrue(port.closed, "a refused fixture left its port open")

    def test_the_check_can_be_waived_deliberately(self):
        port = FakePort("deadbeefdead")
        fixture = client.connect("fake", self.config, check_hash=False, opener=opener_for(port))
        self.assertEqual(fixture.info["config_hash"], "deadbeefdead")

    def test_checking_without_a_config_is_a_usage_error(self):
        port = FakePort(self.hash)
        with self.assertRaises(client.FixtureError):
            client.connect("fake", None, opener=opener_for(port))


class Framing(unittest.TestCase):
    """The parts of the wire that make a one-line-per-request client wrong."""

    def setUp(self):
        self.config = load(str(_DEMO))
        self.hash = config_hash(self.config)

    def test_a_notification_is_not_mistaken_for_a_response(self):
        # A streaming UART pushes uart.data between replies. A client that reads one line and
        # calls it the answer returns a log line as the result of an ADC read.
        stream = json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "uart.data",
                "params": {"channel": "dut_uart", "hex": "626f6f74", "t_ms": 12},
            }
        )
        port = FakePort(self.hash, interleave=[stream + "\n"])
        fixture = client.connect("fake", self.config, opener=opener_for(port))
        rails = fixture.adc_snapshot()
        self.assertEqual(rails[0]["channel"], "dut_3v3")
        pushed = fixture.poll()
        self.assertEqual(len(pushed), 1)
        self.assertEqual(pushed[0].method, "uart.data")
        self.assertEqual(pushed[0].data, b"boot")
        self.assertEqual(pushed[0].channel, "dut_uart")

    def test_boot_chatter_is_skipped_rather_than_fatal(self):
        # A board that was already running prints whatever it prints. None of it is JSON.
        port = FakePort(self.hash, interleave=[b"MicroPython v1.2\r\n", b"\n", b"{partial"])
        fixture = client.connect("fake", self.config, opener=opener_for(port))
        self.assertEqual(fixture.channels()[0]["name"], "dut_uart")

    def test_an_rpc_error_is_raised_with_its_code_and_data(self):
        port = FakePort(self.hash, fail_methods={"spi.transfer"})
        fixture = client.connect("fake", self.config, opener=opener_for(port))
        with self.assertRaises(client.RpcError) as caught:
            fixture.spi_transfer("eth_spi", b"\x0f\x00")
        self.assertEqual(caught.exception.code, -32003)
        self.assertEqual(caught.exception.data, "esp_en")
        self.assertIn("guard not asserted", str(caught.exception))

    def test_a_silent_fixture_times_out_rather_than_hanging(self):
        port = FakePort(self.hash)
        fixture = client.connect("fake", self.config, opener=opener_for(port))
        port.pending.clear()
        port.write = lambda data: len(data)  # accepted, never answered
        with self.assertRaises(client.FixtureError):
            fixture.call("adc.snapshot", timeout=0.05)

    def test_an_echo_is_not_a_response(self):
        """A device that echoes what it was sent must not look like it answered.

        The echoed request carries the very id the client is waiting for, so matching on the id
        alone accepts it, unwraps a `result` that is not there, and reports a successful read of
        None. Found by pointing the client at pyserial's own `loop://` port.
        """
        port = FakePort(self.hash)
        fixture = client.connect("fake", self.config, opener=opener_for(port))

        echoing = json.dumps({"jsonrpc": "2.0", "id": 99, "method": "adc.snapshot"})
        port.interleave = [echoing + "\n"]
        rails = fixture.adc_snapshot()
        self.assertEqual(rails[0]["channel"], "dut_3v3")

    def test_a_message_that_is_neither_response_nor_notification_is_skipped(self):
        port = FakePort(self.hash)
        fixture = client.connect("fake", self.config, opener=opener_for(port))
        port.interleave = ['{"jsonrpc":"2.0","id":4}\n', '{"hello":"world"}\n']
        self.assertTrue(fixture.channels())

    def test_request_ids_increase(self):
        port = FakePort(self.hash)
        fixture = client.connect("fake", self.config, opener=opener_for(port))
        fixture.channels()
        fixture.adc_snapshot()
        ids = [r["id"] for r in port.sent]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(ids), len(set(ids)))


class TypedCalls(unittest.TestCase):
    """The convenience wrappers, which exist so callers do not hand-roll hex every time."""

    def setUp(self):
        self.config = load(str(_DEMO))
        port = FakePort(config_hash(self.config))
        self.port = port
        self.fixture = client.connect("fake", self.config, opener=opener_for(port))

    def test_bytes_go_out_as_hex(self):
        self.fixture.uart_write("dut_uart", b"ok\n")
        self.assertEqual(self.port.sent[-1]["params"]["hex"], "6f6b0a")

    def test_hex_comes_back_as_bytes(self):
        self.assertEqual(self.fixture.uart_read("dut_uart"), b"ok")
        self.assertEqual(self.fixture.spi_transfer("eth_spi", b"\x00"), b"\xaa\xbb")

    def test_i2c_scan_returns_addresses(self):
        self.assertEqual(self.fixture.i2c_scan("ext_i2c"), [0x50])

    def test_gpio_write_sends_a_real_boolean(self):
        self.fixture.gpio_write("dut_reset", 1)
        self.assertIs(self.port.sent[-1]["params"]["value"], True)


class ProbeCommand(unittest.TestCase):
    """`pinside probe`, the bench smoke test."""

    def setUp(self):
        self.config = load(str(_DEMO))
        self.hash = config_hash(self.config)

    def run_probe(self, port: FakePort, args=()):
        real = client.connect

        def fake_connect(device, config=None, **kwargs):
            kwargs.pop("opener", None)
            return real(device, config, opener=opener_for(port), **kwargs)

        client.connect = fake_connect
        try:
            return cli(["probe", str(_DEMO), "--port", "fake", *args])
        finally:
            client.connect = real

    def test_a_matching_fixture_passes(self):
        status, out, _ = self.run_probe(FakePort(self.hash))
        self.assertEqual(status, 0)
        self.assertIn("demo-fixture", out)
        self.assertIn("dut_3v3", out)

    def test_a_mismatched_fixture_fails(self):
        status, _, err = self.run_probe(FakePort("deadbeefdead"))
        self.assertEqual(status, 2)
        self.assertIn("config", err)

    def test_a_rail_out_of_range_fails(self):
        port = FakePort(self.hash)
        original = port._respond

        def low_rail(request):
            reply = original(request)
            if request.get("method") == "adc.snapshot":
                reply["result"][0].update(millivolts=1100, in_range=False)
            return reply

        port._respond = low_rail
        status, _, err = self.run_probe(port)
        self.assertEqual(status, 2)
        self.assertIn("out of range", err)

    def test_json_output_is_parseable(self):
        status, out, _ = self.run_probe(FakePort(self.hash), ["--json"])
        self.assertEqual(status, 0)
        payload = json.loads(out)
        self.assertEqual(payload["port"], "fake")
        self.assertEqual(payload["out_of_range"], [])
        self.assertTrue(payload["channels"])


try:
    import serial  # noqa: F401

    HAVE_PYSERIAL = True
except ImportError:
    HAVE_PYSERIAL = False


@unittest.skipUnless(HAVE_PYSERIAL, "pyserial not installed (pip install 'pinside[client]')")
class AgainstRealPyserial(unittest.TestCase):
    """The fake above stands in for a pyserial port. This checks the stand-in is honest.

    `loop://` is pyserial's own in-memory port: real Serial semantics, and it echoes. That makes
    it the exact adversary for the id-matching bug, and it is where that bug was found.
    """

    def test_the_timeout_is_real_and_bounded(self):
        import time

        import serial

        port = serial.serial_for_url("loop://", baudrate=115200, timeout=1)
        self.addCleanup(port.close)
        fixture = client.Fixture(port, timeout=0.3)
        start = time.monotonic()
        with self.assertRaises(client.FixtureError):
            fixture.call("adc.snapshot")
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 2.0, "the timeout did not bound the wait")

    def test_an_echoing_port_never_looks_like_an_answer(self):
        import serial

        port = serial.serial_for_url("loop://", baudrate=115200, timeout=1)
        self.addCleanup(port.close)
        fixture = client.Fixture(port, timeout=0.3)
        with self.assertRaises(client.FixtureError):
            fixture.call("fixture.info")


class WithoutPyserial(unittest.TestCase):
    def test_the_missing_dependency_says_how_to_install_it(self):
        real = client._require_serial

        def missing():
            raise client.FixtureError(
                "talking to a fixture needs pyserial, which pinside does not install by "
                "default. Install it with: pip install 'pinside[client]'"
            )

        client._require_serial = missing
        try:
            with self.assertRaises(client.FixtureError) as caught:
                client.ports()
        finally:
            client._require_serial = real
        self.assertIn("pinside[client]", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
