"""Shared fixtures: synthetic photos that carry a real EXIF block."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from pli.models import GpsFix, PhotoInfo
from pli.sampledata import write_photo


@pytest.fixture
def make_photo(tmp_path):
    """Write a JPEG whose EXIF DateTimeOriginal (and optional GPS) is set."""

    def factory(name: str, taken: datetime, fix: GpsFix | None = None, offset="+09:00"):
        return write_photo(tmp_path / name, taken, fix, utc_offset=offset)

    return factory


@pytest.fixture
def make_info():
    """Build :class:`PhotoInfo` objects directly, bypassing EXIF parsing."""

    def factory(name: str, timestamp: float, fix: GpsFix | None = None) -> PhotoInfo:
        return PhotoInfo(
            path=Path(f"/photos/{name}"),
            wall_time=datetime.fromtimestamp(timestamp),
            timestamp=timestamp,
            fix=fix,
            time_source="DateTimeOriginal",
        )

    return factory


@pytest.fixture
def make_fix():
    def factory(latitude: float, longitude: float, **kwargs) -> GpsFix:
        return GpsFix(latitude=latitude, longitude=longitude, **kwargs)

    return factory
