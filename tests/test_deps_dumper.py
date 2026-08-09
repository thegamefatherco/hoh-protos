"""Tests for Il2CppDumper dependency URL/asset resolution (no network)."""

from __future__ import annotations

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
