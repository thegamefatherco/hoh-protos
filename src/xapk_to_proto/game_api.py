"""Download HoH server fixtures (startup / gamedesign / loca) from InnoGames.

Also supports WebGL Build artifacts (``wasm`` / ``catalog``) via the CDN after
the same login. Talks only to ``*.heroesgame.com``, ``*.heroesofhistorygame.com``,
APKPure (optional XAPK), and the InnoGames CDN. Writes local files only.
"""

from __future__ import annotations

import gzip
import http.cookiejar
import json
import os
import re
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener

PROTOBUF_CONTENT_TYPE = "application/x-protobuf"
JSON_CONTENT_TYPE = "application/json"
DEFAULT_WORLD = "un0"
DEFAULT_LOCALE = "en_DK"
DEFAULT_OUTPUT = Path("fixtures")
LOGIN_COOKIE = "hoh-protos"
USER_AGENT = "hoh-protos"

ENV_USERNAME = "HOH_USERNAME"
ENV_PASSWORD = "HOH_PASSWORD"
ENV_WORLD = "HOH_WORLD"
ENV_LOCALE = "HOH_LOCALE"

FIXTURE_STARTUP = "startup"
FIXTURE_GAMEDESIGN = "gamedesign"
FIXTURE_LOCA = "loca-compressed"
FIXTURE_WASM = "wasm"
FIXTURE_CATALOG = "catalog"

ONLY_ALIASES = {
    "startup": FIXTURE_STARTUP,
    "gamedesign": FIXTURE_GAMEDESIGN,
    "loca": FIXTURE_LOCA,
    "loca-compressed": FIXTURE_LOCA,
    "wasm": FIXTURE_WASM,
    "catalog": FIXTURE_CATALOG,
}

# Tokens that hit the game protobuf API (not CDN).
PROTOBUF_FIXTURES = frozenset({FIXTURE_STARTUP, FIXTURE_GAMEDESIGN, FIXTURE_LOCA})
# Tokens that pull WebGL CDN artifacts (skip APKPure XAPK by default).
WEBGL_FIXTURES = frozenset({FIXTURE_WASM, FIXTURE_CATALOG})

DEFAULT_ONLY = (FIXTURE_STARTUP, FIXTURE_GAMEDESIGN, FIXTURE_LOCA)

_CLIENT_VERSION_RE = re.compile(r'const\s+clientVersion\s*=\s*"([^"]+)"')


# Prod still serves game APIs on heroesofhistorygame.com; beta nginx 307s that
# apex to heroesgame.com, and urllib will not follow 307 on POST.
DOMAIN_PROD = "heroesofhistorygame.com"
DOMAIN_BETA = "heroesgame.com"


@dataclass(frozen=True)
class WorldEndpoints:
    """Resolved InnoGames hosts for a fixture world key."""

    key: str
    sign_in: str
    account_host: str
    api_host: str
    game_domain: str

    @property
    def login_url(self) -> str:
        return f"https://{self.sign_in}.heroesgame.com/api/login"

    @property
    def account_origin(self) -> str:
        return f"https://{self.account_host}.{self.game_domain}"

    @property
    def account_play_url(self) -> str:
        return f"{self.account_origin}/core/api/account/play"

    def game_url(self, path: str) -> str:
        path = path.lstrip("/")
        return f"https://{self.api_host}.{self.game_domain}/{path}"


# Fixture/CI keys (un0) and FoG-style aliases (un1) share the same hosts.
# Tuple: (sign_in, account_host, api_host, game_domain)
_WORLD_TABLE: dict[str, tuple[str, str, str, str]] = {
    "un0": ("www", "un0", "un1", DOMAIN_PROD),
    "un1": ("www", "un0", "un1", DOMAIN_PROD),
    "zz0": ("beta", "zz0", "zz1", DOMAIN_BETA),
    "zz1": ("beta", "zz0", "zz1", DOMAIN_BETA),
}

KNOWN_WORLDS = tuple(sorted(_WORLD_TABLE))


@dataclass(frozen=True)
class Session:
    world: WorldEndpoints
    session_id: str
    client_version: str
    redirect_html: str = ""
    redirect_url: str = ""


@dataclass
class FixtureFileResult:
    name: str
    path: Path
    size: int
    skipped: bool = False


@dataclass
class DownloadFixturesResult:
    session: Session
    out_dir: Path
    files: list[FixtureFileResult] = field(default_factory=list)
    xapk: FixtureFileResult | None = None


def resolve_world(world: str) -> WorldEndpoints:
    key = world.strip().lower()
    try:
        sign_in, account_host, api_host, game_domain = _WORLD_TABLE[key]
    except KeyError as e:
        known = ", ".join(sorted(_WORLD_TABLE))
        raise ValueError(f"unknown world {world!r}; known: {known}") from e
    return WorldEndpoints(
        key=key,
        sign_in=sign_in,
        account_host=account_host,
        api_host=api_host,
        game_domain=game_domain,
    )


def encode_protobuf_string_field(field_number: int, value: str) -> bytes:
    """Encode a proto3 length-delimited string field."""
    data = value.encode("utf-8")
    tag = (field_number << 3) | 2
    return bytes([tag, len(data)]) + data


def encode_gamedesign_request(*, checksum: str = "invalid") -> bytes:
    return encode_protobuf_string_field(1, checksum)


def encode_localization_request(
    *,
    locale: str = DEFAULT_LOCALE,
    checksum: str = "",
) -> bytes:
    payload = encode_protobuf_string_field(1, locale)
    if checksum:
        payload += encode_protobuf_string_field(2, checksum)
    return payload


def parse_only(spec: str | Iterable[str] | None) -> tuple[str, ...]:
    """Normalize ``--only`` into fixture filenames."""
    if spec is None:
        return DEFAULT_ONLY
    if isinstance(spec, str):
        parts = [p.strip() for p in spec.split(",") if p.strip()]
    else:
        parts = []
        for item in spec:
            parts.extend(p.strip() for p in str(item).split(",") if p.strip())
    if not parts:
        return DEFAULT_ONLY
    resolved: list[str] = []
    seen: set[str] = set()
    for part in parts:
        key = part.lower()
        try:
            name = ONLY_ALIASES[key]
        except KeyError as e:
            known = ", ".join(sorted(ONLY_ALIASES))
            raise ValueError(f"unknown fixture {part!r}; known: {known}") from e
        if name not in seen:
            seen.add(name)
            resolved.append(name)
    return tuple(resolved)


def resolve_credentials(
    *,
    username: str | None = None,
    password: str | None = None,
    environ: dict[str, str] | None = None,
) -> tuple[str, str]:
    """Merge CLI credentials with env; env overwrites flags when set."""
    env = os.environ if environ is None else environ
    user = env.get(ENV_USERNAME) or username or ""
    pwd = env.get(ENV_PASSWORD) or password or ""
    if not user or not pwd:
        raise ValueError(
            f"username and password are required "
            f"(flags --username/--password or env {ENV_USERNAME}/{ENV_PASSWORD})"
        )
    return user, pwd


def resolve_world_arg(
    world: str | None = None,
    *,
    environ: dict[str, str] | None = None,
) -> str:
    env = os.environ if environ is None else environ
    return (env.get(ENV_WORLD) or world or DEFAULT_WORLD).strip()


def resolve_locale_arg(
    locale: str | None = None,
    *,
    environ: dict[str, str] | None = None,
) -> str:
    env = os.environ if environ is None else environ
    return (env.get(ENV_LOCALE) or locale or DEFAULT_LOCALE).strip()


def _maybe_gunzip(data: bytes, content_encoding: str | None) -> bytes:
    if content_encoding and "gzip" in content_encoding.lower():
        return gzip.decompress(data)
    if len(data) >= 2 and data[0] == 0x1F and data[1] == 0x8B:
        try:
            return gzip.decompress(data)
        except OSError:
            return data
    return data


class GameApiClient:
    """Cookie-aware HTTP client for InnoGames login + game protobuf endpoints."""

    def __init__(self) -> None:
        self._jar = http.cookiejar.CookieJar()
        self._opener = build_opener(HTTPCookieProcessor(self._jar))

    def request(
        self,
        method: str,
        url: str,
        *,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        hdrs = dict(headers or {})
        req = Request(url, data=data, headers=hdrs, method=method.upper())
        try:
            with self._opener.open(req) as resp:
                body = resp.read()
                encoding = resp.headers.get("Content-Encoding")
                body = _maybe_gunzip(body, encoding)
                # http.client headers are case-insensitive; normalize to str dict.
                resp_headers = {k: v for k, v in resp.headers.items()}
                return resp.status, resp_headers, body
        except HTTPError as e:
            try:
                err_body = e.read() if e.fp is not None else b""
                encoding = e.headers.get("Content-Encoding") if e.headers else None
                err_body = _maybe_gunzip(err_body, encoding)
                detail = err_body[:500].decode("utf-8", errors="replace")
                raise RuntimeError(
                    f"HTTP {e.code} for {method.upper()} {url}: {detail}"
                ) from e
            finally:
                e.close()
        except URLError as e:
            raise RuntimeError(f"request failed for {method.upper()} {url}: {e}") from e


def login(
    client: GameApiClient,
    world: WorldEndpoints,
    username: str,
    password: str,
    *,
    verbose: bool = False,
) -> Session:
    """Authenticate and open a browser play session."""
    if verbose:
        print(f"  login: {world.login_url}", flush=True)

    login_payload = json.dumps(
        {
            "username": username,
            "password": password,
            "useRememberMe": True,
        }
    ).encode("utf-8")
    _, _, login_body = client.request(
        "POST",
        world.login_url,
        data=login_payload,
        headers={
            "Content-Type": JSON_CONTENT_TYPE,
            "Accept": JSON_CONTENT_TYPE,
            "Cookie": LOGIN_COOKIE,
        },
    )
    try:
        login_data = json.loads(login_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise RuntimeError("login response is not JSON") from e
    redirect_url = login_data.get("redirectUrl")
    if not redirect_url:
        raise RuntimeError("login response missing redirectUrl")

    if verbose:
        print(f"  redirect: {redirect_url}", flush=True)

    _, _, redirect_body = client.request("GET", redirect_url)
    try:
        redirect_html = redirect_body.decode("utf-8")
    except UnicodeDecodeError as e:
        raise RuntimeError("redirect page is not UTF-8 text") from e
    match = _CLIENT_VERSION_RE.search(redirect_html)
    if not match:
        raise RuntimeError("clientVersion not found in redirect page")
    client_version = match.group(1)

    if verbose:
        print(f"  clientVersion: {client_version}", flush=True)
        print(f"  account play: {world.account_play_url}", flush=True)

    play_payload = json.dumps(
        {
            "createDeviceToken": False,
            "meta": {
                "clientVersion": client_version,
                "device": "browser",
                "deviceHardware": "browser",
                "deviceManufacturer": "none",
                "deviceName": "browser",
                "locale": DEFAULT_LOCALE,
                "networkType": "wlan",
                "operatingSystemName": "browser",
                "operatingSystemVersion": "1",
                "userAgent": USER_AGENT,
            },
            "network": "BROWSER_SESSION",
            "token": "",
            "worldId": None,
        }
    ).encode("utf-8")
    _, _, play_body = client.request(
        "POST",
        world.account_play_url,
        data=play_payload,
        headers={
            "Content-Type": JSON_CONTENT_TYPE,
            "Accept": JSON_CONTENT_TYPE,
        },
    )
    try:
        play_data = json.loads(play_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise RuntimeError("account play response is not JSON") from e
    session_id = play_data.get("sessionId")
    if not session_id:
        raise RuntimeError("account play response missing sessionId")

    return Session(
        world=world,
        session_id=str(session_id),
        client_version=client_version,
        redirect_html=redirect_html,
        redirect_url=str(redirect_url),
    )


def _auth_headers(session: Session) -> dict[str, str]:
    return {
        "X-AUTH-TOKEN": session.session_id,
        "X-ClientVersion": session.client_version,
        "X-Request-Id": str(uuid.uuid4()),
        "X-Platform": "browser",
        "X-Action-At": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
        + "Z",
        "Accept": PROTOBUF_CONTENT_TYPE,
        "Content-Type": PROTOBUF_CONTENT_TYPE,
        "Accept-Encoding": "gzip",
    }


def fetch_protobuf(
    client: GameApiClient,
    session: Session,
    path: str,
    payload: bytes,
    *,
    verbose: bool = False,
) -> bytes:
    url = session.world.game_url(path)
    if verbose:
        print(f"  POST {url} ({len(payload)} byte body)", flush=True)
    _, _, body = client.request(
        "POST",
        url,
        data=payload,
        headers=_auth_headers(session),
    )
    if not body:
        raise RuntimeError(f"empty protobuf response from {path}")
    return body


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_bytes(data)
    part.replace(path)


def _payload_for_fixture(name: str, locale: str) -> tuple[str, bytes]:
    if name == FIXTURE_STARTUP:
        return "game/startup", b""
    if name == FIXTURE_GAMEDESIGN:
        return "game/gamedesign", encode_gamedesign_request()
    if name == FIXTURE_LOCA:
        return "game/loca", encode_localization_request(locale=locale)
    raise ValueError(f"unknown fixture {name!r}")


def download_fixtures(
    *,
    username: str | None = None,
    password: str | None = None,
    world: str | None = None,
    output: Path | None = None,
    only: str | Iterable[str] | None = None,
    locale: str | None = None,
    force: bool = False,
    verbose: bool = False,
    download_xapk: bool | None = None,
    wasm_url: str | None = None,
    data_url: str | None = None,
    environ: dict[str, str] | None = None,
    client: GameApiClient | None = None,
) -> DownloadFixturesResult:
    """Login, fetch selected fixtures, write them under ``output/{world}/{version}/``.

    By default also downloads the matching XAPK from APKPure (via apkeep) as
    ``game.xapk`` into the same directory — unless the selection includes WebGL
    tokens (``wasm`` / ``catalog``), which skip XAPK by default. Pass
    ``download_xapk=False`` to skip explicitly, or ``True`` to force an XAPK
    even alongside WebGL fixtures.
    """
    from xapk_to_proto import apkpure, wasm
    from xapk_to_proto.paths import (
        GAME_CATALOG_NAME,
        GAME_DATA_NAME,
        GAME_METADATA_NAME,
        GAME_WASM_NAME,
        GAME_XAPK_NAME,
    )

    user, pwd = resolve_credentials(
        username=username, password=password, environ=environ
    )
    world_key = resolve_world_arg(world, environ=environ)
    locale_value = resolve_locale_arg(locale, environ=environ)
    fixtures = parse_only(only)
    endpoints = resolve_world(world_key)
    wants_webgl = bool(WEBGL_FIXTURES.intersection(fixtures))
    if download_xapk is None:
        download_xapk = not wants_webgl

    api = client or GameApiClient()
    session = login(api, endpoints, user, pwd, verbose=verbose)

    root = (output or DEFAULT_OUTPUT).resolve()
    # Use the CLI/env world key for the fixture folder (CI uses un0).
    out_dir = root / world_key / session.client_version
    out_dir.mkdir(parents=True, exist_ok=True)

    if verbose:
        print(f"  out: {out_dir}", flush=True)

    results: list[FixtureFileResult] = []
    discovery: wasm.DiscoverySources | None = None
    discovery_headers = {
        "X-AUTH-TOKEN": session.session_id,
        "X-ClientVersion": session.client_version,
        "User-Agent": USER_AGENT,
    }

    def _discovery() -> wasm.DiscoverySources:
        nonlocal discovery
        if discovery is None:
            discovery = wasm.collect_discovery_sources(
                api,
                session,
                verbose=verbose,
                headers=discovery_headers,
                environ=environ,
            )
        return discovery

    for name in fixtures:
        if name == FIXTURE_WASM:
            override = wasm.resolve_build_url_overrides(
                wasm_url=wasm_url,
                data_url=data_url,
                cdn_root=wasm.resolve_cdn_root(environ=environ),
            )
            if override is not None:
                urls = override
            else:
                sources = _discovery()
                urls = wasm.discover_build_urls(
                    sources.text,
                    cdn_root=sources.cdn_root,
                    fetches=sources.fetches,
                )
            dl = wasm.download_webgl_build(
                out_dir=out_dir,
                wasm_url=urls.wasm_url,
                data_url=urls.data_url,
                force=force,
                verbose=verbose,
            )
            for label, path in (
                (GAME_WASM_NAME, dl.wasm),
                (GAME_DATA_NAME, dl.data),
                (GAME_METADATA_NAME, dl.metadata),
            ):
                results.append(
                    FixtureFileResult(
                        name=label,
                        path=path,
                        size=path.stat().st_size if path.is_file() else 0,
                        skipped=dl.skipped,
                    )
                )
            continue

        if name == FIXTURE_CATALOG:
            # Catalog does not need Build URL discovery; StreamingAssets first.
            sources_text = discovery.text if discovery is not None else None
            catalog_url = wasm.resolve_webgl_catalog_url(
                api,
                session,
                discovery_text=sources_text,
                verbose=verbose,
            )
            dl = wasm.download_webgl_catalog(
                out_dir=out_dir,
                catalog_url=catalog_url,
                force=force,
                verbose=verbose,
            )
            results.append(
                FixtureFileResult(
                    name=GAME_CATALOG_NAME,
                    path=dl.path,
                    size=dl.path.stat().st_size if dl.path.is_file() else 0,
                    skipped=dl.skipped,
                )
            )
            continue

        dest = out_dir / name
        if dest.is_file() and not force:
            results.append(
                FixtureFileResult(
                    name=name,
                    path=dest,
                    size=dest.stat().st_size,
                    skipped=True,
                )
            )
            continue
        path, payload = _payload_for_fixture(name, locale_value)
        data = fetch_protobuf(api, session, path, payload, verbose=verbose)
        _write_atomic(dest, data)
        results.append(
            FixtureFileResult(name=name, path=dest, size=len(data), skipped=False)
        )
        if verbose:
            print(f"  wrote {dest} ({len(data)} bytes)", file=sys.stderr, flush=True)

    xapk_result: FixtureFileResult | None = None
    if download_xapk:
        xapk_dest = out_dir / GAME_XAPK_NAME
        if verbose:
            print(f"  downloading XAPK -> {xapk_dest}", flush=True)
        dl = apkpure.download_xapk(
            output=xapk_dest,
            version=session.client_version,
            force=force,
            verbose=verbose,
        )
        xapk_result = FixtureFileResult(
            name=GAME_XAPK_NAME,
            path=dl.path,
            size=dl.size,
            skipped=dl.skipped,
        )
        results.append(xapk_result)

    return DownloadFixturesResult(
        session=session,
        out_dir=out_dir,
        files=results,
        xapk=xapk_result,
    )
