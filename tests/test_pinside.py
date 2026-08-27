"""Unit tests. Run with: python -m unittest discover -s tests"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
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
        one = boards.healthy().replace(boards._testpoint("TP91", 22, 20, "GND", value="GND"), "")
        found = codes(one)
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
        text = boards.healthy()
        for ref, x, y in [("H3", 5, 35), ("H4", 45, 35)]:
            text = text.replace(boards._hole(ref, x, y, net="GND"), "")
        path = write(text)
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


if __name__ == "__main__":
    unittest.main()
