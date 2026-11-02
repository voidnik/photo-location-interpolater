"""File discovery for the CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

#: Extensions that are probed by default.  Formats the active EXIF backend
#: cannot handle are reported as skipped instead of silently ignored.
DEFAULT_EXTENSIONS = frozenset(
    {
        ".jpg",
        ".jpeg",
        ".jpe",
        ".tif",
        ".tiff",
        ".png",
        ".webp",
        ".heic",
        ".heif",
        ".avif",
        ".dng",
        ".cr2",
        ".cr3",
        ".nef",
        ".arw",
        ".raf",
        ".orf",
        ".rw2",
    }
)

#: Files produced by this tool or by other editors that must not be re-read.
EXCLUDED_SUFFIXES = (".bak", ".orig", ".backup", ".tmp", ".part")


def iter_image_files(
    root: Path,
    recursive: bool = True,
    extensions: Iterable[str] | None = None,
    include_hidden: bool = False,
) -> list[Path]:
    """Return image candidates below *root*, sorted for a stable run order."""
    suffixes = {ext.lower() for ext in (extensions or DEFAULT_EXTENSIONS)}
    if not suffixes:
        suffixes = set(DEFAULT_EXTENSIONS)
    if not any(ext.startswith(".") for ext in suffixes):  # accept ``jpg`` too
        suffixes = {"." + ext for ext in suffixes}

    walker = root.rglob("*") if recursive else root.glob("*")
    found: list[Path] = []
    for candidate in walker:
        if not candidate.is_file():
            continue
        if candidate.suffix.lower() not in suffixes:
            continue
        if candidate.name.lower().endswith(EXCLUDED_SUFFIXES):
            continue
        if not include_hidden and _is_hidden(candidate, root):
            continue
        found.append(candidate)
    return sorted(found, key=lambda item: str(item).lower())


def _is_hidden(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:  # pragma: no cover - defensive
        return False
    return any(part.startswith(".") for part in relative.parts)
