"""Download game XAPKs from APKPure.

APKPure exposes a direct-download redirector at ``d.apkpure.com/b/XAPK/{package}``.
Its ``?version=`` parameter only accepts ``latest``; any real version string
redirects to the site root. Specific versions therefore require the numeric
``versionCode``, which is only published inside the HTML download page, so a
release is always resolved by scraping that page first.
"""

from __future__ import annotations

import re
import sys
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_PACKAGE = "com.innogames.heroesofhistory"
DEFAULT_SLUG = "heroes-of-history-epic-empire"
DEFAULT_ABI = "arm64-v8a"
DEFAULT_SV = "25"
PAGE_ROOT = "https://apkpure.com"
DOWNLOAD_ROOT = "https://d.apkpure.com/b/XAPK"

# APKPure serves an interstitial to non-browser agents.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_CHUNK = 1 << 20
_VERSION_CODE_RE = re.compile(r"versionCode=(\d+)")
_VERSION_NAME_RE = re.compile(r'"version"\s*:\s*"([^"]+)"')


@dataclass
class Release:
    package: str
    version: str
    version_code: int
    page_url: str


@dataclass
class DownloadResult:
    release: Release
    path: Path
    url: str
    size: int
    skipped: bool = False


def page_url(package: str, version: str | None, slug: str = DEFAULT_SLUG) -> str:
    """URL of the APKPure download page.

    The slug is cosmetic — APKPure resolves the page from the package name alone —
    but a real one is used so the URL stays recognizable in logs.
    """
    suffix = f"/{version}" if version else ""
    return f"{PAGE_ROOT}/{slug}/{package}/download{suffix}"


def download_url(
    release: Release,
    *,
    abi: str = DEFAULT_ABI,
    sv: str = DEFAULT_SV,
) -> str:
    return (
        f"{DOWNLOAD_ROOT}/{release.package}"
        f"?versionCode={release.version_code}&nc={abi}&sv={sv}"
    )


def _fetch_text(url: str) -> str:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError) as e:
        raise RuntimeError(f"failed to fetch {url}: {e}") from e


def resolve_release(
    *,
    package: str = DEFAULT_PACKAGE,
    version: str | None = None,
    slug: str = DEFAULT_SLUG,
) -> Release:
    """Scrape the APKPure download page for a version name and versionCode."""
    url = page_url(package, version, slug)
    html = _fetch_text(url)

    codes = Counter(int(m) for m in _VERSION_CODE_RE.findall(html))
    if not codes:
        raise RuntimeError(
            f"no versionCode found on {url} — the version may not exist, "
            "or APKPure is serving a challenge page"
        )

    name_match = _VERSION_NAME_RE.search(html)
    resolved_name = name_match.group(1) if name_match else (version or "unknown")

    return Release(
        package=package,
        version=resolved_name,
        version_code=codes.most_common(1)[0][0],
        page_url=url,
    )


def resolve_output_path(output: Path | None, release: Release) -> Path:
    """Resolve ``-o`` into a concrete file path.

    Only a path ending in ``.xapk`` is treated as a filename; anything else is a
    directory that receives ``{package}_{version}.xapk``. A trailing separator
    cannot be used to signal a directory because ``Path`` normalizes it away.
    """
    default_name = f"{release.package}_{release.version}.xapk"
    if output is None:
        return Path(default_name).resolve()
    if output.suffix.lower() == ".xapk" and not output.is_dir():
        return output.resolve()
    return (output / default_name).resolve()


def _format_mb(size: int) -> str:
    return f"{size / (1 << 20):.1f} MiB"


def _progress(done: int, total: int, *, tty: bool) -> None:
    if total > 0:
        line = f"  {_format_mb(done)} / {_format_mb(total)} ({done * 100 // total}%)"
    else:
        line = f"  {_format_mb(done)}"
    if tty:
        print(f"\r{line}", end="", flush=True)
    else:
        print(line, flush=True)


def _stream_to_file(url: str, part: Path) -> int:
    """Stream ``url`` into ``part``, resuming from its current size if possible."""
    resume_from = part.stat().st_size if part.is_file() else 0
    headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    if resume_from:
        headers["Range"] = f"bytes={resume_from}-"

    req = Request(url, headers=headers)
    try:
        resp = urlopen(req)
    except (HTTPError, URLError) as e:
        raise RuntimeError(f"download failed: {e}") from e

    with resp:
        # A server that ignores Range replies 200 with the whole body.
        if resume_from and resp.status != 206:
            resume_from = 0
        length = resp.headers.get("Content-Length")
        remaining = int(length) if length is not None else 0
        total = resume_from + remaining

        if resume_from:
            print(f"  resuming at {_format_mb(resume_from)}", flush=True)

        mode = "ab" if resume_from else "wb"
        done = resume_from
        tty = sys.stdout.isatty()
        next_report = done
        with part.open(mode) as fh:
            while chunk := resp.read(_CHUNK):
                fh.write(chunk)
                done += len(chunk)
                if done >= next_report:
                    _progress(done, total, tty=tty)
                    next_report = done + (4 * _CHUNK if tty else 50 * _CHUNK)

    _progress(done, total, tty=tty)
    if tty:
        print(flush=True)

    if total and done != total:
        raise RuntimeError(f"incomplete download: {done} of {total} bytes")
    return done


def download_xapk(
    *,
    output: Path | None = None,
    package: str = DEFAULT_PACKAGE,
    version: str | None = None,
    slug: str = DEFAULT_SLUG,
    abi: str = DEFAULT_ABI,
    sv: str = DEFAULT_SV,
    force: bool = False,
    verbose: bool = False,
) -> DownloadResult:
    """Resolve a release and download its XAPK, resuming a partial file if present."""
    release = resolve_release(package=package, version=version, slug=slug)
    url = download_url(release, abi=abi, sv=sv)
    dest = resolve_output_path(output, release)

    if verbose:
        print(f"  release: {release.version} (versionCode {release.version_code})")
        print(f"  url:     {url}")

    if dest.is_file() and not force:
        return DownloadResult(
            release=release,
            path=dest,
            url=url,
            size=dest.stat().st_size,
            skipped=True,
        )

    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    if force:
        part.unlink(missing_ok=True)

    size = _stream_to_file(url, part)

    if not zipfile.is_zipfile(part):
        raise RuntimeError(
            f"downloaded file is not a valid archive: {part} — "
            "APKPure may have returned an error page"
        )

    part.replace(dest)
    return DownloadResult(release=release, path=dest, url=url, size=size)
