"""EXIF access built on ``piexif``.

Read support: JPEG, TIFF, PNG (``eXIf`` chunk) and WEBP.
Write support: JPEG, WEBP (via piexif) and PNG (own ``eXIf`` chunk writer).
TIFF/RAW files can be analysed but this backend never rewrites them: patching a
TIFF IFD chain safely needs a full container writer.
"""

from __future__ import annotations

import datetime as dt
import io
import os
import shutil
from pathlib import Path

from ..models import GpsFix, PhotoInfo
from ..times import parse_exif_datetime, parse_subseconds, parse_utc_offset
from . import png as png_chunks
from . import tifftags

WRITE_SUFFIXES = frozenset({".jpg", ".jpeg", ".jpe", ".webp", ".png"})
READ_SUFFIXES = WRITE_SUFFIXES | frozenset({".tif", ".tiff"})

PROCESSING_METHOD = b"photo-location-interpolater"


def _piexif():
    import piexif  # imported lazily so that a missing dependency fails late

    return piexif


def _text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("ascii", "ignore")
    value = str(value).strip()
    return value or None


def _rational(value) -> float | None:
    try:
        if isinstance(value, tuple):
            num, den = value
            return float(num) / float(den) if den else None
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _dms_to_degrees(value) -> float | None:
    if value is None or len(value) != 3:
        return None
    parts = [_rational(item) for item in value]
    if any(part is None or part < 0 for part in parts):
        return None
    degrees, minutes, seconds = parts
    return degrees + minutes / 60.0 + seconds / 3600.0


def _degrees_to_dms(degrees: float):
    """Split a degree value into EXIF rationals (1e-6 arcsec resolution)."""
    degrees = abs(degrees)
    deg = int(degrees)
    minutes_full = (degrees - deg) * 60.0
    minutes = int(minutes_full)
    seconds = int(round((minutes_full - minutes) * 60.0 * 1_000_000))
    if seconds >= 60_000_000:
        seconds = 0
        minutes += 1
    if minutes >= 60:
        minutes = 0
        deg += 1
    return ((deg, 1), (minutes, 1), (seconds, 1_000_000))


_SPEED_UNITS = {
    "K": 1.0 / 3.6,
    "M": 0.44704,
    "N": 1.0 / 1.9438444924406047,
}


def _blank_exif() -> dict:
    return {"0th": {}, "Exif": {}, "GPS": {}, "Interop": {}, "1st": {}, "thumbnail": None}


def _atomic_write(target: Path, payload: bytes, source: Path | None = None) -> None:
    """Swap ``payload`` into place so a crash can never truncate a photo."""
    temporary = target.with_name(f".{target.name}.pli.tmp")
    try:
        temporary.write_bytes(payload)
        if source is not None and source != target:
            shutil.copystat(source, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


class PiexifBackend:
    """Read and write EXIF GPS blocks with the pure-python ``piexif`` library."""

    name = "piexif"

    def can_read(self, path: Path) -> bool:
        return path.suffix.lower() in READ_SUFFIXES

    def can_write(self, path: Path) -> bool:
        return path.suffix.lower() in WRITE_SUFFIXES

    # -- reading ---------------------------------------------------------
    def read(self, path: Path) -> PhotoInfo:
        info = PhotoInfo(path=path, mtime=0.0)
        try:
            info.mtime = path.stat().st_mtime
            data = path.read_bytes()
        except FileNotFoundError:
            info.read_error = "file not found"
            return info
        except OSError as exc:
            info.read_error = f"cannot read file: {exc}"
            return info
        try:
            exif = self._load(path, data)
        except Exception as exc:  # noqa: BLE001 - a broken file is data, not a bug
            info.read_error = f"no usable EXIF ({type(exc).__name__}: {exc})"
            return info
        self._read_time(info, exif)
        if info.wall_time is not None and info.offset_seconds is None:
            # ``piexif`` does not know the OffsetTime* tags, read them directly.
            info.offset_seconds = tifftags.offset_time_from_bytes(data)
        info.fix = self._read_gps(exif)
        return info

    def _load(self, path: Path, data: bytes) -> dict:
        if path.suffix.lower() == ".png":
            tiff = png_chunks.read_exif(data)
            if not tiff:
                return _blank_exif()
            return _piexif().load(tiff)
        return _piexif().load(data)

    @staticmethod
    def _read_time(info: PhotoInfo, exif: dict) -> None:
        exif_ifd = exif.get("Exif") or {}
        zeroth = exif.get("0th") or {}
        candidates = (
            ("DateTimeOriginal", exif_ifd.get(0x9003), 0x9291, 0x9010),
            ("DateTimeDigitized", exif_ifd.get(0x9004), 0x9292, 0x9011),
            ("DateTime", zeroth.get(0x0132), 0x9290, 0x8829),
        )
        for source, raw, subsec_tag, offset_tag in candidates:
            text = _text(raw)
            wall = parse_exif_datetime(text)
            if wall is None:
                continue
            subsec = parse_subseconds(_text(exif_ifd.get(subsec_tag)))
            info.wall_time = wall.replace(
                microsecond=int(round(subsec * 1_000_000)) % 1_000_000
            )
            info.time_source = source
            info.time_raw = text
            info.offset_seconds = parse_utc_offset(_text(exif_ifd.get(offset_tag)))
            return

    @staticmethod
    def _read_gps(exif: dict) -> GpsFix | None:
        gps = exif.get("GPS") or {}
        if not gps:
            return None
        piexif = _piexif()
        latitude = _dms_to_degrees(gps.get(piexif.GPSIFD.GPSLatitude))
        longitude = _dms_to_degrees(gps.get(piexif.GPSIFD.GPSLongitude))
        if latitude is None or longitude is None:
            return None
        if (_text(gps.get(piexif.GPSIFD.GPSLatitudeRef)) or "N").upper().startswith("S"):
            latitude = -latitude
        if (_text(gps.get(piexif.GPSIFD.GPSLongitudeRef)) or "E").upper().startswith("W"):
            longitude = -longitude
        altitude = _rational(gps.get(piexif.GPSIFD.GPSAltitude))
        if altitude is not None and gps.get(piexif.GPSIFD.GPSAltitudeRef) == 1:
            altitude = -altitude
        speed = _rational(gps.get(piexif.GPSIFD.GPSSpeed))
        unit = ((_text(gps.get(piexif.GPSIFD.GPSSpeedRef)) or "K").upper() + "K")[0]
        speed_mps = (
            speed * _SPEED_UNITS.get(unit, _SPEED_UNITS["K"]) if speed is not None else None
        )
        track = _rational(gps.get(piexif.GPSIFD.GPSTrack))
        if track is None:
            track = _rational(gps.get(piexif.GPSIFD.GPSImgDirection))
        if track is not None and not 0.0 <= track < 360.0:
            track = None
        return GpsFix(
            latitude=latitude,
            longitude=longitude,
            altitude=altitude,
            speed_mps=speed_mps,
            track_deg=track,
        )

    # -- writing ---------------------------------------------------------
    def write_fix(
        self,
        source: Path,
        target: Path,
        fix: GpsFix,
        when: float | None = None,
        offset_seconds: int | None = None,
    ) -> None:
        """Add the reconstructed fix to ``source`` and store the result at ``target``."""
        piexif = _piexif()
        data = source.read_bytes()
        try:
            exif = self._load(source, data)
        except Exception:  # noqa: BLE001 - fall back to a clean EXIF block
            exif = _blank_exif()
        exif.setdefault("GPS", {})
        self._inject_gps(exif, fix, when)
        if when is not None and offset_seconds is not None:
            _inject_local_time(exif, when, offset_seconds)
        else:
            _carry_offset_tags(exif, data)
        blob = piexif.dump(exif)
        if target.suffix.lower() == ".png":
            payload = png_chunks.write_exif(data, blob[6:])
        else:
            buffer = io.BytesIO()
            piexif.insert(blob, data, buffer)
            payload = buffer.getvalue()
        _atomic_write(target, payload, source)

    @staticmethod
    def _inject_gps(exif: dict, fix: GpsFix, when: float | None) -> None:
        piexif = _piexif()
        gps = exif["GPS"]
        gps[piexif.GPSIFD.GPSVersionID] = (2, 3, 0, 0)
        gps[piexif.GPSIFD.GPSLatitudeRef] = "N" if fix.latitude >= 0 else "S"
        gps[piexif.GPSIFD.GPSLatitude] = _degrees_to_dms(fix.latitude)
        gps[piexif.GPSIFD.GPSLongitudeRef] = "E" if fix.longitude >= 0 else "W"
        gps[piexif.GPSIFD.GPSLongitude] = _degrees_to_dms(fix.longitude)
        if fix.altitude is not None:
            gps[piexif.GPSIFD.GPSAltitudeRef] = b"\x00" if fix.altitude >= 0 else b"\x01"
            gps[piexif.GPSIFD.GPSAltitude] = (int(abs(round(fix.altitude * 100))), 100)
        if fix.speed_mps is not None:
            gps[piexif.GPSIFD.GPSSpeedRef] = "K"
            gps[piexif.GPSIFD.GPSSpeed] = (
                int(round(max(fix.speed_mps, 0.0) * 3.6 * 100)),
                100,
            )
        if fix.track_deg is not None:
            gps[piexif.GPSIFD.GPSTrackRef] = "T"
            gps[piexif.GPSIFD.GPSTrack] = (int(round(fix.track_deg * 100)), 100)
        if when is not None:
            moment = dt.datetime.fromtimestamp(when, dt.timezone.utc)
            gps[piexif.GPSIFD.GPSDateStamp] = moment.strftime("%Y:%m:%d")
            gps[piexif.GPSIFD.GPSTimeStamp] = (
                (moment.hour, 1),
                (moment.minute, 1),
                (int(round((moment.second + moment.microsecond / 1e6) * 100)), 100),
            )
        gps[piexif.GPSIFD.GPSProcessingMethod] = PROCESSING_METHOD


def _offset_text(offset_seconds: int) -> str:
    """Render an offset the way EXIF wants it (``+09:00``)."""
    sign = "+" if offset_seconds >= 0 else "-"
    minutes = abs(offset_seconds) // 60
    return f"{sign}{minutes // 60:02d}:{minutes % 60:02d}"


def _carry_offset_tags(exif: dict, data: bytes) -> None:
    """Keep a camera's own ``OffsetTime*`` tags alive through a rewrite.

    ``piexif`` only writes back the tags it knows, and these are not among them,
    so without this the zone information would quietly disappear from a file we
    only meant to add a GPS block to.
    """
    exif_block = exif.setdefault("Exif", {})
    for tag in tifftags.OFFSET_TAGS:
        text = tifftags.ascii_tag(data, tag)
        if text:
            exif_block[tag] = text.encode("ascii", "ignore")


def _inject_local_time(exif: dict, when: float, offset_seconds: int) -> None:
    """Refresh the textual timestamps so the written file stays unambiguous.

    ``piexif`` can store the ``OffsetTime*`` tags but cannot read them back --
    that gap is what :mod:`pli.exifio.tifftags` closes -- so both the local
    stamp and its offset are written together here.
    """
    piexif = _piexif()
    zone = dt.timezone(dt.timedelta(seconds=int(offset_seconds)))
    moment = dt.datetime.fromtimestamp(when, zone)
    stamp = moment.strftime("%Y:%m:%d %H:%M:%S").encode("ascii")
    text = _offset_text(int(offset_seconds)).encode("ascii")
    exif.setdefault("0th", {})[piexif.ImageIFD.DateTime] = stamp
    exif_block = exif.setdefault("Exif", {})
    exif_block[piexif.ExifIFD.DateTimeOriginal] = stamp
    exif_block[piexif.ExifIFD.DateTimeDigitized] = stamp
    exif_block[tifftags.OFFSET_TIME] = text
    exif_block[tifftags.OFFSET_TIME_ORIGINAL] = text
    exif_block[tifftags.OFFSET_TIME_DIGITIZED] = text


