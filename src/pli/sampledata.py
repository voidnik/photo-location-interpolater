"""Synthetic photo sets for ``pli demo`` and for the test-suite.

The image payload is a placeholder: everything the tool reads and writes lives
in the EXIF block, so the demo files stay tiny while still being real JPEG
streams that ``piexif`` can round-trip.
"""

from __future__ import annotations

import io
import base64
from datetime import datetime, timedelta
from pathlib import Path

from .geo import haversine_m  # noqa: F401 - re-exported for demo tooling
from .models import GpsFix

# A real 8x8 JPEG (SOI, APP0, DQT, SOF0, DHT, SOS, scan, EOI).  ``piexif`` needs
# a complete JPEG segment chain, so a bare SOI+APP0 stub is not enough.  The
# payload is a flat colour: everything the tool reads and writes lives in EXIF.
JPEG_PLACEHOLDER = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAMCAgMCAgMDAwMEAwMEBQgFBQQEBQoHBwYIDAoMDAsK"
    "CwsNDhIQDQ4RDgsLEBYQERMUFRUVDA8XGBYUGBIUFRT/2wBDAQMEBAUEBQkFBQkUDQsNFBQUFBQU"
    "FBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBT/wAARCAAIAAgDASIA"
    "AhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQA"
    "AAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3"
    "ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWm"
    "p6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEA"
    "AwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSEx"
    "BhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElK"
    "U1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3"
    "uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD9E6KK"
    "K+fPTP/Z"
)



def write_photo(
    path: Path,
    taken_at: datetime,
    fix: GpsFix | None = None,
    utc_offset: str | None = None,
    make: str = "pli-demo",
) -> Path:
    """Create a JPEG whose EXIF block looks like a real camera wrote it."""
    import piexif

    exif: dict = {
        "0th": {piexif.ImageIFD.Make: make, piexif.ImageIFD.Software: "photo-location-interpolater"},
        "Exif": {
            piexif.ExifIFD.DateTimeOriginal: taken_at.strftime("%Y:%m:%d %H:%M:%S"),
            piexif.ExifIFD.SubSecTimeOriginal: f"{taken_at.microsecond // 1000:03d}",
        },
        "GPS": {},
        "1st": {},
        "Interop": {},
        "thumbnail": None,
    }
    if utc_offset:
        exif["Exif"][piexif.ExifIFD.OffsetTimeOriginal] = utc_offset
    if fix is not None:
        gps = exif["GPS"]
        gps[piexif.GPSIFD.GPSLatitudeRef] = "N" if fix.latitude >= 0 else "S"
        gps[piexif.GPSIFD.GPSLatitude] = _dms(abs(fix.latitude))
        gps[piexif.GPSIFD.GPSLongitudeRef] = "E" if fix.longitude >= 0 else "W"
        gps[piexif.GPSIFD.GPSLongitude] = _dms(abs(fix.longitude))
        if fix.altitude is not None:
            gps[piexif.GPSIFD.GPSAltitudeRef] = b"\x00" if fix.altitude >= 0 else b"\x01"
            gps[piexif.GPSIFD.GPSAltitude] = (int(abs(fix.altitude) * 100), 100)
        if fix.speed_mps is not None:
            gps[piexif.GPSIFD.GPSSpeedRef] = "K"
            gps[piexif.GPSIFD.GPSSpeed] = (int(fix.speed_mps * 3.6 * 100), 100)
        if fix.track_deg is not None:
            gps[piexif.GPSIFD.GPSTrackRef] = "T"
            gps[piexif.GPSIFD.GPSTrack] = (int(fix.track_deg * 100), 100)

    blob = piexif.dump(exif)
    buffer = io.BytesIO()
    piexif.insert(blob, JPEG_PLACEHOLDER, buffer)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(buffer.getvalue())
    return path


def _dms(degrees: float):
    degrees = abs(degrees)
    deg = int(degrees)
    minutes_full = (degrees - deg) * 60.0
    minutes = int(minutes_full)
    seconds = int(round((minutes_full - minutes) * 60.0 * 1_000_000))
    return ((deg, 1), (minutes, 1), (seconds, 1_000_000))


#: minutes since start, distance walked north in meters, was GPS on?
WALK_PLAN: tuple[tuple[float, float, bool], ...] = (
    (0.0, 0.0, True),
    (1.5, 130.0, False),
    (3.0, 260.0, False),
    (4.0, 350.0, True),
    (5.0, 372.0, False),
    (6.5, 386.0, False),
    (9.0, 396.0, False),
    (12.0, 404.0, False),
    (16.0, 420.0, False),
    (20.0, 440.0, True),
    (26.0, 500.0, False),
)


def create_walk_dataset(folder: Path, count: int | None = None) -> list[Path]:
    """Write a photo set that shows off the speed-aware model.

    The photographer walks briskly at first, then slows down to a crawl while
    looking around, and GPS is only switched on three times.  The two located
    segments therefore have very different speeds: exactly the situation where
    time-linear interpolation drags the photos into the wrong place and the
    speed model does not.
    """
    plan = list(WALK_PLAN)
    if count is not None:
        plan = plan[: max(3, count)]
        if not any(located for _, _, located in plan[1:]):
            plan[-1] = (plan[-1][0], plan[-1][1], True)
    start = datetime(2024, 6, 1, 9, 0, 0)
    origin = (37.5665, 126.9780)  # Seoul City Hall
    paths: list[Path] = []
    for index, (minutes, metres_north, located) in enumerate(plan):
        fix = None
        if located:
            fix = GpsFix(
                latitude=origin[0] + metres_north / 111_320.0,
                longitude=origin[1],
                altitude=30.0,
            )
        paths.append(
            write_photo(
                folder / f"IMG_{index + 1:04d}.jpg",
                start + timedelta(minutes=minutes),
                fix,
                utc_offset="+09:00",
            )
        )
    return paths

