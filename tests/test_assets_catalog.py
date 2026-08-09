"""Tests for Addressables catalog parsing and asset-bundle selection (no network)."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from urllib.error import HTTPError

import pytest

from xapk_to_proto import assets
from xapk_to_proto.assets import (
    CATALOG_MEMBER,
    CDN_ROOT,
    DEFAULT_SKIP,
    AssetsResult,
    DownloadError,
    bundle_url,
    download_assets,
    find_catalog,
    parse_catalog,
    read_catalog,
    read_catalog_from_xapk,
    select_bundles,
    write_manifest,
)

_START = bytes.fromhex("000000")


def make_catalog(names: list[str], *, noise: bytes = b"\x07\x11") -> bytes:
    """Build a catalog-shaped blob: length prefix, name, then the .bundle suffix."""
    out = bytearray()
    for name in names:
        out += noise + _START + name.removesuffix(".bundle").encode() + b".bundle"
    return bytes(out)


def test_parse_catalog_extracts_names_in_order():
    names = ["alpha_aaa.bundle", "beta_bbb.bundle", "gamma_ccc.bundle"]
    assert parse_catalog(make_catalog(names)) == names


def test_parse_catalog_deduplicates_repeated_names():
    data = make_catalog(["dup_a.bundle", "other.bundle", "dup_a.bundle"])
    assert parse_catalog(data) == ["dup_a.bundle", "other.bundle"]


def test_parse_catalog_ignores_bundle_without_start_marker():
    assert parse_catalog(b"no-marker-here.bundle") == []


def test_parse_catalog_on_empty_input():
    assert parse_catalog(b"") == []


def test_select_bundles_skips_default_prefixes():
    names = [
        "vfx_explosion.bundle",
        "pfx_smoke.bundle",
        "hero_cleopatra.bundle",
        "ui_panel.bundle",
    ]
    assert select_bundles(names) == ["hero_cleopatra.bundle", "ui_panel.bundle"]


def test_select_bundles_only_takes_precedence_over_skip():
    names = ["vfx_cleopatra.bundle", "hero_cleopatra.bundle", "ui_panel.bundle"]
    # A vfx bundle is kept when it matches --only, even though vfx is skipped.
    assert select_bundles(names, only=("cleopatra",)) == [
        "vfx_cleopatra.bundle",
        "hero_cleopatra.bundle",
    ]


def test_select_bundles_only_matches_substring_anywhere():
    names = ["a_hero_x.bundle", "unrelated.bundle"]
    assert select_bundles(names, only=("hero",)) == ["a_hero_x.bundle"]


def test_select_bundles_custom_skip_replaces_default():
    names = ["vfx_a.bundle", "ui_b.bundle"]
    assert select_bundles(names, skip=("ui",)) == ["vfx_a.bundle"]


def test_select_bundles_empty_skip_keeps_everything():
    names = ["vfx_a.bundle", "ui_b.bundle"]
    assert select_bundles(names, skip=()) == names


def test_select_bundles_filters_on_basename_not_path():
    names = ["Android/vfx_a.bundle", "Android/ui_b.bundle"]
    assert select_bundles(names) == ["Android/ui_b.bundle"]


def test_bundle_url_joins_cdn_root():
    assert bundle_url("a.bundle") == f"{CDN_ROOT}a.bundle"
    assert bundle_url("a.bundle", "https://x.test/y/") == "https://x.test/y/a.bundle"


def _write_xapk(path: Path, catalog: bytes, *, inner_name: str) -> None:
    """Write an XAPK whose inner APK is stored uncompressed, as real ones are."""
    inner_buf = io.BytesIO()
    with zipfile.ZipFile(inner_buf, "w") as inner:
        inner.writestr(CATALOG_MEMBER, catalog)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as outer:
        outer.writestr(inner_name, inner_buf.getvalue())


def test_read_catalog_from_xapk_reads_nested_member(tmp_path):
    catalog = make_catalog(["hero_a.bundle"])
    xapk = tmp_path / "game.xapk"
    _write_xapk(xapk, catalog, inner_name="AddressablesAssetPack.apk")
    assert read_catalog_from_xapk(xapk) == catalog


def test_read_catalog_from_xapk_skips_apks_without_catalog(tmp_path):
    catalog = make_catalog(["hero_a.bundle"])
    inner_buf = io.BytesIO()
    with zipfile.ZipFile(inner_buf, "w") as inner:
        inner.writestr(CATALOG_MEMBER, catalog)
    other_buf = io.BytesIO()
    with zipfile.ZipFile(other_buf, "w") as other:
        other.writestr("assets/bin/Data/unrelated", b"x")

    xapk = tmp_path / "game.xapk"
    with zipfile.ZipFile(xapk, "w", compression=zipfile.ZIP_STORED) as outer:
        outer.writestr("base.apk", other_buf.getvalue())
        outer.writestr("AddressablesAssetPack.apk", inner_buf.getvalue())
    assert read_catalog_from_xapk(xapk) == catalog


def test_read_catalog_from_xapk_without_catalog_raises(tmp_path):
    xapk = tmp_path / "game.xapk"
    inner_buf = io.BytesIO()
    with zipfile.ZipFile(inner_buf, "w") as inner:
        inner.writestr("assets/other.txt", b"x")
    with zipfile.ZipFile(xapk, "w", compression=zipfile.ZIP_STORED) as outer:
        outer.writestr("base.apk", inner_buf.getvalue())
    with pytest.raises(FileNotFoundError, match=CATALOG_MEMBER):
        read_catalog_from_xapk(xapk)


def test_read_catalog_prefers_explicit_file(tmp_path):
    catalog = make_catalog(["hero_a.bundle"])
    path = tmp_path / "catalog.bin"
    path.write_bytes(catalog)
    data, source = read_catalog(path, None)
    assert data == catalog
    assert source == str(path.resolve())


def test_read_catalog_from_xapk_source_names_the_member(tmp_path):
    xapk = tmp_path / "game.xapk"
    _write_xapk(xapk, make_catalog(["a.bundle"]), inner_name="Addressables.apk")
    _, source = read_catalog(None, xapk)
    assert source.endswith(f"game.xapk!{CATALOG_MEMBER}")


def test_read_catalog_requires_a_source():
    with pytest.raises(ValueError, match="either a catalog file or an xapk"):
        read_catalog(None, None)


def test_read_catalog_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="catalog not found"):
        read_catalog(tmp_path / "absent.bin", None)


def test_find_catalog_locates_extracted_catalog(tmp_path):
    nested = tmp_path / "AddressablesAssetPack" / "assets" / "aa"
    nested.mkdir(parents=True)
    (nested / "catalog.bin").write_bytes(b"x")
    assert find_catalog(tmp_path) == nested / "catalog.bin"


def test_find_catalog_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match=CATALOG_MEMBER):
        find_catalog(tmp_path)


def test_download_assets_dry_run_lists_urls_without_touching_disk(tmp_path, capsys):
    catalog = tmp_path / "catalog.bin"
    catalog.write_bytes(make_catalog(["hero_a.bundle", "vfx_b.bundle"]))
    out_dir = tmp_path / "assets"

    result = download_assets(catalog=catalog, out_dir=out_dir, dry_run=True)

    assert result.selected == 1
    assert result.total_bundles == 2
    assert result.downloaded == 0
    assert not out_dir.exists()
    assert capsys.readouterr().out.strip() == bundle_url("hero_a.bundle")


def test_download_assets_skips_existing_files(tmp_path):
    catalog = tmp_path / "catalog.bin"
    catalog.write_bytes(make_catalog(["hero_a.bundle"]))
    out_dir = tmp_path / "assets"
    out_dir.mkdir()
    (out_dir / "hero_a.bundle").write_bytes(b"already here")

    # No network access is needed because the only selected bundle is present.
    result = download_assets(catalog=catalog, out_dir=out_dir)

    assert result.skipped_existing == 1
    assert result.downloaded == 0
    assert result.failed == []


def test_download_assets_treats_empty_file_as_missing(tmp_path, monkeypatch):
    catalog = tmp_path / "catalog.bin"
    catalog.write_bytes(make_catalog(["hero_a.bundle"]))
    out_dir = tmp_path / "assets"
    out_dir.mkdir()
    (out_dir / "hero_a.bundle").touch()

    calls: list[str] = []

    def fake_download(url: str, dest: Path) -> int:
        calls.append(url)
        dest.write_bytes(b"data")
        return 4

    monkeypatch.setattr(assets, "_download_one", fake_download)
    result = download_assets(catalog=catalog, out_dir=out_dir)

    assert calls == [bundle_url("hero_a.bundle")]
    assert result.downloaded == 1
    assert result.skipped_existing == 0


def test_download_assets_empty_catalog_raises(tmp_path):
    catalog = tmp_path / "catalog.bin"
    catalog.write_bytes(b"nothing useful")
    with pytest.raises(ValueError, match="no bundle names"):
        download_assets(catalog=catalog, out_dir=tmp_path / "assets")


def test_download_assets_warns_when_filters_match_nothing(tmp_path):
    catalog = tmp_path / "catalog.bin"
    catalog.write_bytes(make_catalog(["hero_a.bundle"]))
    result = download_assets(
        catalog=catalog,
        out_dir=tmp_path / "assets",
        only=("nomatch",),
    )
    assert result.selected == 0
    assert "filters matched no bundles" in result.warnings


def test_download_assets_clean_removes_previous_output(tmp_path, monkeypatch):
    catalog = tmp_path / "catalog.bin"
    catalog.write_bytes(make_catalog(["hero_a.bundle"]))
    out_dir = tmp_path / "assets"
    out_dir.mkdir()
    stale = out_dir / "stale.bundle"
    stale.write_bytes(b"old")

    monkeypatch.setattr(
        assets, "_download_one", lambda url, dest: dest.write_bytes(b"new")
    )
    download_assets(catalog=catalog, out_dir=out_dir, clean=True)

    assert not stale.exists()
    assert (out_dir / "hero_a.bundle").read_bytes() == b"new"


def test_download_assets_records_permanent_failures(tmp_path, monkeypatch):
    catalog = tmp_path / "catalog.bin"
    catalog.write_bytes(make_catalog(["gone_a.bundle", "ok_b.bundle"]))
    attempts: list[str] = []

    def fake_download(url: str, dest: Path) -> int:
        attempts.append(url)
        if "gone_a" in url:
            raise DownloadError(f"{url}: HTTP 404", retryable=False)
        dest.write_bytes(b"data")
        return 4

    monkeypatch.setattr(assets, "_download_one", fake_download)
    result = download_assets(catalog=catalog, out_dir=tmp_path / "assets", retries=3)

    assert result.downloaded == 1
    assert result.failed == ["gone_a.bundle"]
    # A 404 is permanent, so it must not be attempted again.
    assert sum("gone_a" in url for url in attempts) == 1
    assert "HTTP 404 (1)" in result.warnings[0]


def test_download_assets_retries_transient_failures(tmp_path, monkeypatch):
    catalog = tmp_path / "catalog.bin"
    catalog.write_bytes(make_catalog(["flaky_a.bundle"]))
    attempts: list[str] = []

    def fake_download(url: str, dest: Path) -> int:
        attempts.append(url)
        if len(attempts) == 1:
            raise DownloadError(f"{url}: HTTP 503", retryable=True)
        dest.write_bytes(b"data")
        return 4

    monkeypatch.setattr(assets, "_download_one", fake_download)
    result = download_assets(catalog=catalog, out_dir=tmp_path / "assets", retries=2)

    assert len(attempts) == 2
    assert result.downloaded == 1
    assert result.failed == []


def test_download_assets_gives_up_after_retries(tmp_path, monkeypatch):
    catalog = tmp_path / "catalog.bin"
    catalog.write_bytes(make_catalog(["flaky_a.bundle"]))
    attempts: list[str] = []

    def fake_download(url: str, dest: Path) -> int:
        attempts.append(url)
        raise DownloadError(f"{url}: HTTP 503", retryable=True)

    monkeypatch.setattr(assets, "_download_one", fake_download)
    result = download_assets(catalog=catalog, out_dir=tmp_path / "assets", retries=3)

    assert len(attempts) == 3
    assert result.failed == ["flaky_a.bundle"]


class _FakeResponse:
    def __init__(self, body: bytes, headers: dict[str, str]) -> None:
        self._stream = io.BytesIO(body)
        self.headers = headers

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def test_download_one_writes_body_and_removes_temp_file(tmp_path, monkeypatch):
    body = b"bundle-bytes"
    monkeypatch.setattr(
        assets,
        "urlopen",
        lambda req: _FakeResponse(body, {"Content-Length": str(len(body))}),
    )
    dest = tmp_path / "a.bundle"
    assert assets._download_one("https://x.test/a.bundle", dest) == len(body)
    assert dest.read_bytes() == body
    assert not dest.with_name("a.bundle.part").exists()


def test_download_one_rejects_truncated_body(tmp_path, monkeypatch):
    monkeypatch.setattr(
        assets,
        "urlopen",
        lambda req: _FakeResponse(b"short", {"Content-Length": "999"}),
    )
    dest = tmp_path / "a.bundle"
    with pytest.raises(DownloadError, match="truncated") as excinfo:
        assets._download_one("https://x.test/a.bundle", dest)
    assert excinfo.value.retryable is True
    # A partial download must never be left behind as a complete-looking file.
    assert not dest.exists()
    assert not dest.with_name("a.bundle.part").exists()


@pytest.mark.parametrize(
    ("status", "retryable"),
    [(404, False), (403, False), (410, False), (429, True), (503, True), (500, True)],
)
def test_download_one_classifies_http_errors(tmp_path, monkeypatch, status, retryable):
    def raise_http(req):
        raise HTTPError("https://x.test/a.bundle", status, "err", {}, None)

    monkeypatch.setattr(assets, "urlopen", raise_http)
    with pytest.raises(DownloadError) as excinfo:
        assets._download_one("https://x.test/a.bundle", tmp_path / "a.bundle")
    assert excinfo.value.retryable is retryable
    assert f"HTTP {status}" in str(excinfo.value)


def test_download_one_decompresses_gzip_response(tmp_path, monkeypatch):
    import gzip as gzip_mod

    payload = b"bundle-bytes"
    compressed = gzip_mod.compress(payload)
    monkeypatch.setattr(
        assets,
        "urlopen",
        lambda req: _FakeResponse(compressed, {"Content-Encoding": "gzip"}),
    )
    dest = tmp_path / "a.bundle"
    assets._download_one("https://x.test/a.bundle", dest)
    assert dest.read_bytes() == payload


def test_write_manifest_records_run_summary(tmp_path):
    result = AssetsResult(
        catalog_source="catalog.bin",
        cdn_root=CDN_ROOT,
        out_dir=tmp_path,
        total_bundles=10,
        selected=4,
        downloaded=3,
        skipped_existing=1,
        failed=["a.bundle"],
        warnings=["1 bundle(s) failed: HTTP 404 (1)"],
    )
    path = write_manifest(result)
    manifest = json.loads(path.read_text())
    assert manifest["total_bundles"] == 10
    assert manifest["downloaded"] == 3
    assert manifest["failed"] == ["a.bundle"]
    assert manifest["cdn_root"] == CDN_ROOT


def test_run_assets_download_skips_manifest_on_dry_run(tmp_path):
    catalog = tmp_path / "catalog.bin"
    catalog.write_bytes(make_catalog(["hero_a.bundle"]))
    out_dir = tmp_path / "assets"
    assets.run_assets_download(catalog=catalog, out_dir=out_dir, dry_run=True)
    assert not (out_dir / "manifest.json").exists()


def test_default_skip_covers_effect_bundles():
    assert DEFAULT_SKIP == ("vfx", "pfx")
