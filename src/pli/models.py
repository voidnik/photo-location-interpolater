"""Data structures shared by the scanner, the interpolator and the writers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ACTION_KEEP = "keep"
ACTION_WRITE = "write"
ACTION_SKIP = "skip"


@dataclass(slots=True)
class GpsFix:
    """A position reading, either stored in the file or reconstructed."""

    latitude: float
    longitude: float
    altitude: float | None = None
    speed_mps: float | None = None
    track_deg: float | None = None


@dataclass(slots=True)
class PhotoInfo:
    """Everything the scanner learned about a single image file."""

    path: Path
    wall_time: datetime | None = None
    """Date/time exactly as written in the EXIF block (timezone stripped)."""

    offset_seconds: int | None = None
    """UTC offset the camera claimed, when the EXIF block carried one."""

    time_source: str = "none"
    time_raw: str = ""
    mtime: float = 0.0
    fix: GpsFix | None = None
    read_error: str | None = None

    # Filled in by the timeline pass (see ``pli.timeline``).
    timestamp: float | None = None
    tz_note: str = ""
    assumed_offset_seconds: int | None = None
    """Offset we had to assume because the file did not record one."""

    @property
    def has_location(self) -> bool:
        return self.fix is not None

    @property
    def effective_offset_seconds(self) -> int | None:
        """Offset that turns ``wall_time`` into an unambiguous instant.

        Prefer what the camera recorded; fall back to what the timeline had to
        assume so that a rewritten file stays self-describing.
        """
        if self.offset_seconds is not None:
            return self.offset_seconds
        return self.assumed_offset_seconds

    @property
    def sortable_key(self) -> tuple[float, str]:
        stamp = self.timestamp if self.timestamp is not None else float("inf")
        return (stamp, str(self.path))


@dataclass(slots=True)
class Estimate:
    """A reconstructed position together with the reason it looks like that."""

    latitude: float
    longitude: float
    altitude: float | None = None
    speed_mps: float | None = None
    track_deg: float | None = None
    method: str = "linear"
    before: Path | None = None
    after: Path | None = None
    fraction: float = 0.0
    anchor_distance_m: float = 0.0
    uncertainty_m: float = 0.0
    note: str = ""


@dataclass(slots=True)
class FilePlan:
    """Per-file outcome of a pipeline run."""

    photo: PhotoInfo
    action: str = ACTION_SKIP
    estimate: Estimate | None = None
    reason: str = ""
    target: Path | None = None
    backup: Path | None = None
    written: bool = False

    @property
    def path(self) -> Path:
        return self.photo.path
