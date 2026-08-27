"""pinside -- read a KiCad board and tell you whether you can build a fixture against it."""

from __future__ import annotations

import argparse
import sys

from .board import read_board, transform
from .checks import ERROR, WARNING, Limits, run
from .report import FORMATS

EXIT_OK, EXIT_WARN, EXIT_ERROR, EXIT_USAGE = 0, 1, 2, 3


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pinside",
        description="Extract test points, mounting holes and the board outline from a "
                    ".kicad_pcb, and check whether a bed-of-nails fixture can be built from them.",
        epilog="Exit status: 0 clean, 1 warnings only, 2 errors, 3 bad usage. The board file is "
               "only ever read.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("board", help="path to the DUT's .kicad_pcb")
    p.add_argument("-f", "--format", choices=sorted(FORMATS), default="table")
    p.add_argument("-o", "--output", help="write here instead of stdout")
    p.add_argument("--origin", choices=["page", "outline"], default="outline",
                   help="fixture-frame origin")
    p.add_argument("--mirror", choices=["none", "x", "y"], default="none",
                   help="mirror the fixture frame; 'x' suits a DUT laid face-down on the probes")

    limits = p.add_argument_group(
        "fixture limits", "the physical facts the checks are measured against")
    d = Limits()
    limits.add_argument("--probe-pitch", type=float, default=d.probe_pitch,
                        help="minimum centre-to-centre spacing of two receptacles, mm")
    limits.add_argument("--edge-clearance", type=float, default=d.edge_clearance,
                        help="minimum probe-centre to board-edge distance, mm")
    limits.add_argument("--hole-clearance", type=float, default=d.hole_clearance,
                        help="minimum gap between a probe pad and a mounting-hole pad, mm")
    limits.add_argument("--min-pad", type=float, default=d.min_pad_diameter,
                        help="smallest DUT test pad a spring tip can be trusted to hit, mm")
    limits.add_argument("--min-holes", type=int, default=d.min_mounting_holes,
                        help="mounting holes needed to locate the board")

    p.add_argument("--ignore", metavar="CODES", default="",
                   help="comma-separated finding codes to suppress, e.g. PS041,PS042")
    p.add_argument("--strict", action="store_true",
                   help="exit non-zero on warnings as well as errors")
    p.add_argument("--no-checks", action="store_true", help="extract only, run no checks")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        board = transform(read_board(args.board), args.origin, args.mirror)
    except (OSError, ValueError) as err:
        print(f"pinside: {err}", file=sys.stderr)
        return EXIT_USAGE

    findings = []
    if not args.no_checks:
        findings = run(board, Limits(
            probe_pitch=args.probe_pitch, edge_clearance=args.edge_clearance,
            hole_clearance=args.hole_clearance, min_pad_diameter=args.min_pad,
            min_mounting_holes=args.min_holes))
        ignored = {c.strip().upper() for c in args.ignore.split(",") if c.strip()}
        findings = [f for f in findings if f.code not in ignored]

    handle = open(args.output, "w", encoding="utf-8") if args.output else sys.stdout
    try:
        FORMATS[args.format](board, findings, handle)
    finally:
        if args.output:
            handle.close()

    # Machine-readable formats carry the findings inside the payload; the rest need stderr.
    if findings and args.format in ("csv", "svg"):
        for f in findings:
            print(f"pinside: {f}", file=sys.stderr)
    elif findings and args.format == "json" and args.output:
        for f in findings:
            print(f"pinside: {f}", file=sys.stderr)

    if any(f.severity == ERROR for f in findings):
        return EXIT_ERROR
    if args.strict and any(f.severity == WARNING for f in findings):
        return EXIT_WARN
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
