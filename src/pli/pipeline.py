"""Pipeline: scan -> read -> timeline -> interpolate -> write -> report."""

from __future__ import annotations

import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .exifio import ExifBackend, get_backend
from .interp import InterpolationOptions, interpolate
from .models import ACTION_KEEP, ACTION_SKIP, ACTION_WRITE, FilePlan, GpsFix, PhotoInfo
from .scan import iter_image_files
from .timeline import build_timeline
from .times import TZ_MODE_AS_WRITTEN, TZ_MODE_AUTO


@dataclass(slots=True)
class RunOptions:
    """Everything the user can turn from the command line."""

    folder: Path
    recursive: bool = True
    extensions: tuple[str, ...] | None = None
    include_hidden: bool = False
    backend: str = "auto"
    method: str = "speed"
    tz_mode: str = TZ_MODE_AUTO
    mtime_fallback: bool = False
    dry_run: bool = False
    output_dir: Path | None = None
    backup: bool = True
    extrapolate: bool = False
    max_gap_seconds: float = 21_600.0
    max_gap_distance_m: float = 50_000.0
    stationary_meters: float = 12.0
    max_extrapolation_seconds: float = 900.0
    max_extrapolation_meters: float = 1_500.0
    write_speed: bool = True
    write_track: bool = True
    write_gps_time: bool = True
    jobs: int = 4

    def interpolation_options(self) -> InterpolationOptions:
        from .interp import InterpolationOptions as _Options

        return _Options(
            method=self.method,
            max_gap_seconds=self.max_gap_seconds,
            max_gap_distance_m=self.max_gap_distance_m,
            stationary_meters=self.stationary_meters,
            extrapolate=self.extrapolate,
            max_extrapolation_seconds=self.max_extrapolation_seconds,
            max_extrapolation_meters=self.max_extrapolation_meters,
        )


def read_photos(backend: ExifBackend, paths: list[Path], jobs: int = 4) -> list[PhotoInfo]:
    """Read EXIF metadata for every path, in parallel when it helps."""

    def read_one(path: Path) -> PhotoInfo:
        if not backend.can_read(path):
            info = PhotoInfo(path=path, mtime=path.stat().st_mtime if path.exists() else 0.0)
            info.read_error = (
                f"backend '{backend.name}' cannot read {path.suffix or 'this file'}"
            )
            return info
        try:
            return backend.read(path)
        except Exception as exc:  # noqa: BLE001 - one bad file must not kill the run
            info = PhotoInfo(path=path, mtime=0.0)
            info.read_error = f"unexpected read failure: {type(exc).__name__}: {exc}"
            return info

    if jobs <= 1 or len(paths) <= 1:
        return [read_one(path) for path in paths]
    with ThreadPoolExecutor(max_workers=min(jobs, len(paths))) as pool:
        return list(pool.map(read_one, paths))


@dataclass(slots=True)
class RunReport:
    """Outcome of one pipeline run."""

    options: RunOptions
    backend: str
    plans: list[FilePlan] = field(default_factory=list)
    located: int = 0
    scanned: int = 0
    timeline: list[PhotoInfo] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        tally = {"written": 0, "kept": 0, "skipped": 0}
        for plan in self.plans:
            if plan.written:
                tally["written"] += 1
            elif plan.action == ACTION_KEEP:
                tally["kept"] += 1
            else:
                tally["skipped"] += 1
        return tally


def run(options: RunOptions) -> RunReport:
    """Execute a full interpolation pass and return the per-file outcome."""
    backend = get_backend(options.backend)
    paths = iter_image_files(
        options.folder,
        recursive=options.recursive,
        extensions=options.extensions,
        include_hidden=options.include_hidden,
    )
    report = RunReport(options=options, backend=backend.name, scanned=len(paths))
    if not paths:
        return report

    photos = read_photos(backend, paths, jobs=options.jobs)
    ordered = build_timeline(
        photos, tz_mode=options.tz_mode, use_mtime_fallback=options.mtime_fallback
    )
    report.timeline = ordered
    result = interpolate(ordered, options.interpolation_options())
    report.located = result.located

    if options.output_dir is not None and not options.dry_run:
        options.output_dir.mkdir(parents=True, exist_ok=True)

    for index, photo in enumerate(ordered):
        plan = _plan_for(
            backend,
            options,
            photo,
            result.estimate_for(index),
            result.skip_note_for(index),
        )
        report.plans.append(plan)
    kept = {id(photo) for photo in ordered}
    for photo in photos:
        if id(photo) not in kept:
            report.plans.append(
                FilePlan(
                    photo=photo,
                    action=ACTION_SKIP,
                    reason=photo.read_error or "no usable timestamp",
                )
            )

    if not options.dry_run:
        _apply(backend, report, options)
    return report


def _plan_for(
    backend: ExifBackend,
    options: RunOptions,
    photo: PhotoInfo,
    estimate,
    note: str | None = None,
) -> FilePlan:
    """Decide what happens to one photo: keep, write, or skip with a reason."""
    plan = FilePlan(photo=photo, estimate=estimate)
    if estimate is None:
        if photo.has_location:
            plan.action = ACTION_KEEP
            plan.reason = "already located"
        else:
            plan.reason = note or "nothing to do"
        return plan
    if not backend.can_write(photo.path):
        plan.reason = (
            f"position computed with '{estimate.method}' but backend '{backend.name}' "
            f"cannot write {photo.path.suffix or 'this file'}"
        )
        return plan
    plan.action = ACTION_WRITE
    plan.target = (
        options.output_dir / photo.path.name
        if options.output_dir is not None
        else photo.path
    )
    if options.backup and options.output_dir is None:
        plan.backup = photo.path.with_name(photo.path.name + ".bak")
    return plan


def _apply(backend: ExifBackend, report: RunReport, options: RunOptions) -> None:
    for plan in report.plans:
        if plan.action != ACTION_WRITE or plan.target is None or plan.estimate is None:
            continue
        estimate = plan.estimate
        fix = GpsFix(
            latitude=estimate.latitude,
            longitude=estimate.longitude,
            altitude=estimate.altitude,
            speed_mps=estimate.speed_mps if options.write_speed else None,
            track_deg=estimate.track_deg if options.write_track else None,
        )
        if plan.backup is not None and not plan.backup.exists():
            shutil.copy2(plan.photo.path, plan.backup)
        try:
            photo = plan.photo
            when = photo.timestamp if options.write_gps_time else None
            offset = None
            if (
                when is not None
                and photo.wall_time is not None
                and options.tz_mode != TZ_MODE_AS_WRITTEN
            ):
                offset = photo.effective_offset_seconds
            backend.write_fix(photo.path, plan.target, fix, when, offset)
            plan.written = True
        except Exception as exc:  # noqa: BLE001 - report and keep going
            plan.action = ACTION_SKIP
            plan.reason = f"write failed: {type(exc).__name__}: {exc}"
            plan.written = False

