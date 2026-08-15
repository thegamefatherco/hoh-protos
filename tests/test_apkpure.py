"""Tests for apkeep-backed XAPK download and output-path handling (no network)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from xapk_to_proto import apkpure
from xapk_to_proto.apkpure import (
    DEFAULT_ABI,
    DEFAULT_PACKAGE,
    Release,
    download_xapk,
    resolve_output_path,
)


def _release(version: str = "1.49.8") -> Release:
    return Release(package=DEFAULT_PACKAGE, version=version)


def _write_minimal_zip(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", "{}")


def test_resolve_output_path_directory_gets_generated_name(tmp_path):
    dest = resolve_output_path(tmp_path, _release())
    assert dest == tmp_path / f"{DEFAULT_PACKAGE}_1.49.8.xapk"


def test_resolve_output_path_nonexistent_dir_gets_generated_name(tmp_path):
    dest = resolve_output_path(tmp_path / "nested", _release())
    assert dest.name == f"{DEFAULT_PACKAGE}_1.49.8.xapk"
    assert dest.parent.name == "nested"


def test_resolve_output_path_xapk_suffix_is_used_verbatim(tmp_path):
    target = tmp_path / "custom.xapk"
    assert resolve_output_path(target, _release()) == target


def test_resolve_output_path_dotted_dir_name_is_not_mistaken_for_a_file(tmp_path):
    # "1.49.8" has a ".8" suffix but is a directory name, not an XAPK filename.
    dest = resolve_output_path(tmp_path / "1.49.8", _release())
    assert dest.parent.name == "1.49.8"
    assert dest.name == f"{DEFAULT_PACKAGE}_1.49.8.xapk"


def test_resolve_output_path_existing_dir_wins_over_suffix(tmp_path):
    weird = tmp_path / "drop.xapk"
    weird.mkdir()
    dest = resolve_output_path(weird, _release())
    assert dest.parent == weird
    assert dest.name == f"{DEFAULT_PACKAGE}_1.49.8.xapk"


def test_resolve_output_path_defaults_to_cwd():
    dest = resolve_output_path(None, _release())
    assert dest.name == f"{DEFAULT_PACKAGE}_1.49.8.xapk"
    assert dest.is_absolute()


def test_download_xapk_skips_existing(tmp_path, monkeypatch):
    dest = tmp_path / "game.xapk"
    dest.write_bytes(b"already")

    def boom(*_args, **_kwargs):
        raise AssertionError("apkeep should not run when destination exists")

    monkeypatch.setattr(apkpure.subprocess, "run", boom)
    monkeypatch.setattr(
        "xapk_to_proto.deps.resolve_apkeep", lambda: "/usr/bin/apkeep"
    )

    result = download_xapk(output=dest, version="1.50.3")
    assert result.skipped is True
    assert result.path == dest
    assert result.size == 7


def test_download_xapk_invokes_apkeep_and_renames(tmp_path, monkeypatch, capsys):
    dest = tmp_path / "game.xapk"
    captured: dict[str, object] = {}

    def fake_run(cmd, check=False):
        captured["cmd"] = list(cmd)
        outdir = Path(cmd[-1])
        produced = outdir / f"{DEFAULT_PACKAGE}@1.50.3.xapk"
        _write_minimal_zip(produced)
        return type("Proc", (), {"returncode": 0})()

    monkeypatch.setattr(apkpure.subprocess, "run", fake_run)
    monkeypatch.setattr(
        "xapk_to_proto.deps.resolve_apkeep", lambda: "/opt/apkeep"
    )

    result = download_xapk(
        output=dest,
        version="1.50.3",
        abi=DEFAULT_ABI,
        verbose=False,
    )

    assert result.skipped is False
    assert result.path == dest
    assert dest.is_file()
    assert zipfile.is_zipfile(dest)

    cmd = captured["cmd"]
    assert cmd[0] == "/opt/apkeep"
    assert cmd[1:5] == ["-a", f"{DEFAULT_PACKAGE}@1.50.3", "-d", "apk-pure"]
    assert cmd[5:7] == ["-o", f"arch={DEFAULT_ABI}"]

    out = capsys.readouterr().out
    assert f"downloading {DEFAULT_PACKAGE}@1.50.3 -> {dest}" in out


def test_download_xapk_latest_omits_version_suffix(tmp_path, monkeypatch):
    dest = tmp_path / "latest.xapk"
    captured: dict[str, object] = {}

    def fake_run(cmd, check=False):
        captured["cmd"] = list(cmd)
        outdir = Path(cmd[-1])
        _write_minimal_zip(outdir / f"{DEFAULT_PACKAGE}.xapk")
        return type("Proc", (), {"returncode": 0})()

    monkeypatch.setattr(apkpure.subprocess, "run", fake_run)
    monkeypatch.setattr(
        "xapk_to_proto.deps.resolve_apkeep", lambda: "apkeep"
    )

    result = download_xapk(output=dest)
    assert result.release.version == "latest"
    assert captured["cmd"][2] == DEFAULT_PACKAGE
    assert result.path == dest


def test_download_xapk_force_replaces_existing(tmp_path, monkeypatch):
    dest = tmp_path / "game.xapk"
    dest.write_bytes(b"old")

    def fake_run(cmd, check=False):
        outdir = Path(cmd[-1])
        _write_minimal_zip(outdir / f"{DEFAULT_PACKAGE}@1.50.3.xapk")
        return type("Proc", (), {"returncode": 0})()

    monkeypatch.setattr(apkpure.subprocess, "run", fake_run)
    monkeypatch.setattr(
        "xapk_to_proto.deps.resolve_apkeep", lambda: "apkeep"
    )

    result = download_xapk(output=dest, version="1.50.3", force=True)
    assert result.skipped is False
    assert dest.read_bytes() != b"old"
    assert zipfile.is_zipfile(dest)


def test_download_xapk_rejects_apk_only(tmp_path, monkeypatch):
    dest = tmp_path / "game.xapk"

    def fake_run(cmd, check=False):
        outdir = Path(cmd[-1])
        (outdir / f"{DEFAULT_PACKAGE}.apk").write_bytes(b"not-xapk")
        return type("Proc", (), {"returncode": 0})()

    monkeypatch.setattr(apkpure.subprocess, "run", fake_run)
    monkeypatch.setattr(
        "xapk_to_proto.deps.resolve_apkeep", lambda: "apkeep"
    )

    with pytest.raises(RuntimeError, match="APK\\(s\\) instead of XAPK"):
        download_xapk(output=dest, version="1.50.3")


def test_download_xapk_missing_apkeep(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "xapk_to_proto.deps.resolve_apkeep",
        lambda: (_ for _ in ()).throw(FileNotFoundError("apkeep not found")),
    )
    with pytest.raises(RuntimeError, match="apkeep not found"):
        download_xapk(output=tmp_path / "game.xapk", version="1.50.3")


def test_download_xapk_skip_prints_nothing_about_download(tmp_path, monkeypatch, capsys):
    dest = tmp_path / "game.xapk"
    dest.write_bytes(b"already")
    monkeypatch.setattr(
        "xapk_to_proto.deps.resolve_apkeep", lambda: "/usr/bin/apkeep"
    )
    monkeypatch.setattr(
        apkpure.subprocess,
        "run",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no run")),
    )
    download_xapk(output=dest, version="1.50.3")
    assert "downloading" not in capsys.readouterr().out
