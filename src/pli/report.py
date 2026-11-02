"""Human and machine readable reporting."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .models import ACTION_KEEP, ACTION_SKIP, ACTION_WRITE, Estimate, FilePlan


def format_time(seconds: float | None) -> str:
    if seconds is None:
        return "--:--:--"
    return datetime.fromtimestamp(seconds, timezone.utc).astimezone().strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def format_plan(plan: FilePlan) -> str:
    """One aligned line describing what will happen (or happened) to a file."""
    photo = plan.photo
    name = photo.path.name
    stamp = format_time(photo.timestamp)
    if plan.action == ACTION_KEEP:
        fix = photo.fix
        return (
            f"  {'keep':<6} {name:<28} {stamp}  "
            f"GPS {fix.latitude:.5f},{fix.longitude:.5f}"
        )
    estimate = plan.estimate
    if plan.action == ACTION_WRITE and estimate is not None:
        state = "wrote" if plan.written else "dry"
        return (
            f"  {state:<6} {name:<28} {stamp}  "
            f"{estimate.latitude:.5f},{estimate.longitude:.5f} "
            f"[{estimate.method}] +/-{estimate.uncertainty_m:.0f}m"
        )
    return f"  {'skip':<6} {name:<28} {stamp}  {plan.reason}"


def render_text(report) -> str:
    """Full console summary of a run."""
    lines: list[str] = []
    counts = report.counts()
    lines.append(f"folder      : {report.options.folder}")
    lines.append(f"backend     : {report.options.backend} (using {report.backend})")
    lines.append(f"method      : {report.options.method}")
    lines.append(
        f"scanned     : {report.scanned} file(s), {report.located} located, "
        f"{counts['written']} written, {counts['kept']} already located, "
        f"{counts['skipped']} skipped"
    )
    if report.options.dry_run:
        lines.append("mode        : dry run (nothing was written)")
    lines.append("")
    for plan in report.plans:
        lines.append(format_plan(plan))
    return "\n".join(lines)


def render_json(report) -> str:
    """Stable JSON structure, suitable for ``--report`` files and piping."""
    payload = {
        "folder": str(report.options.folder),
        "backend": report.backend,
        "requested_backend": report.options.backend,
        "method": report.options.method,
        "dry_run": report.options.dry_run,
        "scanned": report.scanned,
        "located": report.located,
        "counts": report.counts(),
        "files": [_file_payload(plan) for plan in report.plans],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _file_payload(plan: FilePlan) -> dict:
    photo = plan.photo
    return {
        "file": str(photo.path),
        "time": format_time(photo.timestamp),
        "time_source": photo.time_source,
        "time_raw": photo.time_raw,
        "timezone": photo.tz_note,
        "action": plan.action,
        "written": plan.written,
        "target": str(plan.target) if plan.target else None,
        "backup": str(plan.backup) if plan.backup else None,
        "reason": plan.reason or None,
        "existing": None
        if photo.fix is None
        else {
            "latitude": photo.fix.latitude,
            "longitude": photo.fix.longitude,
            "altitude": photo.fix.altitude,
        },
        "estimate": None if plan.estimate is None else _estimate_payload(plan.estimate),
    }


def _estimate_payload(estimate: Estimate) -> dict:
    return {
        "latitude": round(estimate.latitude, 7),
        "longitude": round(estimate.longitude, 7),
        "altitude": None if estimate.altitude is None else round(estimate.altitude, 2),
        "speed_kmh": None if estimate.speed_mps is None else round(estimate.speed_mps * 3.6, 2),
        "track_deg": None if estimate.track_deg is None else round(estimate.track_deg, 1),
        "method": estimate.method,
        "fraction": round(estimate.fraction, 4),
        "anchor_distance_m": round(estimate.anchor_distance_m, 1),
        "uncertainty_m": round(estimate.uncertainty_m, 1),
        "anchor_before": None if estimate.before is None else str(estimate.before),
        "anchor_after": None if estimate.after is None else str(estimate.after),
        "note": estimate.note,
    }


def write_report(destination: str, text: str, json_text: str) -> None:
    """Write the JSON report to a path (``-`` means stdout, handled by caller)."""
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json_text + "\n", encoding="utf-8")
