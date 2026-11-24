"""Tests for AssetRipper input staging (DummyDll + bundles)."""

from __future__ import annotations

from pathlib import Path

import pytest

from xapk_to_proto import assetripper
from xapk_to_proto.assetripper import StageError, stage_input
from xapk_to_proto.cli import build_parser


def _il2cpp(tmp_path: Path, *dlls: str) -> Path:
    il2cpp = tmp_path / "il2cpp"
    folder = il2cpp / "DummyDll"
    folder.mkdir(parents=True)
    for name in dlls:
        (folder / name).write_bytes(b"MZ" + name.encode())
    return il2cpp


def _bundles(tmp_path: Path, *names: str) -> Path:
    folder = tmp_path / "assets"
    folder.mkdir()
    for name in names:
        (folder / name).write_bytes(b"UnityFS" + name.encode())
    (folder / "catalog.json").write_text("{}", encoding="utf-8")
    return folder


def test_stage_input_links_bundles_and_copies_dlls(tmp_path: Path):
    il2cpp = _il2cpp(tmp_path, "InnoGames.Game.dll", "Assembly-CSharp.dll")
    bundles = _bundles(tmp_path, "a.bundle", "b.bundle")

    result = stage_input(
        il2cpp_dir=il2cpp, bundles_dir=bundles, out_dir=tmp_path / "assetripper"
    )

    assert result.assemblies == 2
    assert result.bundles == 2
    assert result.linked is True
    managed = sorted(p.name for p in result.managed_dir.iterdir())
    assert managed == ["Assembly-CSharp.dll", "InnoGames.Game.dll"]
    assert not (result.managed_dir / "InnoGames.Game.dll").is_symlink()
    staged = sorted(p.name for p in result.bundles_dir.iterdir())
    assert staged == ["a.bundle", "b.bundle"]
    link = result.bundles_dir / "a.bundle"
    assert link.is_symlink()
    assert link.read_bytes() == b"UnityFSa.bundle"


def test_stage_input_replaces_previous_stage(tmp_path: Path):
    il2cpp = _il2cpp(tmp_path, "New.dll")
    bundles = _bundles(tmp_path, "a.bundle")
    out = tmp_path / "assetripper"
    (out / "Managed").mkdir(parents=True)
    (out / "Managed" / "Stale.dll").write_bytes(b"MZ")

    result = stage_input(il2cpp_dir=il2cpp, bundles_dir=bundles, out_dir=out)

    assert [p.name for p in result.managed_dir.iterdir()] == ["New.dll"]


def test_stage_input_copies_when_linking_disabled(tmp_path: Path):
    il2cpp = _il2cpp(tmp_path, "InnoGames.Game.dll")
    bundles = _bundles(tmp_path, "a.bundle")

    result = stage_input(
        il2cpp_dir=il2cpp,
        bundles_dir=bundles,
        out_dir=tmp_path / "assetripper",
        link_bundles=False,
    )

    staged = result.bundles_dir / "a.bundle"
    assert result.linked is False
    assert not staged.is_symlink()
    assert staged.read_bytes() == b"UnityFSa.bundle"


def test_stage_input_falls_back_to_copy_when_symlink_fails(
    tmp_path: Path, monkeypatch
):
    il2cpp = _il2cpp(tmp_path, "InnoGames.Game.dll")
    bundles = _bundles(tmp_path, "a.bundle", "b.bundle")

    def deny(*_a, **_k):
        raise OSError("symlinks not permitted")

    monkeypatch.setattr(assetripper.os, "symlink", deny)
    result = stage_input(
        il2cpp_dir=il2cpp, bundles_dir=bundles, out_dir=tmp_path / "assetripper"
    )

    assert result.linked is False
    for name in ("a.bundle", "b.bundle"):
        staged = result.bundles_dir / name
        assert not staged.is_symlink()
        assert staged.read_bytes() == b"UnityFS" + name.encode()


def test_stage_input_rejects_empty_dummy_dll(tmp_path: Path):
    il2cpp = _il2cpp(tmp_path)
    bundles = _bundles(tmp_path, "a.bundle")

    with pytest.raises(StageError, match="DummyDll"):
        stage_input(
            il2cpp_dir=il2cpp, bundles_dir=bundles, out_dir=tmp_path / "out"
        )
    assert not (tmp_path / "out").exists()


def test_stage_input_rejects_missing_bundle_dir(tmp_path: Path):
    il2cpp = _il2cpp(tmp_path, "InnoGames.Game.dll")

    with pytest.raises(StageError, match="--assets"):
        stage_input(
            il2cpp_dir=il2cpp,
            bundles_dir=tmp_path / "assets",
            out_dir=tmp_path / "out",
        )


def test_stage_input_rejects_dir_without_bundles(tmp_path: Path):
    il2cpp = _il2cpp(tmp_path, "InnoGames.Game.dll")
    bundles = _bundles(tmp_path)

    with pytest.raises(StageError, match=r"\*\.bundle"):
        stage_input(il2cpp_dir=il2cpp, bundles_dir=bundles, out_dir=tmp_path / "out")


def test_run_parser_accepts_assetripper_flags(tmp_path: Path):
    args = build_parser().parse_args(
        [
            "run",
            "--from-wasm",
            "--version",
            "1.58.2",
            "--assetripper-input",
            "--assetripper-input-out",
            str(tmp_path / "ar"),
        ]
    )
    assert args.assetripper_input is True
    assert args.assetripper_input_out == tmp_path / "ar"
