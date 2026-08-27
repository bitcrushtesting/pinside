"""pinside -- read a KiCad board, check it, and generate firmware that matches it."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .board import read_board, transform
from .checks import ERROR, WARNING, Limits, run
from .config import ConfigError, load, resolve_board, validate
from .firmware import GenerationError, generate
from .report import FORMATS
from .scaffold import scaffold

EXIT_OK, EXIT_WARN, EXIT_ERROR, EXIT_USAGE = 0, 1, 2, 3


def _print_findings(findings, stream=sys.stderr) -> None:
    for finding in findings:
        print(f"pinside: {finding}", file=stream)


def _limits_from(args) -> Limits:
    return Limits(probe_pitch=args.probe_pitch, edge_clearance=args.edge_clearance,
                  hole_clearance=args.hole_clearance, min_pad_diameter=args.min_pad,
                  min_mounting_holes=args.min_holes)


# --------------------------------------------------------------------------- commands


def cmd_check(args) -> int:
    try:
        board = transform(read_board(args.board), args.origin, args.mirror)
    except (OSError, ValueError) as err:
        print(f"pinside: {err}", file=sys.stderr)
        return EXIT_USAGE

    findings = []
    if not args.no_checks:
        findings = run(board, _limits_from(args))
        ignored = {c.strip().upper() for c in args.ignore.split(",") if c.strip()}
        findings = [f for f in findings if f.code not in ignored]

    handle = open(args.output, "w", encoding="utf-8") if args.output else sys.stdout
    try:
        FORMATS[args.format](board, findings, handle)
    finally:
        if args.output:
            handle.close()

    # The table and JSON formats carry the findings themselves; the rest need stderr.
    if findings and (args.format in ("csv", "svg") or args.output):
        _print_findings(findings)

    if any(f.severity == ERROR for f in findings):
        return EXIT_ERROR
    if args.strict and any(f.severity == WARNING for f in findings):
        return EXIT_WARN
    return EXIT_OK


def cmd_init(args) -> int:
    try:
        board = transform(read_board(args.board))
    except (OSError, ValueError) as err:
        print(f"pinside: {err}", file=sys.stderr)
        return EXIT_USAGE

    name = args.name or Path(args.board).stem
    board_path = args.board
    if args.output:
        # The config records where its board is, relative to itself, so the pair can be moved
        # together. When the two live far apart a relative path is worse than useless -- it is
        # long and it hides the real location -- so fall back to absolute.
        absolute = str(Path(args.board).resolve())
        board_path = absolute
        try:
            relative = str(Path(args.board).resolve().relative_to(
                Path(args.output).resolve().parent, walk_up=True))
            if len(relative) < len(absolute):
                board_path = relative
        except (ValueError, TypeError):
            pass

    try:
        draft = scaffold(board, name=name, mcu=args.mcu, board_path=board_path)
    except ValueError as err:
        print(f"pinside: {err}", file=sys.stderr)
        return EXIT_USAGE

    text = json.dumps(draft, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"pinside: wrote {args.output}", file=sys.stderr)
    else:
        print(text, end="")

    # Say plainly that this is a draft: the names decided the grouping, and names can mislead.
    unclaimed = [t.signal for t in board.test_points
                 if t.signal and not t.is_ground and t.bus == "control"]
    if unclaimed:
        print(f"pinside: {len(unclaimed)} signals were not recognised as belonging to a bus and "
              f"became plain GPIO channels: {', '.join(sorted(unclaimed))}", file=sys.stderr)
    print("pinside: this is a draft -- check the bus grouping and directions before generating",
          file=sys.stderr)
    return EXIT_OK


def cmd_generate(args) -> int:
    try:
        cfg = load(args.config)
        board = resolve_board(cfg, args.board)
    except ConfigError as err:
        print(f"pinside: {err}", file=sys.stderr)
        return EXIT_USAGE
    except (OSError, ValueError) as err:
        print(f"pinside: {err}", file=sys.stderr)
        return EXIT_USAGE

    if args.dry_run:
        findings = validate(cfg, board)
        _print_findings(findings, sys.stdout)
        if any(f.severity == ERROR for f in findings):
            return EXIT_ERROR
        print(f"pinside: {cfg.name} validates against "
              f"{cfg.mcu} and {cfg.dut_board or 'no board'}")
        return EXIT_OK

    try:
        result = generate(cfg, board, args.out, force=args.force)
    except GenerationError as err:
        print(f"pinside: {err} -- nothing was written", file=sys.stderr)
        _print_findings(err.findings)
        return EXIT_ERROR

    if result.findings:
        _print_findings(result.findings)
    print(f"pinside: wrote {len(result.files)} files to {result.out_dir} "
          f"(config {result.config_hash})", file=sys.stderr)
    print(f"pinside: build with cmake, or run {result.out_dir}/test/run.sh for the host tests",
          file=sys.stderr)

    if args.strict and any(f.severity == WARNING for f in result.findings):
        return EXIT_WARN
    return EXIT_OK


# --------------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pinside",
        description="Extract test points, mounting holes and the board outline from a "
                    ".kicad_pcb, check whether a bed-of-nails fixture can be built from them, "
                    "and generate firmware that matches the board.",
        epilog="Exit status: 0 clean, 1 warnings under --strict, 2 errors, 3 bad usage. "
               "KiCad files are only ever read.")
    p.add_argument("--version", action="version", version=f"pinside {__version__}")

    sub = p.add_subparsers(dest="command")
    strict_help = "exit non-zero on warnings as well as errors"

    check = sub.add_parser(
        "check", help="report the board's geometry and whether a fixture can be built",
        description="Read a .kicad_pcb and report every probe, mounting hole and outline "
                    "segment, together with anything that would make a fixture fail.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    check.add_argument("board", help="path to the DUT's .kicad_pcb")
    check.add_argument("-f", "--format", choices=sorted(FORMATS), default="table")
    check.add_argument("-o", "--output", help="write here instead of stdout")
    check.add_argument("--origin", choices=["page", "outline"], default="outline",
                       help="fixture-frame origin")
    check.add_argument("--mirror", choices=["none", "x", "y"], default="none",
                       help="mirror the fixture frame; 'x' suits a DUT laid face-down")
    d = Limits()
    limits = check.add_argument_group("fixture limits",
                                      "the physical facts the checks are measured against")
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
    check.add_argument("--ignore", metavar="CODES", default="",
                       help="comma-separated finding codes to suppress, e.g. PS041,PS042")
    check.add_argument("--no-checks", action="store_true", help="extract only, run no checks")
    check.add_argument("--strict", action="store_true", help=strict_help)
    check.set_defaults(func=cmd_check)

    init = sub.add_parser(
        "init", help="draft a fixture config from a board",
        description="Read a board and write a fixture config covering every test point on it. "
                    "The grouping comes from the signal names, so treat the result as a draft.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    init.add_argument("board", help="path to the DUT's .kicad_pcb")
    init.add_argument("-o", "--output", help="write here instead of stdout")
    init.add_argument("--mcu", default="rp2350b", help="target microcontroller")
    init.add_argument("--name", help="fixture name (default: the board's filename)")
    init.set_defaults(func=cmd_init, strict=False)

    gen = sub.add_parser(
        "generate", help="generate firmware from a fixture config",
        description="Validate a fixture config against its target microcontroller and its DUT "
                    "board, then write a buildable Pico SDK project with host tests. Nothing is "
                    "written if the config does not validate.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    gen.add_argument("config", help="path to the fixture config JSON")
    gen.add_argument("--out", default="firmware", help="directory to write the project into")
    gen.add_argument("--board", help="use this .kicad_pcb instead of the one the config names")
    gen.add_argument("--force", action="store_true",
                     help="write into a non-empty directory pinside did not create")
    gen.add_argument("--dry-run", action="store_true",
                     help="validate and report, writing nothing")
    gen.add_argument("--strict", action="store_true", help=strict_help)
    gen.set_defaults(func=cmd_generate)

    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()

    # `pinside board.kicad_pcb` keeps working as a shorthand for `pinside check`.
    if argv and not argv[0].startswith("-") and argv[0] not in {"check", "init", "generate"}:
        argv.insert(0, "check")

    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_USAGE
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
