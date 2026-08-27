"""Tests for the config model, the scaffolder and the firmware generator.

The last test in here builds the generated C and runs its own test suite. That is the only check
that the templates and the generated tables actually agree, so it is worth the seconds it costs.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

import boards  # noqa: E402

from pinside import read_board, transform  # noqa: E402
from pinside.checks import ERROR  # noqa: E402
from pinside.config import ConfigError, from_dict, load, validate  # noqa: E402
from pinside.firmware import GenerationError, config_hash, generate  # noqa: E402
from pinside.scaffold import scaffold  # noqa: E402
from pinside.targets import get as target  # noqa: E402


def write(text: str, suffix: str = ".kicad_pcb") -> str:
    handle = tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False, encoding="utf-8")
    handle.write(text)
    handle.close()
    return handle.name


def codes(cfg, board=None) -> set[str]:
    return {f.code for f in validate(cfg, board)}


MINIMAL = {
    "name": "demo",
    "target": {"mcu": "rp2350b"},
    "dut": {"require_all_test_points": False},
    "gpio": [{"name": "reset", "pin": 24, "direction": "open_drain", "active_low": True}],
}


class TestTargets(unittest.TestCase):
    """Spot checks against the published RP2350 GPIO function table."""

    def setUp(self):
        self.t = target("rp2350b")

    def test_uart_roles_follow_the_group_of_four(self):
        self.assertEqual(self.t.uart_of(0), (0, "tx"))
        self.assertEqual(self.t.uart_of(1), (0, "rx"))
        self.assertEqual(self.t.uart_of(2), (0, "cts"))
        self.assertEqual(self.t.uart_of(3), (0, "rts"))

    def test_uart_instance_alternates_in_pairs_of_groups(self):
        for gpio, instance in [(0, 0), (4, 1), (8, 1), (12, 0), (16, 0), (20, 1),
                               (24, 1), (28, 0), (32, 0), (36, 1), (40, 1), (44, 0)]:
            self.assertEqual(self.t.uart_of(gpio)[0], instance, f"GPIO{gpio}")

    def test_spi_instance_alternates_every_eight_pins(self):
        for gpio, instance in [(0, 0), (8, 1), (16, 0), (24, 1), (32, 0), (40, 1)]:
            self.assertEqual(self.t.spi_of(gpio)[0], instance, f"GPIO{gpio}")
        self.assertEqual(self.t.spi_of(0)[1], "rx")
        self.assertEqual(self.t.spi_of(3)[1], "tx")

    def test_i2c_data_is_even_and_clock_is_odd(self):
        self.assertEqual(self.t.i2c_of(8), (0, "sda"))
        self.assertEqual(self.t.i2c_of(9), (0, "scl"))
        self.assertEqual(self.t.i2c_of(2)[0], 1)

    def test_adc_reaches_only_the_top_eight_pins(self):
        self.assertEqual(self.t.adc_of(40), 0)
        self.assertEqual(self.t.adc_of(47), 7)
        self.assertIsNone(self.t.adc_of(39))

    def test_the_smaller_part_has_fewer_pins_and_a_different_adc_block(self):
        a = target("rp2350a")
        self.assertEqual(a.gpio_count, 30)
        self.assertEqual(a.adc_of(26), 0)
        self.assertFalse(a.has_pin(40))

    def test_unknown_target_names_the_ones_it_knows(self):
        with self.assertRaises(ValueError) as caught:
            target("stm32f411")
        self.assertIn("rp2350b", str(caught.exception))


class TestConfigLoading(unittest.TestCase):
    def test_round_trip_through_a_file(self):
        cfg = load(write(json.dumps(MINIMAL), ".json"))
        self.assertEqual(cfg.name, "demo")
        self.assertEqual(cfg.gpio[0].pin, 24)

    def test_invalid_json_says_where(self):
        with self.assertRaises(ConfigError) as caught:
            load(write('{"name": }', ".json"))
        self.assertIn("line", str(caught.exception))

    def test_missing_name_is_refused(self):
        with self.assertRaises(ConfigError):
            from_dict({"target": {"mcu": "rp2350b"}})

    def test_unknown_pin_role_is_refused(self):
        with self.assertRaises(ConfigError) as caught:
            from_dict({"name": "x", "i2c": [{"name": "b", "pins": {"sda": 8, "clock": 9}}]})
        self.assertIn("clock", str(caught.exception))

    def test_spi_pin_names_accept_schematic_spelling(self):
        cfg = from_dict({"name": "x", "spi": [
            {"name": "b", "pins": {"miso": 16, "mosi": 19, "sclk": 18, "cs": 17},
             "probes": {"miso": "A", "mosi": "B", "sclk": "C", "cs": "D"}}]})
        self.assertEqual(cfg.spi[0].pins, {"rx": 16, "tx": 19, "sck": 18, "cs": 17})
        self.assertEqual(cfg.spi[0].probes["rx"], "A")


class TestConfigValidation(unittest.TestCase):
    def test_a_good_config_is_silent(self):
        self.assertEqual(codes(from_dict(MINIMAL)), set())

    def test_swapped_i2c_lines_are_caught_with_the_pins_that_would_work(self):
        cfg = from_dict({"name": "x", "dut": {"require_all_test_points": False},
                         "i2c": [{"name": "b", "peripheral": 0, "pins": {"sda": 9, "scl": 8}}]})
        findings = validate(cfg)
        self.assertIn("PF021", {f.code for f in findings})
        self.assertIn("GPIO8", next(f for f in findings if f.code == "PF021").detail)

    def test_a_pin_cannot_serve_two_channels(self):
        cfg = from_dict({"name": "x", "dut": {"require_all_test_points": False},
                         "gpio": [{"name": "a", "pin": 5}, {"name": "b", "pin": 5}]})
        self.assertIn("PF023", codes(cfg))

    def test_adc_on_a_pin_without_a_converter(self):
        cfg = from_dict({"name": "x", "dut": {"require_all_test_points": False},
                         "adc": [{"name": "rail", "pin": 12}]})
        self.assertIn("PF022", codes(cfg))

    def test_pin_beyond_the_package(self):
        cfg = from_dict({"name": "x", "target": {"mcu": "rp2350a"},
                         "dut": {"require_all_test_points": False},
                         "gpio": [{"name": "a", "pin": 40}]})
        self.assertIn("PF020", codes(cfg))

    def test_guard_must_name_a_declared_channel(self):
        cfg = from_dict({"name": "x", "dut": {"require_all_test_points": False},
                         "spi": [{"name": "b", "peripheral": 0, "guard": "nothing",
                                  "pins": {"rx": 16, "cs": 17, "sck": 18, "tx": 19}}]})
        self.assertIn("PF031", codes(cfg))

    def test_duplicate_channel_names(self):
        cfg = from_dict({"name": "x", "dut": {"require_all_test_points": False},
                         "gpio": [{"name": "a", "pin": 5}],
                         "adc": [{"name": "a", "pin": 40}]})
        self.assertIn("PF011", codes(cfg))

    def test_a_name_c_cannot_use(self):
        cfg = from_dict({"name": "x", "dut": {"require_all_test_points": False},
                         "gpio": [{"name": "3-phase", "pin": 5}]})
        self.assertIn("PF010", codes(cfg))

    def test_unknown_mcu_stops_everything_else(self):
        cfg = from_dict({"name": "x", "target": {"mcu": "atmega328"}})
        self.assertEqual(codes(cfg), {"PF001"})

    def test_a_divider_that_would_overrange_the_adc(self):
        board = transform(read_board(write(boards.healthy())))
        cfg = from_dict({"name": "x", "dut": {"require_all_test_points": False},
                         "adc": [{"name": "rail", "pin": 40, "probe": "SCL",
                                  "divider": 0.5, "nominal_v": 3.3}]})
        self.assertIn("PF042", codes(cfg, board))


class TestConfigAgainstBoard(unittest.TestCase):
    def setUp(self):
        self.board = transform(read_board(write(boards.healthy())))

    def test_a_probe_the_board_does_not_have(self):
        cfg = from_dict({"name": "x", "dut": {"require_all_test_points": False},
                         "gpio": [{"name": "a", "pin": 5, "probe": "NOT_ON_THE_BOARD"}]})
        self.assertIn("PF040", codes(cfg, self.board))

    def test_a_test_point_the_config_forgot(self):
        cfg = from_dict({"name": "x", "gpio": [{"name": "a", "pin": 5, "probe": "SCL"}]})
        findings = validate(cfg, self.board)
        missing = next(f for f in findings if f.code == "PF041")
        self.assertEqual(missing.severity, ERROR)
        self.assertIn("SDA", missing.refs)

    def test_a_partial_fixture_can_be_declared_deliberate(self):
        cfg = from_dict({"name": "x", "dut": {"require_all_test_points": False},
                         "gpio": [{"name": "a", "pin": 5, "probe": "SCL"}]})
        findings = validate(cfg, self.board)
        self.assertNotIn(ERROR, {f.severity for f in findings})

    def test_not_reading_the_board_is_itself_reported(self):
        cfg = from_dict({"name": "x", "dut": {"board": "somewhere.kicad_pcb"}})
        self.assertIn("PF002", codes(cfg, None))


class TestScaffold(unittest.TestCase):
    def setUp(self):
        self.board = transform(read_board(write(boards.healthy())))
        self.draft = scaffold(self.board, "demo")

    def test_the_draft_validates_against_its_own_board(self):
        self.assertEqual(codes(from_dict(self.draft), self.board), set())

    def test_uart_directions_are_inverted_for_the_fixture(self):
        board = transform(read_board(write(boards.uart_board())))
        cfg = from_dict(scaffold(board, "demo"))
        bus = cfg.uart[0]
        # The DUT transmits on DUT_TXD, so it must arrive at the fixture's receiver.
        self.assertEqual(bus.probes["rx"], "DUT_TXD")
        self.assertEqual(bus.probes["tx"], "DUT_RXD")
        self.assertEqual(bus.probes["cts"], "DUT_RTS")
        self.assertEqual(bus.probes["rts"], "DUT_CTS")

    def test_every_signal_reaches_a_channel(self):
        cfg = from_dict(self.draft)
        signals = {t.signal for t in self.board.test_points if t.signal and not t.is_ground}
        self.assertEqual(signals - cfg.probe_names(), set())

    def test_lines_the_dut_drives_stay_inputs(self):
        board = transform(read_board(write(boards.uart_board())))
        cfg = from_dict(scaffold(board, "demo"))
        fault = next(g for g in cfg.gpio if g.probe == "PWR_FLT")
        self.assertEqual(fault.direction, "input")

    def test_pins_are_never_handed_out_twice(self):
        cfg = from_dict(self.draft)
        claimed = [p for bus in cfg.buses for p in bus.pins.values()]
        claimed += [g.pin for g in cfg.gpio] + [a.pin for a in cfg.adc]
        self.assertEqual(len(claimed), len(set(claimed)))


class TestGeneration(unittest.TestCase):
    def setUp(self):
        self.board = transform(read_board(write(boards.healthy())))
        self.cfg = from_dict(scaffold(self.board, "demo"))
        self.out = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.out, ignore_errors=True)

    def test_it_writes_a_whole_project(self):
        result = generate(self.cfg, self.board, self.out)
        for expected in ["CMakeLists.txt", "openrpc.json", "README.md",
                         "src/fixture_config.c", "src/fixture_core.c", "src/main.c",
                         "include/fixture_config.h", "test/test_core.c", "test/run.sh"]:
            self.assertTrue((self.out / expected).exists(), expected)
        self.assertTrue(os.access(self.out / "test" / "run.sh", os.X_OK))
        self.assertEqual(len(result.files), len(set(result.files)))

    def test_an_invalid_config_writes_nothing(self):
        bad = from_dict({"name": "bad", "dut": {"require_all_test_points": False},
                         "gpio": [{"name": "a", "pin": 200}]})
        with self.assertRaises(GenerationError):
            generate(bad, None, self.out)
        self.assertEqual(list(self.out.iterdir()), [])

    def test_the_hash_tracks_the_config_and_nothing_else(self):
        """It must move when the hardware moves and hold still when only prose changes."""
        board = transform(read_board(write(boards.uart_board())))
        cfg = from_dict(scaffold(board, "demo"))
        first = config_hash(cfg)
        self.assertEqual(first, config_hash(cfg))

        cfg.description = "reworded, same hardware"
        cfg.gpio[0].description = "also reworded"
        self.assertEqual(first, config_hash(cfg), "wording must not change the hash")

        cfg.gpio[0].pin += 1
        self.assertNotEqual(first, config_hash(cfg), "moving a pin must change the hash")

    def test_the_generated_tables_carry_the_channels(self):
        generate(self.cfg, self.board, self.out)
        source = (self.out / "src" / "fixture_config.c").read_text()
        for channel in self.cfg.channels:
            self.assertIn(f'"{channel.name}"', source)

    def test_the_openrpc_contract_names_every_channel(self):
        result = generate(self.cfg, self.board, self.out)
        contract = json.loads((self.out / "openrpc.json").read_text())
        self.assertEqual(contract["x-pinside"]["config_hash"], result.config_hash)
        listed = {c["name"] for c in contract["x-pinside"]["channels"]}
        self.assertEqual(listed, {c.name for c in self.cfg.channels})

    def test_a_config_with_no_buses_still_generates(self):
        bare = from_dict({"name": "bare", "dut": {"require_all_test_points": False},
                          "gpio": [{"name": "only", "pin": 5}]})
        generate(bare, None, self.out)
        source = (self.out / "src" / "fixture_config.c").read_text()
        self.assertIn("fx_spi_count = 0", source)

    def test_it_refuses_to_overwrite_a_directory_it_did_not_create(self):
        (self.out / "someone_elses_work.txt").write_text("mine")
        with self.assertRaises(GenerationError):
            generate(self.cfg, self.board, self.out)
        generate(self.cfg, self.board, self.out, force=True)   # explicit consent


@unittest.skipUnless(shutil.which("cc"), "no C compiler")
class TestGeneratedFirmwareBuilds(unittest.TestCase):
    """The generated C is compiled and its own tests run. Nothing else checks the templates."""

    def test_generated_firmware_compiles_and_passes_its_tests(self):
        board = transform(read_board(write(boards.healthy())))
        cfg = from_dict(scaffold(board, "demo"))
        out = Path(tempfile.mkdtemp())
        try:
            generate(cfg, board, out)
            proc = subprocess.run([str(out / "test" / "run.sh")], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0,
                             f"generated firmware tests failed:\n{proc.stdout}\n{proc.stderr}")
            self.assertIn("0 failures", proc.stdout)
        finally:
            shutil.rmtree(out, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
