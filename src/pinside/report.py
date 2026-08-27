"""Output formats. Each answers a different question, so none of them is the 'real' one.

table   what did you find?              -- reading it yourself
csv     where does each probe go?       -- a spreadsheet, or a placement script
json    everything, structured          -- CI, and agents driving the fixture
svg     does this look right?           -- printing 1:1 and laying it on the board
"""

from __future__ import annotations

import csv
import json
import sys

from .board import Board
from .checks import Finding


def _rows(board: Board) -> list[list]:
    rows = []
    for t in board.test_points:
        pad = t.pad
        rows.append(
            [
                "test_point",
                t.ref,
                t.signal or "-",
                t.net,
                t.side,
                t.x,
                t.y,
                t.fx,
                t.fy,
                pad.max_dimension if pad else "",
                pad.drill if pad and pad.drill else "",
            ]
        )
    for h in board.mounting_holes:
        rows.append(
            [
                "mounting_hole",
                h.ref,
                "",
                h.net,
                "-",
                h.x,
                h.y,
                h.fx,
                h.fy,
                h.pad_diameter or "",
                h.drill or "",
            ]
        )
    return rows


HEADER = [
    "kind",
    "ref",
    "signal",
    "net",
    "side",
    "dut_x",
    "dut_y",
    "fix_x",
    "fix_y",
    "pad_mm",
    "drill_mm",
]


def emit_table(board: Board, findings: list[Finding], out=sys.stdout) -> None:
    box = board.outline.bbox
    print(f"source        {board.source}", file=out)
    if box:
        d = box.as_dict()
        closed = "closed" if board.outline.closed else "OPEN"
        print(
            f"outline       {d['width_mm']} x {d['height_mm']} mm, {closed}, "
            f"({d['min_x']}, {d['min_y']}) .. ({d['max_x']}, {d['max_y']})",
            file=out,
        )
    print(
        f"frame         origin={board.frame.get('origin')} mirror={board.frame.get('mirror')}",
        file=out,
    )
    print(f"test points   {len(board.test_points)}", file=out)
    print(f"mounting      {len(board.mounting_holes)}", file=out)
    print(file=out)

    widths = [max(len(str(r[i])) for r in [HEADER, *_rows(board)]) for i in range(len(HEADER))]
    for row in [HEADER, *_rows(board)]:
        print("  ".join(str(c).ljust(widths[i]) for i, c in enumerate(row)).rstrip(), file=out)

    if findings:
        print(file=out)
        print("findings", file=out)
        for f in findings:
            print(f"  {f}", file=out)


def emit_csv(board: Board, findings: list[Finding], out=sys.stdout) -> None:
    writer = csv.writer(out)
    writer.writerow(HEADER)
    writer.writerows(_rows(board))


def emit_json(board: Board, findings: list[Finding], out=sys.stdout) -> None:
    box = board.outline.bbox
    payload = {
        "source": board.source,
        "frame": board.frame,
        "outline": {
            "closed": board.outline.closed,
            "bbox": box.as_dict() if box else None,
            "shapes": board.outline.shapes,
        },
        "test_points": [
            {
                "ref": t.ref,
                "value": t.value,
                "signal": t.signal,
                "net": t.net,
                "side": t.side,
                "x": t.x,
                "y": t.y,
                "fx": t.fx,
                "fy": t.fy,
                "pad": None
                if not t.pad
                else {
                    "shape": t.pad.shape,
                    "type": t.pad.type,
                    "size_mm": list(t.pad.size) if t.pad.size else None,
                    "drill_mm": t.pad.drill,
                },
            }
            for t in board.test_points
        ],
        "mounting_holes": [
            {
                "ref": h.ref,
                "x": h.x,
                "y": h.y,
                "fx": h.fx,
                "fy": h.fy,
                "drill_mm": h.drill,
                "pad_dia_mm": h.pad_diameter,
                "plated": h.plated,
                "net": h.net,
            }
            for h in board.mounting_holes
        ],
        "findings": [f.as_dict() for f in findings],
    }
    json.dump(payload, out, indent=2)
    print(file=out)


def emit_svg(board: Board, findings: list[Finding], out=sys.stdout) -> None:
    """A 1:1 drill plan in the fixture frame. Print it, lay it on the DUT, look."""
    box = board.outline.bbox
    w = box.width if box else 100.0
    h = box.height if box else 100.0
    pad = 12.0
    flagged = {
        ref.split()[0].split("-")[0].split("+")[0]
        for f in findings
        if f.severity == "error"
        for ref in f.refs
    }

    span_w, span_h = w + 2 * pad, h + 2 * pad
    print(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{span_w}mm" height="{span_h}mm" '
        f'viewBox="{-pad} {-pad} {span_w} {span_h}">',
        file=out,
    )
    print(
        f'<rect x="{-pad:g}" y="{-pad:g}" width="{span_w:g}" height="{span_h:g}" fill="#ffffff"/>',
        file=out,
    )

    ox, oy = board.frame.get("offset", [0.0, 0.0])
    mirror = board.frame.get("mirror", "none")
    path = []
    for a, b in board.outline.segments:
        pts = []
        for px, py in (a, b):
            x, y = px - ox, py - oy
            if mirror == "x":
                x = w - x
            elif mirror == "y":
                y = h - y
            pts.append((x, y))
        path.append(f"M {pts[0][0]:.4f} {pts[0][1]:.4f} L {pts[1][0]:.4f} {pts[1][1]:.4f}")
    print(
        f'<path d="{" ".join(path)}" fill="none" stroke="#b8860b" stroke-width="0.25"/>', file=out
    )

    for hole in board.mounting_holes:
        r = (hole.drill or 3.0) / 2
        print(
            f'<circle cx="{hole.fx}" cy="{hole.fy}" r="{r:g}" fill="none" stroke="#2f6fd0" '
            f'stroke-width="0.25"/>',
            file=out,
        )
        print(
            f'<text x="{hole.fx + r + 0.6:g}" y="{hole.fy + 0.5:g}" font-size="1.8" '
            f'font-family="monospace" fill="#2f6fd0">{hole.ref}</text>',
            file=out,
        )

    for t in board.test_points:
        colour = "#c62828" if t.ref in flagged else "#1b7f3b"
        print(
            f'<circle cx="{t.fx}" cy="{t.fy}" r="0.685" fill="none" stroke="{colour}" '
            f'stroke-width="0.2"/>',
            file=out,
        )
        print(f'<circle cx="{t.fx}" cy="{t.fy}" r="0.15" fill="{colour}"/>', file=out)
        print(
            f'<text x="{t.fx + 1.2:g}" y="{t.fy + 0.6:g}" font-size="1.5" '
            f'font-family="monospace" fill="#222">{t.ref} {t.signal}</text>',
            file=out,
        )
    print("</svg>", file=out)


FORMATS = {"table": emit_table, "csv": emit_csv, "json": emit_json, "svg": emit_svg}
