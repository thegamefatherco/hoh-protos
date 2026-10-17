"""Unit tests for WebGL / UnityWebData helpers (no live CDN)."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from xapk_to_proto import wasm
from xapk_to_proto.paths import (
    GAME_CATALOG_NAME,
    GAME_DATA_NAME,
    GAME_METADATA_NAME,
    GAME_WASM_NAME,
)
from xapk_to_proto.xapk import METADATA_MAGIC


def _pack_unity_web_data(files: dict[str, bytes]) -> bytes:
    """Build a minimal UnityWebData1.0 archive for tests."""
    # Header: magic (16) + data_start (4) + per-file headers, then payloads.
    header = bytearray(wasm.UNITY_WEB_DATA_MAGIC)
    # Placeholder for data_start; fill after measuring headers.
    header.extend(b"\x00\x00\x00\x00")
    entries: list[tuple[str, bytes]] = list(files.items())
    file_headers = bytearray()
    for name, _payload in entries:
        name_b = name.encode("utf-8")
        # offset filled later
        file_headers.extend(struct.pack("<III", 0, len(_payload), len(name_b)))
        file_headers.extend(name_b)
    data_start = 20 + len(file_headers)
    struct.pack_into("<I", header, 16, data_start)

    # Rewrite offsets now that data_start is known.
    file_headers = bytearray()
    offset = data_start
    payloads = bytearray()
    for name, payload in entries:
        name_b = name.encode("utf-8")
        file_headers.extend(struct.pack("<III", offset, len(payload), len(name_b)))
        file_headers.extend(name_b)
        payloads.extend(payload)
        offset += len(payload)

    return bytes(header) + bytes(file_headers) + bytes(payloads)


def _fake_metadata(version: int = 39) -> bytes:
    # magic + version + padding
    return struct.pack("<II", METADATA_MAGIC, version) + b"\x00" * 64


def test_parse_and_extract_global_metadata(tmp_path: Path):
    meta = _fake_metadata()
    archive = _pack_unity_web_data(
        {
            "data.unity3d": b"assets",
            "Il2CppData/Metadata/global-metadata.dat": meta,
        }
    )
    data_path = tmp_path / GAME_DATA_NAME
    data_path.write_bytes(archive)
    dest = tmp_path / GAME_METADATA_NAME
    out = wasm.extract_global_metadata(data_path, dest)
    assert out == dest
    assert dest.read_bytes() == meta


def test_parse_rejects_bad_magic():
    with pytest.raises(ValueError, match="UnityWebData1.0"):
        wasm.parse_unity_web_data(b"not-unity-web-data")


def test_discover_build_urls_from_shell_text():
    html = """
    createUnityInstance(canvas, {
      dataUrl: "https://heczz.innogamescdn.com/Build/aabbccdd.data",
      codeUrl: "https://heczz.innogamescdn.com/Build/11223344.wasm",
    });
    """
    urls = wasm.discover_build_urls(html)
    assert urls.wasm_url.endswith("/Build/11223344.wasm")
    assert urls.data_url.endswith("/Build/aabbccdd.data")


def test_discover_build_urls_relative_to_cdn():
    text = 'codeUrl: "Build/deadbeef.wasm", dataUrl: "Build/cafebabe.data"'
    urls = wasm.discover_build_urls(
        text, cdn_root="https://heczz.innogamescdn.com/"
    )
    assert urls.wasm_url == "https://heczz.innogamescdn.com/Build/deadbeef.wasm"
    assert urls.data_url == "https://heczz.innogamescdn.com/Build/cafebabe.data"


def test_discover_build_urls_from_code_filename_constants():
    html = """
    const codeFileName = "7f5a3d28b1ea13dc25e84b0e8ac6ec17.wasm";
    const dataFileName = "931ed4b22ae84bf9e09578e647a8eb06.data";
    unity_config = { cdn_url: 'https://heczz.innogamescdn.com/' };
    """
    urls = wasm.discover_build_urls(html)
    assert (
        urls.wasm_url
        == "https://heczz.innogamescdn.com/Build/7f5a3d28b1ea13dc25e84b0e8ac6ec17.wasm"
    )
    assert (
        urls.data_url
        == "https://heczz.innogamescdn.com/Build/931ed4b22ae84bf9e09578e647a8eb06.data"
    )


def test_discover_build_urls_uses_cdn_url_from_unity_config():
    text = """
    unity_config = { cdn_url: 'https://cdn.example.test/' };
    const codeFileName = "aabb.wasm";
    const dataFileName = "ccdd.data";
    """
    urls = wasm.discover_build_urls(text)
    assert urls.wasm_url == "https://cdn.example.test/Build/aabb.wasm"
    assert urls.data_url == "https://cdn.example.test/Build/ccdd.data"


def test_discover_build_urls_error_lists_fetches():
    with pytest.raises(ValueError, match="--wasm-url") as exc:
        wasm.discover_build_urls(
            "no build artifacts here",
            fetches=(("https://zz0.example/config/unity.js", 278),),
        )
    assert "https://zz0.example/config/unity.js" in str(exc.value)
    assert "278 bytes" in str(exc.value)


def test_parse_cdn_url_from_text():
    js = "unity_config = { cdn_url: 'https://heczz.innogamescdn.com' };"
    assert wasm.parse_cdn_url_from_text(js) == "https://heczz.innogamescdn.com/"


def test_streaming_assets_catalog_url():
    assert wasm.streaming_assets_catalog_url("zz0") == (
        "https://zz0.heroesofhistorygame.com/StreamingAssets/aa/catalog.bin"
    )


def test_resolve_build_url_overrides_short_circuit():
    urls = wasm.resolve_build_url_overrides(
        wasm_url="aabb.wasm",
        data_url="ccdd.data",
        cdn_root="https://heczz.innogamescdn.com/",
    )
    assert urls is not None
    assert urls.wasm_url.endswith("/Build/aabb.wasm")
    assert urls.data_url.endswith("/Build/ccdd.data")
    assert wasm.resolve_build_url_overrides() is None
    with pytest.raises(ValueError, match="both --wasm-url and --data-url"):
        wasm.resolve_build_url_overrides(wasm_url="only.wasm")


def test_resolve_webgl_catalog_url_prefers_streaming_assets(monkeypatch):
    class _Sess:
        world = type("W", (), {"account_host": "zz0"})()

    monkeypatch.setattr(wasm, "_url_exists", lambda url: True)
    url = wasm.resolve_webgl_catalog_url(object(), _Sess())
    assert url.endswith("/StreamingAssets/aa/catalog.bin")


def test_resolve_webgl_catalog_url_falls_back_to_scrape(monkeypatch):
    class _Sess:
        world = type("W", (), {"account_host": "zz0"})()

    monkeypatch.setattr(wasm, "_url_exists", lambda url: False)
    url = wasm.resolve_webgl_catalog_url(
        object(),
        _Sess(),
        discovery_text="catalog_2026.10.13.13.25.20.bin",
    )
    assert url.endswith("catalog_2026.10.13.13.25.20.bin")


def test_discover_catalog_url_from_name():
    text = "catalog_2026.09.14.13.40.12.bin plus noise"
    url = wasm.discover_catalog_url(
        text, bundles_root="https://heczz.innogamescdn.com/bundles/WebGL/"
    )
    assert url.endswith("catalog_2026.09.14.13.40.12.bin")


def test_discover_catalog_url_from_settings_json(monkeypatch):
    settings = (
        '{"m_InternalId":"'
        '{REMOTE}bundles/WebGL/catalog_2026.01.02.03.04.05.hash"}'
    )

    def fake_get(url: str) -> bytes:
        assert "settings_2026.01.02.03.04.06.json" in url
        return settings.encode()

    monkeypatch.setattr(wasm, "_cdn_get", fake_get)
    text = "settings_2026.01.02.03.04.06.json"
    url = wasm.discover_catalog_url(
        text, bundles_root="https://heczz.innogamescdn.com/bundles/WebGL/"
    )
    assert url.endswith("catalog_2026.01.02.03.04.05.bin")


def test_discover_wasm_il2cpp_extracts_metadata(tmp_path: Path):
    meta = _fake_metadata()
    archive = _pack_unity_web_data(
        {"Il2CppData/Metadata/global-metadata.dat": meta}
    )
    (tmp_path / GAME_WASM_NAME).write_bytes(b"\x00asm")
    (tmp_path / GAME_DATA_NAME).write_bytes(archive)
    wasm_path, metadata = wasm.discover_wasm_il2cpp(tmp_path)
    assert wasm_path.name == GAME_WASM_NAME
    assert metadata.name == GAME_METADATA_NAME
    assert metadata.read_bytes() == meta


def test_download_webgl_build_skips_existing(tmp_path: Path, monkeypatch):
    (tmp_path / GAME_WASM_NAME).write_bytes(b"w")
    (tmp_path / GAME_DATA_NAME).write_bytes(b"d")
    (tmp_path / GAME_METADATA_NAME).write_bytes(_fake_metadata())

    def boom(url: str) -> bytes:
        raise AssertionError(f"should not download {url}")

    monkeypatch.setattr(wasm, "_cdn_get", boom)
    result = wasm.download_webgl_build(
        out_dir=tmp_path,
        wasm_url="https://example.test/Build/a.wasm",
        data_url="https://example.test/Build/a.data",
        force=False,
    )
    assert result.skipped is True


def test_download_webgl_catalog_writes_normalized_name(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(wasm, "_cdn_get", lambda url: b"catalog-bytes")
    result = wasm.download_webgl_catalog(
        out_dir=tmp_path,
        catalog_url="https://cdn/bundles/WebGL/catalog_2026.09.14.13.40.12.bin",
        force=True,
    )
    assert result.path.name == GAME_CATALOG_NAME
    assert result.path.read_bytes() == b"catalog-bytes"
    assert result.skipped is False
