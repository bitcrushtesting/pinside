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
from typing import ClassVar

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "tests"))

import boards
from boards import write
from pinside import modules, pogo, read_board, run, targets, transform
from pinside.config import from_dict, load, resolve_board, validate
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

# Set in the CI job that runs inside the KiCad container. A skip is the right
# behaviour on a machine without KiCad and exactly the wrong one there: a job
# whose whole purpose is these tests goes green having run none of them.
REQUIRE_KICAD = os.environ.get("PINSIDE_REQUIRE_KICAD") == "1"


def need_kicad(case: unittest.TestCase, what: str) -> None:
    """Skip, unless we were promised KiCad is here, in which case fail."""
    if REQUIRE_KICAD:
        case.fail(f"PINSIDE_REQUIRE_KICAD=1 but {what}")
    case.skipTest(what)


class TestModules(unittest.TestCase):
    def test_the_default_is_a_pico(self):
        module = modules.get(modules.DEFAULT)
        self.assertIsNotNone(module)
        self.assertIn("Pico", module.description)

    def test_every_module_header_matches_kicads_own_symbol(self):
        """The generated schematic uses these symbols, so the two must agree on pin numbers.

        Every module, not just the pico2: this is the check that makes adding a carrier board
        safe, because a header map transcribed by hand is otherwise unverifiable until a probe
        lands on the wrong pin.
        """
        if not HAVE_SYMBOLS:
            need_kicad(self, "KiCad symbol libraries not installed")
        for name, module in modules.MODULES.items():
            with self.subTest(module=name):
                pins = library.symbol_pins(library.load_symbol(module.symbol))
                for gpio, header in module.header.items():
                    # KiCad spells the analogue pins GPIO26_ADC0 and so on.
                    self.assertRegex(
                        pins[str(header)],
                        rf"^GPIO{gpio}(_ADC\d)?$",
                        f"{name}: GPIO{gpio} is not on header pin {header}",
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


@unittest.skipIf(not KICAD_CLI and not REQUIRE_KICAD, "kicad-cli not installed")
class TestNetIdentityAgainstKiCad(unittest.TestCase):
    """PS043 and PS044 claim KiCad loses nets. This asks KiCad.

    `pcb export ipcd356` is KiCad's own answer to "what is this board's netlist", so a pad it
    reports as N/C is one KiCad thinks connects to nothing. Without this the two checks would
    rest on a description of KiCad's behaviour rather than on its behaviour.
    """

    def netlist(self, text: str) -> dict[str, str]:
        """ref -> the net name KiCad says that pad is on."""
        out = Path(tempfile.mkdtemp(prefix="pinside-net-"))
        self.addCleanup(shutil.rmtree, out, True)
        (out / "b.kicad_pcb").write_text(text, encoding="utf-8")
        proc = subprocess.run(
            [KICAD_CLI, "pcb", "export", "ipcd356", "-o", str(out / "b.d356"), "b.kicad_pcb"],
            capture_output=True,
            text=True,
            cwd=out,
            timeout=180,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        found = {}
        for line in (out / "b.d356").read_text(encoding="utf-8").splitlines():
            if line.startswith(("317", "327")):
                # 3x7, then the net padded to 17 columns, then the reference.
                found[line[20:26].strip()] = line[3:20].strip()
        return found

    def codes_for(self, text: str) -> set[str]:
        return {f.code for f in run(transform(read_board(boards.write(text))))}

    def test_kicad_loses_the_nets_pinside_flags_with_ps043(self):
        broken = boards.sharing_one_net_ordinal(boards.healthy())
        self.assertIn("PS043", self.codes_for(broken))

        seen = self.netlist(broken)
        lost = sorted(ref for ref, net in seen.items() if net == "N/C")
        self.assertTrue(lost, f"KiCad kept every net; PS043 would be a false positive: {seen}")

    def test_kicad_keeps_the_nets_when_pinside_is_quiet(self):
        good = boards.healthy()
        self.assertNotIn("PS043", self.codes_for(good))

        seen = self.netlist(good)
        self.assertFalse(
            [ref for ref, net in seen.items() if net == "N/C"],
            f"KiCad dropped a net pinside called fine: {seen}",
        )
        self.assertEqual(seen.get("TP1"), "/SCL")

    def test_kicad_ignores_the_name_on_net_zero_as_ps044_says(self):
        broken = boards.on_net_zero(boards.healthy(), "/SCL")
        self.assertIn("PS044", self.codes_for(broken))
        self.assertEqual(self.netlist(broken).get("TP1"), "N/C")

    def test_several_ordinals_sharing_a_name_really_are_merged(self):
        """The case pinside deliberately does not flag, confirmed rather than assumed."""
        merged = boards.healthy().replace('(net 2 "/SDA")', '(net 99 "/SCL")')
        self.assertNotIn("PS043", self.codes_for(merged))
        seen = self.netlist(merged)
        self.assertEqual(seen.get("TP1"), "/SCL")
        self.assertEqual(seen.get("TP2"), "/SCL")

    def test_the_example_board_keeps_every_net(self):
        """The one that matters: the board pinside ships has to survive being opened."""
        seen = self.netlist((_ROOT / "examples" / "demo-board.kicad_pcb").read_text())
        self.assertFalse([r for r, n in seen.items() if n == "N/C"], seen)
        self.assertEqual(seen.get("TP1"), "/DUT_TXD")
        self.assertEqual(seen.get("TP14"), "/+3.3V")


@unittest.skipIf(not HAVE_SYMBOLS and not REQUIRE_KICAD, "KiCad symbol libraries not installed")
class TestDerivedSymbols(unittest.TestCase):
    """Symbols that inherit from another, which is how KiCad writes half the parts pinside uses.

    `(symbol "RaspberryPi_Pico_W" (extends "RaspberryPi_Pico"))` carries properties and nothing
    else. A schematic's lib_symbols block has to stand alone, so a copy of that reaches KiCad
    with no pins: the file opens, the controller has nothing to wire to, and every net in the
    design is isolated. `pinside project --board pico2w` produced exactly that.
    """

    DERIVED = "MCU_Module:RaspberryPi_Pico_W"
    ROOT = "MCU_Module:RaspberryPi_Pico"

    def test_a_derived_symbol_arrives_with_its_parents_pins(self):
        derived = library.symbol_pins(library.load_symbol(self.DERIVED))
        root = library.symbol_pins(library.load_symbol(self.ROOT))
        self.assertEqual(derived, root)
        self.assertEqual(len(derived), 40)

    def test_the_extends_does_not_survive(self):
        # A dangling extends resolves to nothing, because the parent is not in lib_symbols.
        self.assertNotIn("extends", library.load_symbol(self.DERIVED))

    def test_it_keeps_its_own_footprint_and_datasheet(self):
        # The reason it is a separate symbol at all. Taking the parent's would put the wrong
        # footprint on the board.
        definition = library.load_symbol(self.DERIVED)
        self.assertIn("RaspberryPi_Pico_W_SMD_HandSolder", definition)
        self.assertNotIn("RaspberryPi_Pico_Common_Unspecified", definition)

    def test_the_units_are_renamed_to_match_the_symbol(self):
        """KiCad matches a unit to its symbol by name prefix, and says nothing when it cannot.

        Leaving the units called RaspberryPi_Pico_1_1 inside a symbol called
        RaspberryPi_Pico_W yields "Failed to load schematic" and no further explanation.
        """
        definition = library.load_symbol(self.DERIVED)
        self.assertIn('(symbol "RaspberryPi_Pico_W_1_1"', definition)
        self.assertNotIn('(symbol "RaspberryPi_Pico_1_1"', definition)

    def test_the_flattened_symbol_keeps_the_attributes_kicad_requires(self):
        # A derived symbol has none of these; they come from the parent, and without them
        # KiCad refuses the file.
        definition = library.load_symbol(self.DERIVED)
        for token in ("(pin_names", "(in_bom", "(on_board", "(exclude_from_sim"):
            self.assertIn(token, definition, f"{token} was lost in flattening")

    def test_a_root_symbol_is_untouched(self):
        raw = Path(library.symbol_dir(), "MCU_Module.kicad_sym").read_text(encoding="utf-8")
        definition = library.load_symbol(self.ROOT)
        # Everything but the one renamed line should be verbatim library text.
        body = definition.split("\n", 1)[1]
        self.assertIn(body[:200], raw)


@unittest.skipIf(not HAVE_SYMBOLS and not REQUIRE_KICAD, "KiCad symbol libraries not installed")
class TestTargetsAgainstKiCad(unittest.TestCase):
    """The function map is a datasheet transcribed by hand, which is how it goes wrong.

    KiCad's own symbol library is a second, independent transcription of the same pinout, so
    disagreement between the two means one of them is wrong and it is worth knowing which.
    """

    SYMBOLS: ClassVar[dict[str, str]] = {
        "rp2350a": "MCU_RaspberryPi:RP2350A",
        "rp2350b": "MCU_RaspberryPi:RP2350B",
        "rp2354a": "MCU_RaspberryPi:RP2354A",
        "rp2354b": "MCU_RaspberryPi:RP2354B",
    }

    def test_every_target_has_a_kicad_symbol(self):
        for name in targets.TARGETS:
            self.assertIn(name, self.SYMBOLS, f"{name} has no KiCad symbol to check against")

    def test_the_gpio_count_matches_kicads_symbol(self):
        for name, symbol in self.SYMBOLS.items():
            if name in targets.SAME_PINOUT:
                continue  # checked through the part it extends, below
            with self.subTest(target=name):
                pins = library.symbol_pins(library.load_symbol(symbol))
                gpio = {v.split("/")[0] for v in pins.values() if v.startswith("GPIO")}
                self.assertEqual(len(gpio), targets.get(name).gpio_count)

    def test_the_adc_pins_match_kicads_symbol(self):
        for name, symbol in self.SYMBOLS.items():
            if name in targets.SAME_PINOUT:
                continue
            with self.subTest(target=name):
                pins = library.symbol_pins(library.load_symbol(symbol))
                # KiCad spells them GPIO26/ADC0; the target holds {gpio: adc channel}.
                from_symbol = {}
                for label in pins.values():
                    if "/ADC" in label and label.startswith("GPIO"):
                        gpio, adc = label.split("/")
                        from_symbol[int(gpio[4:])] = int(adc[3:])
                self.assertEqual(from_symbol, targets.get(name).adc_pins)

    def test_the_stacked_flash_parts_really_do_share_a_pinout(self):
        """Not an assumption: KiCad writes it as `(symbol "RP2354A" (extends "RP2350A"))`."""
        raw = Path(library.symbol_dir(), "MCU_RaspberryPi.kicad_sym").read_text(encoding="utf-8")
        for stacked, plain in targets.SAME_PINOUT.items():
            with self.subTest(target=stacked):
                block = raw[raw.index(f'(symbol "{stacked.upper()}"') :][:200]
                self.assertIn(f'(extends "{plain.upper()}")', block)
                # And pinside's own two entries agree with each other.
                self.assertEqual(targets.get(stacked).gpio_count, targets.get(plain).gpio_count)
                self.assertEqual(targets.get(stacked).adc_pins, targets.get(plain).adc_pins)


class TestProbeProvenance(unittest.TestCase):
    """Where the probe dimensions came from, which is not a detail: they become drill sizes."""

    def test_every_probe_records_its_source(self):
        for name, probe in pogo.PROBES.items():
            with self.subTest(probe=name):
                self.assertTrue(probe.source, f"{name} does not say where its numbers came from")

    def test_verification_is_opt_in_rather_than_assumed(self):
        # An empty `verified` is the honest state for a value read off a catalogue page. This
        # asserts the field exists and defaults to unverified, not that it stays that way: when
        # somebody does check one against a drawing they fill it in and this still passes.
        for probe in pogo.PROBES.values():
            self.assertIsInstance(probe.verified, str)
        self.assertEqual(
            len(pogo.unverified()), len([p for p in pogo.PROBES.values() if not p.verified])
        )

    def test_the_generated_readme_says_so(self):
        from pinside.kicad.project import _readme

        config = load(str(_ROOT / "examples" / "demo-fixture.json"))
        text = _readme(config, None, 14, [])
        if config.probe_part.verified:
            self.assertIn(config.probe_part.verified, text)
        else:
            # Somebody ordering a board off this needs to see it, not find it in a docstring.
            self.assertIn("before you order", text)


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


@unittest.skipIf(not HAVE_SYMBOLS and not REQUIRE_KICAD, "KiCad symbol libraries not installed")
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


@unittest.skipIf(not HAVE_SYMBOLS and not REQUIRE_KICAD, "KiCad symbol libraries not installed")
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


@unittest.skipIf(not (KICAD_CLI and HAVE_SYMBOLS) and not REQUIRE_KICAD, "kicad-cli not installed")
class TestHardwareNote(unittest.TestCase):
    """The plate-force arithmetic, which needs no KiCad."""

    def setUp(self):
        self.probe = pogo.get("millmax_0985")

    def note(self, probes: int, holes: int = 4) -> str:
        from pinside.kicad.project import _hardware_note

        return _hardware_note(probes, self.probe, holes)

    def test_a_small_fixture_says_a_thumbscrew_will_do(self):
        self.assertIn("thumbscrew", self.note(4))

    def test_a_medium_fixture_asks_for_a_clamp(self):
        self.assertIn("toggle clamp", self.note(60))

    def test_a_large_fixture_warns_about_plate_deflection(self):
        text = self.note(200)
        self.assertIn("pneumatic", text)
        self.assertIn("deflects", text)

    def test_the_per_standoff_load_divides_by_the_holes(self):
        # 60 probes at 0.75 N is 45 N over four holes: 11 N each.
        self.assertIn("11 N", self.note(60, holes=4))

    def test_no_mounting_holes_says_so_rather_than_dividing_by_zero(self):
        text = self.note(14, holes=0)
        self.assertIn("no mounting holes", text)
        self.assertNotIn("per standoff", text)

    def test_the_travel_the_standoff_has_to_allow_is_named(self):
        self.assertIn(f"{self.probe.travel_mm} mm of probe travel", self.note(14))


class TestKiCadAcceptsIt(unittest.TestCase):
    """The only test that proves the output is a project KiCad will actually open."""

    @classmethod
    def setUpClass(cls):
        cls.out = Path(tempfile.mkdtemp())
        dut = _ROOT / "examples" / "demo-board.kicad_pcb"
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

    def test_the_board_carries_a_ground_pour(self):
        """Not routing, and not optional: on a fixture the pour is most of the return path.

        DRC passing says the zone is legal. This says it is there at all, on both copper
        layers and on GND, which DRC would be just as happy without.
        """
        text = (self.out / f"{self.name}.kicad_pcb").read_text()
        self.assertIn("(zone", text)
        self.assertIn('(name "GND pour")', text)
        zone = text[text.index("(zone") :]
        self.assertIn('"F.Cu"', zone[:400])
        self.assertIn('"B.Cu"', zone[:400])

    def test_the_pour_stays_inside_the_board(self):
        from pinside.kicad.pcb import GROUND_POUR_INSET_MM

        board = resolve_board(
            load(str(_ROOT / "examples" / "demo-fixture.json")),
            str(_ROOT / "examples" / "demo-board.kicad_pcb"),
        )
        box = board.outline.bbox
        text = (self.out / f"{self.name}.kicad_pcb").read_text()
        zone = text[text.index('(name "GND pour")') :]
        pts = zone[zone.index("(polygon") : zone.index("(polygon") + 400]
        coords = [
            tuple(float(v) for v in line.strip()[4:-1].split())
            for line in pts.splitlines()
            if line.strip().startswith("(xy ")
        ]
        self.assertEqual(len(coords), 4)
        for x, y in coords:
            # A pour drawn past the outline is copper KiCad clips and DRC complains about.
            self.assertGreaterEqual(x, GROUND_POUR_INSET_MM - 1e-6)
            self.assertGreaterEqual(y, GROUND_POUR_INSET_MM - 1e-6)
            self.assertLessEqual(x, box.width - GROUND_POUR_INSET_MM + 1e-6)
            self.assertLessEqual(y, box.height - GROUND_POUR_INSET_MM + 1e-6)

    def test_the_readme_turns_the_force_into_hardware(self):
        text = (self.out / "README.md").read_text()
        self.assertIn("Closing force", text)
        # Newtons alone are not actionable. The generated note has to reach the thing somebody
        # actually has to buy or decide: the clamp, and the standoffs carrying the load.
        self.assertIn("per standoff", text)
        self.assertIn("kgf", text)
        self.assertIn("travel", text)

    def test_a_pico2w_project_is_equally_clean(self):
        """The carrier whose symbol is derived, run through KiCad end to end.

        Every other test here uses the pico2, whose symbol is a root symbol. The pico2w was the
        one that produced a schematic KiCad could not load at all, and no unit test of the
        emitter noticed: the file was well-formed S-expressions the whole time.
        """
        config = load(str(_ROOT / "examples" / "demo-fixture.json"))
        config.board = "pico2w"
        config.mcu = "rp2350a"
        board = resolve_board(config, str(_ROOT / "examples" / "demo-board.kicad_pcb"))
        out = Path(tempfile.mkdtemp(prefix="pinside-picow-"))
        self.addCleanup(shutil.rmtree, out, True)
        generate_project(config, board, out, force=True)

        proc = subprocess.run(
            [KICAD_CLI, "sch", "erc", "-o", str(out / "erc.rpt"), f"{config.name}.kicad_sch"],
            capture_output=True,
            text=True,
            cwd=out,
            timeout=180,
        )
        combined = proc.stdout + proc.stderr
        self.assertNotIn("Failed to load", combined, combined)
        self.assertIn("Found 0 violations", combined, combined)

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
