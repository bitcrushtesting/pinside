"""Everything that can be wrong with a board you are about to build a bed-of-nails against.

Each check answers one question a fixture builder would otherwise only discover after the boards
come back from the fab. They are ordered roughly by how expensive the mistake is: geometry that
makes the fixture unbuildable first, then things that make it unreliable, then things that make
it merely annoying.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .board import Board, TestPoint
from .geometry import BBox

ERROR, WARNING, INFO = "error", "warning", "info"


@dataclass
class Limits:
    """The physical facts a fixture is built from. Defaults suit a Mill-Max 0985 receptacle."""

    probe_pitch: float = 2.54          # centre-to-centre minimum between two receptacles
    edge_clearance: float = 2.0        # probe centre to board edge
    hole_clearance: float = 1.0        # probe pad edge to mounting-hole pad edge
    min_pad_diameter: float = 0.9      # DUT pad the spring tip has to land on
    min_mounting_holes: int = 3        # three points locate a plane; two let the board pivot
    grid_tolerance: float = 0.01       # how exactly a coordinate must sit on a lattice, mm
    grid_fraction: float = 0.7         # share of probes on it before we call it an import grid


@dataclass
class Finding:
    code: str
    severity: str
    summary: str
    refs: list[str] = field(default_factory=list)
    detail: str = ""

    def as_dict(self) -> dict:
        return {"code": self.code, "severity": self.severity, "summary": self.summary,
                "refs": self.refs, "detail": self.detail}

    def __str__(self) -> str:
        refs = f" [{', '.join(self.refs)}]" if self.refs else ""
        detail = f" -- {self.detail}" if self.detail else ""
        return f"{self.severity}: {self.code} {self.summary}{refs}{detail}"


def _pairs(items: list) -> list[tuple]:
    return [(items[i], items[j]) for i in range(len(items)) for j in range(i + 1, len(items))]


def _lattice_fraction(values: list[float], tolerance: float) -> tuple[float, float]:
    """Guess a pitch from these coordinates and report what fraction of them sit on it.

    Returns (pitch, fraction). A fresh netlist import puts every footprint on one exact lattice;
    a laid-out board does not, even when parts happen to line up in rows.
    """
    uniq = sorted(set(round(v, 4) for v in values))
    if len(uniq) < 3:
        return 0.0, 0.0
    steps = [round(b - a, 4) for a, b in zip(uniq, uniq[1:]) if b - a > tolerance]
    if not steps:
        return 0.0, 0.0
    pitch = min(steps, key=lambda s: (-steps.count(s), s))  # most common, smallest on a tie
    if pitch <= tolerance:
        return 0.0, 0.0

    # The lattice origin is whichever offset the most coordinates agree on -- taking the lowest
    # coordinate instead lets one stray part outside the block shift the whole lattice and hide
    # the very pattern we are looking for.
    residues = [v % pitch for v in values]
    best = max(sum(1 for r in residues
                   if min(abs(r - c), pitch - abs(r - c)) <= tolerance)
               for c in residues)
    return pitch, best / len(values)


# --------------------------------------------------------------------------- individual checks


def check_outline(board: Board, limits: Limits) -> list[Finding]:
    if not board.outline.segments:
        return [Finding("PS001", ERROR, "no Edge.Cuts outline: the board size is unknown",
                        detail="every geometric check below is disabled without one")]
    if not board.outline.closed:
        return [Finding("PS002", ERROR, "the Edge.Cuts outline does not close into one ring",
                        detail="KiCad cannot fill zones and the fab cannot mill it; "
                               "checks fall back to the bounding box")]
    return []


def check_placement(board: Board, limits: Limits) -> list[Finding]:
    """Anything sitting outside the board was never placed -- its coordinates are not a location."""
    if not board.outline.segments:
        return []
    stray = [t.ref for t in board.test_points if not board.outline.contains(t.x, t.y)]
    stray_holes = [h.ref for h in board.mounting_holes if not board.outline.contains(h.x, h.y)]
    out = []
    if stray:
        out.append(Finding(
            "PS010", ERROR,
            f"{len(stray)} of {len(board.test_points)} test points sit outside the board outline",
            stray,
            "these are still at their netlist-import positions, so their coordinates are not a "
            "placement -- do not cut a fixture from them"))
    if stray_holes:
        out.append(Finding("PS011", ERROR,
                           f"{len(stray_holes)} mounting holes sit outside the board outline",
                           stray_holes))
    return out


def check_import_grid(board: Board, limits: Limits) -> list[Finding]:
    """A regular lattice is what KiCad makes when it drops a netlist onto a fresh board.

    Real placement is never that tidy, so it is a second, independent signal that nobody has laid
    the board out yet. It fires even when the parts happen to land inside the outline, which the
    outside-the-outline check cannot catch.
    """
    pts = board.test_points
    if len(pts) < 6:
        return []
    pitch_x, frac_x = _lattice_fraction([t.x for t in pts], limits.grid_tolerance)
    pitch_y, frac_y = _lattice_fraction([t.y for t in pts], limits.grid_tolerance)
    if not (pitch_x and pitch_y):
        return []
    if frac_x < limits.grid_fraction or frac_y < limits.grid_fraction:
        return []
    if abs(pitch_x - pitch_y) > limits.grid_tolerance:
        return []
    share = min(frac_x, frac_y)
    return [Finding(
        "PS012", WARNING,
        f"{share:.0%} of test points lie on a uniform {pitch_x:g} mm lattice",
        detail="that is KiCad's default spread for freshly imported footprints, not a layout")]


def check_stacked(board: Board, limits: Limits) -> list[Finding]:
    seen: dict[tuple[float, float], str] = {}
    clashes = []
    for t in board.test_points:
        key = (round(t.x, 3), round(t.y, 3))
        if key in seen:
            clashes.append(f"{seen[key]}+{t.ref}")
        seen[key] = t.ref
    if clashes:
        return [Finding("PS020", ERROR, f"{len(clashes)} test points share a position",
                        clashes, "one probe cannot serve two nets")]
    return []


def check_pitch(board: Board, limits: Limits) -> list[Finding]:
    tight = []
    for a, b in _pairs(board.test_points):
        d = math.hypot(a.x - b.x, a.y - b.y)
        if 0 < d < limits.probe_pitch:
            tight.append(f"{a.ref}-{b.ref} {d:.2f}mm")
    if tight:
        return [Finding("PS021", ERROR,
                        f"{len(tight)} probe pairs are closer than the {limits.probe_pitch} mm "
                        "receptacle pitch", tight,
                        "the receptacle bodies collide; move the pads or use a finer probe")]
    return []


def check_edge_clearance(board: Board, limits: Limits) -> list[Finding]:
    if not board.outline.segments:
        return []
    close = []
    for t in board.test_points:
        d = board.outline.distance_to_edge(t.x, t.y)
        if d is not None and d < limits.edge_clearance and board.outline.contains(t.x, t.y):
            close.append(f"{t.ref} {d:.2f}mm")
    if close:
        return [Finding("PS022", WARNING,
                        f"{len(close)} probes are within {limits.edge_clearance} mm of the board edge",
                        close, "the fixture wall and any board-edge chamfer live here")]
    return []


def check_hole_clearance(board: Board, limits: Limits) -> list[Finding]:
    close = []
    for t in board.test_points:
        tip = (t.pad.max_dimension / 2) if t.pad else 0.0
        for h in board.mounting_holes:
            ring = (h.pad_diameter or h.drill or 0.0) / 2
            gap = math.hypot(t.x - h.x, t.y - h.y) - ring - tip
            if gap < limits.hole_clearance:
                close.append(f"{t.ref}-{h.ref} {gap:.2f}mm")
    if close:
        return [Finding("PS023", WARNING,
                        f"{len(close)} probes crowd a mounting hole", close,
                        "the standoff or screw head sits on that pad")]
    return []


def check_obstructions(board: Board, limits: Limits) -> list[Finding]:
    """A probe descending onto a pad that a component already occupies hits the component."""
    hits = []
    for t in board.test_points:
        for fp in board.obstacles:
            if fp.side != t.side:
                continue
            box: BBox | None = fp.bbox
            if box and box.contains(t.x, t.y):
                hits.append(f"{t.ref} in {fp.ref}")
    if hits:
        return [Finding("PS024", ERROR,
                        f"{len(hits)} probes land inside another footprint's pad envelope", hits,
                        "the spring pin would strike the component, not the test pad")]
    return []


def check_pad_size(board: Board, limits: Limits) -> list[Finding]:
    small = [f"{t.ref} {t.pad.min_dimension:g}mm" for t in board.test_points
             if t.pad and t.pad.size and t.pad.min_dimension < limits.min_pad_diameter]
    if small:
        return [Finding("PS025", WARNING,
                        f"{len(small)} test pads are under {limits.min_pad_diameter} mm across",
                        small, "a spring tip plus placement tolerance needs more target than that")]
    return []


def check_sides(board: Board, limits: Limits) -> list[Finding]:
    sides = {t.side for t in board.test_points}
    if len(sides) > 1:
        bottom = [t.ref for t in board.test_points if t.side == "bottom"]
        return [Finding("PS026", WARNING, "test points are on both sides of the board", bottom,
                        "a single-sided fixture cannot reach the ones listed; "
                        "you need a clamshell or a second plate")]
    return []


def check_ground(board: Board, limits: Limits) -> list[Finding]:
    out = []
    grounds = [t.ref for t in board.test_points if t.is_ground]
    if not grounds:
        out.append(Finding(
            "PS030", ERROR, "no ground test point",
            detail="every probed signal is measured against a return path that does not exist; "
                   "add at least two ground pads before building a fixture"))
    elif len(grounds) < 2:
        out.append(Finding("PS031", WARNING, "only one ground test point", grounds,
                           "a second one halves the return inductance and guards against a "
                           "single bad contact"))
    ungrounded = [h.ref for h in board.mounting_holes if h.plated and not h.net]
    if ungrounded:
        out.append(Finding(
            "PS032", INFO, f"{len(ungrounded)} plated mounting holes carry no net", ungrounded,
            "netting them to GND turns the fixture standoffs into a free, low-inductance return"))
    return out


def check_nets(board: Board, limits: Limits) -> list[Finding]:
    out = []
    unnetted = [t.ref for t in board.test_points if not t.net]
    if unnetted:
        out.append(Finding("PS040", WARNING, f"{len(unnetted)} test points have no net", unnetted,
                           "they probe nothing; either wire them or delete them"))
    anonymous = [t.ref for t in board.test_points if t.net and t.anonymous_net]
    if anonymous:
        out.append(Finding(
            "PS041", INFO, f"{len(anonymous)} test points sit on auto-named nets", anonymous,
            "KiCad named these, not you -- label them in the schematic so the fixture, the "
            "firmware and the test report all use one name"))

    by_net: dict[str, list[str]] = {}
    for t in board.test_points:
        # Several ground probes are the point, not a mistake -- only signals are worth flagging.
        if t.net and not t.anonymous_net and not t.is_ground:
            by_net.setdefault(t.net, []).append(t.ref)
    dupes = [f"{net}: {'+'.join(refs)}" for net, refs in by_net.items() if len(refs) > 1]
    if dupes:
        out.append(Finding("PS042", INFO, f"{len(dupes)} signal nets carry more than one test point",
                           dupes, "deliberate for a power rail, a wasted fixture channel otherwise"))
    return out


def check_mounting(board: Board, limits: Limits) -> list[Finding]:
    out = []
    holes = board.mounting_holes
    if len(holes) < limits.min_mounting_holes:
        out.append(Finding(
            "PS050", WARNING,
            f"{len(holes)} mounting holes: fewer than the {limits.min_mounting_holes} needed to "
            "locate the board", [h.ref for h in holes],
            "with fewer, the DUT can pivot or rock and contact becomes intermittent"))
    drills = {round(h.drill, 3) for h in holes if h.drill}
    if len(drills) > 1:
        out.append(Finding("PS051", INFO, "mounting holes have differing drill sizes",
                           [f"{h.ref} {h.drill}mm" for h in holes if h.drill],
                           "the fixture needs matching hardware per hole"))

    if holes and board.test_points:
        hx = [h.x for h in holes]
        hy = [h.y for h in holes]
        span = BBox(min(hx), min(hy), max(hx), max(hy))
        outside = [t.ref for t in board.test_points if not span.contains(t.x, t.y)]
        # Only meaningful once the probes are actually placed.
        placed = board.outline.segments and all(
            board.outline.contains(t.x, t.y) for t in board.test_points)
        if outside and placed:
            out.append(Finding(
                "PS052", INFO,
                f"{len(outside)} probes lie outside the mounting-hole footprint", outside,
                "the plate cantilevers past its supports there and contact force will vary"))
    return out


CHECKS = [check_outline, check_placement, check_import_grid, check_stacked, check_pitch,
          check_edge_clearance, check_hole_clearance, check_obstructions, check_pad_size,
          check_sides, check_ground, check_nets, check_mounting]

_ORDER = {ERROR: 0, WARNING: 1, INFO: 2}


def run(board: Board, limits: Limits | None = None) -> list[Finding]:
    limits = limits or Limits()
    findings: list[Finding] = []
    for check in CHECKS:
        findings.extend(check(board, limits))
    return sorted(findings, key=lambda f: (_ORDER[f.severity], f.code))
