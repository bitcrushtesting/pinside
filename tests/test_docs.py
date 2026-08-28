"""Tests that keep the documentation honest about the code.

Finding codes are a public interface: people put them in `--ignore` lists and in baselines, and
the README's two tables are the only place their meaning is written down. Both the tables and
the codes are maintained by hand, so they drift, and they have: PF003 and PF004 existed for a
release without appearing in any table. These tests make that a failure rather than a surprise.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "tests"))

_README = (_ROOT / "README.md").read_text(encoding="utf-8")
_SOURCE = _ROOT / "src" / "pinside"

# A code as it appears where a Finding is constructed: a bare quoted literal.
#
# PS board checks, PF config checks, PK project generation. The PK family was missed for a
# release because this pattern only looked for the first two, which is the same drift the test
# exists to catch, one level up.
_EMITTED = re.compile(r'"(P[SFK]\d{3})"')
# A code as the README refers to it, either alone or as an endpoint of a PF030-PF038 range.
_DOCUMENTED = re.compile(r"\b(P[SFK]\d{3})\b")


def _emitted_codes() -> dict[str, set[str]]:
    """Every finding code constructed under src/, and which files construct it."""
    where: dict[str, set[str]] = {}
    for path in sorted(_SOURCE.rglob("*.py")):
        for code in _EMITTED.findall(path.read_text(encoding="utf-8")):
            where.setdefault(code, set()).add(str(path.relative_to(_ROOT)))
    return where


def _documented_codes() -> set[str]:
    """Every code the README names, expanding `PFxxx-PFyyy` ranges."""
    named = set(_DOCUMENTED.findall(_README))
    for family, low, high in re.findall(r"\b(P[SFK])(\d{3})\s*-\s*(?:P[SFK])?(\d{3})\b", _README):
        named.update(f"{family}{n:03d}" for n in range(int(low), int(high) + 1))
    return named


class DocumentedCodes(unittest.TestCase):
    def test_the_source_emits_codes_at_all(self):
        # A guard on the guard: if the regex stops matching, every other assertion here passes
        # vacuously and the drift it exists to catch goes back to being invisible.
        codes = _emitted_codes()
        self.assertGreater(len(codes), 30, "the emitted-code scan found almost nothing")
        self.assertIn("PS001", codes)
        self.assertIn("PF001", codes)
        self.assertIn("PK001", codes)

    def test_every_emitted_code_is_in_the_readme(self):
        documented = _documented_codes()
        missing = {
            code: sorted(files)
            for code, files in _emitted_codes().items()
            if code not in documented
        }
        self.assertFalse(
            missing,
            "these finding codes are emitted but appear in no README table: "
            + ", ".join(f"{c} ({', '.join(f)})" for c, f in sorted(missing.items())),
        )

    def test_the_readme_documents_no_code_that_does_not_exist(self):
        # The other direction: a code withdrawn from the source but left in the table sends
        # people to `--ignore` entries that will never match anything.
        emitted = set(_emitted_codes())
        stale = sorted(c for c in _documented_codes() if c not in emitted)
        self.assertFalse(stale, f"the README documents codes nothing emits: {stale}")


class Versions(unittest.TestCase):
    """The version lives in two files and the changelog. The release workflow fails on a tag
    that disagrees with pyproject, which is the right place to catch it and the latest."""

    def _pyproject_version(self) -> str:
        # Not tomllib: that is 3.11 and later, and pinside supports 3.10. The whole point of
        # this file is to notice when something claims a version it does not have, so it would
        # be a poor place to depend on one.
        text = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        project = text.split("\n[project]\n", 1)[1].split("\n[", 1)[0]
        found = re.search(r'^version\s*=\s*"([^"]+)"', project, re.M)
        self.assertIsNotNone(found, "pyproject.toml has no version in [project]")
        return found.group(1)

    def test_the_package_and_the_metadata_agree(self):
        import pinside

        self.assertEqual(pinside.__version__, self._pyproject_version())

    def test_the_changelog_has_a_section_for_this_version(self):
        changelog = (_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        version = self._pyproject_version()
        self.assertIn(f"## [{version}]", changelog)

    def test_the_changelog_links_every_version_it_names(self):
        changelog = (_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        named = set(re.findall(r"^## \[([^\]]+)\]", changelog, re.M))
        linked = set(re.findall(r"^\[([^\]]+)\]: https://", changelog, re.M))
        self.assertFalse(named - linked, f"unlinked versions: {sorted(named - linked)}")


class DocumentedCatalogues(unittest.TestCase):
    """The probe and board tables in the README against the catalogues they describe."""

    def test_every_probe_is_in_the_readme(self):
        from pinside import pogo

        for name in pogo.PROBES:
            self.assertIn(f"`{name}`", _README, f"probe {name} is in no README table")

    def test_every_carrier_board_is_in_the_readme(self):
        from pinside import modules

        for name in modules.MODULES:
            self.assertIn(f"`{name}`", _README, f"board {name} is in no README table")

    def test_every_target_is_named(self):
        from pinside import targets

        for name in targets.TARGETS:
            self.assertTrue(
                name.upper() in _README.upper(),
                f"target {name} is in targets.TARGETS but the README never names it",
            )


if __name__ == "__main__":
    unittest.main()
