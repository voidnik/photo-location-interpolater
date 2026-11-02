"""Edge cases: no fixes, track ends, datelines, missing altitudes."""

from __future__ import annotations

import math

import pytest

from pli.geo import EARTH_RADIUS_M
from pli.interp import (
    METHOD_EXTRAPOLATE,
    METHOD_LINEAR,
    InterpolationOptions,
    interpolate,
)
from pli.models import GpsFix

START = 1_700_000_000.0


def north(metres: float) -> GpsFix:
    return GpsFix(latitude=math.degrees(metres / EARTH_RADIUS_M), longitude=0.0)


def timeline(specs, make_info):
    photos = [
        make_info(
            f"{index:02d}.jpg",
            START + offset,
            None if metres is None else north(metres),
        )
        for index, (offset, metres) in enumerate(specs)
    ]
    photos.sort(key=lambda photo: photo.sortable_key)
    return photos



def test_photos_outside_the_track_need_extrapolation(make_info):
    photos = timeline([(-60, None), (0, 0.0), (100, 100.0), (200, None)], make_info)
    result = interpolate(photos, InterpolationOptions())
    assert "extrapolate" in result.skipped[0]
    assert "extrapolate" in result.skipped[3]


def test_extrapolation_walks_on_along_the_track(make_info):
    photos = timeline([(0, 0.0), (100, 100.0), (200, None)], make_info)
    result = interpolate(
        photos, InterpolationOptions(extrapolate=True, extrapolation_tau=1000.0)
    )
    estimate = result.estimates[2]
    assert estimate.method == METHOD_EXTRAPOLATE
    assert estimate.latitude > photos[1].fix.latitude
    assert estimate.anchor_distance_m <= 1_500.0


def test_extrapolation_decays_instead_of_running_forever(make_info):
    photos = timeline([(0, 0.0), (100, 100.0), (3000, None)], make_info)
    near = timeline([(0, 0.0), (100, 100.0), (200, None)], make_info)
    options = InterpolationOptions(extrapolate=True, max_extrapolation_seconds=4000.0)
    far = interpolate(photos, options).estimates[2]
    close = interpolate(near, options).estimates[2]
    assert far.anchor_distance_m >= close.anchor_distance_m
    assert far.anchor_distance_m <= 1_500.0  # capped


def test_extrapolation_gives_up_beyond_the_time_limit(make_info):
    photos = timeline([(0, 0.0), (100, 100.0), (5000, None)], make_info)
    result = interpolate(
        photos, InterpolationOptions(extrapolate=True, max_extrapolation_seconds=900.0)
    )
    assert 2 not in result.estimates
    assert "exceeds limit" in result.skipped[2]


def test_single_located_photo_can_be_extended_from_its_own_speed(make_info):
    photos = timeline([(0, 0.0), (60, None)], make_info)
    photos[0].fix.speed_mps = 1.5
    photos[0].fix.track_deg = 0.0
    result = interpolate(photos, InterpolationOptions(extrapolate=True))
    assert result.estimates[1].method == METHOD_EXTRAPOLATE
    assert result.estimates[1].latitude > photos[0].fix.latitude


def test_single_located_photo_without_speed_is_not_guessed(make_info):
    photos = timeline([(0, 0.0), (60, None)], make_info)
    result = interpolate(photos, InterpolationOptions(extrapolate=True))
    assert "no speed or direction" in result.skipped[1]


def test_without_any_fix_everything_is_skipped(make_info):
    photos = timeline([(0, None), (10, None)], make_info)
    result = interpolate(photos)
    assert not result.estimates
    assert all("no located photo" in reason for reason in result.skipped.values())


def test_photo_without_timestamp_is_skipped(make_info):
    photos = timeline([(0, 0.0), (60, None), (120, 100.0)], make_info)
    photos.append(make_info("broken.jpg", 0, None))
    photos[-1].timestamp = None
    result = interpolate(photos)
    assert "no usable timestamp" in result.skipped[3]


def test_altitude_is_blended_only_when_an_anchor_has_one(make_info):
    photos = timeline([(0, 0.0), (60, None), (120, 100.0)], make_info)
    photos[0].fix.altitude = 10.0
    photos[2].fix.altitude = 30.0
    result = interpolate(photos, InterpolationOptions(method=METHOD_LINEAR))
    assert result.estimates[1].altitude == pytest.approx(20.0)
    photos[2].fix.altitude = None
    result = interpolate(photos, InterpolationOptions(method=METHOD_LINEAR))
    assert result.estimates[1].altitude == pytest.approx(10.0)
    photos[0].fix.altitude = None
    result = interpolate(photos, InterpolationOptions(method=METHOD_LINEAR))
    assert result.estimates[1].altitude is None


def test_interpolation_across_the_antimeridian(make_info):
    photos = [
        make_info("a.jpg", START, GpsFix(0.0, 179.99)),
        make_info("b.jpg", START + 60.0, None),
        make_info("c.jpg", START + 120.0, GpsFix(0.0, -179.99)),
    ]
    result = interpolate(photos, InterpolationOptions(method=METHOD_LINEAR))
    longitude = result.estimates[1].longitude
    assert longitude > 179.9 or longitude < -179.9  # never pulled round to 0


def test_direction_is_reported_in_the_written_direction(make_info):
    photos = timeline([(0, 0.0), (60, None), (120, 200.0)], make_info)
    result = interpolate(photos, InterpolationOptions(method=METHOD_LINEAR))
    assert result.estimates[1].track_deg == pytest.approx(0.0, abs=1e-6)
    assert result.estimates[1].uncertainty_m > 0.0
