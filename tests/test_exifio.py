"""The piexif backend: reading, writing and format limits."""

from __future__ import annotations

import datetime
import io
from pathlib import Path

import pytest

Image = pytest.importorskip("PIL.Image", reason="Pillow is a test-only dependency")

from pli.exifio import get_backend  # noqa: E402
from pli.exifio.piexif_backend import (  # noqa: E402
    PiexifBackend,
    _dms_to_degrees,
    _degrees_to_dms,
)
from pli.models import GpsFix  # noqa: E402
from pli.sampledata import write_photo  # noqa: E402


@pytest.fixture
def backend():
    return get_backend("auto")


def test_auto_backend_is_piexif(backend):
    assert backend.name == "piexif"
    with pytest.raises(ValueError):
        get_backend("nope")


def test_gps_roundtrip_keeps_camera_tags_and_precision(tmp_path, backend):
    fix = GpsFix(37.5665, 126.978, altitude=23.5, speed_mps=1.4, track_deg=90.0)
    path = write_photo(
        tmp_path / "a.jpg",
        datetime.datetime(2024, 5, 1, 10, 0, 0),
        fix,
        utc_offset="+09:00",
    )
    info = backend.read(path)
    assert info.time_raw == "2024:05:01 10:00:00"
    assert info.time_source == "DateTimeOriginal"
    assert info.offset_seconds == 9 * 3600
    assert info.fix.latitude == pytest.approx(37.5665, abs=1e-6)
    assert info.fix.longitude == pytest.approx(126.978, abs=1e-6)
    assert info.fix.altitude == pytest.approx(23.5, abs=0.01)
    assert info.fix.speed_mps == pytest.approx(1.4, abs=0.02)
    assert info.fix.track_deg == pytest.approx(90.0, abs=0.02)


def test_write_fix_keeps_the_original_exif(tmp_path, backend):
    path = write_photo(tmp_path / "b.jpg", datetime.datetime(2024, 5, 1, 10, 0, 0))
    before = path.read_bytes()
    backend.write_fix(path, path, GpsFix(-33.87, 151.21, altitude=-5.0), when=1_700_000_000.0)
    info = backend.read(path)
    assert info.fix.latitude == pytest.approx(-33.87, abs=1e-6)
    assert info.fix.longitude == pytest.approx(151.21, abs=1e-6)
    assert info.fix.altitude == pytest.approx(-5.0, abs=0.01)
    assert info.time_raw == "2024:05:01 10:00:00"
    assert info.fix is not None
    assert path.read_bytes() != before


def test_write_fix_adds_exif_to_a_bare_jpeg(tmp_path, backend):
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (10, 20, 30)).save(buffer, "JPEG")
    path = tmp_path / "bare.jpg"
    path.write_bytes(buffer.getvalue())
    assert backend.read(path).fix is None
    backend.write_fix(path, path, GpsFix(1.5, 2.5))
    info = backend.read(path)
    assert info.fix.latitude == pytest.approx(1.5, abs=1e-6)
    assert info.wall_time is None  # no camera timestamp was invented


def test_png_exif_roundtrip_keeps_the_image(tmp_path, backend):
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (1, 2, 3)).save(buffer, "PNG")
    path = tmp_path / "x.png"
    path.write_bytes(buffer.getvalue())
    backend.write_fix(path, path, GpsFix(37.5, -122.0, altitude=10.0))
    info = backend.read(path)
    assert info.fix.latitude == pytest.approx(37.5, abs=1e-6)
    assert info.fix.longitude == pytest.approx(-122.0, abs=1e-6)
    image = Image.open(path)
    image.verify()
    assert image.size == (8, 8)


def test_webp_roundtrip(tmp_path, backend):
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (4, 5, 6)).save(buffer, "WEBP")
    path = tmp_path / "x.webp"
    path.write_bytes(buffer.getvalue())
    backend.write_fix(path, path, GpsFix(10.0, 20.0))
    assert backend.read(path).fix.latitude == pytest.approx(10.0, abs=1e-6)


def test_write_fix_preserves_the_offset_piexif_cannot_read(tmp_path, backend):
    path = write_photo(
        tmp_path / "z.jpg",
        datetime.datetime(2024, 5, 1, 10, 0, 0),
        utc_offset="-03:30",
    )
    backend.write_fix(path, path, GpsFix(1.0, 2.0))  # no timestamp rewrite
    info = backend.read(path)
    assert info.offset_seconds == -(3 * 3600 + 30 * 60)
    assert info.time_raw == "2024:05:01 10:00:00"
    assert info.fix.latitude == pytest.approx(1.0, abs=1e-6)


def test_tiff_can_be_read_but_not_written(tmp_path, backend):
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buffer, "TIFF")
    path = tmp_path / "x.tif"
    path.write_bytes(buffer.getvalue())
    assert backend.can_read(path)
    assert not backend.can_write(path)
    assert backend.read(path).fix is None


def test_unsupported_extension_is_reported(backend, tmp_path):
    path = tmp_path / "clip.heic"
    path.write_bytes(b"\x00" * 32)
    assert not backend.can_read(path)
    info = backend.read(path)
    assert info.fix is None


def test_broken_file_reports_an_error_instead_of_raising(backend, tmp_path):
    path = tmp_path / "broken.jpg"
    path.write_bytes(b"definitely not a jpeg")
    info = backend.read(path)
    assert info.read_error and "EXIF" in info.read_error


@pytest.mark.parametrize("degrees", [0.0, 0.00001, 12.345678, 37.5665, 89.9999, 179.9999])
def test_dms_conversion_is_reversible(degrees):
    assert _dms_to_degrees(_degrees_to_dms(degrees)) == pytest.approx(degrees, abs=1e-6)


def test_dms_components_stay_in_range():
    for value in (59.99999999, 89.9999999, 179.9999999):
        (degrees, _), (minutes, _), (seconds, _) = _degrees_to_dms(value)
        assert 0 <= minutes < 60
        assert 0 <= seconds < 60_000_000
        assert _dms_to_degrees(_degrees_to_dms(value)) == pytest.approx(value, abs=1e-6)


def test_negative_coordinates_use_the_right_hemispheres(tmp_path, backend):
    path = write_photo(
        tmp_path / "s.jpg",
        datetime.datetime(2024, 1, 1, 0, 0, 0),
        GpsFix(-33.87, -70.66),
    )
    fix = backend.read(path).fix
    assert fix.latitude < 0 and fix.longitude < 0
