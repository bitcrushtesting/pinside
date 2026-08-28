"""Everything that can be wrong with a board you are about to build a bed-of-nails against.

Each check answers one question a fixture builder would otherwise only discover after the boards
come back from the fab. They are ordered roughly by how expensive the mistake is: geometry that
makes the fixture unbuildable first, then things that make it unreliable, then things that make
it merely annoying.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from itertools import pairwise

from .board import CONTROL_NET, POWER_NET, Board
from .geometry import BBox, ring_area

ERROR, WARNING, INFO = "error", "warning", "info"


@dataclass
class Limits:
    """The physical facts a fixture is built from. Defaults suit a Mill-Max 0985 receptacle."""

    probe_pitch: float = 2.54  # centre-to-centre minimum between two receptacles
    probe_body: float = 1.70  # outside diameter of the receptacle body, at the DUT face
    edge_clearance: float = 2.0  # probe centre to board edge
    hole_clearance: float = 1.0  # probe pad edge to mounting-hole pad edge
    min_pad_diameter: float = 0.9  # DUT pad the spring tip has to land on
    min_mounting_holes: int = 3  # three points locate a plane; two let the board pivot
    grid_tolerance: float = 0.01  # how exactly a coordinate must sit on a lattice, mm
    grid_fraction: float = 0.7  # share of probes on it before we call it an import grid


@dataclass
class Finding:
    code: str
    severity: str
    summary: str
    refs: list[str] = field(default_factory=list)
    detail: str = ""

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "summary": self.summary,
            "refs": self.refs,
            "detail": self.detail,
        }

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
    uniq = sorted({round(v, 4) for v in values})
    if len(uniq) < 3:
        return 0.0, 0.0
    steps = [round(b - a, 4) for a, b in pairwise(uniq) if b - a > tolerance]
    if not steps:
        return 0.0, 0.0
    pitch = min(steps, key=lambda s: (-steps.count(s), s))  # most common, smallest on a tie
    if pitch <= tolerance:
        return 0.0, 0.0

    # The lattice origin is whichever offset the most coordinates agree on -- taking the lowest
    # coordinate instead lets one stray part outside the block shift the whole lattice and hide
    # the very pattern we are looking for.
    residues = [v % pitch for v in values]
    best = max(
        sum(1 for r in residues if min(abs(r - c), pitch - abs(r - c)) <= tolerance)
        for c in residues
    )
    return pitch, best / len(values)


# --------------------------------------------------------------------------- individual checks


def check_outline(board: Board, limits: Limits) -> list[Finding]:
    if not board.outline.segments:
        return [
            Finding(
                "PS001",
                ERROR,
                "no Edge.Cuts outline: the board size is unknown",
                detail="every geometric check below is disabled without one",
            )
        ]
    outline = board.outline
    if not outline.closed:
        return [
            Finding(
                "PS002",
                ERROR,
                f"{len(outline.open_segments)} Edge.Cuts segments do not close into a ring",
                detail="KiCad cannot fill zones and the fab cannot mill it; "
                "checks fall back to the bounding box",
            )
        ]

    out = []
    if outline.cutouts:
        out.append(
            Finding(
                "PS003",
                INFO,
                f"the outline has {len(outline.cutouts)} internal cutouts",
                [f"{a:.1f}mm2" for a in sorted(ring_area(c) for c in outline.cutouts)],
                "there is no board over these, so a probe landing on one reaches nothing; "
                "they are treated as holes, not as a broken outline",
            )
        )
    if outline.islands:
        out.append(
            Finding(
                "PS004",
                WARNING,
                f"Edge.Cuts carries {len(outline.islands)} closed shapes outside the board",
                [f"{a:.1f}mm2" for a in sorted(ring_area(i) for i in outline.islands)],
                "the largest shape was taken as the board; the rest are a panel, or an outline "
                "somebody left behind, and either way the fixture is cut to the wrong extent",
            )
        )
    return out


def check_placement(board: Board, limits: Limits) -> list[Finding]:
    """Anything sitting outside the board was never placed -- its coordinates are not a location."""
    if not board.outline.segments:
        return []
    # within_perimeter, not contains: a probe in the middle of a cutout is inside the board's
    # extent and is PS013's finding, not this one. Reporting it here too would say it was never
    # placed, which is both wrong and the opposite of what to do about it.
    inside = board.outline.within_perimeter
    stray = [t.ref for t in board.test_points if not inside(t.x, t.y)]
    stray_holes = [h.ref for h in board.mounting_holes if not inside(h.x, h.y)]
    out = []
    if stray:
        out.append(
            Finding(
                "PS010",
                ERROR,
                f"{len(stray)} of {len(board.test_points)} test points sit outside "
                "the board outline",
                stray,
                "these are still at their netlist-import positions, so their coordinates are not a "
                "placement -- do not cut a fixture from them",
            )
        )
    if stray_holes:
        out.append(
            Finding(
                "PS011",
                ERROR,
                f"{len(stray_holes)} mounting holes sit outside the board outline",
                stray_holes,
            )
        )
    return out


def check_cutouts(board: Board, limits: Limits) -> list[Finding]:
    """A probe over a slot or a window descends through the board and touches nothing.

    This is separate from the outside-the-outline check because it fails the other way round:
    the coordinates are a real placement, inside the board's extent, and still wrong. Nothing
    about the drill plan looks odd, and the fault shows up as a channel that reads open on a
    fixture everybody has already paid for.
    """
    if not board.outline.cutouts:
        return []
    over = [t.ref for t in board.test_points if board.outline.in_cutout(t.x, t.y)]
    holes = [h.ref for h in board.mounting_holes if board.outline.in_cutout(h.x, h.y)]
    out = []
    if over:
        out.append(
            Finding(
                "PS013",
                ERROR,
                f"{len(over)} test points sit over a cutout in the board",
                over,
                "there is no copper there and no board to hold it; the probe would pass through",
            )
        )
    if holes:
        out.append(
            Finding(
                "PS014",
                ERROR,
                f"{len(holes)} mounting holes sit over a cutout",
                holes,
                "nothing to bolt the fixture to",
            )
        )
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
    return [
        Finding(
            "PS012",
            WARNING,
            f"{share:.0%} of test points lie on a uniform {pitch_x:g} mm lattice",
            detail="that is KiCad's default spread for freshly imported footprints, not a layout",
        )
    ]


def check_stacked(board: Board, limits: Limits) -> list[Finding]:
    seen: dict[tuple[float, float], str] = {}
    clashes = []
    for t in board.test_points:
        key = (round(t.x, 3), round(t.y, 3))
        if key in seen:
            clashes.append(f"{seen[key]}+{t.ref}")
        seen[key] = t.ref
    if clashes:
        return [
            Finding(
                "PS020",
                ERROR,
                f"{len(clashes)} test points share a position",
                clashes,
                "one probe cannot serve two nets",
            )
        ]
    return []


def check_pitch(board: Board, limits: Limits) -> list[Finding]:
    tight = []
    for a, b in _pairs(board.test_points):
        d = math.hypot(a.x - b.x, a.y - b.y)
        if 0 < d < limits.probe_pitch:
            tight.append(f"{a.ref}-{b.ref} {d:.2f}mm")
    if tight:
        return [
            Finding(
                "PS021",
                ERROR,
                f"{len(tight)} probe pairs are closer than the {limits.probe_pitch} mm "
                "receptacle pitch",
                tight,
                "the receptacle bodies collide; move the pads or use a finer probe",
            )
        ]
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
        return [
            Finding(
                "PS022",
                WARNING,
                f"{len(close)} probes are within {limits.edge_clearance} mm of the board edge",
                close,
                "the fixture wall and any board-edge chamfer live here",
            )
        ]
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
        return [
            Finding(
                "PS023",
                WARNING,
                f"{len(close)} probes crowd a mounting hole",
                close,
                "the standoff or screw head sits on that pad",
            )
        ]
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
        return [
            Finding(
                "PS024",
                ERROR,
                f"{len(hits)} probes land inside another footprint's pad envelope",
                hits,
                "the spring pin would strike the component, not the test pad",
            )
        ]
    return []


def check_probe_body(board: Board, limits: Limits) -> list[Finding]:
    """The tip lands clear of the component and the receptacle around it does not.

    PS024 asks where the spring tip comes down. This asks how much room the thing holding it
    needs: a 0985 receptacle is 1.70 mm across its body, so a probe whose tip clears a QFN by
    half a millimetre still has that body pressing on the package. The tip lands where it should
    and the plate never closes far enough for it to make contact.
    """
    radius = limits.probe_body / 2
    close = []
    for t in board.test_points:
        for fp in board.obstacles:
            if fp.side != t.side:
                continue
            box: BBox | None = fp.bbox
            if not box or box.contains(t.x, t.y):
                continue  # inside is PS024's finding, and a worse one
            gap = box.distance_to(t.x, t.y)
            if gap < radius:
                close.append(f"{t.ref}-{fp.ref} {gap:.2f}mm")
    if close:
        return [
            Finding(
                "PS027",
                WARNING,
                f"{len(close)} probes have less than the {radius:.2f} mm body radius "
                "to a neighbouring component",
                close,
                "the tip clears it but the receptacle body does not, so the plate cannot close; "
                "use a finer probe or move the pad",
            )
        ]
    return []


def check_pad_size(board: Board, limits: Limits) -> list[Finding]:
    small = [
        f"{t.ref} {t.pad.min_dimension:g}mm"
        for t in board.test_points
        if t.pad and t.pad.size and t.pad.min_dimension < limits.min_pad_diameter
    ]
    if small:
        return [
            Finding(
                "PS025",
                WARNING,
                f"{len(small)} test pads are under {limits.min_pad_diameter} mm across",
                small,
                "a spring tip plus placement tolerance needs more target than that",
            )
        ]
    return []


def check_sides(board: Board, limits: Limits) -> list[Finding]:
    sides = {t.side for t in board.test_points}
    if len(sides) > 1:
        bottom = [t.ref for t in board.test_points if t.side == "bottom"]
        return [
            Finding(
                "PS026",
                WARNING,
                "test points are on both sides of the board",
                bottom,
                "a single-sided fixture cannot reach the ones listed; "
                "you need a clamshell or a second plate",
            )
        ]
    return []


def check_ground(board: Board, limits: Limits) -> list[Finding]:
    out = []
    grounds = [t.ref for t in board.test_points if t.is_ground]
    if not grounds:
        out.append(
            Finding(
                "PS030",
                ERROR,
                "no ground test point",
                detail="every probed signal is measured against a return path that does not exist; "
                "add at least two ground pads before building a fixture",
            )
        )
    elif len(grounds) < 2:
        out.append(
            Finding(
                "PS031",
                WARNING,
                "only one ground test point",
                grounds,
                "a second one halves the return inductance and guards against a single bad contact",
            )
        )
    ungrounded = [h.ref for h in board.mounting_holes if h.plated and not h.net]
    if ungrounded:
        out.append(
            Finding(
                "PS032",
                INFO,
                f"{len(ungrounded)} plated mounting holes carry no net",
                ungrounded,
                "netting them to GND turns the fixture standoffs into a free, "
                "low-inductance return",
            )
        )
    return out


def check_nets(board: Board, limits: Limits) -> list[Finding]:
    out = []
    unnetted = [t.ref for t in board.test_points if not t.net]
    if unnetted:
        out.append(
            Finding(
                "PS040",
                WARNING,
                f"{len(unnetted)} test points have no net",
                unnetted,
                "they probe nothing; either wire them or delete them",
            )
        )
    anonymous = [t.ref for t in board.test_points if t.net and t.anonymous_net]
    if anonymous:
        out.append(
            Finding(
                "PS041",
                INFO,
                f"{len(anonymous)} test points sit on auto-named nets",
                anonymous,
                "KiCad named these, not you -- label them in the schematic so the fixture, the "
                "firmware and the test report all use one name",
            )
        )

    by_net: dict[str, list[str]] = {}
    for t in board.test_points:
        # Several ground probes are the point, not a mistake -- only signals are worth flagging.
        if t.net and not t.anonymous_net and not t.is_ground:
            by_net.setdefault(t.net, []).append(t.ref)
    dupes = [f"{net}: {'+'.join(refs)}" for net, refs in by_net.items() if len(refs) > 1]
    if dupes:
        out.append(
            Finding(
                "PS042",
                INFO,
                f"{len(dupes)} signal nets carry more than one test point",
                dupes,
                "deliberate for a power rail, a wasted fixture channel otherwise",
            )
        )
    return out


def check_net_identity(board: Board, limits: Limits) -> list[Finding]:
    """Whether the board's netlist survives being opened.

    Through KiCad 9 a net is identified by its *ordinal*; the name beside it is a label. Give two
    different names the same ordinal and KiCad reads them as one net, keeps whichever name it saw
    first, and drops the rest to no-net the moment the file is saved. Nothing warns anyone: the
    file is well-formed, it opens, and fifteen test points quietly stop being connected to
    anything.

    That is not hypothetical. It is what pinside's own example board did, and pinside called it
    clean for two releases, because the reader takes the name off each pad and never compares the
    ordinals. Confirmed against `kicad-cli pcb export ipcd356`, which is KiCad's own answer to
    "what is this board's netlist".

    Silent on a KiCad 10 board: that format dropped the ordinal, so there is nothing to collide.
    """
    out = []

    collisions = {
        ordinal: sorted(names)
        for ordinal, names in board.net_ordinals.items()
        if ordinal != 0 and len(names) > 1
    }
    if collisions:
        # In board order, which read_board already sorted naturally: sorting again here gives
        # TP1, TP10, TP11, TP2, which reads as though the refs were picked at random.
        affected = [t.ref for t in board.test_points if t.pad and t.pad.net_ordinal in collisions]
        out.append(
            Finding(
                "PS043",
                ERROR,
                f"{len(collisions)} net numbers carry more than one net name",
                [f"net {n}: {', '.join(names)}" for n, names in sorted(collisions.items())],
                "KiCad identifies a net by its number, so it reads these as one net and keeps "
                "only the first name; the rest lose their connection on the next save"
                + (f". Affects {', '.join(affected)}" if affected else ""),
            )
        )

    named_zero = sorted(board.net_ordinals.get(0, ()))
    if named_zero:
        refs = [t.ref for t in board.test_points if t.pad and t.pad.net_ordinal == 0 and t.net]
        out.append(
            Finding(
                "PS044",
                ERROR,
                f"{len(named_zero)} named nets are on net number 0",
                refs or named_zero,
                "net 0 is KiCad's no-connection net, so the name is ignored and these pads probe "
                "nothing; pinside reads the name and would build a fixture channel for each",
            )
        )
    return out


def _bare(net: str) -> str:
    return net.rsplit("/", 1)[-1]


def check_signal_coverage(board: Board, limits: Limits) -> list[Finding]:
    """Which nets the board has that the fixture will not be able to reach.

    Every other check here asks whether the probes that exist are placed correctly. This one
    asks what is missing, which is the failure nothing else can see: a fixture that reaches
    every data bus and no reset line can watch a board it cannot put into a known state, and
    that is discovered on the bench, after the plate is built.
    """
    if not board.test_points:
        return []

    probed = {_bare(n) for n in board.probed_nets}
    unprobed = sorted(_bare(n) for n in board.nets if _bare(n) not in probed)

    out = []
    rails = [n for n in unprobed if POWER_NET.match(n)]
    if rails:
        out.append(
            Finding(
                "PS033",
                WARNING,
                f"{len(rails)} supply rails have no test point",
                rails,
                "a rail nobody probes is a rail the fixture cannot prove came up; one pad each "
                "turns a dead board into a measurement",
            )
        )

    control = [n for n in unprobed if CONTROL_NET.search(n) and not POWER_NET.match(n)]
    if control:
        out.append(
            Finding(
                "PS034",
                INFO,
                f"{len(control)} reset or strap lines have no test point",
                control,
                "without one the fixture can read the DUT but not put it into a known state, "
                "so a test that fails cannot be retried from a clean start",
            )
        )
    return out


def check_mounting(board: Board, limits: Limits) -> list[Finding]:
    out = []
    holes = board.mounting_holes
    if len(holes) < limits.min_mounting_holes:
        out.append(
            Finding(
                "PS050",
                WARNING,
                f"{len(holes)} mounting holes: fewer than the "
                f"{limits.min_mounting_holes} needed to locate the board",
                [h.ref for h in holes],
                "with fewer, the DUT can pivot or rock and contact becomes intermittent",
            )
        )
    drills = {round(h.drill, 3) for h in holes if h.drill}
    if len(drills) > 1:
        out.append(
            Finding(
                "PS051",
                INFO,
                "mounting holes have differing drill sizes",
                [f"{h.ref} {h.drill}mm" for h in holes if h.drill],
                "the fixture needs matching hardware per hole",
            )
        )

    if holes and board.test_points:
        hx = [h.x for h in holes]
        hy = [h.y for h in holes]
        span = BBox(min(hx), min(hy), max(hx), max(hy))
        outside = [t.ref for t in board.test_points if not span.contains(t.x, t.y)]
        # Only meaningful once the probes are actually placed.
        placed = board.outline.segments and all(
            board.outline.within_perimeter(t.x, t.y) for t in board.test_points
        )
        if outside and placed:
            out.append(
                Finding(
                    "PS052",
                    INFO,
                    f"{len(outside)} probes lie outside the mounting-hole footprint",
                    outside,
                    "the plate cantilevers past its supports there and contact force will vary",
                )
            )
    return out


CHECKS = [
    check_outline,
    check_placement,
    check_cutouts,
    check_import_grid,
    check_stacked,
    check_pitch,
    check_edge_clearance,
    check_hole_clearance,
    check_obstructions,
    check_probe_body,
    check_pad_size,
    check_sides,
    check_ground,
    check_nets,
    check_net_identity,
    check_signal_coverage,
    check_mounting,
]

_ORDER = {ERROR: 0, WARNING: 1, INFO: 2}


def run(board: Board, limits: Limits | None = None) -> list[Finding]:
    limits = limits or Limits()
    findings: list[Finding] = []
    for check in CHECKS:
        findings.extend(check(board, limits))
    return sorted(findings, key=lambda f: (_ORDER[f.severity], f.code))
