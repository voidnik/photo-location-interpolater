"""EXIF timestamp parsing and timezone resolution."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from pli.times import (
    format_offset,
    offset_from_longitude,
    parse_exif_datetime,
    parse_subseconds,
    parse_utc_offset,
    resolve_epoch,
)


def test_parses_the_exif_datetime_layout():
    assert parse_exif_datetime("2024:05:01 13:45:12") == datetime(2024, 5, 1, 13, 45, 12)


def test_tolerates_iso_dashes_spaces_and_missing_seconds():
    assert parse_exif_datetime("2024-05-01 13:45:12") == datetime(2024, 5, 1, 13, 45, 12)
    assert parse_exif_datetime("2024:05:01T13:45") == datetime(2024, 5, 1, 13, 45)
    assert parse_exif_datetime("  2024:05:01 13:45:12  ") == datetime(2024, 5, 1, 13, 45, 12)


@pytest.mark.parametrize("garbage", [None, "", "not a date", "2024:13:01 00:00:00"])
def test_rejects_unusable_timestamps(garbage):
    assert parse_exif_datetime(garbage) is None


def test_subseconds_become_a_fraction_of_a_second():
    assert parse_subseconds("450") == pytest.approx(0.45)
    assert parse_subseconds("05") == pytest.approx(0.05)
    assert parse_subseconds("9/2") == pytest.approx(4.5)
    assert parse_subseconds(None) == 0.0
    assert parse_subseconds("junk") == 0.0


def test_utc_offset_parsing():
    assert parse_utc_offset("+09:00") == 9 * 3600
    assert parse_utc_offset("-0330") == -(3 * 3600 + 30 * 60)
    assert parse_utc_offset("+00:00") == 0
    assert parse_utc_offset("UTC") == 0
    assert parse_utc_offset("+25:00") is None
    assert parse_utc_offset(None) is None


def test_offset_from_longitude_uses_the_nautical_theory_zones():
    # Theory zones are 15 deg wide and ignore political borders, so Seoul
    # (126.98 deg E) falls in +08 even though the clock says +09.
    assert offset_from_longitude(126.98) == 8 * 3600
    assert offset_from_longitude(135.0) == 9 * 3600
    assert offset_from_longitude(-74.0) == -5 * 3600
    assert offset_from_longitude(0.0) == 0


def test_format_offset():
    assert format_offset(9 * 3600) == "+09:00"
    assert format_offset(-(5 * 3600 + 30 * 60)) == "-05:30"
    assert format_offset(None) == "unknown"


def test_resolve_epoch_prefers_the_recorded_offset():
    stamp, note = resolve_epoch(datetime(2024, 1, 1, 12, 0), 9 * 3600, "auto", 0)
    assert note == "EXIF offset"
    assert stamp == datetime(2024, 1, 1, 3, 0, tzinfo=timezone.utc).timestamp()


def test_resolve_epoch_assumes_the_suggested_offset_for_naive_stamps():
    stamp, note = resolve_epoch(datetime(2024, 1, 1, 12, 0), None, "auto", 9 * 3600)
    assert "assumed offset +09:00" in note
    assert stamp == datetime(2024, 1, 1, 3, 0, tzinfo=timezone.utc).timestamp()


def test_resolve_epoch_in_as_written_mode_ignores_offsets():
    aware, _ = resolve_epoch(datetime(2024, 1, 1, 12, 0), 9 * 3600, "as-written", 0)
    naive, _ = resolve_epoch(datetime(2024, 1, 1, 12, 0), None, "as-written", 0)
    assert aware == naive
    assert aware == datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc).timestamp()


def test_resolve_epoch_falls_back_to_the_file_mtime():
    stamp, note = resolve_epoch(None, None, "utc", 0, fallback_mtime=1_700_000_000.0)
    assert stamp == 1_700_000_000.0
    assert note == "file mtime"
    assert resolve_epoch(None, None, "utc", 0)[0] is None
