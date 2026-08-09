"""Tests for APKPure release resolution and output-path handling (no network)."""

from __future__ import annotations

import pytest

from xapk_to_proto import apkpure
from xapk_to_proto.apkpure import (
    DEFAULT_ABI,
    DEFAULT_PACKAGE,
    Release,
    download_url,
    page_url,
    resolve_output_path,
    resolve_release,
)

# Trimmed shape of a real download page: the versionCode appears several times
# and the version name comes from an inline JSON blob.
SAMPLE_PAGE = """\
<html><head><title>Download</title></head><body>
<script>window.__NUXT__={"app":{"version":"1.49.8","package":"x"}}</script>
<a href="https://d.apkpure.com/b/XAPK/com.innogames.heroesofhistory?versionCode=1049008&amp;nc=arm64-v8a&amp;sv=25">Download</a>
<a href="https://d.apkpure.com/b/XAPK/com.innogames.heroesofhistory?versionCode=1049008&amp;nc=armeabi-v7a&amp;sv=25">Download v7a</a>
</body></html>
"""

CHALLENGE_PAGE = "<html><body>Checking your browser…</body></html>"


def _release(version: str = "1.49.8", code: int = 1049008) -> Release:
    return Release(
        package=DEFAULT_PACKAGE,
        version=version,
        version_code=code,
        page_url="https://example.invalid",
    )


def test_page_url_includes_version_only_when_given():
    assert page_url(DEFAULT_PACKAGE, None).endswith(f"/{DEFAULT_PACKAGE}/download")
    assert page_url(DEFAULT_PACKAGE, "1.49.8").endswith("/download/1.49.8")


def test_download_url_uses_version_code_not_version_name():
    # ?version=<name> redirects to the site root, so only versionCode is usable.
    url = download_url(_release(), abi=DEFAULT_ABI)
    assert "versionCode=1049008" in url
    assert "version=1.49.8" not in url
    assert f"nc={DEFAULT_ABI}" in url


def test_resolve_release_scrapes_version_and_code(monkeypatch):
    monkeypatch.setattr(apkpure, "_fetch_text", lambda url: SAMPLE_PAGE)
    release = resolve_release(version="1.49.8")
    assert release.version == "1.49.8"
    assert release.version_code == 1049008


def test_resolve_release_latest_reports_resolved_version(monkeypatch):
    monkeypatch.setattr(apkpure, "_fetch_text", lambda url: SAMPLE_PAGE)
    release = resolve_release()
    assert release.version == "1.49.8"


def test_resolve_release_rejects_page_without_version_code(monkeypatch):
    monkeypatch.setattr(apkpure, "_fetch_text", lambda url: CHALLENGE_PAGE)
    with pytest.raises(RuntimeError, match="no versionCode"):
        resolve_release(version="0.0.1")


def test_resolve_release_picks_most_common_code(monkeypatch):
    page = SAMPLE_PAGE + "\n<a href='?versionCode=999'>other version</a>"
    monkeypatch.setattr(apkpure, "_fetch_text", lambda url: page)
    assert resolve_release().version_code == 1049008


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
