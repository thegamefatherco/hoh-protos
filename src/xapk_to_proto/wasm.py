"""WebGL build helpers: UnityWebData extract, CDN URL discovery, downloads.

Fetches ``Build/*.wasm`` + ``Build/*.data`` and Addressables ``catalog.bin``
from the InnoGames CDN / world host (override with ``HOH_CDN_URL``). Bundle
downloads still use :data:`xapk_to_proto.assets.CDN_ROOT`.
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
# Post-process injects bare filenames (no Build/ prefix) into the login shell.
_CODE_FILENAME_RE = re.compile(
    r"""(?:const\s+)?codeFileName\s*=\s*["'](?P<name>[a-fA-F0-9]+\.wasm)["']""",
    re.IGNORECASE,
)
_DATA_FILENAME_RE = re.compile(
    r"""(?:const\s+)?dataFileName\s*=\s*["'](?P<name>[a-fA-F0-9]+\.data)["']""",
    re.IGNORECASE,
)
_CODE_URL_FIELD_RE = re.compile(
    r"""codeUrl\s*[:=]\s*["'](?P<url>[^"']+\.wasm)["']""",
    re.IGNORECASE,
)
_DATA_URL_FIELD_RE = re.compile(
    r"""dataUrl\s*[:=]\s*["'](?P<url>[^"']+\.data)["']""",
    re.IGNORECASE,
)
_CDN_URL_RE = re.compile(
    r"""cdn_url\s*:\s*['"](?P<url>https?://[^'"]+)['"]""",
    re.IGNORECASE,
)
_CATALOG_FILENAME_RE = re.compile(
    r"""(?:const\s+)?catalogFileName\s*=\s*["'](?P<name>catalog_\d{4}(?:\.\d{2}){5}\.bin)["']""",
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
class DiscoverySources:
    """Scraped shell/config text plus fetch diagnostics for error messages."""

    text: str
    cdn_root: str
    fetches: tuple[tuple[str, int], ...] = ()


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


def parse_cdn_url_from_text(text: str) -> str | None:
    """Extract ``cdn_url`` from ``config/unity.js`` / ``unity_config`` text."""
    match = _CDN_URL_RE.search(text or "")
    if match is None:
        return None
    raw = match.group("url").strip()
    if not raw.endswith("/"):
        raw += "/"
    return raw


def streaming_assets_catalog_url(account_host: str) -> str:
    """Public Addressables catalog on the world master host."""
    host = account_host.strip().rstrip("/")
    if host.startswith("http://") or host.startswith("https://"):
        parsed = urlparse(host)
        host = parsed.netloc or host
    return f"https://{host}.heroesofhistorygame.com/StreamingAssets/aa/catalog.bin"


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


def _normalize_build_ref(ref: str) -> str:
    """Accept full URL, ``Build/{hash}.ext``, bare ``{hash}.ext``, or bare hash."""
    raw = (ref or "").strip()
    if not raw:
        raise ValueError("empty build URL / hash")
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    if raw.startswith("/"):
        return raw
    if raw.startswith("Build/"):
        return raw
    # Bare filename or hash (+ optional extension).
    lower = raw.lower()
    if lower.endswith(".wasm") or lower.endswith(".data"):
        return f"Build/{raw}"
    if re.fullmatch(r"[a-fA-F0-9]+", raw):
        return f"Build/{raw}"
    return raw


def resolve_build_url_overrides(
    *,
    wasm_url: str | None = None,
    data_url: str | None = None,
    cdn_root: str | None = None,
) -> BuildUrls | None:
    """Resolve CLI/env overrides into absolute CDN URLs.

    Returns ``None`` when both overrides are unset. Requires both when either
    is set. Accepts full URLs, ``Build/{hash}.ext``, or bare hashes/filenames.
    """
    wasm_raw = (wasm_url or "").strip() or None
    data_raw = (data_url or "").strip() or None
    if wasm_raw is None and data_raw is None:
        return None
    if wasm_raw is None or data_raw is None:
        raise ValueError(
            "both --wasm-url and --data-url are required when overriding "
            "WebGL Build discovery"
        )
    root = cdn_root or resolve_cdn_root()
    return BuildUrls(
        wasm_url=_absolute_url(_normalize_build_ref(wasm_raw), cdn_root=root),
        data_url=_absolute_url(_normalize_build_ref(data_raw), cdn_root=root),
    )


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


def _pick_wasm_ref(text: str) -> str | None:
    for pattern in (_BUILD_WASM_RE, _CODE_URL_FIELD_RE):
        match = pattern.search(text)
        if match is not None:
            return match.group("url")
    match = _CODE_FILENAME_RE.search(text)
    if match is not None:
        return match.group("name")
    return None


def _pick_data_ref(text: str) -> str | None:
    for pattern in (_BUILD_DATA_RE, _DATA_URL_FIELD_RE):
        match = pattern.search(text)
        if match is not None:
            return match.group("url")
    match = _DATA_FILENAME_RE.search(text)
    if match is not None:
        return match.group("name")
    return None


def _format_fetch_diagnostics(fetches: tuple[tuple[str, int], ...] | None) -> str:
    if not fetches:
        return "  (no discovery fetches recorded)"
    lines = [f"  {url} ({size} bytes)" for url, size in fetches]
    return "\n".join(lines)


def discover_build_urls(
    text: str,
    *,
    cdn_root: str | None = None,
    base_url: str | None = None,
    fetches: tuple[tuple[str, int], ...] | None = None,
) -> BuildUrls:
    """Scrape shell HTML/JS for ``Build/*.wasm`` and ``Build/*.data`` URLs.

    Accepts literal ``Build/{hash}.ext`` paths, ``codeUrl``/``dataUrl`` fields,
    and post-process ``codeFileName``/``dataFileName`` constants (resolved
    against ``cdn_root`` + ``Build/``).
    """
    root = cdn_root or parse_cdn_url_from_text(text) or resolve_cdn_root()
    wasm_ref = _pick_wasm_ref(text)
    data_ref = _pick_data_ref(text)
    if wasm_ref is None or data_ref is None:
        diag = _format_fetch_diagnostics(fetches)
        raise ValueError(
            "Build/*.wasm and/or Build/*.data URL not found in shell sources "
            "(login redirect HTML / loader JS / config). "
            "Pass --wasm-url and --data-url to override discovery.\n"
            f"Scraped sources:\n{diag}"
        )
    return BuildUrls(
        wasm_url=_absolute_url(
            _normalize_build_ref(wasm_ref), cdn_root=root, base=base_url
        ),
        data_url=_absolute_url(
            _normalize_build_ref(data_ref), cdn_root=root, base=base_url
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

    # Prefer post-process catalogFileName from the login shell.
    named = _CATALOG_FILENAME_RE.search(text)
    if named is not None:
        return urljoin(root, named.group("name"))

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


def _url_exists(url: str) -> bool:
    """Cheap existence probe (Range GET); returns False on HTTP/network errors."""
    req = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Encoding": "identity",
            "Range": "bytes=0-0",
        },
    )
    try:
        with urlopen(req, timeout=30) as resp:
            return 200 <= getattr(resp, "status", 200) < 400
    except (HTTPError, URLError, TimeoutError, OSError):
        return False


def resolve_webgl_catalog_url(
    client: object,
    session: object,
    *,
    discovery_text: str | None = None,
    verbose: bool = False,
) -> str:
    """Prefer StreamingAssets catalog; fall back to CDN scrape from shell text."""
    world = session.world
    primary = streaming_assets_catalog_url(world.account_host)
    if verbose:
        print(f"  catalog: probe {primary}", flush=True)
    if _url_exists(primary):
        return primary
    if verbose:
        print(
            "  catalog: StreamingAssets unavailable, falling back to CDN scrape",
            flush=True,
        )

    text = discovery_text
    if text is None:
        text = collect_discovery_text(client, session, verbose=verbose)
    return discover_catalog_url(text)


def _account_origin(account_host: str) -> str:
    return f"https://{account_host}.heroesofhistorygame.com"


def _script_base_url(page_url: str, account_host: str) -> str:
    """Resolve relative script srcs against the world origin.

    Login redirect lives under ``/browser-session/…``; Scripts/ assets are
    served from the account host root, not that path prefix.
    """
    origin = _account_origin(account_host)
    if "/browser-session" in (page_url or ""):
        return origin + "/"
    if page_url:
        return page_url
    return origin + "/"


def collect_discovery_sources(
    client: object,
    session: object,
    *,
    verbose: bool = False,
    headers: dict[str, str] | None = None,
    environ: dict[str, str] | None = None,
) -> DiscoverySources:
    """Fetch authenticated game shell HTML (+ linked scripts) for URL scraping.

    Always includes ``/config/unity.js`` so ``cdn_url`` is available. Relative
    script tags from the login redirect are resolved against the account host
    origin (not ``/browser-session/``).
    """
    chunks: list[str] = []
    fetches: list[tuple[str, int]] = []
    req_headers = dict(headers or {})
    if "User-Agent" not in req_headers:
        req_headers["User-Agent"] = USER_AGENT

    redirect_html = getattr(session, "redirect_html", "") or ""
    if redirect_html:
        chunks.append(redirect_html)
        fetches.append(("(redirect_html)", len(redirect_html.encode("utf-8"))))

    world = session.world
    origin = _account_origin(world.account_host)
    shell_urls = [
        f"{origin}/",
        f"{origin}/config/unity.js",
    ]
    redirect_url = getattr(session, "redirect_url", "") or ""
    if redirect_url:
        shell_urls.insert(0, redirect_url)

    seen: set[str] = set()
    script_queue: list[str] = []

    def _fetch(url: str) -> str | None:
        if url in seen:
            return None
        seen.add(url)
        try:
            if verbose:
                print(f"  discover: GET {url}", flush=True)
            _status, _headers, body = client.request(  # type: ignore[attr-defined]
                "GET", url, headers=req_headers
            )
            fetches.append((url, len(body)))
            return body.decode("utf-8", errors="replace")
        except (RuntimeError, UnicodeDecodeError):
            fetches.append((url, -1))
            return None

    for url in shell_urls:
        text = _fetch(url)
        if text is None:
            continue
        chunks.append(text)
        base = _script_base_url(url, world.account_host)
        for src in _SCRIPT_SRC_RE.findall(text)[:16]:
            abs_src = urljoin(base, src)
            if abs_src in seen:
                continue
            if not any(
                part in abs_src
                for part in (
                    "/Build/",
                    "/Scripts/",
                    "loader",
                    "framework",
                    "Wasm",
                    "Game.js",
                    ".js",
                )
            ):
                continue
            script_queue.append(abs_src)

    for abs_src in script_queue[:20]:
        script = _fetch(abs_src)
        if script is not None:
            chunks.append(script)

    combined = "\n".join(chunks)
    if not combined.strip():
        diag = _format_fetch_diagnostics(tuple(fetches))
        raise RuntimeError(
            "could not fetch game shell HTML for WebGL URL discovery "
            "(authenticated GET to the world host failed).\n"
            f"Attempted:\n{diag}"
        )

    env_cdn = resolve_cdn_root(environ=environ)
    # Prefer explicit HOH_CDN_URL when set; else unity.js cdn_url; else default.
    env = os.environ if environ is None else environ
    if env.get(ENV_CDN_URL):
        cdn_root = env_cdn
    else:
        cdn_root = parse_cdn_url_from_text(combined) or env_cdn

    return DiscoverySources(
        text=combined,
        cdn_root=cdn_root,
        fetches=tuple(fetches),
    )


def collect_discovery_text(
    client: object,
    session: object,
    *,
    verbose: bool = False,
    headers: dict[str, str] | None = None,
    environ: dict[str, str] | None = None,
) -> str:
    """Fetch authenticated game shell HTML (+ linked scripts) for URL scraping.

    ``client`` must expose ``request(method, url, ...)`` like
    :class:`xapk_to_proto.game_api.GameApiClient`. ``session`` needs
    ``.world.account_host`` and optionally ``.redirect_html``.
    """
    return collect_discovery_sources(
        client,
        session,
        verbose=verbose,
        headers=headers,
        environ=environ,
    ).text


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
