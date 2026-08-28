"""The baseline file: findings a board has already been judged on.

The property worth testing is the one that makes a baseline safe to check in. `--ignore PS041`
silences a code forever; a baseline silences one occurrence of it. If the second thing quietly
behaved like the first, a board could pick up a new defect under an existing suppression and CI
would stay green.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "tests"))

import boards
from boards import write as write_board
from pinside import baseline, read_board, run, transform
from pinside.checks import Finding
from pinside.cli import main


def cli(args: list[str]) -> int:
    with contextlib.redirect_stderr(io.StringIO()):
        return main(args)


def scratch(name: str) -> str:
    return str(Path(tempfile.mkdtemp(prefix="pinside-baseline-")) / name)


class Matching(unittest.TestCase):
    def test_an_entry_with_no_refs_covers_the_whole_code(self):
        entry = baseline.Entry(code="PS041")
        self.assertTrue(entry.matches(Finding("PS041", "info", "x", ["TP1"])))
        self.assertTrue(entry.matches(Finding("PS041", "info", "x", [])))
        self.assertFalse(entry.matches(Finding("PS042", "info", "x", [])))

    def test_an_entry_with_refs_covers_only_those(self):
        entry = baseline.Entry(code="PS041", refs={"TP5"})
        self.assertTrue(entry.matches(Finding("PS041", "info", "x", ["TP5"])))
        self.assertFalse(entry.matches(Finding("PS041", "info", "x", ["TP9"])))

    def test_a_new_reference_brings_the_finding_back(self):
        # The whole point. TP5 was accepted; TP5 and TP9 together were not.
        entry = baseline.Entry(code="PS041", refs={"TP5"})
        self.assertFalse(entry.matches(Finding("PS041", "info", "x", ["TP5", "TP9"])))

    def test_a_finding_that_lost_a_reference_stays_accepted(self):
        # Fixing half of an accepted finding should not make the other half fail: the refs that
        # remain are a subset of what was already judged.
        entry = baseline.Entry(code="PS041", refs={"TP5", "TP9"})
        self.assertTrue(entry.matches(Finding("PS041", "info", "x", ["TP5"])))


class RoundTrip(unittest.TestCase):
    def setUp(self):
        self.board = write_board(boards.troubled())
        self.path = scratch("baseline.json")

    def test_a_written_baseline_accepts_the_board_it_came_from(self):
        self.assertEqual(
            cli([self.board, "--write-baseline", self.path, "-f", "json", "-o", "/dev/null"]), 0
        )
        # troubled() has errors, so without the baseline this is exit 2.
        self.assertEqual(cli([self.board, "-f", "json", "-o", "/dev/null"]), 2)
        self.assertEqual(
            cli([self.board, "--baseline", self.path, "-f", "json", "-o", "/dev/null"]), 0
        )

    def test_every_entry_has_an_empty_note_to_fill_in(self):
        cli([self.board, "--write-baseline", self.path, "-f", "json", "-o", "/dev/null"])
        data = json.loads(Path(self.path).read_text())
        self.assertTrue(data["accepted"])
        self.assertTrue(all(e["note"] == "" for e in data["accepted"]))
        self.assertEqual(data["version"], baseline.VERSION)

    def test_the_baseline_records_which_board_it_was_taken_from(self):
        cli([self.board, "--write-baseline", self.path, "-f", "json", "-o", "/dev/null"])
        self.assertEqual(json.loads(Path(self.path).read_text())["board"], self.board)

    def test_a_baseline_does_not_hide_a_new_finding(self):
        cli([self.board, "--write-baseline", self.path, "-f", "json", "-o", "/dev/null"])
        # The same board with one more probe stacked on an existing one: PS020's refs change,
        # so the accepted entry no longer covers it.
        worse = boards.troubled().replace(
            "\n)\n", boards._testpoint("TP11", 10, 10, "/ALSO_DUP") + "\n)\n"
        )
        self.assertEqual(
            cli([write_board(worse), "--baseline", self.path, "-f", "json", "-o", "/dev/null"]),
            2,
        )


class BadFiles(unittest.TestCase):
    def test_a_missing_baseline_is_a_usage_error_not_a_silent_pass(self):
        board = write_board(boards.healthy())
        self.assertEqual(
            cli([board, "--baseline", "/nonexistent/b.json", "-f", "json", "-o", "/dev/null"]), 3
        )

    def test_a_future_version_is_refused(self):
        path = scratch("future.json")
        Path(path).write_text(json.dumps({"version": 99, "accepted": []}))
        with self.assertRaises(baseline.BaselineError):
            baseline.load(path)

    def test_malformed_json_is_refused(self):
        path = scratch("bad.json")
        Path(path).write_text("{not json")
        with self.assertRaises(baseline.BaselineError):
            baseline.load(path)

    def test_an_entry_without_a_code_is_refused(self):
        path = scratch("nocode.json")
        Path(path).write_text(json.dumps({"version": 1, "accepted": [{"refs": ["TP1"]}]}))
        with self.assertRaises(baseline.BaselineError):
            baseline.load(path)


class JsonFindings(unittest.TestCase):
    """generate and project write their output to a directory and their findings to stderr,
    which leaves a caller nothing to parse. --json puts the findings on stdout."""

    def _run(self, args: list[str]) -> tuple[int, dict]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = main(args)
        return status, json.loads(out.getvalue())

    def test_generate_dry_run_reports_json(self):
        board = write_board(boards.uart_board())
        cfg = scratch("fixture.json")
        self.assertEqual(cli(["init", board, "-o", cfg]), 0)
        status, payload = self._run(["generate", cfg, "--dry-run", "--json"])
        self.assertEqual(status, 0)
        self.assertIn("findings", payload)
        self.assertEqual(payload["errors"], 0)
        self.assertFalse(payload["written"])

    def test_a_refusal_still_reports_json(self):
        # A config that cannot generate must still be machine-readable: a caller that only
        # parses the success path has to fall back to scraping stderr exactly when it matters.
        board = write_board(boards.uart_board())
        cfg = scratch("fixture.json")
        self.assertEqual(cli(["init", board, "-o", cfg]), 0)
        data = json.loads(Path(cfg).read_text())
        data["gpio"] = [{"name": "nope", "pin": 999, "probe": "PWR_FLT"}]
        Path(cfg).write_text(json.dumps(data))

        status, payload = self._run(["generate", cfg, "--dry-run", "--json"])
        self.assertEqual(status, 2)
        self.assertGreater(payload["errors"], 0)
        self.assertTrue(any(f["severity"] == "error" for f in payload["findings"]))

    def test_generate_writes_and_reports_what_it_wrote(self):
        board = write_board(boards.uart_board())
        cfg = scratch("fixture.json")
        self.assertEqual(cli(["init", board, "-o", cfg]), 0)
        out = scratch("firmware")
        status, payload = self._run(["generate", cfg, "--out", out, "--json"])
        self.assertEqual(status, 0)
        self.assertTrue(payload["written"])
        self.assertTrue(payload["config_hash"])
        self.assertIn("CMakeLists.txt", " ".join(payload["files"]))


class Splitting(unittest.TestCase):
    def test_split_partitions_every_finding(self):
        board = transform(read_board(write_board(boards.troubled())))
        findings = run(board)
        accepted = baseline.from_findings(findings[:3])
        kept, suppressed = accepted.split(findings)
        self.assertEqual(len(kept) + len(suppressed), len(findings))
        self.assertEqual(len(suppressed), 3)


if __name__ == "__main__":
    unittest.main()
