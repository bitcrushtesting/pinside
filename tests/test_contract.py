"""The generated openrpc.json, checked against the firmware it describes.

The contract is the whole agent-facing story: `fixture.info` and `fixture.channels` are supposed
to tell a caller everything it needs, with no other map. That only holds if the document is
actually valid OpenRPC and actually describes the firmware sitting next to it.

The existing CI check asserted the file parses as JSON and has a non-empty `methods` list. Both
would stay true of a contract that promises three methods the firmware does not implement.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "tests"))

from pinside.config import load, resolve_board
from pinside.firmware import generate

_TEMPLATES = _ROOT / "src" / "pinside" / "firmware" / "templates"
_CORE_C = (_TEMPLATES / "fixture_core.c").read_text(encoding="utf-8")

# `if (strcmp(method, "gpio.read") == 0) return ...` -- the firmware's real method list.
_DISPATCHED = re.compile(r'strcmp\(method,\s*"([a-z0-9_.]+)"\)')
# A notification the firmware sends unasked. Every one goes out through notify_begin, whose
# second argument is the method name: `notify_begin(&o, "uart.data")`.
_NOTIFIED = re.compile(r'notify_begin\([^,]+,\s*"([a-z0-9_.]+)"')


def _generate() -> tuple[dict, Path]:
    config = load(str(_ROOT / "examples" / "demo-fixture.json"))
    board = resolve_board(config, str(_ROOT / "examples" / "demo-board.kicad_pcb"))
    out = Path(tempfile.mkdtemp(prefix="pinside-contract-"))
    result = generate(config, board, out, force=True)
    contract = json.loads((out / "openrpc.json").read_text(encoding="utf-8"))
    return contract, result


class Structure(unittest.TestCase):
    """OpenRPC 1.x, as the specification defines it rather than as JSON."""

    @classmethod
    def setUpClass(cls):
        cls.contract, cls.result = _generate()

    def test_it_declares_an_openrpc_version(self):
        self.assertRegex(self.contract["openrpc"], r"^1\.\d+\.\d+$")

    def test_the_info_object_is_complete(self):
        info = self.contract["info"]
        # title and version are the two required fields of the Info object.
        self.assertTrue(info["title"])
        self.assertTrue(info["version"])

    def test_every_method_is_a_valid_method_object(self):
        for method in self.contract["methods"]:
            with self.subTest(method=method.get("name")):
                self.assertTrue(method["name"], "a method with no name")
                self.assertIsInstance(method["params"], list)
                for param in method["params"]:
                    self.assertTrue(param["name"])
                    self.assertIsInstance(param["schema"], dict)
                    self.assertTrue(param["schema"], "an empty schema describes nothing")
                self.assertIn("result", method)
                self.assertTrue(method["result"]["name"])
                self.assertIsInstance(method["result"]["schema"], dict)

    def test_method_names_are_unique(self):
        names = [m["name"] for m in self.contract["methods"]]
        self.assertEqual(len(names), len(set(names)), f"duplicate method names in {names}")

    def test_every_ref_resolves(self):
        """A dangling $ref is a schema that says nothing, and JSON validity will not catch it."""
        components = self.contract.get("components", {})

        def walk(node, path="$"):
            if isinstance(node, dict):
                ref = node.get("$ref")
                if isinstance(ref, str):
                    self.assertTrue(
                        ref.startswith("#/components/"),
                        f"{path}: external $ref {ref!r}; the contract must stand alone",
                    )
                    target = components
                    for part in ref.split("/")[2:]:
                        self.assertIn(part, target, f"{path}: $ref {ref!r} does not resolve")
                        target = target[part]
                for key, value in node.items():
                    walk(value, f"{path}.{key}")
            elif isinstance(node, list):
                for i, value in enumerate(node):
                    walk(value, f"{path}[{i}]")

        walk(self.contract)


class AgainstTheFirmware(unittest.TestCase):
    """The drift that matters: a contract describing firmware that is not there."""

    @classmethod
    def setUpClass(cls):
        cls.contract, cls.result = _generate()
        cls.described = {m["name"] for m in cls.contract["methods"]}
        cls.dispatched = set(_DISPATCHED.findall(_CORE_C))

    def test_the_scan_found_a_dispatch_table_at_all(self):
        # Without this, both directions below pass vacuously the moment the C is reformatted.
        self.assertGreater(len(self.dispatched), 10)
        self.assertIn("fixture.info", self.dispatched)

    def test_the_contract_promises_nothing_the_firmware_lacks(self):
        missing = sorted(self.described - self.dispatched)
        self.assertFalse(missing, f"described but not dispatched by fixture_core.c: {missing}")

    def test_the_firmware_implements_nothing_the_contract_hides(self):
        undocumented = sorted(self.dispatched - self.described)
        self.assertFalse(undocumented, f"dispatched but absent from the contract: {undocumented}")

    def test_every_notification_is_actually_sent(self):
        promised = {n["name"] for n in self.contract["x-notifications"]}
        sent = set(_NOTIFIED.findall(_CORE_C))
        self.assertTrue(sent, "no notification names found in fixture_core.c")
        self.assertFalse(
            sorted(promised - sent),
            f"promised but never sent: {sorted(promised - sent)}",
        )


class AgainstTheConfig(unittest.TestCase):
    """The channel map, which is the part an agent navigates by."""

    @classmethod
    def setUpClass(cls):
        cls.contract, cls.result = _generate()
        cls.config = load(str(_ROOT / "examples" / "demo-fixture.json"))

    def test_the_hash_matches_what_generation_reported(self):
        # If these two disagree, fixture.info reports a hash nobody can compare against.
        self.assertEqual(self.contract["x-pinside"]["config_hash"], self.result.config_hash)

    def test_every_configured_channel_is_in_the_contract(self):
        described = {c["name"] for c in self.contract["x-pinside"]["channels"]}
        configured = {c.name for c in [*self.config.gpio, *self.config.adc]}
        configured |= {b.name for b in self.config.buses}
        self.assertEqual(described, configured)

    def test_every_channel_declares_its_kind(self):
        for channel in self.contract["x-pinside"]["channels"]:
            with self.subTest(channel=channel["name"]):
                self.assertIn(channel["kind"], {"gpio", "adc", "uart", "i2c", "spi"})

    def test_the_target_is_named(self):
        self.assertEqual(self.contract["x-pinside"]["mcu"], self.config.mcu)


if __name__ == "__main__":
    unittest.main()
