"""Geometry helpers: distances, bearings, monotone splines."""

from __future__ import annotations

import math

import pytest

from pli.geo import (
    EARTH_RADIUS_M,
    LocalPlane,
    bearing_deg,
    hermite_integral,
    hermite_value,
    haversine_m,
    interpolate_bearing,
    monotone_slopes,
    wrap_longitude,
)


def test_haversine_matches_one_degree_of_latitude():
    expected = math.radians(1.0) * EARTH_RADIUS_M
    assert haversine_m(0.0, 0.0, 1.0, 0.0) == pytest.approx(expected, rel=1e-9)


def test_haversine_is_symmetric_and_zero_on_itself():
    assert haversine_m(37.5, 127.0, 37.5, 127.0) == pytest.approx(0.0)
    assert haversine_m(10.0, 20.0, 12.0, 25.0) == pytest.approx(
        haversine_m(12.0, 25.0, 10.0, 20.0)
    )


def test_wrap_longitude_crosses_the_antimeridian():
    assert wrap_longitude(181.0) == pytest.approx(-179.0)
    assert wrap_longitude(-181.0) == pytest.approx(179.0)
    assert wrap_longitude(360.0) == pytest.approx(0.0)


def test_plane_roundtrip_is_stable():
    plane = LocalPlane(37.5665, 126.978)
    east, north = plane.forward(37.6, 127.1)
    assert plane.inverse(east, north) == pytest.approx((37.6, 127.1), abs=1e-9)


def test_plane_measures_east_west_in_metres_at_the_reference_latitude():
    plane = LocalPlane(0.0, 0.0)
    east, _ = plane.forward(0.0, 1.0)
    assert east == pytest.approx(EARTH_RADIUS_M * math.radians(1.0), rel=1e-9)
    plane_60 = LocalPlane(60.0, 0.0)
    east_60, _ = plane_60.forward(60.0, 1.0)
    assert east_60 == pytest.approx(east * math.cos(math.radians(60.0)), rel=1e-9)


def test_plane_handles_antimeridian_neighbourhood():
    plane = LocalPlane(0.0, 179.9)
    east, _ = plane.forward(0.0, -179.9)
    assert east == pytest.approx(EARTH_RADIUS_M * math.radians(0.2), rel=1e-9)


def test_plane_mix_stays_near_the_dateline():
    plane = LocalPlane(0.0, 179.9)
    latitude, longitude = plane.mix((0.0, 179.9), (0.0, -179.9), 0.5)
    assert latitude == pytest.approx(0.0, abs=1e-9)
    # The midpoint of 179.9 and -179.9 is the dateline itself, which the local
    # plane reports as -180.0 (the same meridian as +180.0).
    assert abs(longitude) == pytest.approx(180.0, abs=1e-6)


def test_cardinal_bearings():
    assert bearing_deg(0.0, 0.0, 1.0, 0.0) == pytest.approx(0.0, abs=1e-9)
    assert bearing_deg(0.0, 0.0, 0.0, 1.0) == pytest.approx(90.0, abs=1e-6)
    assert bearing_deg(0.0, 0.0, -1.0, 0.0) == pytest.approx(180.0, abs=1e-9)
    assert bearing_deg(0.0, 0.0, 0.0, -1.0) == pytest.approx(270.0, abs=1e-6)


def test_interpolate_bearing_takes_the_short_way_round():
    assert interpolate_bearing(350.0, 10.0, 0.5) == pytest.approx(0.0)
    assert interpolate_bearing(10.0, 350.0, 0.5) == pytest.approx(0.0)


def test_monotone_slopes_never_overshoot_a_flat_segment():
    slopes = monotone_slopes([0.0, 1.0, 2.0, 3.0], [0.0, 1.0, 1.0, 2.0])
    assert slopes[0] == pytest.approx(1.0)
    assert slopes[2] == pytest.approx(0.0)  # flat on both sides -> derivative 0
    assert slopes[-1] == pytest.approx(1.0)


def test_monotone_slopes_handles_degenerate_input():
    assert monotone_slopes([], []) == []
    assert monotone_slopes([1.0], [5.0]) == [0.0]
    assert monotone_slopes([0.0, 1.0], [3.0, 3.0]) == [0.0, 0.0]


def test_hermite_integral_matches_numeric_integration():
    x0, x1 = 100.0, 400.0
    y0, y1, d0, d1 = 1.2, 0.3, -0.004, 0.002
    analytic = hermite_integral(x0, x1, y0, y1, d0, d1, x1)
    steps = 2000
    step = (x1 - x0) / steps
    numeric = 0.0
    for index in range(steps):
        left = x0 + index * step
        mid = left + step / 2.0
        numeric += step * hermite_value(x0, x1, y0, y1, d0, d1, mid)
    assert analytic == pytest.approx(numeric, rel=1e-6)


def test_hermite_integral_is_monotone_for_positive_values():
    previous = 0.0
    for moment in range(0, 101, 5):
        value = hermite_integral(0.0, 100.0, 1.0, 1.0, -0.01, 0.01, float(moment))
        assert value >= previous
        previous = value
