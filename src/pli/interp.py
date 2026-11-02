"""Speed-aware position reconstruction over a shooting timeline.

Naive GPS interpolation places a photo of unknown position at the *time*
midpoint between the two located photos around it.  That is wrong whenever the
photographer was not moving at a constant pace: pictures taken while standing
still cluster in time, and a time midpoint then drags them into the middle of
the previous walk.

The model used here instead predicts the **speed** along the located track and
converts the requested time into a travelled distance:

1. fix-to-fix segment speeds ``L_i / dt_i`` are turned into per-node speeds with
   Fritsch-Carlson weights, so the node speed never overshoots its neighbours;
2. speeds measured by the camera (EXIF ``GPSSpeed``) override the estimates;
3. the node speeds are smoothed into a cubic Hermite curve;
4. the distance covered inside a segment is the *analytic integral* of that
   curve, normalised to the segment length, which yields the position fraction.

The result keeps both anchors exact, never backtracks, and lets photos bunch up
where the track was slow.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from typing import Sequence

from .geo import (
    LocalPlane,
    clamp,
    hermite_integral,
    hermite_value,
    haversine_m,
    monotone_slopes,
    wrap_degrees,
)
from .models import Estimate, GpsFix, PhotoInfo

METHOD_LINEAR = "linear"
METHOD_SPEED = "speed"
METHOD_SPLINE = "spline"
METHOD_STATIONARY = "stationary"
METHOD_EXTRAPOLATE = "extrapolate"
INTERPOLATION_METHODS = (METHOD_LINEAR, METHOD_SPEED, METHOD_SPLINE)

_EPS = 1e-9


@dataclass(slots=True)
class InterpolationOptions:
    """Tunables for :func:`interpolate`."""

    method: str = METHOD_SPEED
    max_gap_seconds: float = 21_600.0
    max_gap_distance_m: float = 50_000.0
    stationary_meters: float = 12.0
    extrapolate: bool = False
    max_extrapolation_seconds: float = 900.0
    max_extrapolation_meters: float = 1_500.0
    extrapolation_tau: float = 240.0
    gps_noise_meters: float = 12.0
    path_deviation_ratio: float = 0.30
    use_exif_speed: bool = True

    def uncertainty(self, anchor_distance_m: float, travelled_m: float = 0.0) -> float:
        """Heuristic 1-sigma radius of a reconstructed position, meters."""
        return math.hypot(
            self.gps_noise_meters,
            self.path_deviation_ratio * (anchor_distance_m + travelled_m),
        )


class SpeedModel:
    """Speed profile over the located fixes."""

    def __init__(
        self, times: Sequence[float], fixes: Sequence[GpsFix], options: InterpolationOptions
    ) -> None:
        if len(times) != len(fixes) or len(times) < 2:
            raise ValueError("a speed model needs at least two located fixes")
        self.times = list(times)
        self.options = options
        self.points: list[tuple[float, float]] = [(f.latitude, f.longitude) for f in fixes]
        self.lengths = [
            haversine_m(*self.points[i], *self.points[i + 1])
            for i in range(len(self.points) - 1)
        ]
        self.durations = [
            max(self.times[i + 1] - self.times[i], _EPS)
            for i in range(len(self.times) - 1)
        ]
        cumulative = [0.0]
        for length in self.lengths:
            cumulative.append(cumulative[-1] + length)

        estimated = monotone_slopes(self.times, cumulative)
        if options.use_exif_speed:
            for index, fix in enumerate(fixes):
                if fix.speed_mps is not None and fix.speed_mps >= 0.0:
                    estimated[index] = fix.speed_mps
        self.node_speeds = [max(value, 0.0) for value in estimated]
        self.node_derivs = monotone_slopes(self.times, self.node_speeds)
        self._integrals = [self._raw_integral(index, self.times[index + 1])
                           for index in range(len(self.lengths))]

    # -- segment helpers -------------------------------------------------
    def mean_speed(self, segment: int) -> float:
        return self.lengths[segment] / self.durations[segment]

    def _raw_integral(self, segment: int, moment: float) -> float:
        return hermite_integral(
            self.times[segment],
            self.times[segment + 1],
            self.node_speeds[segment],
            self.node_speeds[segment + 1],
            self.node_derivs[segment],
            self.node_derivs[segment + 1],
            moment,
        )

    def _speed_curve(self, segment: int, moment: float) -> float:
        return max(
            0.0,
            hermite_value(
                self.times[segment],
                self.times[segment + 1],
                self.node_speeds[segment],
                self.node_speeds[segment + 1],
                self.node_derivs[segment],
                self.node_derivs[segment + 1],
                moment,
            ),
        )

    def fraction_at(self, segment: int, moment: float) -> float:
        """Travelled fraction of ``segment`` at ``moment`` (0..1)."""
        total = self._integrals[segment]
        if total <= _EPS:
            span = self.durations[segment]
            return clamp((moment - self.times[segment]) / span, 0.0, 1.0)
        return clamp(self._raw_integral(segment, moment) / total, 0.0, 1.0)

    def speed_at(self, segment: int, moment: float) -> float:
        """Ground speed (m/s) at ``moment`` inside ``segment``.

        Scaling the smoothed curve by ``length / integral`` makes the implied
        distance over the segment exactly the observed one, so the speed that
        ends up in EXIF stays consistent with the position that was written.
        """
        total = self._integrals[segment]
        if total <= _EPS:
            return self.mean_speed(segment)
        return self.lengths[segment] * self._speed_curve(segment, moment) / total


class SplineTrack:
    """Monotone cubic (PCHIP) through every located fix, in a local plane."""

    def __init__(self, times: Sequence[float], fixes: Sequence[GpsFix]) -> None:
        self.times = list(times)
        self.plane = LocalPlane(fixes[0].latitude, fixes[0].longitude)
        projected = [self.plane.forward(fix.latitude, fix.longitude) for fix in fixes]
        self.east = [point[0] for point in projected]
        self.north = [point[1] for point in projected]
        self.east_slope = monotone_slopes(self.times, self.east)
        self.north_slope = monotone_slopes(self.times, self.north)

    def _segment(self, moment: float) -> int:
        moment = clamp(moment, self.times[0], self.times[-1])
        index = bisect.bisect_right(self.times, moment) - 1
        return clamp(index, 0, len(self.times) - 2)

    def at(self, moment: float) -> tuple[float, float, float]:
        """Return ``(latitude, longitude, bearing_deg)`` at ``moment``."""
        moment = clamp(moment, self.times[0], self.times[-1])
        index = self._segment(moment)
        span = self.times[index + 1] - self.times[index]
        u = 0.0 if span <= _EPS else (moment - self.times[index]) / span
        u2 = u * u
        east = hermite_value(
            self.times[index], self.times[index + 1],
            self.east[index], self.east[index + 1],
            self.east_slope[index], self.east_slope[index + 1], moment,
        )
        north = hermite_value(
            self.times[index], self.times[index + 1],
            self.north[index], self.north[index + 1],
            self.north_slope[index], self.north_slope[index + 1], moment,
        )
        de = ((6 * u2 - 6 * u) * self.east[index]
              + (3 * u2 - 4 * u + 1) * span * self.east_slope[index]
              + (-6 * u2 + 6 * u) * self.east[index + 1]
              + (3 * u2 - 2 * u) * span * self.east_slope[index + 1])
        dn = ((6 * u2 - 6 * u) * self.north[index]
              + (3 * u2 - 4 * u + 1) * span * self.north_slope[index]
              + (-6 * u2 + 6 * u) * self.north[index + 1]
              + (3 * u2 - 2 * u) * span * self.north_slope[index + 1])
        if span > _EPS:
            de /= span
            dn /= span
        bearing = wrap_degrees(math.degrees(math.atan2(de, dn)))
        if abs(de) < 1e-12 and abs(dn) < 1e-12:
            bearing = 0.0
        return self.plane.inverse(east, north) + (bearing,)


def blend_altitude(before: GpsFix, after: GpsFix, fraction: float) -> float | None:
    if before.altitude is None and after.altitude is None:
        return None
    if before.altitude is None:
        return after.altitude
    if after.altitude is None:
        return before.altitude
    return before.altitude + (after.altitude - before.altitude) * fraction


@dataclass(slots=True)
class InterpolationResult:
    """Estimates and skip reasons keyed by the index into the photo list."""

    estimates: dict[int, Estimate]
    skipped: dict[int, str]
    located: int

    def estimate_for(self, index: int) -> Estimate | None:
        return self.estimates.get(index)

    def skip_note_for(self, index: int) -> str | None:
        """Why ``index`` did not get a position, if it did not."""
        return self.skipped.get(index)


def _advance(
    anchor: GpsFix, heading: float, metres: float, plane: LocalPlane
) -> tuple[float, float]:
    """Walk ``metres`` from ``anchor`` along ``heading`` inside ``plane``."""
    east, north = plane.forward(anchor.latitude, anchor.longitude)
    radians = math.radians(heading)
    return plane.inverse(
        east + metres * math.sin(radians), north + metres * math.cos(radians)
    )



def interpolate(
    photos: Sequence[PhotoInfo],
    options: InterpolationOptions | None = None,
) -> InterpolationResult:
    """Reconstruct a position for every timed photo that has no GPS block."""
    options = options or InterpolationOptions()
    estimates: dict[int, Estimate] = {}
    skipped: dict[int, str] = {}

    timed: list[int] = []
    for index, photo in enumerate(photos):
        if photo.timestamp is None:
            skipped[index] = photo.read_error or "no usable timestamp"
        else:
            timed.append(index)
    timed.sort(key=lambda index: photos[index].sortable_key)

    known = [index for index in timed if photos[index].has_location]
    result = InterpolationResult(estimates, skipped, len(known))
    if not known:
        for index in timed:
            skipped[index] = "no located photo in this set"
        return result

    stamps = [photos[index].timestamp for index in known]
    fixes = [photos[index].fix for index in known]
    model = SpeedModel(stamps, fixes, options) if len(known) >= 2 else None
    spline = (
        SplineTrack(stamps, fixes)
        if options.method == METHOD_SPLINE and len(known) >= 3
        else None
    )

    position_of = {index: position for position, index in enumerate(known)}
    leading: list[int] = []
    buckets: dict[int, list[int]] = {}
    last_known: int | None = None
    for index in timed:
        if photos[index].has_location:
            last_known = position_of[index]
            continue
        if last_known is None:
            leading.append(index)
        else:
            buckets.setdefault(last_known, []).append(index)

    trailing = buckets.pop(len(known) - 1, [])
    for segment, members in sorted(buckets.items()):
        _fill_segment(
            photos, model, spline, options, estimates, skipped, known, segment, members
        )

    if options.extrapolate:
        _fill_outer(photos, model, options, estimates, skipped, known, leading, "leading")
        _fill_outer(photos, model, options, estimates, skipped, known, trailing, "trailing")
    else:
        note = "outside the located part of the track (enable --extrapolate)"
        for index in leading + trailing:
            skipped[index] = note
    return result


def _project_fraction(plane: LocalPlane, pair, point: tuple[float, float]) -> float:
    """Progress of ``point`` along the straight line between two anchors."""
    ax, ay = plane.forward(*pair[0])
    bx, by = plane.forward(*pair[1])
    px, py = plane.forward(*point)
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        return 0.5
    return clamp(((px - ax) * dx + (py - ay) * dy) / (length * length), 0.0, 1.0)


def _fill_segment(
    photos, model, spline, options, estimates, skipped, known, segment, members
) -> None:
    """Fill one interior run of unlocated photos sitting between two anchors."""
    before_photo = photos[known[segment]]
    after_photo = photos[known[segment + 1]]
    before = before_photo.fix
    after = after_photo.fix
    gap = model.durations[segment]
    distance = model.lengths[segment]
    anchors = f"{before_photo.path.name} -> {after_photo.path.name}"

    if gap > options.max_gap_seconds:
        reason = (
            f"time gap {gap:.0f}s exceeds limit {options.max_gap_seconds:.0f}s ({anchors})"
        )
        for member in members:
            skipped[member] = reason
        return
    if distance > options.max_gap_distance_m:
        reason = (
            f"anchor distance {distance:.0f}m exceeds limit "
            f"{options.max_gap_distance_m:.0f}m ({anchors})"
        )
        for member in members:
            skipped[member] = reason
        return

    plane = LocalPlane(before.latitude, before.longitude)
    pair = ((before.latitude, before.longitude), (after.latitude, after.longitude))
    straight_track = plane.bearing(*pair)
    previous_fraction = 0.0

    for member in members:
        photo = photos[member]
        moment = photo.timestamp
        if gap <= 1e-6:
            fraction, speed, track, method = 0.5, 0.0, None, METHOD_STATIONARY
        elif distance <= options.stationary_meters:
            fraction = (
                0.0
                if abs(moment - before_photo.timestamp) <= abs(after_photo.timestamp - moment)
                else 1.0
            )
            speed, track, method = 0.0, None, METHOD_STATIONARY
        elif spline is not None:
            latitude, longitude, track = spline.at(moment)
            fraction = _project_fraction(plane, pair, (latitude, longitude))
            speed, method = model.mean_speed(segment), METHOD_SPLINE
        elif options.method == METHOD_SPEED:
            fraction = model.fraction_at(segment, moment)
            speed = model.speed_at(segment, moment)
            track, method = straight_track, METHOD_SPEED
        else:
            fraction = clamp((moment - before_photo.timestamp) / gap, 0.0, 1.0)
            speed, track, method = distance / gap, straight_track, METHOD_LINEAR

        if method != METHOD_STATIONARY:
            fraction = max(fraction, previous_fraction)
        previous_fraction = fraction

        latitude, longitude = plane.mix(*pair, fraction)
        estimates[member] = Estimate(
            latitude=latitude,
            longitude=longitude,
            altitude=blend_altitude(before, after, fraction),
            speed_mps=speed,
            track_deg=track,
            method=method,
            before=before_photo.path,
            after=after_photo.path,
            fraction=fraction,
            anchor_distance_m=distance,
            uncertainty_m=options.uncertainty(distance),
            note=f"{gap:.0f}s / {distance:.0f}m between anchors",
        )


def _segment_heading(model: SpeedModel, segment: int) -> float | None:
    """Travel direction of a track segment, or ``None`` when undefined."""
    if segment < 0 or segment >= len(model.lengths):
        return None
    point_a = model.points[segment]
    point_b = model.points[segment + 1]
    if model.lengths[segment] <= 1e-6:
        return None
    return LocalPlane(point_a[0], point_a[1]).bearing(point_a, point_b)


def _fill_outer(
    photos, model, options, estimates, skipped, known, members, side: str
) -> None:
    """Extrapolate photos taken before the first / after the last located fix."""
    if not members:
        return
    anchor_photo = photos[known[0] if side == "leading" else known[-1]]
    anchor = anchor_photo.fix
    if model is not None and len(model.lengths):
        segment = 0 if side == "leading" else len(model.lengths) - 1
        speed = model.node_speeds[0 if side == "leading" else -1]
        heading = _segment_heading(model, segment)
    else:
        speed = None
        heading = None
    if speed is None:
        speed = anchor.speed_mps
    if heading is None and anchor.track_deg is not None:
        heading = wrap_degrees(anchor.track_deg + (180.0 if side == "leading" else 0.0))
    elif heading is not None and side == "leading":
        heading = wrap_degrees(heading + 180.0)
    if speed is None or heading is None:
        for member in members:
            skipped[member] = f"cannot extrapolate {side}: no speed or direction available"
        return

    plane = LocalPlane(anchor.latitude, anchor.longitude)
    for member in members:
        photo = photos[member]
        delta = abs(anchor_photo.timestamp - photo.timestamp)
        if delta > options.max_extrapolation_seconds:
            skipped[member] = (
                f"{delta:.0f}s {side} the located track exceeds limit "
                f"{options.max_extrapolation_seconds:.0f}s"
            )
            continue
        travelled = _extrapolated_distance(speed, delta, options)
        latitude, longitude = _advance(anchor, heading, travelled, plane)
        residual = max(0.0, speed) * math.exp(-delta / max(options.extrapolation_tau, 1.0))
        estimates[member] = Estimate(
            latitude=latitude,
            longitude=longitude,
            altitude=anchor.altitude,
            speed_mps=residual,
            track_deg=heading if side != "leading" else wrap_degrees(heading + 180.0),
            method=METHOD_EXTRAPOLATE,
            before=None if side == "leading" else anchor_photo.path,
            after=anchor_photo.path if side == "leading" else None,
            fraction=0.0 if side == "leading" else 1.0,
            anchor_distance_m=travelled,
            uncertainty_m=options.uncertainty(2.0 * travelled),
            note=f"extrapolated {delta:.0f}s / {travelled:.0f}m {side} of anchor",
        )


def _extrapolated_distance(
    speed: float, delta: float, options: InterpolationOptions
) -> float:
    """Distance covered ``delta`` seconds away from the nearest anchor.

    The velocity at the anchor is extrapolated as it decays exponentially,
    which models the common "walked out of coverage and stopped again" case
    better than an open-ended straight line.
    """
    tau = max(options.extrapolation_tau, 1.0)
    return max(0.0, speed) * tau * (1.0 - math.exp(-delta / tau))



