"""File discovery."""

from __future__ import annotations

from pathlib import Path

from pli.scan import DEFAULT_EXTENSIONS, iter_image_files


def touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00")
    return path


def test_finds_images_recursively_by_default(tmp_path):
    touch(tmp_path / "a.jpg")
    touch(tmp_path / "deep" / "nested" / "b.jpeg")
    touch(tmp_path / "notes.txt")
    found = iter_image_files(tmp_path)
    assert [path.name for path in found] == ["a.jpg", "b.jpeg"]


def test_non_recursive_only_lists_the_top_folder(tmp_path):
    touch(tmp_path / "a.jpg")
    touch(tmp_path / "sub" / "b.jpg")
    assert [path.name for path in iter_image_files(tmp_path, recursive=False)] == ["a.jpg"]


def test_extension_filter_accepts_values_with_or_without_a_dot(tmp_path):
    touch(tmp_path / "a.jpg")
    touch(tmp_path / "b.png")
    assert [p.name for p in iter_image_files(tmp_path, extensions=["png"])] == ["b.png"]
    assert [p.name for p in iter_image_files(tmp_path, extensions=[".JPG"])] == ["a.jpg"]


def test_hidden_and_backup_files_are_skipped_unless_asked(tmp_path):
    touch(tmp_path / ".hidden" / "a.jpg")
    touch(tmp_path / "IMG_1.jpg.bak")
    touch(tmp_path / "IMG_2.jpg")
    assert [p.name for p in iter_image_files(tmp_path)] == ["IMG_2.jpg"]
    assert [p.name for p in iter_image_files(tmp_path, include_hidden=True)] == [
        "a.jpg",
        "IMG_2.jpg",
    ]


def test_results_are_sorted_case_insensitively(tmp_path):
    touch(tmp_path / "b.JPG")
    touch(tmp_path / "a.jpg")
    touch(tmp_path / "C.jpeg")
    assert [p.name for p in iter_image_files(tmp_path)] == ["a.jpg", "b.JPG", "C.jpeg"]


def test_every_default_extension_is_a_dot_prefixed_suffix():
    assert all(ext.startswith(".") for ext in DEFAULT_EXTENSIONS)
