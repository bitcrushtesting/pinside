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

    def as_dict(self) -> dict:
        return {"min_x": round(self.min_x, 4), "min_y": round(self.min_y, 4),
                "max_x": round(self.max_x, 4), "max_y": round(self.max_y, 4),
                "width_mm": round(self.width, 4), "height_mm": round(self.height, 4)}


@dataclass
class Outline:
    """The board edge, as drawn and as flattened."""

    shapes: list[dict] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)
    ring: list[Point] = field(default_factory=list)   # empty when the outline is not closed

    @property
    def closed(self) -> bool:
        return bool(self.ring)

    @property
    def bbox(self) -> BBox | None:
        pts = [p for seg in self.segments for p in seg]
        if not pts:
            return None
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return BBox(min(xs), min(ys), max(xs), max(ys))

    def contains(self, x: float, y: float) -> bool:
        """Inside the board? Uses the true ring when the outline closes, else its bounding box."""
        if self.ring:
            return point_in_ring((x, y), self.ring)
        box = self.bbox
        return bool(box and box.contains(x, y))

    def distance_to_edge(self, x: float, y: float) -> float | None:
        """Shortest distance from a point to the board edge. None when there is no outline."""
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
    ux = ((x1 ** 2 + y1 ** 2) * (y2 - y3) + (x2 ** 2 + y2 ** 2) * (y3 - y1)
          + (x3 ** 2 + y3 ** 2) * (y1 - y2)) / d
    uy = ((x1 ** 2 + y1 ** 2) * (x3 - x2) + (x2 ** 2 + y2 ** 2) * (x1 - x3)
          + (x3 ** 2 + y3 ** 2) * (x2 - x1)) / d
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
    return [(ux + r * math.cos(a1 + sweep * i / steps),
             uy + r * math.sin(a1 + sweep * i / steps)) for i in range(steps + 1)]


def circle_points(center: Point, edge: Point) -> list[Point]:
    cx, cy = center
    r = math.hypot(edge[0] - cx, edge[1] - cy)
    steps = max(16, int(360 / ARC_STEP_DEG))
    pts = [(cx + r * math.cos(2 * math.pi * i / steps),
            cy + r * math.sin(2 * math.pi * i / steps)) for i in range(steps)]
    return pts + [pts[0]]


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
    corners = [((x2 - r, y1 + r), -math.pi / 2), ((x2 - r, y2 - r), 0.0),
               ((x1 + r, y2 - r), math.pi / 2), ((x1 + r, y1 + r), math.pi)]
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


def chain_ring(segments: list[Segment], tolerance: float = JOIN_TOLERANCE) -> list[Point]:
    """Walk the segments into one closed ring, or return [] if they do not form exactly one.

    A board edge that does not close is a real defect -- KiCad refuses to fill zones and the fab
    cannot mill it -- so failing here is a finding, not an inconvenience to route around.
    """
    if not segments:
        return []
    remaining = list(segments)
    start, current = remaining[0][0], remaining[0][1]
    ring = [start, current]
    remaining.pop(0)

    while remaining:
        for i, (a, b) in enumerate(remaining):
            if math.dist(current, a) <= tolerance:
                current = b
            elif math.dist(current, b) <= tolerance:
                current = a
            else:
                continue
            ring.append(current)
            remaining.pop(i)
            break
        else:
            return []  # a gap: the edge is open, or there is more than one closed shape
        if math.dist(current, start) <= tolerance:
            break

    if remaining or math.dist(current, start) > tolerance:
        return []
    ring[-1] = start
    return ring
