"""CLI path resolution for version-default layouts (no network / Unity)."""

from __future__ import annotations

from pathlib import Path

from xapk_to_proto.cli import (
    _resolve_download_assets_args,
    _resolve_download_xapk_args,
    _resolve_emit_args,
    _resolve_gamedesign_args,
    _resolve_link_assets_args,
    _resolve_run_args,
    _resolve_unpack_assets_args,
    build_parser,
)
from xapk_to_proto.game_api import ENV_WORLD


def test_download_assets_version_defaults(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(ENV_WORLD, raising=False)
    args = build_parser().parse_args(["download-assets", "--version", "1.50.3"])
    assert _resolve_download_assets_args(args) is None
    assert args.xapk == (
        tmp_path / "fixtures" / "un0" / "1.50.3" / "game.xapk"
    ).resolve()
    assert args.output == (
        tmp_path / "output" / "un0" / "1.50.3" / "assets"
    ).resolve()
    assert args.unpack_out == (
        tmp_path / "output" / "un0" / "1.50.3" / "unpacked"
    ).resolve()


def test_download_assets_explicit_overrides(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    catalog = tmp_path / "catalog.bin"
    catalog.write_bytes(b"c")
    out = tmp_path / "custom-assets"
    args = build_parser().parse_args(
        [
            "download-assets",
            "--version",
            "1.50.3",
            "--catalog",
            str(catalog),
            "-o",
            str(out),
        ]
    )
    assert _resolve_download_assets_args(args) is None
    assert args.catalog == catalog
    assert args.xapk is None
    assert args.output == out


def test_download_assets_requires_source_or_version(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    args = build_parser().parse_args(["download-assets", "-o", str(tmp_path / "a")])
    err = _resolve_download_assets_args(args)
    assert err is not None
    assert "--version" in err


def test_unpack_assets_defaults_to_bundles(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(ENV_WORLD, raising=False)
    args = build_parser().parse_args(
        ["unpack-assets", "--version", "1.50.3", "--only", "spriteatlas"]
    )
    assert _resolve_unpack_assets_args(args) is None
    assert args.bundles == (
        tmp_path / "output" / "un0" / "1.50.3" / "assets"
    ).resolve()
    assert args.xapk is None
    assert args.output == (
        tmp_path / "output" / "un0" / "1.50.3" / "unpacked"
    ).resolve()


def test_unpack_assets_xapk_layout_default(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(ENV_WORLD, raising=False)
    args = build_parser().parse_args(["unpack-assets", "--version", "1.50.3", "--xapk"])
    assert _resolve_unpack_assets_args(args) is None
    assert args.xapk == (
        tmp_path / "fixtures" / "un0" / "1.50.3" / "game.xapk"
    ).resolve()
    assert args.bundles is None


def test_download_xapk_defaults_to_fixture_path(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(ENV_WORLD, raising=False)
    args = build_parser().parse_args(["download-xapk", "1.50.3"])
    _resolve_download_xapk_args(args)
    assert args.output == (
        tmp_path / "fixtures" / "un0" / "1.50.3" / "game.xapk"
    ).resolve()


def test_download_xapk_world_flag(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(ENV_WORLD, raising=False)
    args = build_parser().parse_args(
        ["download-xapk", "1.50.3", "--world", "zz0"]
    )
    _resolve_download_xapk_args(args)
    assert args.output == (
        tmp_path / "fixtures" / "zz0" / "1.50.3" / "game.xapk"
    ).resolve()


def test_run_version_fills_xapk_and_fixture_inputs(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(ENV_WORLD, raising=False)
    fixtures = tmp_path / "fixtures" / "un0" / "1.50.3"
    fixtures.mkdir(parents=True)
    (fixtures / "game.xapk").write_bytes(b"x")
    (fixtures / "gamedesign").write_bytes(b"g")
    (fixtures / "loca-compressed").write_bytes(b"l")
    (fixtures / "startup").write_bytes(b"s")

    args = build_parser().parse_args(["run", "--version", "1.50.3"])
    assert _resolve_run_args(args) is None
    assert args.xapk == (fixtures / "game.xapk").resolve()
    assert args.gamedesign_input == (fixtures / "gamedesign").resolve()
    assert args.loca_input == (fixtures / "loca-compressed").resolve()
    assert args.startup_input == (fixtures / "startup").resolve()


def test_run_path_inference_fills_inputs_without_version(
    tmp_path: Path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(ENV_WORLD, raising=False)
    fixtures = tmp_path / "fixtures" / "zz0" / "1.50.3"
    fixtures.mkdir(parents=True)
    xapk = fixtures / "game.xapk"
    xapk.write_bytes(b"x")
    (fixtures / "gamedesign").write_bytes(b"g")

    args = build_parser().parse_args(["run", str(xapk)])
    assert args.world is None
    assert _resolve_run_args(args) is None
    assert args.gamedesign_input == (fixtures / "gamedesign").resolve()


def test_emit_version_defaults(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(ENV_WORLD, raising=False)
    args = build_parser().parse_args(["emit", "--version", "1.50.3"])
    assert _resolve_emit_args(args) is None
    assert args.inp == (
        tmp_path / "output" / "un0" / "1.50.3" / "descriptors.pb"
    ).resolve()
    assert args.out_dir == (
        tmp_path / "output" / "un0" / "1.50.3" / "proto"
    ).resolve()


def test_gamedesign_version_defaults(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(ENV_WORLD, raising=False)
    args = build_parser().parse_args(["gamedesign", "--version", "1.50.3"])
    assert _resolve_gamedesign_args(args) is None
    assert args.descriptors.name == "descriptors.pb"
    assert args.input.name == "gamedesign"
    assert args.out_dir.name == "gamedesign"


def test_link_assets_version_defaults(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(ENV_WORLD, raising=False)
    out = tmp_path / "output" / "un0" / "1.50.3"
    (out / "gamedesign").mkdir(parents=True)
    (out / "startup").mkdir(parents=True)
    (out / "unpacked").mkdir(parents=True)
    (out / "descriptors.pb").write_bytes(b"d")

    args = build_parser().parse_args(["link-assets", "--version", "1.50.3"])
    assert _resolve_link_assets_args(args) is None
    assert args.index == (out / "unpacked").resolve()
    assert args.descriptors == (out / "descriptors.pb").resolve()
    assert args.out_dir == (out / "asset_links").resolve()
    assert {d.name for d in args.definitions} == {"gamedesign", "startup"}
