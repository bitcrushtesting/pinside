"""Unit tests. Run with: python -m unittest discover -s tests"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "tests"))

import boards
from boards import write
from pinside import read_board, run, transform
from pinside.checks import ERROR, Limits
from pinside.cli import main
from pinside.geometry import (
    chain_ring,
    point_in_ring,
    polyline_segments,
    rounded_rect_points,
)
from pinside.report import FORMATS


def cli(args: list[str]) -> int:
    """Run the CLI with its finding chatter captured, so test output stays readable."""
    with contextlib.redirect_stderr(io.StringIO()):
        return main(args)


def codes(text: str, limits: Limits | None = None) -> set[str]:
    board = transform(read_board(write(text)))
    return {f.code for f in run(board, limits)}


class TestGeometry(unittest.TestCase):
    def test_rounded_rect_closes(self):
        ring = chain_ring(polyline_segments(rounded_rect_points((0, 0), (10, 10), 2)))
        self.assertTrue(ring)
        self.assertEqual(ring[0], ring[-1])

    def test_fillet_corner_is_outside(self):
        ring = chain_ring(polyline_segments(rounded_rect_points((0, 0), (10, 10), 3)))
        self.assertTrue(point_in_ring((5, 5), ring))
        self.assertFalse(point_in_ring((0.2, 0.2), ring))  # cut away by the fillet
        self.assertTrue(point_in_ring((0.2, 5), ring))  # mid-edge, still inside

    def test_open_outline_yields_no_ring(self):
        board = read_board(write(boards._wrap(boards.segment_outline(gap=True))))
        self.assertFalse(board.outline.closed)

    def test_closed_segment_outline(self):
        board = read_board(write(boards._wrap(boards.segment_outline())))
        self.assertTrue(board.outline.closed)


class TestExtraction(unittest.TestCase):
    def setUp(self):
        self.board = transform(read_board(write(boards.healthy())))

    def test_counts(self):
        self.assertEqual(len(self.board.test_points), 6)
        self.assertEqual(len(self.board.mounting_holes), 4)

    def test_outline_measured(self):
        box = self.board.outline.bbox
        self.assertAlmostEqual(box.width, 50.0)
        self.assertAlmostEqual(box.height, 40.0)

    def test_signal_falls_back_to_value(self):
        board = transform(read_board(write(boards.troubled())))
        tp5 = next(t for t in board.test_points if t.ref == "TP5")
        self.assertTrue(tp5.anonymous_net)
        # No meaningful Value either, so the auto name is all there is.
        self.assertEqual(tp5.signal, "Net-(U2-EN)")

    def test_ground_recognised_by_net_or_value(self):
        grounds = {t.ref for t in self.board.test_points if t.is_ground}
        self.assertEqual(grounds, {"TP90", "TP91"})

    def test_origin_shifts_to_outline_corner(self):
        board = transform(read_board(write(boards.unplaced())), origin="outline")
        hole = next(h for h in board.mounting_holes if h.ref == "H1")
        self.assertAlmostEqual(hole.fx, 5.0)  # 105 - 100
        self.assertAlmostEqual(hole.fy, 5.0)  # 55 - 50

    def test_mirror_x_flips_within_the_outline(self):
        board = transform(read_board(write(boards.unplaced())), mirror="x")
        h1 = next(h for h in board.mounting_holes if h.ref == "H1")
        h2 = next(h for h in board.mounting_holes if h.ref == "H2")
        self.assertAlmostEqual(h1.fx, 45.0)  # width 50 - 5
        self.assertAlmostEqual(h2.fx, 5.0)
        self.assertAlmostEqual(h1.fy, h2.fy)  # mirroring X leaves Y alone

    def test_page_origin_keeps_raw_coordinates(self):
        board = transform(read_board(write(boards.unplaced())), origin="page")
        hole = next(h for h in board.mounting_holes if h.ref == "H1")
        self.assertAlmostEqual(hole.fx, 105.0)


class TestChecks(unittest.TestCase):
    def test_healthy_board_is_clean(self):
        self.assertEqual(codes(boards.healthy()), set())

    def test_unplaced_board_reports_placement_and_grid(self):
        found = codes(boards.unplaced())
        self.assertIn("PS010", found)  # outside the outline
        self.assertIn("PS012", found)  # on the import lattice
        self.assertIn("PS030", found)  # and no ground probe

    def test_grid_check_does_not_fire_on_a_real_layout(self):
        self.assertNotIn("PS012", codes(boards.healthy()))

    def test_troubled_board_reports_each_defect(self):
        found = codes(boards.troubled())
        for code in [
            "PS020",  # stacked probes
            "PS021",  # under the receptacle pitch
            "PS022",  # against the board edge
            "PS023",  # crowding a mounting hole
            "PS024",  # landing on a component
            "PS025",  # pad too small
            "PS026",  # probes on both sides
            "PS040",  # test point with no net
            "PS041",  # auto-named net
            "PS042",  # two probes on one net
            "PS050",  # too few mounting holes
            "PS051",
        ]:  # mismatched drills
            self.assertIn(code, found, f"expected {code}")

    def test_missing_outline_is_an_error(self):
        found = codes(boards._wrap(boards._testpoint("TP1", 1, 1, "/A")))
        self.assertIn("PS001", found)

    def test_open_outline_is_an_error(self):
        found = codes(boards._wrap(boards.segment_outline(gap=True)))
        self.assertIn("PS002", found)

    def test_limits_are_honoured(self):
        loose = Limits(probe_pitch=1.0, edge_clearance=0.1, hole_clearance=0.0)
        self.assertNotIn("PS021", codes(boards.troubled(), loose))
        self.assertNotIn("PS022", codes(boards.troubled(), loose))

    def test_one_ground_warns_and_two_do_not(self):
        found = codes(boards.without(boards.healthy(), "TP91"))
        self.assertIn("PS031", found)  # only one ground probe
        self.assertNotIn("PS030", found)  # but not "no ground at all"
        self.assertNotIn("PS031", codes(boards.healthy()))

    def test_duplicate_ground_probes_are_not_flagged(self):
        self.assertNotIn("PS042", codes(boards.healthy()))

    def test_missing_outline_still_reports_other_findings(self):
        found = codes(boards._wrap(boards._testpoint("TP1", 1, 1, "")))
        self.assertIn("PS001", found)  # no outline
        self.assertIn("PS040", found)  # and the netless probe is still noticed


class TestCLI(unittest.TestCase):
    def test_exit_codes(self):
        self.assertEqual(cli([write(boards.healthy()), "-f", "json", "-o", os.devnull]), 0)
        self.assertEqual(cli([write(boards.unplaced()), "-f", "json", "-o", os.devnull]), 2)

    def test_strict_promotes_warnings(self):
        # Two mounting holes let the board pivot: a warning, not an error.
        path = write(boards.without(boards.healthy(), "H3", "H4"))
        self.assertEqual(cli([path, "-f", "json", "-o", os.devnull]), 0)
        self.assertEqual(cli([path, "-f", "json", "-o", os.devnull, "--strict"]), 1)

    def test_ignore_suppresses_a_code(self):
        path = write(boards.unplaced())
        self.assertEqual(
            cli([path, "-f", "json", "-o", os.devnull, "--ignore", "PS010,PS012,PS030"]), 0
        )

    def test_every_format_produces_output(self):
        board = transform(read_board(write(boards.troubled())))
        findings = run(board)
        for name, emit in FORMATS.items():
            buf = io.StringIO()
            emit(board, findings, buf)
            self.assertTrue(buf.getvalue().strip(), f"{name} produced nothing")

    def test_json_is_valid_and_carries_findings(self):
        board = transform(read_board(write(boards.troubled())))
        buf = io.StringIO()
        FORMATS["json"](board, run(board), buf)
        payload = json.loads(buf.getvalue())
        self.assertEqual(len(payload["test_points"]), 10)
        self.assertTrue(any(f["severity"] == ERROR for f in payload["findings"]))

    def test_missing_file_is_usage_error(self):
        self.assertEqual(cli(["/nonexistent/board.kicad_pcb"]), 3)


class InitCommand(unittest.TestCase):
    """`pinside init` is the entry point for everything downstream, so it has to actually run.

    It did not: the positional `board` and the carrier option `--board` shared an argparse dest,
    the option won, and every invocation looked the .kicad_pcb path up in the module catalogue
    and refused. The end-to-end CI job ran this exact command and it failed there too.
    """

    def _draft(self, args: list[str]) -> dict:
        out = Path(tempfile.mkdtemp(prefix="pinside-init-")) / "fixture.json"
        self.assertEqual(cli(["init", write(boards.uart_board()), "-o", str(out), *args]), 0)
        return json.loads(out.read_text())

    def test_init_drafts_a_config_from_a_board(self):
        draft = self._draft([])
        self.assertTrue(draft["name"])
        self.assertIn("target", draft)

    def test_the_carrier_defaults_to_the_pico(self):
        self.assertEqual(self._draft([])["target"]["board"], "pico2")

    def test_the_carrier_can_be_chosen(self):
        draft = self._draft(["--carrier", "bare", "--mcu", "rp2350b"])
        self.assertEqual(draft["target"]["board"], "bare")
        self.assertEqual(draft["target"]["mcu"], "rp2350b")

    def test_the_positional_is_still_the_board_path(self):
        # The collision this guards against is silent: argparse accepts both spellings and one
        # of them quietly wins, so only reading a value back out catches it.
        draft = self._draft([])
        self.assertIn(".kicad_pcb", draft["dut"]["board"])

    def test_a_drafted_config_generates(self):
        board = write(boards.uart_board())
        cfg = Path(tempfile.mkdtemp(prefix="pinside-init-")) / "fixture.json"
        self.assertEqual(cli(["init", board, "-o", str(cfg)]), 0)
        self.assertEqual(cli(["generate", str(cfg), "--dry-run"]), 0)


class Cutouts(unittest.TestCase):
    """Edge.Cuts holds every edge the board has, not one ring."""

    def test_a_slot_is_not_a_broken_outline(self):
        # The regression this guards: a board with a milled window used to chain into no ring
        # at all and report PS002, an error, on geometry a fab would cut without comment.
        board = transform(read_board(write(boards.slotted())))
        self.assertTrue(board.outline.closed)
        self.assertEqual(len(board.outline.cutouts), 1)
        self.assertNotIn("PS002", codes(boards.slotted()))
        self.assertIn("PS003", codes(boards.slotted()))

    def test_there_is_no_board_inside_a_cutout(self):
        board = transform(read_board(write(boards.slotted())))
        self.assertTrue(board.outline.within_perimeter(25, 20))
        self.assertTrue(board.outline.in_cutout(25, 20))
        self.assertFalse(board.outline.contains(25, 20))
        self.assertTrue(board.outline.contains(10, 10))

    def test_a_probe_over_a_cutout_is_an_error(self):
        found = codes(boards.probe_over_a_slot())
        self.assertIn("PS013", found)
        # Not also PS010: the probe was placed, it was placed over a hole. Saying "never
        # placed" would send somebody to re-run the netlist import instead of moving the pad.
        self.assertNotIn("PS010", found)

    def test_a_second_outline_is_reported_not_swallowed(self):
        found = codes(boards.panelised())
        self.assertIn("PS004", found)
        self.assertNotIn("PS002", found)

    def test_an_open_edge_is_still_an_error(self):
        text = boards._wrap(boards.segment_outline(gap=True))
        self.assertIn("PS002", codes(text))


class NetIdentity(unittest.TestCase):
    """Whether the board's netlist survives being opened in KiCad.

    Through KiCad 9 a net is identified by its ordinal and the name is a label. pinside reads the
    name, which is why it called its own example board clean for two releases while KiCad, asked
    for the same board's netlist, reported fifteen of sixteen test points as N/C.

    Every expectation here was checked against `kicad-cli pcb export ipcd356`.
    """

    def test_one_ordinal_with_several_names_is_an_error(self):
        found = codes(boards.sharing_one_net_ordinal(boards.healthy()))
        self.assertIn("PS043", found)

    def test_the_finding_names_the_nets_and_the_probes(self):
        board = transform(read_board(write(boards.sharing_one_net_ordinal(boards.healthy()))))
        finding = next(f for f in run(board) if f.code == "PS043")
        self.assertIn("/SCL", finding.refs[0])
        self.assertIn("/SDA", finding.refs[0])
        # And which test points go dark, in board order rather than lexicographic.
        self.assertIn("TP1, TP2", finding.detail)

    def test_a_properly_numbered_board_says_nothing(self):
        self.assertNotIn("PS043", codes(boards.healthy()))
        self.assertNotIn("PS044", codes(boards.healthy()))

    def test_a_named_net_on_ordinal_zero_is_an_error(self):
        # Net 0 is KiCad's no-connection net: it ignores the name, pinside would not.
        found = codes(boards.on_net_zero(boards.healthy(), "/SCL"))
        self.assertIn("PS044", found)

    def test_the_kicad_10_form_has_no_ordinals_to_collide(self):
        # That format dropped the ordinal, so neither check can apply and neither should fire.
        board = transform(read_board(write(boards.as_kicad10(boards.healthy()))))
        found = {f.code for f in run(board)}
        self.assertNotIn("PS043", found)
        self.assertNotIn("PS044", found)
        self.assertEqual(board.net_ordinals, {})

    def test_several_ordinals_sharing_a_name_is_not_a_finding(self):
        """KiCad merges those into one net, which is what pinside already reads. No defect."""
        text = boards.healthy().replace('(net 2 "/SDA")', '(net 99 "/SCL")')
        self.assertNotIn("PS043", codes(text))

    def test_the_board_that_shipped_in_0_1_0_would_have_been_caught(self):
        """The regression this check exists for.

        The example board gave all sixteen test points net ordinal 1 with sixteen different
        names. `pinside check` passed it. Opening it in KiCad and saving destroyed the netlist.
        """
        shipped = boards.sharing_one_net_ordinal(
            (_ROOT / "examples" / "demo-board.kicad_pcb").read_text(encoding="utf-8")
        )
        board = transform(read_board(write(shipped)))
        finding = next(f for f in run(board) if f.code == "PS043")
        self.assertEqual(finding.severity, ERROR)
        self.assertIn("/DUT_TXD", finding.refs[0])
        self.assertIn("TP90", finding.detail)

    def test_the_example_board_as_it_stands_is_clean(self):
        board = transform(read_board(str(_ROOT / "examples" / "demo-board.kicad_pcb")))
        self.assertEqual([f for f in run(board) if f.code in ("PS043", "PS044")], [])
        # Sixteen test points, fifteen distinct nets (the two grounds share one).
        self.assertEqual(len(board.net_ordinals), 15)


class SignalCoverage(unittest.TestCase):
    """What the board has that the fixture will not reach."""

    def test_unprobed_rails_and_reset_lines_are_reported(self):
        found = codes(boards.unreachable_rails())
        self.assertIn("PS033", found)
        self.assertIn("PS034", found)

    def test_a_board_whose_rails_are_probed_says_nothing(self):
        self.assertNotIn("PS033", codes(boards.healthy()))
        self.assertNotIn("PS034", codes(boards.healthy()))

    def test_the_finding_names_the_nets(self):
        board = transform(read_board(write(boards.unreachable_rails())))
        rails = next(f for f in run(board) if f.code == "PS033")
        self.assertCountEqual(rails.refs, ["+3V3", "+1V8"])
        reset = next(f for f in run(board) if f.code == "PS034")
        self.assertIn("MCU_NRST", reset.refs)


class ProbeBody(unittest.TestCase):
    def test_a_receptacle_body_overlapping_a_part_is_reported(self):
        # U1's pad envelope starts at x=22. TP1 at x=21.5 clears it by 0.5 mm, so the tip
        # lands on copper, and the 0.85 mm body radius of a 0985 receptacle does not fit.
        body = boards.rect_outline()
        body += boards._testpoint("TP1", 21.5, 20.0, "/SIG")
        body += boards._testpoint("TP90", 10, 30, "GND", value="GND")
        body += boards._testpoint("TP91", 14, 30, "GND", value="GND")
        body += boards._part("U1", 24.0, 20.0, w=4.0, h=2.0)
        found = codes(boards._wrap(body))
        self.assertIn("PS027", found)
        self.assertNotIn("PS024", found)  # the tip itself is clear

    def test_a_finer_probe_relaxes_it(self):
        # A soldered_1mm body is 1.00 mm across, so 0.5 mm of clearance is exactly enough.
        body = boards.rect_outline()
        body += boards._testpoint("TP1", 21.5, 20.0, "/SIG")
        body += boards._testpoint("TP90", 10, 30, "GND", value="GND")
        body += boards._testpoint("TP91", 14, 30, "GND", value="GND")
        body += boards._part("U1", 24.0, 20.0, w=4.0, h=2.0)
        self.assertNotIn("PS027", codes(boards._wrap(body), Limits(probe_body=1.0)))


class NetRecordFormats(unittest.TestCase):
    """KiCad 9 writes (net <ordinal> "NAME"); KiCad 10 dropped the ordinal.

    Reading only one of the two forms does not fail loudly. It yields test points whose net is
    the string "3", which classifies as power, probes nothing, and produces a fixture wired to
    signals that do not exist.
    """

    def test_both_forms_read_the_same_nets(self):
        old = transform(read_board(write(boards.healthy())))
        new = transform(read_board(write(boards.as_kicad10(boards.healthy()))))
        self.assertEqual(
            [(t.ref, t.net, t.signal) for t in old.test_points],
            [(t.ref, t.net, t.signal) for t in new.test_points],
        )
        self.assertIn("/SCL", [t.net for t in new.test_points])

    def test_both_forms_produce_the_same_findings(self):
        for board in (boards.healthy(), boards.troubled(), boards.unplaced()):
            with self.subTest(board=board[:40]):
                self.assertEqual(codes(board), codes(boards.as_kicad10(board)))

    def test_the_kicad_10_form_still_grounds_mounting_holes(self):
        board = transform(read_board(write(boards.as_kicad10(boards.healthy()))))
        self.assertTrue(all(h.net == "GND" for h in board.mounting_holes))


if __name__ == "__main__":
    unittest.main()
