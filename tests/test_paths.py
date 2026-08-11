"""Unit tests for world/version path helpers."""

from __future__ import annotations

from pathlib import Path

from xapk_to_proto.paths import (
    infer_world_version_from_path,
    resolve_run_paths,
    version_from_filename,
)


def test_version_from_filename():
    assert version_from_filename(Path("game.xapk")) is None
    assert (
        version_from_filename(Path("com.innogames.heroesofhistory_1.50.3.xapk"))
        == "1.50.3"
    )
    assert version_from_filename(Path("1.49.8.xapk")) == "1.49.8"


def test_infer_from_fixtures_layout(tmp_path: Path):
    xapk = tmp_path / "fixtures" / "un0" / "1.50.3" / "game.xapk"
    xapk.parent.mkdir(parents=True)
    xapk.write_bytes(b"x")
    world, version = infer_world_version_from_path(xapk)
    assert world == "un0"
    assert version == "1.50.3"


def test_infer_zz0_layout(tmp_path: Path):
    xapk = tmp_path / "fixtures" / "zz0" / "1.50.3" / "game.xapk"
    xapk.parent.mkdir(parents=True)
    xapk.write_bytes(b"x")
    world, version = infer_world_version_from_path(xapk)
    assert world == "zz0"
    assert version == "1.50.3"


def test_resolve_default_output_from_path(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    xapk = tmp_path / "fixtures" / "un0" / "1.50.3" / "game.xapk"
    xapk.parent.mkdir(parents=True)
    xapk.write_bytes(b"x")
    paths = resolve_run_paths(xapk)
    assert paths.world == "un0"
    assert paths.version == "1.50.3"
    assert paths.output == (tmp_path / "output" / "un0" / "1.50.3").resolve()


def test_resolve_explicit_output_wins(tmp_path: Path):
    xapk = tmp_path / "game.xapk"
    xapk.write_bytes(b"x")
    out = tmp_path / "custom"
    paths = resolve_run_paths(xapk, world="zz0", version="1.0.0", output=out)
    assert paths.output == out.resolve()
    assert paths.world == "zz0"
    assert paths.version == "1.0.0"


def test_resolve_legacy_fallback_without_version(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    xapk = tmp_path / "somewhere" / "game.xapk"
    xapk.parent.mkdir(parents=True)
    xapk.write_bytes(b"x")
    paths = resolve_run_paths(xapk, world="un0")
    assert paths.version is None
    assert paths.output == (tmp_path / "game_protos").resolve()
