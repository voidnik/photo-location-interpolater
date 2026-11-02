"""Lightweight geodesy and monotone-spline helpers.

The interpolator never works on raw latitude/longitude pairs: every segment is
projected into a local tangent plane (equirectangular approximation around a
reference point) where plain vector arithmetic is safe and cheap.  The plane
also takes care of the anti-meridian, because longitude differences are wrapped
into ``[-180, 180)``.

All distances are meters, all angles degrees unless noted otherwise.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

EARTH_RADIUS_M = 6371008.8
"""Mean Earth radius (IUGG) used for every distance computation."""

_MAX_ABS_LAT = 89.5


def wrap_longitude(lon: float) -> float:
    """Normalize a longitude into ``[-180, 180)``."""
    return (lon + 180.0) % 360.0 - 180.0


def wrap_degrees(angle: float) -> float:
    """Normalize an angle into ``[0, 360)``."""
    return angle % 360.0


def clamp(value: float, low: float, high: float) -> float:
    return low if value < low else (high if value > high else value)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points, in meters."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = phi2 - phi1
    dlam = math.radians(wrap_longitude(lon2 - lon1))
    a = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2.0) ** 2
    )
    return 2.0 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing from point 1 to point 2 (0 = north)."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dlam = math.radians(wrap_longitude(lon2 - lon1))
    y = math.sin(dlam) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlam)
    if abs(x) < 1e-15 and abs(y) < 1e-15:
        return 0.0
    return wrap_degrees(math.degrees(math.atan2(y, x)))


def interpolate_bearing(b1: float, b2: float, fraction: float) -> float:
    """Interpolate two compass bearings along the short way around the circle."""
    delta = wrap_degrees(b2 - b1 + 180.0) - 180.0
    return wrap_degrees(b1 + delta * fraction)


@dataclass(frozen=True)
class LocalPlane:
    """Equirectangular tangent plane anchored at ``(lat0, lon0)``.

    Accurate enough for interpolation between photographic fixes (errors stay
    far below the interpolation uncertainty for segments of a few hundred
    kilometres) and exact at the reference point.
    """

    lat0: float
    lon0: float

    def __post_init__(self) -> None:
        # Guard against a zero east-west scale factor at the poles.
        object.__setattr__(self, "lat0", clamp(self.lat0, -_MAX_ABS_LAT, _MAX_ABS_LAT))
        object.__setattr__(self, "lon0", wrap_longitude(self.lon0))

    @property
    def _east_scale(self) -> float:
        return EARTH_RADIUS_M * math.cos(math.radians(self.lat0))

    def forward(self, lat: float, lon: float) -> tuple[float, float]:
        """Project geographic coordinates onto the plane as ``(east, north)``."""
        east = math.radians(wrap_longitude(lon - self.lon0)) * self._east_scale
        north = math.radians(lat - self.lat0) * EARTH_RADIUS_M
        return east, north

    def inverse(self, east: float, north: float) -> tuple[float, float]:
        """Map plane coordinates back to ``(latitude, longitude)``."""
        lat = clamp(self.lat0 + math.degrees(north / EARTH_RADIUS_M), -90.0, 90.0)
        lon = wrap_longitude(self.lon0 + math.degrees(east / self._east_scale))
        return lat, lon

    def mix(
        self,
        point_a: tuple[float, float],
        point_b: tuple[float, float],
        fraction: float,
    ) -> tuple[float, float]:
        """Linear blend of two geographic points inside the plane."""
        ax, ay = self.forward(*point_a)
        bx, by = self.forward(*point_b)
        return self.inverse(ax + (bx - ax) * fraction, ay + (by - ay) * fraction)

    def bearing(
        self,
        point_a: tuple[float, float],
        point_b: tuple[float, float],
    ) -> float:
        """Plane-space bearing between two geographic points."""
        ax, ay = self.forward(*point_a)
        bx, by = self.forward(*point_b)
        dx = bx - ax
        dy = by - ay
        if abs(dx) < 1e-12 and abs(dy) < 1e-12:
            return 0.0
        return wrap_degrees(math.degrees(math.atan2(dx, dy)))


def monotone_slopes(xs: list[float], ys: list[float]) -> list[float]:
    """Fritsch-Carlson derivatives that keep the cubic interpolant monotone.

    Used twice: once to read node speeds off a cumulative arc-length curve, and
    once to smooth the speed curve itself without introducing oscillation.
    """
    n = len(xs)
    if n == 0:
        return []
    if n == 1:
        return [0.0]
    dx = [xs[i + 1] - xs[i] for i in range(n - 1)]
    slope = [
        (ys[i + 1] - ys[i]) / dx[i] if dx[i] > 1e-9 else 0.0 for i in range(n - 1)
    ]
    out = [0.0] * n
    out[0] = slope[0]
    out[-1] = slope[-1]
    for i in range(1, n - 1):
        prev, nxt = slope[i - 1], slope[i]
        if prev * nxt <= 0.0:
            out[i] = 0.0
            continue
        w1 = 2.0 * dx[i] + dx[i - 1]
        w2 = dx[i] + 2.0 * dx[i - 1]
        out[i] = (w1 + w2) / (w1 / prev + w2 / nxt)
    return out


def hermite_value(
    x0: float, x1: float, y0: float, y1: float, d0: float, d1: float, x: float
) -> float:
    """Cubic Hermite interpolant over ``[x0, x1]`` evaluated at ``x``."""
    span = x1 - x0
    if span <= 0.0:
        return y0
    u = clamp((x - x0) / span, 0.0, 1.0)
    u2 = u * u
    u3 = u2 * u
    return (
        (2.0 * u3 - 3.0 * u2 + 1.0) * y0
        + (u3 - 2.0 * u2 + u) * span * d0
        + (-2.0 * u3 + 3.0 * u2) * y1
        + (u3 - u2) * span * d1
    )


def hermite_integral(
    x0: float, x1: float, y0: float, y1: float, d0: float, d1: float, x: float
) -> float:
    """Definite integral of :func:`hermite_value` from ``x0`` to ``x``.

    Integrated analytically so that the speed-aware reparametrisation stays
    exact and allocation free.
    """
    span = x1 - x0
    if span <= 0.0:
        return 0.0
    u = clamp((x - x0) / span, 0.0, 1.0)
    u2 = u * u
    u3 = u2 * u
    u4 = u3 * u
    return span * (
        (0.5 * u4 - u3 + u) * y0
        + (0.25 * u4 - (2.0 / 3.0) * u3 + 0.5 * u2) * span * d0
        + (-0.5 * u4 + u3) * y1
        + (0.25 * u4 - u3 / 3.0) * span * d1
    )

