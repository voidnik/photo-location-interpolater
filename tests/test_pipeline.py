"""End-to-end pipeline behaviour on a synthetic folder."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pli.exifio import get_backend
from pli.exifio.piexif_backend import PiexifBackend
from pli.models import ACTION_KEEP
from pli.pipeline import RunOptions, read_photos, run
from pli.report import render_json, render_text
from pli.sampledata import create_walk_dataset
from pli.scan import iter_image_files

WRITTEN = 7
KEPT = 3
SKIPPED = 1  # the last photo was taken after the final fix


@pytest.fixture
def folder(tmp_path):
    photos = tmp_path / "photos"
    create_walk_dataset(photos)
    return photos


def options(folder, **kwargs):
    return RunOptions(folder=folder, jobs=1, **kwargs)


def snapshot(folder: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted(folder.iterdir())}


def fixes(folder: Path):
    backend = get_backend()
    infos = read_photos(backend, iter_image_files(folder), jobs=1)
    return {info.path.name: info.fix for info in infos}


def test_counts_match_the_walk_dataset(folder):
    report = run(options(folder, dry_run=True))
    counts = report.counts()
    assert report.scanned == WRITTEN + KEPT + SKIPPED
    assert report.located == KEPT
    assert counts == {"written": 0, "kept": KEPT, "skipped": WRITTEN + SKIPPED}


def test_dry_run_leaves_every_file_untouched(folder):
    before = snapshot(folder)
    report = run(options(folder, dry_run=True))
    assert report.counts()["written"] == 0
    assert snapshot(folder) == before


def test_inplace_run_writes_positions_and_backups(folder):
    before = snapshot(folder)
    report = run(options(folder))
    assert report.counts()["written"] == WRITTEN
    every_fix = fixes(folder)
    # 7 reconstructed + 3 from the camera; the trailing photo stays unlocated.
    assert sum(1 for fix in every_fix.values() if fix is not None) == WRITTEN + KEPT
    for plan in report.plans:
        if plan.written and plan.backup is not None:
            assert plan.backup.read_bytes() == before[plan.photo.path.name]
    assert list(folder.glob("*.bak"))


def test_backups_can_be_turned_off(folder):
    run(options(folder, backup=False))
    assert not list(folder.glob("*.bak"))


def test_output_dir_copies_instead_of_editing(folder, tmp_path):
    before = snapshot(folder)
    destination = tmp_path / "out"
    report = run(options(folder, output_dir=destination))
    assert snapshot(folder) == before
    written = report.counts()["written"]
    assert len(list(destination.glob("*.jpg"))) == written
    assert not list(folder.glob("*.bak"))


def test_photos_that_already_have_gps_are_left_alone(folder):
    report = run(options(folder))
    kept = [plan for plan in report.plans if plan.action == ACTION_KEEP]
    assert len(kept) == KEPT
    assert all(plan.reason == "already located" for plan in kept)


def test_trailing_photo_is_skipped_with_a_reason(folder):
    report = run(options(folder))
    skipped = [plan for plan in report.plans if plan.reason and plan.estimate is None]
    assert any("extrapolate" in plan.reason for plan in skipped)


def test_extrapolate_removes_the_trailing_gap(folder):
    report = run(options(folder, extrapolate=True))
    assert report.counts()["written"] == WRITTEN + SKIPPED


def test_method_can_be_switched(folder):
    linear = run(options(folder, method="linear", dry_run=True))
    speed = run(options(folder, method="speed", dry_run=True))
    linear_points = [
        (plan.estimate.latitude, plan.estimate.longitude)
        for plan in linear.plans
        if plan.estimate is not None
    ]
    speed_points = [
        (plan.estimate.latitude, plan.estimate.longitude)
        for plan in speed.plans
        if plan.estimate is not None
    ]
    assert linear_points != speed_points


def test_json_report_is_stable(folder):
    report = run(options(folder, dry_run=True))
    payload = json.loads(render_json(report))
    assert payload["counts"]["kept"] == KEPT
    assert payload["backend"] == "piexif"
    first = payload["files"][0]
    assert set(first) >= {"file", "time", "action", "estimate", "existing"}
    assert "method" in render_text(report)


def test_write_failures_are_reported_per_file(folder, monkeypatch):
    def explode(self, *args, **kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(PiexifBackend, "write_fix", explode)
    report = run(options(folder))
    assert report.counts()["written"] == 0
    assert any(
        plan.reason and plan.reason.startswith("write failed") for plan in report.plans
    )


def test_formats_the_backend_cannot_write_are_skipped(tmp_path):
    photos = tmp_path / "mixed"
    create_walk_dataset(photos, count=6)
    (photos / "clip.heic").write_bytes(b"\x00" * 64)
    report = run(options(photos))
    reasons = " ".join(plan.reason or "" for plan in report.plans)
    assert "cannot read" in reasons
