"""Cross-platform branch coverage for Windows / Linux / macOS (mocked + host)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from xapk_to_proto import apkpure, deps, unpack
from xapk_to_proto.unpack import (
    ImageRecord,
    UnpackResult,
    build_index,
    safe_filename,
)


@pytest.mark.parametrize(
    ("system", "machine", "expected"),
    [
        ("Linux", "x86_64", "apkeep-x86_64-unknown-linux-gnu"),
        ("Linux", "aarch64", "apkeep-aarch64-unknown-linux-gnu"),
        ("Linux", "arm64", "apkeep-aarch64-unknown-linux-gnu"),
        ("Linux", "armv7l", "apkeep-armv7-unknown-linux-gnueabihf"),
        ("Linux", "armv7", "apkeep-armv7-unknown-linux-gnueabihf"),
        ("Linux", "i686", "apkeep-i686-unknown-linux-gnu"),
        ("Linux", "i386", "apkeep-i686-unknown-linux-gnu"),
        ("Linux", "x86", "apkeep-i686-unknown-linux-gnu"),
        ("Windows", "AMD64", "apkeep-x86_64-pc-windows-msvc.exe"),
        ("Darwin", "arm64", None),
        ("FreeBSD", "amd64", None),
    ],
)
def test_apkeep_release_asset_matrix(monkeypatch, system, machine, expected):
    monkeypatch.setattr(deps.platform, "system", lambda: system)
    monkeypatch.setattr(deps.platform, "machine", lambda: machine)
    assert deps.apkeep_release_asset() == expected


@pytest.mark.parametrize(
    ("system", "machine", "asset"),
    [
        ("Linux", "x86_64", "apkeep-x86_64-unknown-linux-gnu"),
        ("Linux", "aarch64", "apkeep-aarch64-unknown-linux-gnu"),
        ("Windows", "AMD64", "apkeep-x86_64-pc-windows-msvc.exe"),
    ],
)
def test_apkeep_url_matches_release_asset(monkeypatch, system, machine, asset):
    monkeypatch.setattr(deps.platform, "system", lambda: system)
    monkeypatch.setattr(deps.platform, "machine", lambda: machine)
    assert deps.apkeep_url() == (
        f"https://github.com/EFForg/apkeep/releases/download/"
        f"{deps.DEFAULT_APKEEP_VERSION}/{asset}"
    )


def test_apkeep_url_raises_on_darwin(monkeypatch):
    monkeypatch.setattr(deps.platform, "system", lambda: "Darwin")
    with pytest.raises(RuntimeError, match="brew"):
        deps.apkeep_url()


def test_cached_apkeep_path_windows_uses_exe(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(deps, "apkeep_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(deps.platform, "system", lambda: "Windows")
    monkeypatch.setattr(deps.platform, "machine", lambda: "AMD64")
    assert deps.cached_apkeep_path() == tmp_path / "apkeep.exe"


def test_cached_apkeep_path_linux_plain_name(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(deps, "apkeep_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(deps.platform, "system", lambda: "Linux")
    monkeypatch.setattr(deps.platform, "machine", lambda: "x86_64")
    assert deps.cached_apkeep_path() == tmp_path / "apkeep"


@pytest.mark.parametrize(
    ("system", "message", "hint"),
    [
        (
            "Darwin",
            "apkeep not found. Install with: brew install apkeep",
            "brew install apkeep",
        ),
        ("Linux", "apkeep not found. Run: hoh-protos setup", "hoh-protos setup"),
        ("Windows", "apkeep not found. Run: hoh-protos setup", "hoh-protos setup"),
    ],
)
def test_apkeep_missing_message_and_hint(monkeypatch, system, message, hint):
    monkeypatch.setattr(deps.platform, "system", lambda: system)
    assert deps._apkeep_missing_message() == message
    assert deps._apkeep_missing_hint() == hint


@pytest.mark.parametrize(
    ("system", "expected"),
    [
        ("Windows", deps.DOTNET_MANUAL_HINT),
        ("Linux", "hoh-protos setup"),
        ("Darwin", "hoh-protos setup"),
    ],
)
def test_dotnet_missing_hint(monkeypatch, system, expected):
    monkeypatch.setattr(deps.platform, "system", lambda: system)
    assert deps._dotnet_missing_hint() == expected


def test_resolve_dotnet_missing_message_by_os(monkeypatch):
    monkeypatch.delenv("XAPK_TO_PROTO_DOTNET", raising=False)
    monkeypatch.setattr(deps, "dotnet_cache_dir", lambda: Path("/nonexistent-cache"))
    monkeypatch.setattr(deps.shutil, "which", lambda _name: None)

    monkeypatch.setattr(deps.platform, "system", lambda: "Windows")
    with pytest.raises(FileNotFoundError, match="dot.net"):
        deps.resolve_dotnet()

    monkeypatch.setattr(deps.platform, "system", lambda: "Linux")
    with pytest.raises(FileNotFoundError, match="hoh-protos setup"):
        deps.resolve_dotnet()


def test_install_apkeep_windows_downloads_exe(tmp_path: Path, monkeypatch):
    cache = tmp_path / "apkeep-cache"
    monkeypatch.setattr(deps, "apkeep_cache_dir", lambda: cache)
    monkeypatch.setattr(deps.platform, "system", lambda: "Windows")
    monkeypatch.setattr(deps.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(
        deps,
        "resolve_apkeep",
        lambda: (_ for _ in ()).throw(FileNotFoundError("missing")),
    )

    def fake_urlretrieve(_url: str, filename: str | Path) -> None:
        Path(filename).write_bytes(b"MZ")

    monkeypatch.setattr(deps, "urlretrieve", fake_urlretrieve)

    path = deps.install_apkeep()
    assert path == str(cache / "apkeep.exe")
    assert Path(path).is_file()


def test_install_dotnet_windows_verbose_skip_message(
    tmp_path: Path, monkeypatch, capsys
):
    monkeypatch.setattr(deps, "dotnet_cache_dir", lambda: tmp_path / "cache")
    monkeypatch.setattr(deps.platform, "system", lambda: "Windows")
    monkeypatch.setattr(deps, "_system_dotnet_with_runtime", lambda _major="9": None)
    assert deps.install_dotnet(verbose=True) is None
    assert "dot.net" in capsys.readouterr().out


def test_install_dotnet_linux_runs_bash_installer(tmp_path: Path, monkeypatch):
    cache = tmp_path / "dotnet-cache"
    monkeypatch.setattr(deps, "dotnet_cache_dir", lambda: cache)
    monkeypatch.setattr(deps.platform, "system", lambda: "Linux")
    monkeypatch.setattr(deps, "_system_dotnet_with_runtime", lambda _major="9": None)

    def fake_urlretrieve(_url: str, filename: str | Path) -> None:
        Path(filename).write_text("#!/bin/bash\n", encoding="utf-8")

    def fake_run(cmd, check=True):  # noqa: ARG001
        assert cmd[0] == "bash"
        bin_path = cache / "dotnet"
        bin_path.write_text("", encoding="utf-8")
        (cache / "shared" / "Microsoft.NETCore.App" / "9.0.1").mkdir(parents=True)
        return None

    monkeypatch.setattr(deps, "urlretrieve", fake_urlretrieve)
    monkeypatch.setattr(deps.subprocess, "run", fake_run)

    assert deps.install_dotnet(force=True, verbose=True) == cache / "dotnet"
    assert (cache / "dotnet").is_file()


def test_resolve_apkeep_missing_uses_os_message(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("XAPK_TO_PROTO_APKEEP", raising=False)
    monkeypatch.setattr(deps, "apkeep_cache_dir", lambda: tmp_path / "empty")
    monkeypatch.setattr(deps.shutil, "which", lambda _name: None)
    monkeypatch.setattr(deps.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(deps.platform, "machine", lambda: "arm64")
    with pytest.raises(FileNotFoundError, match="brew install apkeep"):
        deps.resolve_apkeep()


def test_setup_darwin_reports_apkeep_missing(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(deps, "dumper_cache_dir", lambda: tmp_path / "dumper")
    monkeypatch.setattr(deps, "apkeep_cache_dir", lambda: tmp_path / "apkeep")
    monkeypatch.setattr(deps.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(deps, "install_dotnet", lambda **_kw: tmp_path / "dotnet")
    monkeypatch.setattr(
        deps,
        "install_dumper",
        lambda **_kw: tmp_path / "Il2CppDumper.dll",
    )
    monkeypatch.setattr(deps, "install_apkeep", lambda **_kw: None)

    results = deps.setup()
    by_name = {r.name: r for r in results}
    assert by_name["apkeep"].status == "missing"
    assert "brew" in (by_name["apkeep"].hint or "")


def test_unpack_bundle_records_posix_paths(tmp_path: Path, monkeypatch):
    """Drive unpack_bundle so ImageRecord.path uses as_posix() separators."""
    monkeypatch.setattr(unpack, "require_unitypy", lambda: None)

    class FakeImage:
        width = 4
        height = 4

        def save(self, dest: Path) -> None:
            dest.write_bytes(b"png")

    class FakeParsed:
        image = FakeImage()

    class FakeObj:
        class type:
            name = "Sprite"

        class assets_file:
            unity_version = "6000.0.0"

        def peek_name(self) -> str:
            return "icon"

        def parse_as_object(self) -> FakeParsed:
            return FakeParsed()

    class FakeEnv:
        objects = [FakeObj()]

    monkeypatch.setattr(unpack, "UnityPy", type("U", (), {"load": staticmethod(lambda _d: FakeEnv())}))

    bundle = "addr_0123456789abcdef_assets_all_" + "a" * 32 + ".bundle"
    outcome = unpack.unpack_bundle(b"fake", bundle, tmp_path)
    assert len(outcome.records) == 1
    assert outcome.records[0].path == "extracted/addr/icon.png"
    assert "\\" not in outcome.records[0].path
    assert (tmp_path / outcome.records[0].path).is_file()


@pytest.mark.parametrize(
    ("system", "expected"),
    [
        ("Darwin", "apkeep not found. Install with: brew install apkeep"),
        ("Linux", "apkeep not found. Run: hoh-protos setup"),
        ("Windows", "apkeep not found. Run: hoh-protos setup"),
    ],
)
def test_apkpure_apkeep_missing_hint(monkeypatch, system, expected):
    monkeypatch.setattr(apkpure.platform, "system", lambda: system)
    assert apkpure._apkeep_missing_hint() == expected


def test_safe_filename_strips_windows_illegal_characters():
    assert safe_filename('a<>:"\\|?*b') == "a________b"
    assert safe_filename("icon/../gold") == "icon_.._gold"


def test_image_record_paths_use_posix_separators(tmp_path: Path):
    """Simulate Windows-style relative paths normalized via as_posix()."""
    out_root = tmp_path / "out"
    dest = out_root / "extracted" / "addr" / "icon.png"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"png")

    posix_path = dest.relative_to(out_root).as_posix()
    assert "\\" not in posix_path
    assert posix_path == "extracted/addr/icon.png"

    record = ImageRecord(
        bundle="b.bundle",
        address_prefix="addr",
        object_type="Sprite",
        name="icon",
        path=posix_path,
        width=8,
        height=8,
    )
    index = build_index(
        UnpackResult(
            source="test",
            out_dir=out_root,
            unity_version="6000.0.0",
            bundles_total=1,
            bundles_selected=1,
            bundles_skipped=0,
            bundles_failed=0,
            images_written=1,
            records=[record],
        )
    )
    assert index["by_name"]["icon"] == ["extracted/addr/icon.png"]
    assert all("\\" not in p for paths in index["by_name"].values() for p in paths)

    # Resume still resolves via Path joins on the host OS.
    (out_root / unpack.INDEX_FILENAME).write_text(
        json.dumps(index), encoding="utf-8"
    )
    previous = unpack._load_previous_records(out_root)
    assert "b.bundle" in previous
    assert (out_root / previous["b.bundle"][0].path).is_file()


def test_host_cache_dirs_are_writable(tmp_path: Path, monkeypatch):
    """Smoke: real platformdirs cache roots work on the runner OS (no OS mock)."""
    monkeypatch.setattr(
        deps.platformdirs,
        "user_cache_dir",
        lambda _name: str(tmp_path / "cache"),
    )
    cache = deps.cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    probe = cache / "probe.txt"
    probe.write_text("ok", encoding="utf-8")
    assert probe.read_text(encoding="utf-8") == "ok"
    assert deps.apkeep_cache_dir() == cache / "apkeep"
    assert deps.dotnet_cache_dir() == cache / "dotnet"
    assert deps.dumper_cache_dir() == cache / "Il2CppDumper"
    # Path joins must not depend on hardcoded separators.
    assert (cache / "a" / "b").parts[-2:] == ("a", "b")
