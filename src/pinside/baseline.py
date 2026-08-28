"""A checked-in record of the findings a board has already been judged on.

`--ignore PS041,PS042` is per-invocation and global: it silences a code everywhere, on every
board, forever. That is the wrong shape for the usual situation, which is a board with two
findings somebody has looked at and accepted and one that has not happened yet.

A baseline records the accepted ones by code *and by reference*. Suppressing "PS041 on TP5" says
nothing about PS041 on TP9, so a new occurrence still fails CI while the old one stays quiet.
That is the property that makes a baseline safe to check in: it cannot silently absorb a finding
nobody has seen.

The file is JSON, meant to be committed and reviewed in a diff. Each entry carries an empty
`note` for the reason, because a suppression whose reason nobody wrote down is indistinguishable
from a mistake six months later.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .checks import Finding

VERSION = 1


class BaselineError(Exception):
    """A baseline file that cannot be used, as opposed to one that suppresses nothing."""


@dataclass
class Entry:
    code: str
    refs: set[str] = field(default_factory=set)
    note: str = ""

    def matches(self, finding: Finding) -> bool:
        """Does this entry cover the whole of that finding?

        An entry with no refs covers the code outright, which is `--ignore` written down. An
        entry with refs covers a finding only when every reference in it was already accepted:
        one new reference and the finding comes back, which is the point.
        """
        if self.code != finding.code:
            return False
        if not self.refs:
            return True
        return set(finding.refs) <= self.refs


@dataclass
class Baseline:
    entries: list[Entry] = field(default_factory=list)
    source: str = ""

    def split(self, findings: list[Finding]) -> tuple[list[Finding], list[Finding]]:
        """Partition findings into (still reported, suppressed by this baseline)."""
        kept, suppressed = [], []
        for finding in findings:
            if any(entry.matches(finding) for entry in self.entries):
                suppressed.append(finding)
            else:
                kept.append(finding)
        return kept, suppressed

    def as_dict(self, board: str = "") -> dict:
        return {
            "version": VERSION,
            "board": board,
            "accepted": [
                {"code": e.code, "refs": sorted(e.refs), "note": e.note}
                for e in sorted(self.entries, key=lambda e: (e.code, sorted(e.refs)))
            ],
        }


def from_findings(findings: list[Finding]) -> Baseline:
    """A baseline accepting exactly these findings, and nothing else."""
    return Baseline(
        entries=[Entry(code=f.code, refs=set(f.refs)) for f in findings],
    )


def load(path: str | Path) -> Baseline:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as err:
        raise BaselineError(f"cannot read baseline {path}: {err}") from None
    except json.JSONDecodeError as err:
        raise BaselineError(f"{path} is not valid JSON: {err}") from None

    if not isinstance(raw, dict):
        raise BaselineError(f"{path}: expected an object at the top level")
    version = raw.get("version")
    # Refusing an unknown version rather than reading what it recognises: a baseline read
    # half-correctly suppresses the wrong findings, and does it quietly.
    if version != VERSION:
        raise BaselineError(
            f"{path}: baseline version {version!r}, but this pinside writes version {VERSION}"
        )

    accepted = raw.get("accepted", [])
    if not isinstance(accepted, list):
        raise BaselineError(f"{path}: 'accepted' must be a list")

    entries = []
    for i, item in enumerate(accepted):
        if not isinstance(item, dict) or not isinstance(item.get("code"), str):
            raise BaselineError(f"{path}: accepted[{i}] has no code")
        refs = item.get("refs") or []
        if not isinstance(refs, list):
            raise BaselineError(f"{path}: accepted[{i}].refs must be a list")
        entries.append(
            Entry(
                code=item["code"].strip().upper(),
                refs={str(r) for r in refs},
                note=str(item.get("note", "")),
            )
        )
    return Baseline(entries=entries, source=str(path))


def write(path: str | Path, findings: list[Finding], board: str = "") -> int:
    """Write a baseline accepting these findings. Returns how many were accepted."""
    baseline = from_findings(findings)
    text = json.dumps(baseline.as_dict(board), indent=2) + "\n"
    Path(path).write_text(text, encoding="utf-8")
    return len(baseline.entries)
