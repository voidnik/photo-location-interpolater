"""EXIF date/time parsing and timezone normalisation.

Only *relative* time matters for interpolation, so the tricky part is not
getting the absolute instant perfect - it is keeping every photo on one
consistent timeline.  Cameras that travelled across time zones (or that never
recorded an offset at all) are handled by :func:`resolve_epoch`, which decides
which UTC offset to assume for a wall-clock timestamp.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

TZ_MODE_AS_WRITTEN = "as-written"
TZ_MODE_UTC = "utc"
TZ_MODE_AUTO = "auto"
TZ_MODES = (TZ_MODE_AS_WRITTEN, TZ_MODE_UTC, TZ_MODE_AUTO)

_DATETIME_RE = re.compile(
    r"^\s*(\d{4})[:\-](\d{2})[:\-](\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?"
)
_OFFSET_RE = re.compile(r"^\s*([+-])(\d{1,2}):?(\d{2})?\s*$")


def parse_exif_datetime(text: str | None) -> datetime | None:
    """Parse ``"2024:05:01 13:45:12"`` (the EXIF layout) into a datetime."""
    if not text:
        return None
    match = _DATETIME_RE.match(str(text))
    if not match:
        return None
    year, month, day, hour, minute, second = match.groups()
    try:
        return datetime(
            int(year), int(month), int(day), int(hour), int(minute), int(second or 0)
        )
    except ValueError:
        return None


def parse_subseconds(text: str | None) -> float:
    """Parse ``SubsecTime*`` (``"450"`` -> ``0.45`` s)."""
    if not text:
        return 0.0
    text = str(text).strip()
    if not text:
        return 0.0
    if "/" in text:  # some writers store a rational
        try:
            num, den = text.split("/", 1)
            den_f = float(den)
            return float(num) / den_f if den_f else 0.0
        except ValueError:
            return 0.0
    digits = text[:6]
    try:
        return float("0." + digits)
    except ValueError:
        return 0.0


def parse_utc_offset(text: str | None) -> int | None:
    """Parse ``"+09:00"`` / ``"-0330"`` into a UTC offset in seconds."""
    if not text:
        return None
    text = str(text).strip()
    if text.upper() in {"UTC", "Z", "GMT"}:
        return 0
    match = _OFFSET_RE.match(text)
    if not match:
        return None
    sign, hours, minutes = match.groups()
    total = (int(hours) * 3600 + int(minutes or 0) * 60) * (1 if sign == "+" else -1)
    if abs(total) > 14 * 3600:
        return None
    return total


def offset_from_longitude(longitude: float) -> int:
    """Nautical-theory UTC offset for a longitude, in seconds."""
    hours = int(round(longitude / 15.0))
    return max(-12, min(14, hours)) * 3600


def format_offset(offset_seconds: int | None) -> str:
    if offset_seconds is None:
        return "unknown"
    sign = "+" if offset_seconds >= 0 else "-"
    value = abs(offset_seconds)
    return f"{sign}{value // 3600:02d}:{(value % 3600) // 60:02d}"


def resolve_epoch(
    wall_time: datetime | None,
    recorded_offset: int | None,
    mode: str,
    assumed_offset: int,
    fallback_mtime: float | None = None,
) -> tuple[float | None, str]:
    """Turn a wall-clock EXIF stamp into epoch seconds.

    Returns the timestamp (or ``None``) plus a short human readable note about
    which assumption was used.
    """
    if wall_time is None:
        if fallback_mtime is None:
            return None, "no timestamp"
        return float(fallback_mtime), "file mtime"

    if mode == TZ_MODE_AS_WRITTEN:
        # Everything is treated as plain wall clock, so a per-photo offset must
        # not be applied or two cameras in different zones drift apart.
        offset = 0
        note = "wall clock (time-zone offsets ignored)"
    elif recorded_offset is not None:
        offset = recorded_offset
        note = "EXIF offset"
    else:
        offset = assumed_offset
        note = f"assumed offset {format_offset(offset)} ({mode})"

    try:
        stamp = wall_time.replace(tzinfo=timezone(timedelta(seconds=offset))).timestamp()
    except (OverflowError, OSError, ValueError):
        return None, "unrepresentable timestamp"
    return stamp, note
