"""Board outline geometry: turning Edge.Cuts primitives into something you can measure against.

The outline matters twice over. A probe outside it is not really placed, and a probe just inside
it collides with the fixture's own wall. Both questions need the real outline, not its bounding
box, so arcs and polygons are flattened to segments and the segments are chained into a ring.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

Point = tuple[float, float]
Segment = tuple[Point, Point]

# How far apart two outline endpoints may be and still count as the same corner. KiCad rounds
# coordinates to nanometres, and hand-drawn outlines routinely miss by a rounding step.
JOIN_TOLERANCE = 0.01

# Arc flattening step. 1 degree keeps a 4 mm fillet within ~0.2 um of true.
ARC_STEP_DEG = 1.0


@dataclass
class BBox:
    min_x: float
    min_y: float
    max_x: float
    max_y: float

    @property
    def width(self) -> float:
        return self.max_x - self.min_x

    @property
    def height(self) -> float:
        return self.max_y - self.min_y

    def contains(self, x: float, y: float) -> bool:
        return self.min_x <= x <= self.max_x and self.min_y <= y <= self.max_y

    def distance_to(self, x: float, y: float) -> float:
        """Distance from a point to the nearest edge of the box, or 0 inside it."""
        dx = max(self.min_x - x, 0.0, x - self.max_x)
        dy = max(self.min_y - y, 0.0, y - self.max_y)
        return math.hypot(dx, dy)

    def as_dict(self) -> dict:
        return {
            "min_x": round(self.min_x, 4),
            "min_y": round(self.min_y, 4),
            "max_x": round(self.max_x, 4),
            "max_y": round(self.max_y, 4),
            "width_mm": round(self.width, 4),
            "height_mm": round(self.height, 4),
        }


@dataclass
class Outline:
    """The board edge, as drawn and as flattened.

    Edge.Cuts is one layer holding every edge the board has, and a board with a slot, a
    connector relief or a mouse-bite window has more than one closed shape on it. The largest
    is the perimeter; anything closed and inside it is a hole in the board, and a probe over
    one is a probe over nothing.
    """

    shapes: list[dict] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)
    ring: list[Point] = field(default_factory=list)  # the perimeter; empty when nothing closed
    cutouts: list[list[Point]] = field(default_factory=list)  # closed rings inside the perimeter
    islands: list[list[Point]] = field(default_factory=list)  # closed rings outside it
    open_segments: list[Segment] = field(default_factory=list)  # never joined into any ring

    @property
    def closed(self) -> bool:
        """One perimeter, and no edge left dangling. Cutouts do not make an outline open."""
        return bool(self.ring) and not self.open_segments

    @property
    def bbox(self) -> BBox | None:
        pts = [p for seg in self.segments for p in seg]
        if not pts:
            return None
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return BBox(min(xs), min(ys), max(xs), max(ys))

    def contains(self, x: float, y: float) -> bool:
        """Is there board here? Inside the perimeter and not inside a cutout.

        Falls back to the bounding box when nothing closed, so the geometric checks still say
        something useful about a board whose edge is broken.
        """
        if not self.ring:
            box = self.bbox
            return bool(box and box.contains(x, y))
        if not point_in_ring((x, y), self.ring):
            return False
        return not self.in_cutout(x, y)

    def within_perimeter(self, x: float, y: float) -> bool:
        """Inside the board's extent, cutouts ignored.

        This is the question "was this ever placed", which a cutout does not change: a probe in
        the middle of a slot is somewhere deliberate and wrong, not still sitting where KiCad
        dropped it. ``contains`` answers the other question, whether there is board underneath.
        """
        if not self.ring:
            box = self.bbox
            return bool(box and box.contains(x, y))
        return point_in_ring((x, y), self.ring)

    def in_cutout(self, x: float, y: float) -> bool:
        """Inside one of the holes in the board."""
        return any(point_in_ring((x, y), c) for c in self.cutouts)

    def distance_to_edge(self, x: float, y: float) -> float | None:
        """Shortest distance to any board edge, cutout edges included.

        A cutout edge is as much of a wall as the perimeter is, so the clearance a probe needs
        from one is the clearance it needs from the other.
        """
        if not self.segments:
            return None
        return min(point_segment_distance((x, y), a, b) for a, b in self.segments)


def rotate(x: float, y: float, degrees: float) -> Point:
    """Rotate about the origin.

    KiCad stores footprint rotation as counter-clockwise on screen, and screen Y grows downward,
    so in raw file coordinates the rotation is clockwise -- hence the negated angle.
    """
    rad = math.radians(-degrees)
    cos, sin = math.cos(rad), math.sin(rad)
    return x * cos - y * sin, x * sin + y * cos


def arc_points(start: Point, mid: Point, end: Point) -> list[Point]:
    """Flatten a KiCad three-point arc. Falls back to a chord if the points are collinear."""
    (x1, y1), (x2, y2), (x3, y3) = start, mid, end
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(d) < 1e-12:
        return [start, end]
    ux = (
        (x1**2 + y1**2) * (y2 - y3) + (x2**2 + y2**2) * (y3 - y1) + (x3**2 + y3**2) * (y1 - y2)
    ) / d
    uy = (
        (x1**2 + y1**2) * (x3 - x2) + (x2**2 + y2**2) * (x1 - x3) + (x3**2 + y3**2) * (x2 - x1)
    ) / d
    r = math.hypot(x1 - ux, y1 - uy)

    a1 = math.atan2(y1 - uy, x1 - ux)
    a2 = math.atan2(y2 - uy, x2 - ux)
    a3 = math.atan2(y3 - uy, x3 - ux)
    # Walk start -> end the way that passes through the midpoint.
    sweep = a3 - a1
    while sweep <= -math.pi:
        sweep += 2 * math.pi
    while sweep > math.pi:
        sweep -= 2 * math.pi
    mid_off = a2 - a1
    while mid_off <= -math.pi:
        mid_off += 2 * math.pi
    while mid_off > math.pi:
        mid_off -= 2 * math.pi
    if (sweep >= 0) != (mid_off >= 0):
        sweep += 2 * math.pi if sweep < 0 else -2 * math.pi

    steps = max(2, int(abs(math.degrees(sweep)) / ARC_STEP_DEG) + 1)
    return [
        (ux + r * math.cos(a1 + sweep * i / steps), uy + r * math.sin(a1 + sweep * i / steps))
        for i in range(steps + 1)
    ]


def circle_points(center: Point, edge: Point) -> list[Point]:
    cx, cy = center
    r = math.hypot(edge[0] - cx, edge[1] - cy)
    steps = max(16, int(360 / ARC_STEP_DEG))
    pts = [
        (cx + r * math.cos(2 * math.pi * i / steps), cy + r * math.sin(2 * math.pi * i / steps))
        for i in range(steps)
    ]
    return [*pts, pts[0]]


def rounded_rect_points(start: Point, end: Point, radius: float) -> list[Point]:
    """A KiCad 10 rounded rectangle, corner by corner."""
    x1, y1 = min(start[0], end[0]), min(start[1], end[1])
    x2, y2 = max(start[0], end[0]), max(start[1], end[1])
    r = max(0.0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    if r == 0:
        return [(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)]

    steps = max(2, int(90 / ARC_STEP_DEG))
    pts: list[Point] = []
    # Corner centres in draw order, with the angle each quarter-turn starts at (Y grows down).
    corners = [
        ((x2 - r, y1 + r), -math.pi / 2),
        ((x2 - r, y2 - r), 0.0),
        ((x1 + r, y2 - r), math.pi / 2),
        ((x1 + r, y1 + r), math.pi),
    ]
    for (cx, cy), a0 in corners:
        for i in range(steps + 1):
            a = a0 + (math.pi / 2) * i / steps
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    pts.append(pts[0])
    return pts


def polyline_segments(points: list[Point]) -> list[Segment]:
    return [(points[i], points[i + 1]) for i in range(len(points) - 1)]


def point_segment_distance(p: Point, a: Point, b: Point) -> float:
    px, py = p
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def point_in_ring(p: Point, ring: list[Point]) -> bool:
    """Even-odd ray cast. The ring must be closed (first point repeated at the end)."""
    x, y = p
    inside = False
    for i in range(len(ring) - 1):
        x1, y1 = ring[i]
        x2, y2 = ring[i + 1]
        if (y1 > y) != (y2 > y):
            xi = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xi:
                inside = not inside
    return inside


def ring_area(ring: list[Point]) -> float:
    """Enclosed area by the shoelace formula, unsigned so winding direction does not matter."""
    total = 0.0
    for (x1, y1), (x2, y2) in polyline_segments(ring):
        total += x1 * y2 - x2 * y1
    return abs(total) / 2


def chain_rings(
    segments: list[Segment], tolerance: float = JOIN_TOLERANCE
) -> tuple[list[list[Point]], list[Segment]]:
    """Walk the segments into as many closed rings as they form.

    Returns (rings, leftovers). A leftover is a segment that never joined anything into a closed
    shape, which is the real defect: KiCad refuses to fill zones against an open edge and the fab
    cannot mill it.

    More than one ring is not a defect. A board with a slot, a connector relief or an antenna
    keepout window has several closed shapes on Edge.Cuts, and treating that as an open outline
    -- which is what a single-ring walk does -- calls a perfectly good board unbuildable.
    """
    remaining = list(segments)
    rings: list[list[Point]] = []
    orphans: list[Segment] = []

    while remaining:
        start, current = remaining.pop(0)
        path = [start, current]
        advanced = True
        while advanced and math.dist(current, start) > tolerance:
            advanced = False
            for i, (a, b) in enumerate(remaining):
                if math.dist(current, a) <= tolerance:
                    current = b
                elif math.dist(current, b) <= tolerance:
                    current = a
                else:
                    continue
                path.append(current)
                remaining.pop(i)
                advanced = True
                break

        # Three points is the least that can enclose anything; two is a line drawn back on
        # itself, which closes arithmetically and encloses nothing.
        if math.dist(current, start) <= tolerance and len(path) > 3:
            path[-1] = start
            rings.append(path)
        else:
            orphans.extend(polyline_segments(path))

    return rings, orphans


def chain_ring(segments: list[Segment], tolerance: float = JOIN_TOLERANCE) -> list[Point]:
    """The single closed ring these segments form, or [] if they do not form exactly one."""
    rings, orphans = chain_rings(segments, tolerance)
    return rings[0] if len(rings) == 1 and not orphans else []


def resolve_rings(
    rings: list[list[Point]],
) -> tuple[list[Point], list[list[Point]], list[list[Point]]]:
    """Sort closed rings into (perimeter, cutouts, islands).

    The perimeter is the one enclosing the most area. A ring inside it is a hole in the board.
    A ring outside it is a second board: a panel, or an Edge.Cuts shape somebody left behind.
    """
    if not rings:
        return [], [], []
    ordered = sorted(rings, key=ring_area, reverse=True)
    perimeter, rest = ordered[0], ordered[1:]
    cutouts, islands = [], []
    for ring in rest:
        # Any vertex will do to place a ring: rings on this layer do not cross each other, so
        # one point inside the perimeter means the whole ring is.
        inside = any(point_in_ring(p, perimeter) for p in ring[:-1])
        (cutouts if inside else islands).append(ring)
    return perimeter, cutouts, islands
