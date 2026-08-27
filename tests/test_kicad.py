"""Tests for the module and probe catalogues and the KiCad project generator.

The last class runs KiCad's own ERC and DRC over a generated project. That is the only check
that what pinside writes is a file KiCad agrees with -- every other test here would pass just as
happily on a project KiCad refuses to open. It skips itself when kicad-cli is not installed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "tests"))

import boards
from boards import write
from pinside import modules, pogo, read_board, transform
from pinside.config import from_dict, resolve_board, validate
from pinside.kicad import library
from pinside.kicad.footprint import mounting_hole_shape, pogo_shape
from pinside.kicad.project import ProjectError, generate_project
from pinside.kicad.schematic import channel_slots, pin_geometry
from pinside.kicad.write import Node, Raw, Verbatim, document, num, uid
from pinside.scaffold import scaffold
from pinside.sexpr import atom, child, find_all
from pinside.sexpr import load as load_sexpr

KICAD_CLI = shutil.which("kicad-cli") or (
    "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"
    if Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli").exists()
    else None
)

HAVE_SYMBOLS = True
try:
    library.symbol_dir()
except library.LibraryError:
    HAVE_SYMBOLS = False


class TestModules(unittest.TestCase):
    def test_the_default_is_a_pico(self):
        module = modules.get(modules.DEFAULT)
        self.assertIsNotNone(module)
        self.assertIn("Pico", module.description)

    def test_the_pico_header_matches_kicads_own_symbol(self):
        """The generated schematic uses this symbol, so the two must agree on pin numbers."""
        if not HAVE_SYMBOLS:
            self.skipTest("KiCad symbol libraries not installed")
        module = modules.get("pico2")
        pins = library.symbol_pins(library.load_symbol(module.symbol))
        for gpio, header in module.header.items():
            # KiCad spells the analogue pins GPIO26_ADC0 and so on.
            self.assertRegex(
                pins[str(header)],
                rf"^GPIO{gpio}(_ADC\d)?$",
                f"GPIO{gpio} is not on header pin {header}",
            )

    def test_the_pins_the_module_swallows(self):
        module = modules.get("pico2")
        # Consumed on the module itself: regulator mode, VBUS sense, LED, VSYS sense.
        self.assertEqual(module.unexposed([23, 24, 25, 29]), [23, 24, 25, 29])
        self.assertEqual(module.unexposed([0, 22, 26]), [])

    def test_only_the_exposed_adc_pins_count(self):
        self.assertEqual(sorted(modules.get("pico2").adc_gpios()), [26, 27, 28])

    def test_bare_is_not_a_module(self):
        self.assertIsNone(modules.get("bare"))
        self.assertIsNone(modules.get(""))

    def test_an_unknown_board_lists_the_known_ones(self):
        with self.assertRaises(ValueError) as caught:
            modules.get("teensy41")
        self.assertIn("pico2", str(caught.exception))


class TestProbes(unittest.TestCase):
    def test_the_default_probe_is_a_receptacle_and_a_pin(self):
        probe = pogo.get()
        self.assertTrue(probe.receptacle)
        self.assertTrue(probe.pin)
        self.assertGreater(probe.min_pitch_mm, 0)

    def test_a_finer_probe_allows_a_tighter_pitch(self):
        self.assertLess(pogo.get("millmax_0906").min_pitch_mm, pogo.get().min_pitch_mm)

    def test_an_unknown_probe_lists_the_known_ones(self):
        with self.assertRaises(ValueError) as caught:
            pogo.get("nope")
        self.assertIn("millmax_0985", str(caught.exception))


class TestConfigWithABoard(unittest.TestCase):
    def test_the_board_decides_the_chip(self):
        cfg = from_dict({"name": "x", "target": {"board": "pico2"}})
        self.assertEqual(cfg.mcu, "rp2350a")

    def test_naming_a_chip_the_board_does_not_carry_is_an_error(self):
        cfg = from_dict({"name": "x", "target": {"board": "pico2", "mcu": "rp2350b"}})
        self.assertIn("PF006", {f.code for f in validate(cfg)})

    def test_a_pin_the_board_does_not_bring_out(self):
        cfg = from_dict(
            {
                "name": "x",
                "target": {"board": "pico2"},
                "dut": {"require_all_test_points": False},
                "gpio": [{"name": "a", "pin": 25}],
            }
        )
        finding = next(f for f in validate(cfg) if f.code == "PF024")
        self.assertIn("GPIO25", finding.refs[0])

    def test_a_bare_chip_may_use_every_pin(self):
        cfg = from_dict(
            {
                "name": "x",
                "target": {"board": "bare", "mcu": "rp2350b"},
                "dut": {"require_all_test_points": False},
                "gpio": [{"name": "a", "pin": 47}],
            }
        )
        self.assertEqual({f.code for f in validate(cfg)}, set())

    def test_an_unrecognised_mirror(self):
        cfg = from_dict(
            {"name": "x", "target": {"board": "bare"}, "fixture": {"mirror": "diagonal"}}
        )
        self.assertIn("PF008", {f.code for f in validate(cfg)})

    def test_resolving_the_board_puts_it_in_the_fixture_frame(self):
        """Untransformed, every probe sits at the origin -- a fixture drilled in one place."""
        path = write(boards.healthy())
        cfg = from_dict(
            {
                "name": "x",
                "target": {"board": "bare"},
                "dut": {"board": path, "require_all_test_points": False},
            }
        )
        dut = resolve_board(cfg)
        self.assertEqual(dut.frame["mirror"], "x")
        self.assertTrue(any(t.fx or t.fy for t in dut.test_points))


class TestWithoutKiCad(unittest.TestCase):
    """A machine with no KiCad should be told so, not handed a broken schematic."""

    def setUp(self):
        self._candidates = library._CANDIDATES
        library._CANDIDATES = []
        self._env = {
            v: os.environ.pop(v, None)
            for v in ("KICAD_SYMBOL_DIR", "KICAD10_SYMBOL_DIR", "KICAD9_SYMBOL_DIR")
        }
        self.out = Path(tempfile.mkdtemp())

    def tearDown(self):
        library._CANDIDATES = self._candidates
        for name, value in self._env.items():
            if value is not None:
                os.environ[name] = value
        shutil.rmtree(self.out, ignore_errors=True)

    def test_it_says_kicad_is_missing_and_writes_nothing(self):
        path = write(boards.healthy())
        board = transform(read_board(path), mirror="x")
        config = from_dict(
            {
                "name": "demo",
                "target": {"board": "bare", "mcu": "rp2350b"},
                "dut": {"board": path, "require_all_test_points": False},
                "gpio": [{"name": "a", "pin": 5, "probe": "SCL"}],
            },
            source=path,
        )
        with self.assertRaises(ProjectError) as caught:
            generate_project(config, board, self.out)
        self.assertIn("KiCad", caught.exception.findings[0].summary)
        self.assertEqual(list(self.out.iterdir()), [])


class TestWriter(unittest.TestCase):
    def test_numbers_lose_their_trailing_zeros(self):
        self.assertEqual(num(2.0).text, "2")
        self.assertEqual(num(1.5).text, "1.5")
        self.assertEqual(num(-0.0).text, "0")

    def test_bare_tokens_stay_bare_and_strings_get_quoted(self):
        rendered = document(Node("x", Raw("yes"), "a b"))
        self.assertIn('(x yes "a b")', rendered)

    def test_uuids_are_stable_across_runs(self):
        self.assertEqual(uid("p", "k"), uid("p", "k"))
        self.assertNotEqual(uid("p", "k"), uid("p", "j"))
        self.assertNotEqual(uid("q", "k"), uid("p", "k"))

    def test_verbatim_text_is_reindented_not_reparsed(self):
        rendered = Verbatim("(a\n\t(b default)\n)").render(1)
        self.assertIn("(b default)", rendered)
        self.assertTrue(rendered.startswith("\t("))


@unittest.skipUnless(HAVE_SYMBOLS, "KiCad symbol libraries not installed")
class TestLibrary(unittest.TestCase):
    def test_a_symbol_keeps_its_bare_tokens(self):
        """Re-serialising a parse would quote these, and KiCad would refuse the file."""
        text = library.load_symbol("Device:R")
        self.assertIn("(type default)", text)
        self.assertNotIn('"default"', text.split("(pin", 1)[0])

    def test_the_definition_is_renamed_to_its_full_id(self):
        self.assertTrue(library.load_symbol("Device:R").startswith('(symbol "Device:R"'))

    def test_a_missing_symbol_says_so(self):
        with self.assertRaises(library.LibraryError):
            library.load_symbol("Device:NotARealPart")

    def test_pin_geometry_flips_the_y_axis(self):
        """A library is drawn Y-up and a schematic Y-down; getting this wrong misses every pin."""
        pins = pin_geometry(library.load_symbol("Device:R"), 100, 100)
        self.assertEqual(pins["1"][:2], (100.0, 96.19))
        self.assertEqual(pins["2"][:2], (100.0, 103.81))


class TestFootprints(unittest.TestCase):
    def test_both_copies_are_built_from_one_description(self):
        """The board carries its own copy and DRC compares the two, so they must match."""
        shape = pogo_shape(pogo.get(), "demo")
        library_pad = [n for n in load_sexpr_text(shape.library()) if True]
        placed = shape.placed("TP1", "SIG", 10, 10, (1, "NET"), None).render()
        for fragment in ("(drill 1.37)", "(size 2.29 2.29)", "(end 1 0)"):
            self.assertIn(fragment, shape.library(), fragment)
            self.assertIn(fragment, placed, fragment)
        self.assertTrue(library_pad)

    def test_the_placed_copy_carries_the_reference_and_net(self):
        shape = mounting_hole_shape(3.5, 6.0, "demo")
        placed = shape.placed("H1", "3.5mm", 4, 4, (1, "GND"), None).render()
        self.assertIn('"H1"', placed)
        self.assertIn('(net 1 "GND")', placed)


def load_sexpr_text(text: str):
    from pinside.sexpr import parse, tokenize

    return parse(tokenize(text))


class TestChannelSlots(unittest.TestCase):
    def setUp(self):
        board = transform(read_board(write(boards.uart_board())))
        self.config = from_dict(scaffold(board, "demo"))

    def test_designators_are_valid_kicad_references(self):
        """KiCad wants a letter prefix and a number; anything else fails annotation."""
        import re

        for slot in channel_slots(self.config):
            self.assertRegex(slot.test_point, r"^TP\d+$")
            self.assertRegex(slot.resistor, r"^R\d+$")
        self.assertTrue(re.match(r"^TP\d+$", channel_slots(self.config)[0].test_point))

    def test_designators_are_unique(self):
        slots = channel_slots(self.config)
        self.assertEqual(len({s.test_point for s in slots}), len(slots))

    def test_analogue_channels_take_no_series_resistor(self):
        adc = [s for s in channel_slots(self.config) if s.key in {a.name for a in self.config.adc}]
        self.assertTrue(adc)
        self.assertFalse(any(s.series for s in adc))


@unittest.skipUnless(HAVE_SYMBOLS, "KiCad symbol libraries not installed")
class TestProjectGeneration(unittest.TestCase):
    def setUp(self):
        self.dut_path = write(boards.healthy())
        board = transform(read_board(self.dut_path))
        draft = scaffold(board, "demo", board_path=self.dut_path)
        self.config = from_dict(draft, source=self.dut_path)
        self.board = resolve_board(self.config)
        self.out = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.out, ignore_errors=True)

    def test_it_writes_a_whole_project(self):
        result = generate_project(self.config, self.board, self.out)
        for name in (
            "demo.kicad_pro",
            "demo.kicad_sch",
            "demo.kicad_pcb",
            "fp-lib-table",
            "README.md",
        ):
            self.assertTrue((self.out / name).exists(), name)
        self.assertTrue(list((self.out / "pinside.pretty").glob("PogoPin_*.kicad_mod")))
        self.assertGreater(result.probes_placed, 0)

    def test_probes_land_on_the_duts_own_coordinates(self):
        """The whole point: a probe 0.3 mm out misses its pad and the board is scrap."""
        generate_project(self.config, self.board, self.out)
        expect = {t.signal: (t.fx, t.fy) for t in self.board.test_points if t.signal}
        placed = 0
        for footprint in find_all(load_sexpr(str(self.out / "demo.kicad_pcb")), "footprint"):
            props = {p[1]: p[2] for p in find_all(footprint, "property") if len(p) > 2}
            if not props.get("Reference", "").startswith("TP"):
                continue
            from pinside.sexpr import floats

            x, y = floats(child(footprint, "at"))[:2]
            want = expect[props["Value"]]
            self.assertAlmostEqual(x, want[0], places=4)
            self.assertAlmostEqual(y, want[1], places=4)
            placed += 1
        self.assertGreater(placed, 0)

    def test_regenerating_an_unchanged_config_changes_nothing(self):
        """Derived UUIDs; random ones would rewrite every line and bury the real diff."""
        generate_project(self.config, self.board, self.out)
        first = (self.out / "demo.kicad_pcb").read_text()
        generate_project(self.config, self.board, self.out, force=True)
        self.assertEqual(first, (self.out / "demo.kicad_pcb").read_text())

    def test_without_the_dut_board_there_is_nothing_to_lay_out(self):
        with self.assertRaises(ProjectError) as caught:
            generate_project(self.config, None, self.out)
        self.assertIn("PK001", {f.code for f in caught.exception.findings})

    def test_it_refuses_a_dut_that_has_not_been_laid_out(self):
        """The check that matters: unplaced test points make every hole wrong, invisibly."""
        unplaced_path = write(boards.unplaced())
        board = transform(read_board(unplaced_path), mirror="x")
        config = from_dict(
            {
                "name": "demo",
                "target": {"board": "bare", "mcu": "rp2350b"},
                "dut": {"board": unplaced_path, "require_all_test_points": False},
                "gpio": [{"name": "a", "pin": 5, "probe": "/SCL"}],
            },
            source=unplaced_path,
        )
        with self.assertRaises(ProjectError) as caught:
            generate_project(config, board, self.out)
        codes = {f.code for f in caught.exception.findings}
        self.assertIn("PS010", codes)
        self.assertEqual(list(self.out.iterdir()), [])

    def test_a_finer_probe_relaxes_the_spacing_limit(self):
        """The probe sets the pitch, so it should not need saying twice."""
        from pinside.checks import Limits
        from pinside.checks import run as check_board

        tight = check_board(self.board, Limits(probe_pitch=2.54))
        loose = check_board(self.board, Limits(probe_pitch=pogo.get("millmax_0906").min_pitch_mm))
        self.assertGreaterEqual(len(tight), len(loose))

    def test_an_invalid_config_writes_nothing(self):
        bad = from_dict(
            {
                "name": "bad",
                "target": {"board": "pico2"},
                "dut": {"require_all_test_points": False},
                "gpio": [{"name": "a", "pin": 25}],
            }
        )
        with self.assertRaises(ProjectError):
            generate_project(bad, self.board, self.out)
        self.assertEqual(list(self.out.iterdir()), [])


@unittest.skipUnless(KICAD_CLI and HAVE_SYMBOLS, "kicad-cli not installed")
class TestKiCadAcceptsIt(unittest.TestCase):
    """The only test that proves the output is a project KiCad will actually open."""

    @classmethod
    def setUpClass(cls):
        cls.out = Path(tempfile.mkdtemp())
        dut = _ROOT / "examples" / "demo-board.kicad_pcb"
        from pinside.config import load

        config = load(str(_ROOT / "examples" / "demo-fixture.json"))
        board = resolve_board(config, str(dut))
        cls.result = generate_project(config, board, cls.out, force=True)
        cls.name = config.name

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.out, ignore_errors=True)

    def _run(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(
            [KICAD_CLI, *args], capture_output=True, text=True, cwd=self.out, timeout=180
        )

    def test_erc_is_clean(self):
        report = self.out / "erc.rpt"
        proc = self._run("sch", "erc", "-o", str(report), f"{self.name}.kicad_sch")
        self.assertNotIn("Failed to load", proc.stdout + proc.stderr)
        self.assertIn(
            "Found 0 violations",
            proc.stdout,
            f"{proc.stdout}\n{report.read_text() if report.exists() else ''}",
        )

    def test_the_board_loads_and_has_no_clearance_errors(self):
        report = self.out / "drc.rpt"
        proc = self._run("pcb", "drc", "-o", str(report), f"{self.name}.kicad_pcb")
        self.assertNotIn("Failed to load", proc.stdout + proc.stderr)
        text = report.read_text()
        for serious in (
            "[shorting_items]",
            "[holes_co_located]",
            "[lib_footprint_mismatch]",
            "[courtyards_overlap]",
        ):
            self.assertNotIn(serious, text, f"{serious}\n{text[:2000]}")

    def test_the_netlist_runs_probe_through_resistor_to_the_controller(self):
        netlist = self.out / "net.net"
        proc = self._run("sch", "export", "netlist", "-o", str(netlist), f"{self.name}.kicad_sch")
        self.assertTrue(netlist.exists(), proc.stdout + proc.stderr)
        self.assertNotIn("annotation errors", proc.stdout + proc.stderr)

        nets = {}
        for net in find_all(load_sexpr(str(netlist)), "net"):
            name = atom(child(net, "name"), 1).lstrip("/")
            nets[name] = sorted(
                f"{atom(child(n, 'ref'), 1)}.{atom(child(n, 'pin'), 1)}"
                for n in find_all(net, "node")
            )

        probe_nets = [n for n in nets if n.startswith("PROBE_")]
        self.assertTrue(probe_nets)
        # A probe reaches its resistor, and the resistor reaches the controller.
        joined = [n for n in probe_nets if any(r.startswith("R") for r in nets[n])]
        self.assertTrue(joined, f"no probe net reaches a resistor: {nets}")
        fix_nets = [n for n in nets if n.startswith("FIX_")]
        self.assertTrue(
            all(any(r.startswith("U1.") for r in nets[n]) for n in fix_nets),
            "a FIX_ net does not reach the controller",
        )


if __name__ == "__main__":
    unittest.main()
