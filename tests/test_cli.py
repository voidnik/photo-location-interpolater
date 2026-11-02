"""The ``pli`` command line surface."""

from __future__ import annotations

import json

import pytest

from pli.cli import build_parser, main
from pli.exifio import get_backend
from pli.pipeline import read_photos
from pli.scan import iter_image_files


@pytest.fixture
def trip(tmp_path):
    folder = tmp_path / "trip"
    assert main(["demo", str(folder)]) == 0
    return folder


def test_demo_creates_photos(tmp_path, capsys):
    folder = tmp_path / "walk"
    assert main(["demo", str(folder)]) == 0
    assert len(list(folder.glob("*.jpg"))) == 11
    assert "next:" in capsys.readouterr().out


def test_bare_folder_runs_interpolation(trip, capsys):
    assert main([str(trip)]) == 0
    output = capsys.readouterr().out
    assert "wrote" in output
    every_fix = _fixes(trip)
    assert sum(1 for fix in every_fix.values() if fix is not None) == 10


def test_dry_run_reports_without_writing(trip, capsys):
    assert main([str(trip), "--dry-run"]) == 0
    assert "dry run" in capsys.readouterr().out.lower()
    assert any(fix is None for fix in _fixes(trip).values())


def test_explicit_interpolate_subcommand(trip, capsys):
    assert main(["interpolate", str(trip), "--method", "spline"]) == 0
    assert "wrote" in capsys.readouterr().out


def test_output_dir_is_passed_through(trip, tmp_path, capsys):
    destination = tmp_path / "copy"
    assert main([str(trip), "-o", str(destination)]) == 0
    assert destination.exists()
    assert any(fix is None for fix in _fixes(trip).values())


def test_report_file_contains_the_run(trip, tmp_path, capsys):
    report = tmp_path / "run.json"
    assert main([str(trip), "--report", str(report)]) == 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["counts"]["written"] > 0
    assert payload["files"]


def test_json_output_parses(trip, capsys):
    assert main([str(trip), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["counts"]["kept"] == 3


def test_inspect_lists_the_timeline(trip, capsys):
    assert main(["inspect", str(trip)]) == 0
    output = capsys.readouterr().out
    assert "runs of photos without GPS" in output


def test_inspect_json(trip, capsys):
    assert main(["inspect", str(trip), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["located"] == 3
    assert len(payload["files"]) == 11


def test_ext_filter(trip, tmp_path, capsys):
    (trip / "notes.txt").write_text("hello")
    assert main(["inspect", str(trip), "--ext", "jpg"]) == 0
    assert "11 file(s)" in capsys.readouterr().out


def test_missing_folder_is_reported(tmp_path):
    assert main([str(tmp_path / "nowhere")]) == 1


def test_invalid_timezone_mode_exits_2(trip):
    with pytest.raises(SystemExit) as exit_info:
        main([str(trip), "--tz", "somewhere/weird"])
    assert exit_info.value.code == 2


def test_valid_offset_timezone(trip):
    assert main([str(trip), "--tz", "+09:00", "--dry-run"]) == 0


def test_version_and_help_exit_cleanly():
    with pytest.raises(SystemExit) as version:
        main(["--version"])
    assert version.value.code == 0
    with pytest.raises(SystemExit) as help_exit:
        main(["--help"])
    assert help_exit.value.code == 0


def test_no_arguments_prints_help(capsys):
    assert main([]) == 0
    assert "usage: pli" in capsys.readouterr().out


def test_extension_helper_accepts_lists():
    parser = build_parser()
    args = parser.parse_args(["interpolate", "somewhere", "--ext", "jpg, .PNG"])
    assert args.ext == "jpg, .PNG"


def _fixes(folder):
    backend = get_backend()
    infos = read_photos(backend, iter_image_files(folder), jobs=1)
    return {info.path.name: info.fix for info in infos}
