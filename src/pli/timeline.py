"""Turn raw EXIF stamps into one consistent, sorted timeline."""

from __future__ import annotations

import bisect
from datetime import timezone

from .models import PhotoInfo
from .times import (
    TZ_MODE_AS_WRITTEN,
    TZ_MODE_AUTO,
    TZ_MODES,
    offset_from_longitude,
    parse_utc_offset,
    resolve_epoch,
)


def _naive_seconds(wall_time) -> float:
    """Wall-clock value as a plain number, for offset-free comparisons."""
    return wall_time.replace(tzinfo=timezone.utc).timestamp()


class _AnchorIndex:
    """Located photos indexed by wall-clock time, used to guess UTC offsets."""

    def __init__(self, photos: list[PhotoInfo]) -> None:
        pairs = [
            (_naive_seconds(photo.wall_time), photo.fix.longitude)
            for photo in photos
            if photo.fix is not None and photo.wall_time is not None
        ]
        pairs.sort()
        self.moments = [moment for moment, _ in pairs]
        self.longitudes = [longitude for _, longitude in pairs]

    def offset_for(self, wall_time) -> int | None:
        if not self.moments:
            return None
        moment = _naive_seconds(wall_time)
        position = bisect.bisect_left(self.moments, moment)
        best = None
        best_gap = None
        for candidate in (position - 1, position):
            if 0 <= candidate < len(self.moments):
                gap = abs(self.moments[candidate] - moment)
                if best_gap is None or gap < best_gap:
                    best_gap = gap
                    best = candidate
        if best is None:
            return None
        return offset_from_longitude(self.longitudes[best])


def build_timeline(
    photos: list[PhotoInfo],
    tz_mode: str = TZ_MODE_AUTO,
    use_mtime_fallback: bool = False,
) -> list[PhotoInfo]:
    """Resolve epochs, drop unusable files and sort what is left by time.

    Photos that already carry an EXIF UTC offset always win: the assumed offset
    (``auto``: nearest located photo's longitude, a fixed ``+HH:MM``, or UTC) is
    only used for wall-clock stamps without one.
    """
    fixed_offset = None
    if tz_mode not in TZ_MODES:
        fixed_offset = parse_utc_offset(tz_mode)
        if fixed_offset is None:
            raise ValueError(f"invalid timezone mode: {tz_mode!r}")

    anchors = _AnchorIndex(photos)
    for photo in photos:
        if fixed_offset is not None:
            assumed = fixed_offset
        elif tz_mode == TZ_MODE_AUTO and photo.wall_time is not None:
            assumed = anchors.offset_for(photo.wall_time) or 0
        else:
            assumed = 0
        epoch, note = resolve_epoch(
            photo.wall_time,
            photo.offset_seconds,
            tz_mode,
            assumed,
            photo.mtime if use_mtime_fallback else None,
        )
        photo.timestamp = epoch
        photo.tz_note = note
        if photo.offset_seconds is None and tz_mode != TZ_MODE_AS_WRITTEN:
            photo.assumed_offset_seconds = assumed

    usable = [photo for photo in photos if photo.timestamp is not None]
    usable.sort(key=lambda photo: photo.sortable_key)
    return usable
