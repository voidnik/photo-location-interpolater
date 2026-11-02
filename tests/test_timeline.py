"""Timeline construction: timezone assumptions and ordering."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from pli.models import GpsFix, PhotoInfo
from pli.timeline import build_timeline
from pli.times import offset_from_longitude


def info(name, wall, offset=None, fix=None):
    return PhotoInfo(
        path=Path(f"/photos/{name}"),
        wall_time=wall,
        offset_seconds=offset,
        fix=fix,
        time_source="DateTimeOriginal",
        mtime=1_700_000_000.0,
    )


def test_photos_are_sorted_by_time():
    early = info("early.jpg", datetime(2024, 5, 1, 9, 0, 0))
    late = info("late.jpg", datetime(2024, 5, 1, 17, 0, 0))
    ordered = build_timeline([late, early], tz_mode="utc")
    assert [photo.path.name for photo in ordered] == ["early.jpg", "late.jpg"]


def test_recorded_offsets_win_across_a_timezone_change():
    tokyo = info("tokyo.jpg", datetime(2024, 5, 1, 9, 0), offset=9 * 3600)
    london = info("london.jpg", datetime(2024, 5, 1, 8, 0), offset=0)
    # 09:00+09:00 is midnight UTC, three hours *before* 08:00+00:00 even though
    # the camera dial reads a later time -- the whole point of honouring offsets.
    ordered = build_timeline([london, tokyo], tz_mode="utc")
    assert [photo.path.name for photo in ordered] == ["tokyo.jpg", "london.jpg"]


def test_auto_mode_borrows_the_offset_of_the_nearest_located_photo():
    anchor = info("fix.jpg", datetime(2024, 5, 1, 12, 0), fix=GpsFix(35.68, 139.69))
    naive = info("naive.jpg", datetime(2024, 5, 1, 12, 30))
    ordered = build_timeline([anchor, naive], tz_mode="auto")
    assert naive.tz_note.startswith("assumed offset +09:00")
    assert naive.timestamp - anchor.timestamp == pytest.approx(30 * 60)


def test_utc_mode_assumes_greenwich_when_nothing_is_recorded():
    naive = info("naive.jpg", datetime(2024, 5, 1, 12, 0))
    build_timeline([naive], tz_mode="utc")
    assert naive.tz_note.startswith("assumed offset +00:00")


def test_as_written_ignores_every_offset():
    with_offset = info("a.jpg", datetime(2024, 5, 1, 12, 0), offset=9 * 3600)
    without = info("b.jpg", datetime(2024, 5, 1, 12, 0))
    build_timeline([with_offset, without], tz_mode="as-written")
    assert with_offset.timestamp == without.timestamp


def test_a_fixed_offset_can_be_forced():
    naive = info("naive.jpg", datetime(2024, 5, 1, 12, 0))
    build_timeline([naive], tz_mode="+05:30")
    assert naive.timestamp == datetime(2024, 5, 1, 6, 30, tzinfo=timezone.utc).timestamp()


def test_invalid_timezone_mode_is_rejected():
    with pytest.raises(ValueError):
        build_timeline([info("a.jpg", datetime(2024, 5, 1))], tz_mode="Mars/Time")


def test_photos_without_any_time_are_dropped_unless_mtime_is_allowed():
    no_time = PhotoInfo(path="/photos/x.jpg", wall_time=None, mtime=1_600_000_000.0)
    assert build_timeline([no_time], tz_mode="utc") == []
    ordered = build_timeline([no_time], tz_mode="utc", use_mtime_fallback=True)
    assert ordered[0].timestamp == 1_600_000_000.0


def test_offset_from_longitude_is_used_for_the_assumption():
    assert offset_from_longitude(139.69) == 9 * 3600
