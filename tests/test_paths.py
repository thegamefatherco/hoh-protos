"""Unit tests for world/version path helpers."""

from __future__ import annotations

from pathlib import Path

from xapk_to_proto.game_api import DEFAULT_WORLD, ENV_WORLD
from xapk_to_proto.paths import (
    coalesce,
    infer_world_version_from_path,
    resolve_run_paths,
    resolve_world,
    version_from_filename,
    version_layout,
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


def test_resolve_zz0_without_explicit_world(tmp_path: Path, monkeypatch):
    """Path inference must win when --world is omitted (None)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(ENV_WORLD, raising=False)
    xapk = tmp_path / "fixtures" / "zz0" / "1.50.3" / "game.xapk"
    xapk.parent.mkdir(parents=True)
    xapk.write_bytes(b"x")
    paths = resolve_run_paths(xapk, world=None)
    assert paths.world == "zz0"
    assert paths.output == (tmp_path / "output" / "zz0" / "1.50.3").resolve()


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


def test_resolve_world_order(monkeypatch):
    monkeypatch.delenv(ENV_WORLD, raising=False)
    assert resolve_world(None) == DEFAULT_WORLD
    assert resolve_world("zz0") == "zz0"
    monkeypatch.setenv(ENV_WORLD, "zz1")
    assert resolve_world(None) == "zz1"
    # Explicit flag wins over env (unlike download-fixtures' resolve_world_arg).
    assert resolve_world("un1") == "un1"


def test_version_layout_paths(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    layout = version_layout("1.50.3", world="un0")
    assert layout.xapk == (tmp_path / "fixtures" / "un0" / "1.50.3" / "game.xapk").resolve()
    assert layout.assets == (tmp_path / "output" / "un0" / "1.50.3" / "assets").resolve()
    assert layout.unpacked == (
        tmp_path / "output" / "un0" / "1.50.3" / "unpacked"
    ).resolve()
    assert layout.descriptors == (
        tmp_path / "output" / "un0" / "1.50.3" / "descriptors.pb"
    ).resolve()
    assert layout.constants == (
        tmp_path / "output" / "un0" / "1.50.3" / "gamedesign" / "constants"
    ).resolve()


def test_existing_fixture_blobs(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fixtures = tmp_path / "fixtures" / "un0" / "1.50.3"
    fixtures.mkdir(parents=True)
    (fixtures / "gamedesign").write_bytes(b"gd")
    (fixtures / "startup").write_bytes(b"st")
    layout = version_layout("1.50.3", world="un0")
    names = {p.name for p in layout.existing_fixture_blobs()}
    assert names == {"gamedesign", "startup"}


def test_coalesce():
    assert coalesce(None, Path("default")) == Path("default")
    assert coalesce(Path("explicit"), Path("default")) == Path("explicit")
    assert coalesce(None, None) is None
