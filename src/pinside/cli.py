"""pinside -- read a KiCad board, check it, and generate firmware that matches it."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, baseline, modules, pogo
from .board import read_board, transform
from .checks import ERROR, WARNING, Finding, Limits, run
from .config import ConfigError, load, resolve_board, validate
from .firmware import GenerationError, generate
from .kicad.project import ProjectError, generate_project
from .report import FORMATS
from .scaffold import scaffold

EXIT_OK, EXIT_WARN, EXIT_ERROR, EXIT_USAGE = 0, 1, 2, 3

_BASELINE_HELP = (
    "JSON file of findings already accepted for this board. Suppression is by code and by "
    "reference, so a new occurrence of an accepted code still fails."
)


def _print_findings(findings, stream=None) -> None:
    # Resolved on the call, not in the signature: a default of sys.stderr binds whatever
    # sys.stderr was when this module was imported, which is the wrong stream for anything
    # that redirects it afterwards.
    stream = sys.stderr if stream is None else stream
    for finding in findings:
        print(f"pinside: {finding}", file=stream)


def _apply_baseline(findings: list[Finding], path: str | None) -> list[Finding] | None:
    """Drop the findings a baseline already accepts. None means the baseline was unusable."""
    if not path:
        return findings
    try:
        accepted = baseline.load(path)
    except baseline.BaselineError as err:
        print(f"pinside: {err}", file=sys.stderr)
        return None
    kept, suppressed = accepted.split(findings)
    if suppressed:
        # Said out loud, every time. A baseline that silently swallows half the report is how a
        # board ships with a finding nobody remembers accepting.
        print(
            f"pinside: {len(suppressed)} finding(s) accepted by {path}: "
            f"{', '.join(sorted({f.code for f in suppressed}))}",
            file=sys.stderr,
        )
    return kept


def _emit_json_findings(findings: list[Finding], **extra) -> None:
    """The machine-readable half of what generate and project report.

    Both of them write their real output to a directory and their findings to stderr, which
    leaves nothing for a caller to parse. This goes to stdout so the two do not mix.
    """
    payload = {
        "findings": [f.as_dict() for f in findings],
        "errors": sum(1 for f in findings if f.severity == ERROR),
        "warnings": sum(1 for f in findings if f.severity == WARNING),
        **extra,
    }
    json.dump(payload, sys.stdout, indent=2)
    print()


def _limits_from(args) -> Limits:
    return Limits(
        probe_pitch=args.probe_pitch,
        probe_body=args.probe_body,
        edge_clearance=args.edge_clearance,
        hole_clearance=args.hole_clearance,
        min_pad_diameter=args.min_pad,
        min_mounting_holes=args.min_holes,
    )


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

    # Written before the baseline is applied, or the second run would record an empty file.
    if args.write_baseline:
        count = baseline.write(args.write_baseline, findings, args.board)
        print(
            f"pinside: wrote {args.write_baseline} accepting {count} finding(s); "
            "add a note to each before committing it",
            file=sys.stderr,
        )
        return EXIT_OK

    findings = _apply_baseline(findings, args.baseline)
    if findings is None:
        return EXIT_USAGE

    if args.output:
        with Path(args.output).open("w", encoding="utf-8") as handle:
            FORMATS[args.format](board, findings, handle)
    else:
        FORMATS[args.format](board, findings, sys.stdout)

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
            relative = str(
                Path(args.board)
                .resolve()
                .relative_to(Path(args.output).resolve().parent, walk_up=True)
            )
            if len(relative) < len(absolute):
                board_path = relative
        except (ValueError, TypeError):
            pass

    try:
        draft = scaffold(
            board,
            name=name,
            mcu=args.mcu,
            board_path=board_path,
            module_name=args.carrier,
            probe=args.probe,
        )
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
    unclaimed = [
        t.signal for t in board.test_points if t.signal and not t.is_ground and t.bus == "control"
    ]
    if unclaimed:
        print(
            f"pinside: {len(unclaimed)} signals were not recognised as belonging to a bus and "
            f"became plain GPIO channels: {', '.join(sorted(unclaimed))}",
            file=sys.stderr,
        )
    print(
        "pinside: this is a draft -- check the bus grouping and directions before generating",
        file=sys.stderr,
    )
    return EXIT_OK


def cmd_project(args) -> int:
    try:
        cfg = load(args.config)
        board = resolve_board(cfg, args.board)
    except ConfigError as err:
        print(f"pinside: {err}", file=sys.stderr)
        return EXIT_USAGE

    try:
        result = generate_project(cfg, board, args.out, force=args.force)
    except ProjectError as err:
        print(f"pinside: {err}", file=sys.stderr)
        if args.json:
            _emit_json_findings(err.findings, written=False)
        else:
            _print_findings(err.findings)
        return EXIT_ERROR

    findings = _apply_baseline(result.findings, args.baseline)
    if findings is None:
        return EXIT_USAGE

    if args.json:
        _emit_json_findings(
            findings,
            out_dir=str(result.out_dir),
            files=[str(f) for f in result.files],
            probes_placed=result.probes_placed,
            unplaced=list(result.unplaced),
        )
    elif findings:
        _print_findings(findings)
    probe = cfg.probe_part
    print(f"pinside: wrote {len(result.files)} files to {result.out_dir}", file=sys.stderr)
    print(
        f"pinside: {result.probes_placed} probes placed at their DUT coordinates "
        f"({probe.receptacle})",
        file=sys.stderr,
    )
    if result.unplaced:
        print(
            f"pinside: {len(result.unplaced)} channel(s) had no test point to sit on: "
            f"{', '.join(result.unplaced)}",
            file=sys.stderr,
        )
    print(
        "pinside: a GND pour is drawn on both layers; routing and the controller's placement "
        "are left to you",
        file=sys.stderr,
    )

    if args.strict and any(f.severity == WARNING for f in findings):
        return EXIT_WARN
    return EXIT_OK


def cmd_probe(args) -> int:
    """Connect to a fixture and prove it is the right one, wired the way the config says."""
    from . import client  # imported here: it needs pyserial, and nothing else does

    try:
        cfg = load(args.config)
    except ConfigError as err:
        print(f"pinside: {err}", file=sys.stderr)
        return EXIT_USAGE

    try:
        device = args.port or _sole_port(client)
    except client.FixtureError as err:
        print(f"pinside: {err}", file=sys.stderr)
        return EXIT_USAGE

    try:
        fixture = client.connect(
            device, cfg, baud=args.baud, timeout=args.timeout, check_hash=not args.no_hash_check
        )
    except client.ConfigMismatchError as err:
        # Its own exit code would be nicer, but this is what --strict-style tooling already
        # keys on, and a mismatched rig is an error by any reading.
        print(f"pinside: {err}", file=sys.stderr)
        return EXIT_ERROR
    except client.FixtureError as err:
        print(f"pinside: {err}", file=sys.stderr)
        return EXIT_USAGE

    with fixture:
        try:
            channels = fixture.channels()
            rails = fixture.adc_snapshot()
            gpio = fixture.gpio_snapshot() if not args.no_gpio else []
        except client.FixtureError as err:
            print(f"pinside: {err}", file=sys.stderr)
            return EXIT_ERROR

    out_of_range = [r for r in rails if not r.get("in_range", True)]

    if args.json:
        json.dump(
            {
                "port": device,
                "info": fixture.info,
                "channels": channels,
                "adc": rails,
                "gpio": gpio,
                "out_of_range": [r.get("channel") for r in out_of_range],
            },
            sys.stdout,
            indent=2,
        )
        print()
    else:
        info = fixture.info or {}
        print(f"{info.get('fixture', '?')} on {device}")
        print(f"  firmware {info.get('version', '?')}  config {info.get('config_hash', '?')}")
        print(f"  {len(channels)} channels")
        for channel in channels:
            probe = channel.get("probe") or "-"
            print(f"    {channel.get('kind', '?'):5} {channel.get('name', '?'):20} {probe}")
        if rails:
            print("  rails")
            for rail in rails:
                mark = " " if rail.get("in_range", True) else "!"
                print(
                    f"   {mark}  {rail.get('channel', '?'):20} "
                    f"{rail.get('millivolts', 0) / 1000:.3f} V  ({rail.get('probe', '-')})"
                )

    if out_of_range:
        names = ", ".join(r.get("channel", "?") for r in out_of_range)
        print(f"pinside: {len(out_of_range)} rail(s) out of range: {names}", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_OK


def _sole_port(client) -> str:
    """The one serial port on this machine, or a message naming the ones there are.

    Guessing between several is worse than asking: the wrong guess talks to whatever else is
    plugged in, and the hash check only catches it if that thing happens to answer.
    """
    found = client.ports()
    if len(found) == 1:
        return found[0][0]
    if not found:
        raise client.FixtureError("no serial ports found; is the fixture plugged in?")
    listing = "\n".join(f"  {device}  {description}" for device, description in found)
    raise client.FixtureError(f"several serial ports; name one with --port:\n{listing}")


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
        findings = _apply_baseline(validate(cfg, board), args.baseline)
        if findings is None:
            return EXIT_USAGE
        if args.json:
            _emit_json_findings(findings, config=cfg.name, mcu=cfg.mcu, written=False)
        else:
            _print_findings(findings, sys.stdout)
        if any(f.severity == ERROR for f in findings):
            return EXIT_ERROR
        if not args.json:
            print(
                f"pinside: {cfg.name} validates against {cfg.mcu} and {cfg.dut_board or 'no board'}"
            )
        return EXIT_OK

    try:
        result = generate(cfg, board, args.out, force=args.force)
    except GenerationError as err:
        print(f"pinside: {err} -- nothing was written", file=sys.stderr)
        if args.json:
            _emit_json_findings(err.findings, config=cfg.name, written=False)
        else:
            _print_findings(err.findings)
        return EXIT_ERROR

    findings = _apply_baseline(result.findings, args.baseline)
    if findings is None:
        return EXIT_USAGE

    if args.json:
        _emit_json_findings(
            findings,
            config=cfg.name,
            config_hash=result.config_hash,
            out_dir=str(result.out_dir),
            files=[str(f) for f in result.files],
            written=True,
        )
    else:
        if findings:
            _print_findings(findings)
        print(
            f"pinside: wrote {len(result.files)} files to {result.out_dir} "
            f"(config {result.config_hash})",
            file=sys.stderr,
        )
        print(
            f"pinside: build with cmake, or run {result.out_dir}/test/run.sh for the host tests",
            file=sys.stderr,
        )

    if args.strict and any(f.severity == WARNING for f in findings):
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
        "KiCad files are only ever read.",
    )
    p.add_argument("--version", action="version", version=f"pinside {__version__}")

    sub = p.add_subparsers(dest="command")
    strict_help = "exit non-zero on warnings as well as errors"

    check = sub.add_parser(
        "check",
        help="report the board's geometry and whether a fixture can be built",
        description="Read a .kicad_pcb and report every probe, mounting hole and outline "
        "segment, together with anything that would make a fixture fail.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    check.add_argument("board", help="path to the DUT's .kicad_pcb")
    check.add_argument("-f", "--format", choices=sorted(FORMATS), default="table")
    check.add_argument("-o", "--output", help="write here instead of stdout")
    check.add_argument(
        "--origin", choices=["page", "outline"], default="outline", help="fixture-frame origin"
    )
    check.add_argument(
        "--mirror",
        choices=["none", "x", "y"],
        default="none",
        help="mirror the fixture frame; 'x' suits a DUT laid face-down",
    )
    d = Limits()
    limits = check.add_argument_group(
        "fixture limits", "the physical facts the checks are measured against"
    )
    limits.add_argument(
        "--probe-pitch",
        type=float,
        default=d.probe_pitch,
        help="minimum centre-to-centre spacing of two receptacles, mm",
    )
    limits.add_argument(
        "--probe-body",
        type=float,
        default=d.probe_body,
        help="outside diameter of the receptacle body, mm; what has to clear a neighbouring part",
    )
    limits.add_argument(
        "--edge-clearance",
        type=float,
        default=d.edge_clearance,
        help="minimum probe-centre to board-edge distance, mm",
    )
    limits.add_argument(
        "--hole-clearance",
        type=float,
        default=d.hole_clearance,
        help="minimum gap between a probe pad and a mounting-hole pad, mm",
    )
    limits.add_argument(
        "--min-pad",
        type=float,
        default=d.min_pad_diameter,
        help="smallest DUT test pad a spring tip can be trusted to hit, mm",
    )
    limits.add_argument(
        "--min-holes",
        type=int,
        default=d.min_mounting_holes,
        help="mounting holes needed to locate the board",
    )
    check.add_argument(
        "--ignore",
        metavar="CODES",
        default="",
        help="comma-separated finding codes to suppress, e.g. PS041,PS042",
    )
    check.add_argument(
        "--baseline",
        metavar="FILE",
        help=_BASELINE_HELP,
    )
    check.add_argument(
        "--write-baseline",
        metavar="FILE",
        help="write a baseline accepting every finding this board currently has, and stop. "
        "Review it, write the reason into each note, and commit it.",
    )
    check.add_argument("--no-checks", action="store_true", help="extract only, run no checks")
    check.add_argument("--strict", action="store_true", help=strict_help)
    check.set_defaults(func=cmd_check)

    init = sub.add_parser(
        "init",
        help="draft a fixture config from a board",
        description="Read a board and write a fixture config covering every test point on it. "
        "The grouping comes from the signal names, so treat the result as a draft.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    init.add_argument("board", help="path to the DUT's .kicad_pcb")
    init.add_argument("-o", "--output", help="write here instead of stdout")
    # --carrier, not --board: the positional is already a board, argparse gives both the same
    # dest, and the option silently won. Every `pinside init` then looked up the .kicad_pcb path
    # in the module catalogue and refused.
    init.add_argument(
        "--carrier",
        default=modules.DEFAULT,
        choices=[*sorted(modules.MODULES), modules.BARE],
        help="carrier board the fixture is built around",
    )
    init.add_argument("--mcu", default="rp2350b", help="microcontroller, when --carrier is 'bare'")
    init.add_argument(
        "--probe",
        default=pogo.DEFAULT,
        choices=sorted(pogo.PROBES),
        help="spring-pin probe the fixture is drilled for",
    )
    init.add_argument("--name", help="fixture name (default: the board's filename)")
    init.set_defaults(func=cmd_init, strict=False)

    gen = sub.add_parser(
        "generate",
        help="generate firmware from a fixture config",
        description="Validate a fixture config against its target microcontroller and its DUT "
        "board, then write a buildable Pico SDK project with host tests. Nothing is "
        "written if the config does not validate.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    gen.add_argument("config", help="path to the fixture config JSON")
    gen.add_argument("--out", default="firmware", help="directory to write the project into")
    gen.add_argument("--board", help="use this .kicad_pcb instead of the one the config names")
    gen.add_argument(
        "--force",
        action="store_true",
        help="write into a non-empty directory pinside did not create",
    )
    gen.add_argument("--dry-run", action="store_true", help="validate and report, writing nothing")
    gen.add_argument("--strict", action="store_true", help=strict_help)
    gen.add_argument(
        "--json",
        action="store_true",
        help="report findings as JSON on stdout instead of prose on stderr",
    )
    gen.add_argument(
        "--baseline",
        metavar="FILE",
        help=_BASELINE_HELP,
    )
    gen.set_defaults(func=cmd_generate)

    proj = sub.add_parser(
        "project",
        help="generate a KiCad project for the fixture board",
        description="Write a KiCad project whose probes sit at the DUT's own test-point "
        "coordinates, with the DUT's outline and mounting holes. Routing is left "
        "to you; the placement is the part that has to be exact.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    proj.add_argument("config", help="path to the fixture config JSON")
    proj.add_argument(
        "--out", default="fixture-board", help="directory to write the KiCad project into"
    )
    proj.add_argument("--board", help="use this .kicad_pcb instead of the one the config names")
    proj.add_argument(
        "--force",
        action="store_true",
        help="write into a non-empty directory pinside did not create",
    )
    proj.add_argument("--strict", action="store_true", help=strict_help)
    proj.add_argument(
        "--json",
        action="store_true",
        help="report findings as JSON on stdout instead of prose on stderr",
    )
    proj.add_argument(
        "--baseline",
        metavar="FILE",
        help=_BASELINE_HELP,
    )
    proj.set_defaults(func=cmd_project)

    probe_cmd = sub.add_parser(
        "probe",
        help="connect to a fixture and check it against its config",
        description="Open the fixture's serial port, confirm the firmware on it was generated "
        "from this config, and report every channel and rail. This is the bench smoke test: "
        "it answers 'is the fixture the one I think it is, and is it wired up'.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    probe_cmd.add_argument("config", help="path to the fixture config JSON")
    probe_cmd.add_argument(
        "--port", help="serial device; the only one present is used when there is exactly one"
    )
    probe_cmd.add_argument("--baud", type=int, default=115200, help="USB CDC ignores this")
    probe_cmd.add_argument("--timeout", type=float, default=5.0, help="seconds to wait per call")
    probe_cmd.add_argument(
        "--no-hash-check",
        action="store_true",
        help="talk to the fixture even when its config hash disagrees with this config. "
        "The mismatch is exactly what this command exists to catch, so say why in the commit.",
    )
    probe_cmd.add_argument(
        "--no-gpio", action="store_true", help="skip the GPIO snapshot; ADC rails only"
    )
    probe_cmd.add_argument("--json", action="store_true", help="report as JSON on stdout")
    probe_cmd.set_defaults(func=cmd_probe, strict=False)

    return p


def commands(parser: argparse.ArgumentParser) -> set[str]:
    """Every subcommand the parser accepts, asked of the parser itself."""
    # argparse exposes no public accessor for this, and the alternative -- a list of names
    # kept beside the parser -- is the thing that broke.
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices)
    return set()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()

    # `pinside board.kicad_pcb` keeps working as a shorthand for `pinside check`. The command
    # names come from the parser rather than a list kept alongside it: a hand-maintained set
    # silently swallows any subcommand added later, turning `pinside probe cfg.json` into
    # `pinside check probe`, which fails while complaining about the wrong thing.
    if argv and not argv[0].startswith("-") and argv[0] not in commands(parser):
        argv.insert(0, "check")

    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_USAGE
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
