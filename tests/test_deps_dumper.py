"""Tests for Il2CppDumper dependency URL/asset resolution (no network)."""

from __future__ import annotations

import zipfile
from pathlib import Path

from xapk_to_proto import deps
from xapk_to_proto.dumper import _failure_message, _filter_dumper_output


def test_dumper_url_defaults():
    url = deps.dumper_url()
    assert url == (
        "https://github.com/Windows81/Il2CppDumper/releases/download/"
        "v20260329T093452Z/Il2CppDumper-CLI-20260329T093452Z_0507132.zip"
    )


def test_dumper_url_explicit_overrides():
    url = deps.dumper_url(
        repo="acme/Il2CppDumper",
        version="v1.0.0",
        asset="tool.zip",
    )
    assert url == "https://github.com/acme/Il2CppDumper/releases/download/v1.0.0/tool.zip"


def test_dumper_url_env_overrides(monkeypatch):
    monkeypatch.setenv("IL2CPP_DUMPER_REPO", "roytu/Il2CppDumper")
    monkeypatch.setenv("IL2CPP_DUMPER_VERSION", "v39")
    monkeypatch.setenv("IL2CPP_DUMPER_ASSET", "Il2CppDumper-net6.zip")
    assert deps.dumper_url() == (
        "https://github.com/roytu/Il2CppDumper/releases/download/"
        "v39/Il2CppDumper-net6.zip"
    )


def test_find_dumper_dll_direct(tmp_path: Path):
    dll = tmp_path / "Il2CppDumper.dll"
    dll.write_bytes(b"MZ")
    assert deps.find_dumper_dll(tmp_path) == dll


def test_find_dumper_dll_nested(tmp_path: Path):
    nested = tmp_path / "cli" / "Il2CppDumper.dll"
    nested.parent.mkdir()
    nested.write_bytes(b"MZ")
    assert deps.find_dumper_dll(tmp_path) == nested


def test_find_dumper_dll_missing(tmp_path: Path):
    assert deps.find_dumper_dll(tmp_path) is None


def test_has_netcore_runtime(tmp_path: Path):
    assert deps.has_netcore_runtime(tmp_path, "9") is False
    runtime = tmp_path / "shared" / "Microsoft.NETCore.App" / "9.0.5"
    runtime.mkdir(parents=True)
    assert deps.has_netcore_runtime(tmp_path, "9") is True
    assert deps.has_netcore_runtime(tmp_path, "8") is False


def test_system_dotnet_with_runtime_uses_dotnet_root(tmp_path: Path, monkeypatch):
    root = tmp_path / "dotnet"
    exe = root / "dotnet"
    exe.parent.mkdir(parents=True)
    exe.write_text("", encoding="utf-8")
    (root / "shared" / "Microsoft.NETCore.App" / "9.0.1").mkdir(parents=True)

    monkeypatch.setenv("DOTNET_ROOT", str(root))
    monkeypatch.setattr(deps.shutil, "which", lambda _name: str(exe))

    assert deps._system_dotnet_with_runtime("9") == exe
    assert deps._system_dotnet_with_runtime("8") is None


def test_install_dotnet_prefers_system_over_bash_installer(
    tmp_path: Path, monkeypatch
):
    cache = tmp_path / "cache"
    monkeypatch.setattr(deps, "dotnet_cache_dir", lambda: cache)

    system = tmp_path / "system-dotnet"
    system.write_text("", encoding="utf-8")
    monkeypatch.setattr(deps, "_system_dotnet_with_runtime", lambda _major="9": system)

    def boom(*_args, **_kwargs):
        raise AssertionError("bash installer should not run when system dotnet exists")

    monkeypatch.setattr(deps, "urlretrieve", boom)

    assert deps.install_dotnet() == system


def test_install_dumper_extracts_from_zip(tmp_path: Path, monkeypatch):
    cache = tmp_path / "cache"
    monkeypatch.setattr(deps, "dumper_cache_dir", lambda: cache)

    def fake_urlretrieve(_url: str, filename: str | Path) -> None:
        path = Path(filename)
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("Il2CppDumper.dll", b"MZ")

    monkeypatch.setattr(deps, "urlretrieve", fake_urlretrieve)

    dll = deps.install_dumper()
    assert dll == cache / "Il2CppDumper.dll"
    assert dll.is_file()


def test_filter_dumper_output_drops_readkey_noise():
    raw = (
        "Initializing metadata...\n"
        "System.NotSupportedException: ERROR: Metadata file supplied is not a supported version[39].\n"
        "Press any key to exit...\n"
        "Unhandled exception. System.InvalidOperationException: Cannot read keys when either "
        "application does not have a console or when console input has been redirected.\n"
    )
    filtered = _filter_dumper_output(raw)
    assert "version[39]" in filtered
    assert "Press any key" not in filtered
    assert "Cannot read keys" not in filtered


def test_failure_message_highlights_unsupported_version():
    raw = (
        "System.NotSupportedException: ERROR: Metadata file supplied is not a supported version[39].\n"
        "Cannot read keys when either application does not have a console\n"
    )
    msg = _failure_message(raw)
    assert "metadata version 39" in msg
    assert "setup --force" in msg
    assert "Cannot read keys" not in msg
