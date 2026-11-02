"""Command line interface for ``pli``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .interp import INTERPOLATION_METHODS, METHOD_SPEED
from .pipeline import RunOptions, read_photos, run
from .report import format_time, render_json, render_text, write_report
from .sampledata import create_walk_dataset
from .scan import iter_image_files
from .times import TZ_MODES, parse_utc_offset

COMMANDS = ("interpolate", "inspect", "demo")

EXAMPLES = """examples:
  pli photos/ --dry-run                 preview every position that would be written
  pli photos/                           fill missing GPS in place (keeps .bak copies)
  pli photos/ --method spline -o out/   smooth spline track, write copies to out/
  pli photos/ --extrapolate             also guess photos taken before/after the trip
  pli inspect photos/                   show the timeline and where fixes are missing
"""


def build_parser() -> argparse.ArgumentParser:
    """Build the full argument parser (``pli`` plus its sub-commands)."""
    parser = argparse.ArgumentParser(
        prog="pli",
        description="Reconstruct missing photo GPS positions by interpolating the "
        "shooting timeline, using a predicted movement speed.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"pli {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("folder", type=Path, help="folder to scan for photos")
    common.add_argument(
        "--ext",
        metavar="jpg,png",
        help="comma separated extension filter (default: jpeg, tiff, png, webp, raw)",
    )
    common.add_argument(
        "--no-recursive", action="store_true", help="do not descend into sub-folders"
    )
    common.add_argument(
        "--include-hidden", action="store_true", help="include hidden files and folders"
    )
    common.add_argument(
        "--backend", default="auto", choices=["auto", "piexif"], help="EXIF reader/writer"
    )
    common.add_argument("--jobs", type=int, default=4, help="parallel EXIF readers")
    common.add_argument(
        "--tz",
        default="auto",
        metavar="MODE",
        help="timezone handling for stamps without an offset: "
        + " | ".join(TZ_MODES)
        + " | +HH:MM (default: auto)",
    )

    interpolate = sub.add_parser(
        "interpolate", parents=[common], help="fill in the missing GPS data (default)"
    )
    interpolate.add_argument(
        "--method",
        default=METHOD_SPEED,
        choices=list(INTERPOLATION_METHODS),
        help="interpolation strategy (default: speed)",
    )
    interpolate.add_argument("--dry-run", action="store_true", help="do not touch any file")
    interpolate.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=None,
        help="write copies here instead of in place",
    )
    interpolate.add_argument(
        "--no-backup", action="store_true", help="skip the .bak copy when editing in place"
    )
    interpolate.add_argument(
        "--extrapolate",
        action="store_true",
        help="also estimate photos taken before the first / after the last fix",
    )
    interpolate.add_argument("--max-gap-seconds", type=float, default=21_600.0)
    interpolate.add_argument("--max-gap-distance", type=float, default=50_000.0, metavar="M")
    interpolate.add_argument(
        "--stationary-meters",
        type=float,
        default=12.0,
        help="anchors closer than this count as standing still (default: 12 m)",
    )
    interpolate.add_argument("--max-extrapolation-seconds", type=float, default=900.0)
    interpolate.add_argument("--max-extrapolation-meters", type=float, default=1_500.0)
    interpolate.add_argument("--no-speed", action="store_true", help="do not write GPSSpeed")
    interpolate.add_argument("--no-track", action="store_true", help="do not write GPSTrack")
    interpolate.add_argument(
        "--no-gps-time",
        action="store_true",
        help="do not write GPSDateStamp/GPSTimeStamp",
    )
    interpolate.add_argument(
        "--mtime-fallback",
        action="store_true",
        help="fall back to the file modification time when EXIF has no date",
    )
    interpolate.add_argument(
        "--report", dest="report", metavar="PATH", help="also write a JSON report"
    )
    interpolate.add_argument("--json", action="store_true", help="print JSON instead of a table")

    inspect = sub.add_parser(
        "inspect", parents=[common], help="show the timeline and what is missing"
    )
    inspect.add_argument("--json", action="store_true", help="print JSON instead of a table")
    inspect.add_argument(
        "--mtime-fallback",
        action="store_true",
        help="fall back to the file modification time when EXIF has no date",
    )

    demo = sub.add_parser("demo", help="create a small synthetic photo folder")
    demo.add_argument("folder", type=Path, help="where to create the demo photos")
    demo.add_argument("--count", type=int, default=None, help="how many photos to create")

    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point; returns the process exit code."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in COMMANDS and not argv[0].startswith("-"):
        argv.insert(0, "interpolate")  # ``pli photos/`` is the common case
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    if getattr(args, "tz", None) is not None:
        _validate_tz(args.tz, parser)
    try:
        if args.command == "interpolate":
            return _cmd_interpolate(args)
        if args.command == "inspect":
            return _cmd_inspect(args)
        return _cmd_demo(args)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _validate_tz(mode: str, parser: argparse.ArgumentParser) -> None:
    if mode in TZ_MODES or parse_utc_offset(mode) is not None:
        return
    parser.error(f"invalid --tz value: {mode!r}")


def _extensions(value: str | None) -> tuple[str, ...] | None:
    if not value:
        return None
    parts = [part.strip().lower() for part in value.replace(";", ",").split(",")]
    return tuple(part for part in parts if part)


def _run_options(args) -> RunOptions:
    if not args.folder.exists():
        raise FileNotFoundError(f"no such folder: {args.folder}")
    return RunOptions(
        folder=args.folder,
        recursive=not args.no_recursive,
        extensions=_extensions(args.ext),
        include_hidden=args.include_hidden,
        backend=args.backend,
        method=args.method,
        tz_mode=args.tz,
        mtime_fallback=args.mtime_fallback,
        dry_run=args.dry_run,
        output_dir=args.output_dir,
        backup=not args.no_backup,
        extrapolate=args.extrapolate,
        max_gap_seconds=args.max_gap_seconds,
        max_gap_distance_m=args.max_gap_distance,
        stationary_meters=args.stationary_meters,
        max_extrapolation_seconds=args.max_extrapolation_seconds,
        max_extrapolation_meters=args.max_extrapolation_meters,
        write_speed=not args.no_speed,
        write_track=not args.no_track,
        write_gps_time=not args.no_gps_time,
        jobs=max(1, args.jobs),
    )


def _cmd_interpolate(args) -> int:
    report = run(_run_options(args))
    if args.json:
        print(render_json(report))
    else:
        print(render_text(report))
    if args.report:
        write_report(args.report, render_text(report), render_json(report))
        print(f"report written to {args.report}")
    return 0


def _cmd_inspect(args) -> int:
    from .exifio import get_backend
    from .timeline import build_timeline

    if not args.folder.exists():
        raise FileNotFoundError(f"no such folder: {args.folder}")
    backend = get_backend(args.backend)
    paths = iter_image_files(
        args.folder,
        recursive=not args.no_recursive,
        extensions=_extensions(args.ext),
        include_hidden=args.include_hidden,
    )
    photos = read_photos(backend, paths, jobs=max(1, args.jobs))
    ordered = build_timeline(photos, tz_mode=args.tz, use_mtime_fallback=args.mtime_fallback)
    located = [photo for photo in ordered if photo.has_location]
    if args.json:
        _print_inspect_json(ordered, located)
        return 0

    print(f"{len(paths)} file(s), {len(located)} with GPS, backend {backend.name}")
    print(f"timezone mode: {args.tz}\n")
    print(f"  {'time':<20} {'GPS':<24} {'source':<18} file")
    for photo in ordered:
        if photo.has_location:
            where = f"{photo.fix.latitude:.5f},{photo.fix.longitude:.5f}"
        elif photo.read_error:
            where = "unreadable"
        else:
            where = "-"
        print(
            f"  {format_time(photo.timestamp):<20} {where:<24} "
            f"{photo.time_source:<18} {photo.path.name}"
        )
    _print_gap_summary(ordered, located)
    return 0


def _print_gap_summary(ordered, located) -> None:
    if not located:
        if ordered:
            print("\nno photo carries GPS: nothing can be interpolated")
        return
    if len(located) < 2:
        print("\nonly one located photo: use --extrapolate to extend the track")
        return
    gaps = []
    for before, after in zip(located, located[1:]):
        missing = sum(
            1
            for photo in ordered
            if not photo.has_location
            and before.timestamp <= photo.timestamp <= after.timestamp
        )
        if missing:
            gaps.append((before, after, missing))
    if not gaps:
        print("\nevery photo already has a position")
        return
    print("\nruns of photos without GPS:")
    for before, after, missing in gaps:
        span = after.timestamp - before.timestamp
        print(
            f"  {missing:>3} photo(s) between {before.path.name} and "
            f"{after.path.name} ({span / 60.0:.1f} min)"
        )


def _print_inspect_json(ordered, located) -> None:
    import json

    payload = {
        "scanned": len(ordered),
        "located": len(located),
        "files": [
            {
                "file": str(photo.path),
                "time": format_time(photo.timestamp),
                "time_source": photo.time_source,
                "timezone": photo.tz_note,
                "latitude": photo.fix.latitude if photo.has_location else None,
                "longitude": photo.fix.longitude if photo.has_location else None,
                "error": photo.read_error,
            }
            for photo in ordered
        ],
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def _cmd_demo(args) -> int:
    created = create_walk_dataset(args.folder, count=args.count)
    print(f"created {len(created)} synthetic photo(s) in {args.folder}")
    for path in created:
        print(f"  {path.name}")
    print("\nnext:")
    print(f"  pli inspect {args.folder}")
    print(f"  pli {args.folder} --dry-run                        # speed model")
    print(f"  pli {args.folder} --dry-run --method linear        # plain time-linear")
    print(f"  pli {args.folder} --dry-run --extrapolate          # past the last fix")
    return 0





if __name__ == "__main__":  # pragma: no cover - convenience for `python -m pli.cli`
    raise SystemExit(main())
