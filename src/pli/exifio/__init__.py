"""EXIF access backends.

A backend knows how to pull a timestamp and a GPS fix out of an image file and
how to write a reconstructed fix back without touching the image payload.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from ..models import GpsFix, PhotoInfo

BACKEND_NAMES = ("auto", "piexif")


@runtime_checkable
class ExifBackend(Protocol):
    """Read/write contract implemented by every backend."""

    name: str

    def can_read(self, path: Path) -> bool:
        ...

    def can_write(self, path: Path) -> bool:
        ...

    def read(self, path: Path) -> PhotoInfo:
        ...

    def write_fix(
        self,
        source: Path,
        target: Path,
        fix: GpsFix,
        when: float | None = None,
        offset_seconds: int | None = None,
    ) -> None:
        """Store ``fix`` in ``target``, derived from ``source``.

        ``when`` (an epoch) and ``offset_seconds`` (the zone that epoch should be
        written in) are optional: when both are given the textual timestamps are
        refreshed so that other tools read the same instant we interpolated.
        """
        ...


def get_backend(name: str = "auto") -> ExifBackend:
    """Instantiate a backend by name (``auto`` picks the best available)."""
    if name in {"auto", "piexif"}:
        from .piexif_backend import PiexifBackend

        return PiexifBackend()
    raise ValueError(f"unknown EXIF backend: {name!r} (choose from {', '.join(BACKEND_NAMES)})")
