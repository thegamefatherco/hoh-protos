"""WebGL build helpers: UnityWebData extract, CDN URL discovery, downloads.

Fetches ``Build/*.wasm`` + ``Build/*.data`` and Addressables ``catalog_*.bin``
from the InnoGames CDN (override with ``HOH_CDN_URL``). Bundle downloads still
use :data:`xapk_to_proto.assets.CDN_ROOT`.
"""

from __future__ import annotations

import gzip
import os
import re
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from xapk_to_proto.assets import CDN_ROOT as BUNDLES_CDN_ROOT
from xapk_to_proto.xapk import METADATA_MAGIC, validate_metadata

ENV_CDN_URL = "HOH_CDN_URL"
DEFAULT_CDN_ROOT = "https://heczz.innogamescdn.com/"
USER_AGENT = "hoh-protos"

UNITY_WEB_DATA_MAGIC = b"UnityWebData1.0\x00"
METADATA_MEMBER_SUFFIX = "global-metadata.dat"

# Absolute or host-relative CDN / Build artifact URLs in shell HTML/JS.
_BUILD_WASM_RE = re.compile(
    r"""(?P<url>(?:https?://[^\s"'<>]+|/)?Build/[a-fA-F0-9]+\.wasm)""",
    re.IGNORECASE,
)
_BUILD_DATA_RE = re.compile(
    r"""(?P<url>(?:https?://[^\s"'<>]+|/)?Build/[a-fA-F0-9]+\.data)""",
    re.IGNORECASE,
)
_CATALOG_BIN_RE = re.compile(
    r"""(?P<name>catalog_\d{4}(?:\.\d{2}){5}\.bin)""",
    re.IGNORECASE,
)
_SETTINGS_JSON_RE = re.compile(
    r"""(?P<url>(?:https?://[^\s"'<>]+)?(?:bundles/WebGL/)?settings_\d{4}(?:\.\d{2}){5}\.json)""",
    re.IGNORECASE,
)
_SCRIPT_SRC_RE = re.compile(
    r"""<script[^>]+src=["']([^"']+)["']""",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class BuildUrls:
    wasm_url: str
    data_url: str


@dataclass(frozen=True)
class WebglDownloadResult:
    wasm: Path
    data: Path
    metadata: Path
    skipped: bool = False


@dataclass(frozen=True)
class CatalogDownloadResult:
    path: Path
    source_url: str
    skipped: bool = False


def resolve_cdn_root(*, environ: dict[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    raw = (env.get(ENV_CDN_URL) or DEFAULT_CDN_ROOT).strip()
    if not raw.endswith("/"):
        raw += "/"
    return raw


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_bytes(data)
    part.replace(path)


def _cdn_get(url: str) -> bytes:
    req = Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"},
    )
    try:
        with urlopen(req) as resp:
            raw = resp.read()
            encoding = (resp.headers.get("Content-Encoding") or "").lower()
            if "gzip" in encoding:
                return gzip.decompress(raw)
            return raw
    except HTTPError as e:
        try:
            detail = (e.read() or b"")[:200].decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {e.code} for GET {url}: {detail}") from e
        finally:
            e.close()
    except URLError as e:
        raise RuntimeError(f"request failed for GET {url}: {e}") from e


def _absolute_url(url: str, *, cdn_root: str, base: str | None = None) -> str:
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if url.startswith("/"):
        # Host-relative on the game origin — prefer CDN for Build/ artifacts.
        if url.startswith("/Build/"):
            return urljoin(cdn_root, url.lstrip("/"))
        if base:
            parsed = urlparse(base)
            return f"{parsed.scheme}://{parsed.netloc}{url}"
        return urljoin(cdn_root, url.lstrip("/"))
    return urljoin(cdn_root, url)


def parse_unity_web_data(data: bytes) -> list[tuple[str, int, int]]:
    """Parse UnityWebData1.0 headers into ``(name, offset, size)`` entries."""
    if not data.startswith(UNITY_WEB_DATA_MAGIC):
        raise ValueError("not a UnityWebData1.0 archive (bad magic)")
    if len(data) < 20:
        raise ValueError("UnityWebData archive too small")
    data_start = struct.unpack_from("<I", data, 16)[0]
    if data_start <= 20 or data_start > len(data):
        raise ValueError(f"invalid UnityWebData file-table end offset: {data_start}")

    entries: list[tuple[str, int, int]] = []
    pos = 20
    while pos < data_start:
        if pos + 12 > data_start:
            raise ValueError("truncated UnityWebData file header")
        offset, size, name_len = struct.unpack_from("<III", data, pos)
        pos += 12
        if pos + name_len > data_start:
            raise ValueError("truncated UnityWebData filename")
        name = data[pos : pos + name_len].decode("utf-8", errors="replace")
        pos += name_len
        entries.append((name, offset, size))
    return entries


def extract_global_metadata(data_path: Path, dest: Path) -> Path:
    """Extract ``global-metadata.dat`` from a UnityWebData ``.data`` file."""
    blob = data_path.read_bytes()
    # Some builds ship gzip-wrapped .data; detect and unwrap.
    if blob[:2] == b"\x1f\x8b":
        blob = gzip.decompress(blob)
    entries = parse_unity_web_data(blob)
    match = next(
        (
            (name, off, size)
            for name, off, size in entries
            if name.endswith(METADATA_MEMBER_SUFFIX) or METADATA_MEMBER_SUFFIX in name
        ),
        None,
    )
    if match is None:
        raise FileNotFoundError(
            f"{METADATA_MEMBER_SUFFIX} not found inside {data_path}"
        )
    _name, offset, size = match
    if offset + size > len(blob):
        raise ValueError(f"metadata extent exceeds archive size in {data_path}")
    meta = blob[offset : offset + size]
    if len(meta) < 8 or struct.unpack_from("<I", meta, 0)[0] != METADATA_MAGIC:
        raise ValueError(f"extracted metadata has bad magic from {data_path}")
    _write_atomic(dest, meta)
    validate_metadata(dest)
    return dest


def discover_wasm_il2cpp(fixtures_dir: Path) -> tuple[Path, Path]:
    """Locate ``game.wasm`` + ``global-metadata.dat`` under a fixtures folder.

    If metadata is missing but ``game.data`` exists, extract it first.
    """
    from xapk_to_proto.paths import (
        GAME_DATA_NAME,
        GAME_METADATA_NAME,
        GAME_WASM_NAME,
    )

    fixtures_dir = fixtures_dir.resolve()
    wasm = fixtures_dir / GAME_WASM_NAME
    metadata = fixtures_dir / GAME_METADATA_NAME
    data = fixtures_dir / GAME_DATA_NAME

    if not wasm.is_file():
        raise FileNotFoundError(f"{GAME_WASM_NAME} not found under {fixtures_dir}")

    if not metadata.is_file():
        if not data.is_file():
            raise FileNotFoundError(
                f"{GAME_METADATA_NAME} (and {GAME_DATA_NAME}) not found under "
                f"{fixtures_dir}"
            )
        extract_global_metadata(data, metadata)

    validate_metadata(metadata)
    return wasm, metadata


def discover_build_urls(
    text: str,
    *,
    cdn_root: str | None = None,
    base_url: str | None = None,
) -> BuildUrls:
    """Scrape shell HTML/JS for ``Build/*.wasm`` and ``Build/*.data`` URLs."""
    root = cdn_root or resolve_cdn_root()
    wasm_match = _BUILD_WASM_RE.search(text)
    data_match = _BUILD_DATA_RE.search(text)
    if wasm_match is None or data_match is None:
        raise ValueError(
            "Build/*.wasm and/or Build/*.data URL not found in shell sources "
            "(login page / loader JS). The WebGL build config may have changed."
        )
    return BuildUrls(
        wasm_url=_absolute_url(
            wasm_match.group("url"), cdn_root=root, base=base_url
        ),
        data_url=_absolute_url(
            data_match.group("url"), cdn_root=root, base=base_url
        ),
    )


def discover_catalog_url(
    text: str,
    *,
    bundles_root: str | None = None,
) -> str:
    """Scrape shell / settings JSON for a ``catalog_YYYY.MM.DD.HH.MM.SS.bin`` URL."""
    root = bundles_root or BUNDLES_CDN_ROOT
    if not root.endswith("/"):
        root += "/"

    # Prefer an explicit .bin URL / name.
    match = _CATALOG_BIN_RE.search(text)
    if match is not None:
        name = match.group("name")
        return urljoin(root, name)

    # settings_*.json embeds the catalog basename inside InternalId strings.
    settings_match = _SETTINGS_JSON_RE.search(text)
    if settings_match is not None:
        settings_url = settings_match.group("url")
        if not settings_url.startswith("http"):
            settings_url = urljoin(root, settings_url.split("bundles/WebGL/")[-1])
        settings_body = _cdn_get(settings_url).decode("utf-8", errors="replace")
        cat = _CATALOG_BIN_RE.search(settings_body)
        if cat is not None:
            return urljoin(root, cat.group("name"))
        # .hash reference → swap extension to .bin
        hash_name = re.search(
            r"catalog_\d{4}(?:\.\d{2}){5}\.hash", settings_body, re.IGNORECASE
        )
        if hash_name is not None:
            return urljoin(root, hash_name.group(0)[:-5] + ".bin")

    raise ValueError(
        "Addressables catalog_*.bin not found in shell sources or settings JSON"
    )


def collect_discovery_text(
    client: object,
    session: object,
    *,
    verbose: bool = False,
) -> str:
    """Fetch authenticated game shell HTML (+ linked scripts) for URL scraping.

    ``client`` must expose ``request(method, url, ...)`` like
    :class:`xapk_to_proto.game_api.GameApiClient`. ``session`` needs
    ``.world.account_host`` and optionally ``.redirect_html``.
    """
    chunks: list[str] = []
    redirect_html = getattr(session, "redirect_html", "") or ""
    if redirect_html:
        chunks.append(redirect_html)

    world = session.world
    shell_urls = [
        f"https://{world.account_host}.heroesofhistorygame.com/",
        f"https://{world.account_host}.heroesofhistorygame.com/play",
    ]
    # Prefer the login redirect host when available.
    redirect_url = getattr(session, "redirect_url", "") or ""
    if redirect_url:
        shell_urls.insert(0, redirect_url)

    seen: set[str] = set()
    for url in shell_urls:
        if url in seen:
            continue
        seen.add(url)
        try:
            if verbose:
                print(f"  discover: GET {url}", flush=True)
            _status, _headers, body = client.request("GET", url)  # type: ignore[attr-defined]
            text = body.decode("utf-8", errors="replace")
            chunks.append(text)
            # Pull a few script tags that likely embed Unity config.
            for src in _SCRIPT_SRC_RE.findall(text)[:12]:
                abs_src = urljoin(url, src)
                if abs_src in seen:
                    continue
                if not any(
                    part in abs_src
                    for part in ("/Build/", "loader", "framework", "Wasm", ".js")
                ):
                    continue
                seen.add(abs_src)
                try:
                    if verbose:
                        print(f"  discover: GET {abs_src}", flush=True)
                    _s, _h, script = client.request("GET", abs_src)  # type: ignore[attr-defined]
                    chunks.append(script.decode("utf-8", errors="replace"))
                except (RuntimeError, UnicodeDecodeError):
                    continue
        except RuntimeError:
            continue

    combined = "\n".join(chunks)
    if not combined.strip():
        raise RuntimeError(
            "could not fetch game shell HTML for WebGL URL discovery "
            "(authenticated GET to the world host failed)"
        )
    return combined


def download_webgl_build(
    *,
    out_dir: Path,
    wasm_url: str,
    data_url: str,
    force: bool = False,
    verbose: bool = False,
) -> WebglDownloadResult:
    """Download ``game.wasm`` + ``game.data`` and extract ``global-metadata.dat``."""
    from xapk_to_proto.paths import (
        GAME_DATA_NAME,
        GAME_METADATA_NAME,
        GAME_WASM_NAME,
    )

    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    wasm_dest = out_dir / GAME_WASM_NAME
    data_dest = out_dir / GAME_DATA_NAME
    meta_dest = out_dir / GAME_METADATA_NAME

    have_all = wasm_dest.is_file() and data_dest.is_file() and meta_dest.is_file()
    if have_all and not force:
        return WebglDownloadResult(
            wasm=wasm_dest, data=data_dest, metadata=meta_dest, skipped=True
        )

    if force or not wasm_dest.is_file():
        if verbose:
            print(f"  GET {wasm_url} -> {wasm_dest}", file=sys.stderr, flush=True)
        _write_atomic(wasm_dest, _cdn_get(wasm_url))

    if force or not data_dest.is_file():
        if verbose:
            print(f"  GET {data_url} -> {data_dest}", file=sys.stderr, flush=True)
        _write_atomic(data_dest, _cdn_get(data_url))

    if force or not meta_dest.is_file():
        if verbose:
            print(f"  extract {meta_dest.name} from {data_dest.name}", flush=True)
        extract_global_metadata(data_dest, meta_dest)

    return WebglDownloadResult(
        wasm=wasm_dest, data=data_dest, metadata=meta_dest, skipped=False
    )


def download_webgl_catalog(
    *,
    out_dir: Path,
    catalog_url: str,
    force: bool = False,
    verbose: bool = False,
) -> CatalogDownloadResult:
    """Download Addressables catalog bytes as normalized ``catalog.bin``."""
    from xapk_to_proto.paths import GAME_CATALOG_NAME

    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / GAME_CATALOG_NAME
    if dest.is_file() and not force:
        return CatalogDownloadResult(path=dest, source_url=catalog_url, skipped=True)

    if verbose:
        print(f"  GET {catalog_url} -> {dest}", file=sys.stderr, flush=True)
    data = _cdn_get(catalog_url)
    if not data:
        raise RuntimeError(f"empty catalog response from {catalog_url}")
    _write_atomic(dest, data)
    return CatalogDownloadResult(path=dest, source_url=catalog_url, skipped=False)
