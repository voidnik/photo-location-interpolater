"""The interpolation engine: behaviour of the speed model."""

from __future__ import annotations

import math

import pytest

from pli.geo import EARTH_RADIUS_M, haversine_m
from pli.interp import (
    METHOD_LINEAR,
    METHOD_SPLINE,
    METHOD_STATIONARY,
    InterpolationOptions,
    SpeedModel,
    interpolate,
)
from pli.models import GpsFix

START = 1_700_000_000.0


def north(metres: float) -> GpsFix:
    """A fix ``metres`` north of the equator origin (exact for ``haversine_m``)."""
    return GpsFix(latitude=math.degrees(metres / EARTH_RADIUS_M), longitude=0.0)


def timeline(specs, make_info):
    """Build photos from ``(offset_seconds, metres_north_or_None)`` tuples."""
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


def test_constant_speed_matches_time_linear(make_info):
    photos = timeline([(0, 0.0), (50, None), (100, 100.0)], make_info)
    linear = interpolate(photos, InterpolationOptions(method=METHOD_LINEAR))
    speed = interpolate(photos, InterpolationOptions(method="speed"))
    assert linear.estimates[1].latitude == pytest.approx(speed.estimates[1].latitude, abs=1e-12)
    assert speed.estimates[1].fraction == pytest.approx(0.5, abs=1e-9)


def test_photo_taken_at_an_anchor_gets_the_anchor_position(make_info):
    photos = timeline([(0, 0.0), (0.001, None), (120, 200.0)], make_info)
    result = interpolate(photos, InterpolationOptions(method="speed"))
    first = result.estimates[1]
    assert haversine_m(first.latitude, first.longitude, 0.0, 0.0) < 0.01


def test_slowing_down_pulls_photos_towards_the_far_anchor(make_info):
    """Fast approach then a crawl: the photographer had already arrived."""
    photos = timeline([(0, 0.0), (950, None), (1000, 1000.0), (2000, 1010.0)], make_info)
    linear = interpolate(photos, InterpolationOptions(method=METHOD_LINEAR))
    speed = interpolate(photos, InterpolationOptions(method="speed"))
    far = photos[2].fix
    linear_gap = haversine_m(
        linear.estimates[1].latitude, linear.estimates[1].longitude, far.latitude, far.longitude
    )
    speed_gap = haversine_m(
        speed.estimates[1].latitude, speed.estimates[1].longitude, far.latitude, far.longitude
    )
    assert speed.estimates[1].fraction > linear.estimates[1].fraction
    assert speed_gap < linear_gap


def test_positions_never_backtrack(make_info):
    photos = timeline(
        [
            (0, 0.0),
            (200, None),
            (300, 300.0),
            (450, None),
            (600, 600.0),
            (800, None),
            (1000, 620.0),
        ],
        make_info,
    )
    result = interpolate(photos, InterpolationOptions(method="speed"))
    latitudes = [result.estimates[index].latitude for index in sorted(result.estimates)]
    assert latitudes == sorted(latitudes)


def test_near_identical_anchors_are_treated_as_standing_still(make_info):
    photos = timeline([(0, 0.0), (60, None), (120, 5.0)], make_info)
    result = interpolate(photos, InterpolationOptions(method="speed", stationary_meters=12.0))
    estimate = result.estimates[1]
    assert estimate.method == METHOD_STATIONARY
    assert estimate.speed_mps == 0.0
    assert estimate.fraction in (0.0, 1.0)
    assert haversine_m(estimate.latitude, estimate.longitude, 0.0, 0.0) < 6.0


def test_gap_wider_than_the_limit_is_skipped(make_info):
    photos = timeline([(0, 0.0), (60, None), (7200, 100.0)], make_info)
    result = interpolate(photos, InterpolationOptions(max_gap_seconds=3600.0))
    assert 1 not in result.estimates
    assert "exceeds limit" in result.skipped[1]


def test_anchor_distance_wider_than_the_limit_is_skipped(make_info):
    photos = timeline([(0, 0.0), (60, None), (120, 100_000.0)], make_info)
    result = interpolate(photos, InterpolationOptions(max_gap_distance_m=50_000.0))
    assert "exceeds limit" in result.skipped[1]


def test_reported_speed_stays_consistent_with_the_distance(make_info):
    photos = timeline([(0, 0.0), (50, None), (100, 100.0)], make_info)
    result = interpolate(photos, InterpolationOptions())
    estimate = result.estimates[1]
    assert estimate.speed_mps == pytest.approx(1.0, rel=1e-6)


def test_measured_gps_speed_overrides_the_estimate(make_info):
    photos = timeline([(0, 0.0), (100, 100.0)], make_info)
    photos[0].fix.speed_mps = 4.0
    model = SpeedModel(
        [photo.timestamp for photo in photos],
        [photo.fix for photo in photos],
        InterpolationOptions(),
    )
    assert model.node_speeds[0] == pytest.approx(4.0)
    assert model.node_speeds[1] == pytest.approx(1.0)


def test_spline_passes_between_the_located_fixes(make_info):
    photos = timeline([(0, 0.0), (60, 100.0), (90, None), (120, 250.0), (180, 300.0)], make_info)
    result = interpolate(photos, InterpolationOptions(method=METHOD_SPLINE))
    estimate = result.estimates[2]
    assert estimate.method == METHOD_SPLINE
    assert photos[1].fix.latitude < estimate.latitude < photos[3].fix.latitude
    assert 0.0 <= estimate.fraction <= 1.0
