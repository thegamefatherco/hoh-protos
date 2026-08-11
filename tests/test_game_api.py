"""Tests for InnoGames fixture download helpers (no live network)."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from xapk_to_proto import game_api
from xapk_to_proto.game_api import (
    ENV_LOCALE,
    ENV_PASSWORD,
    ENV_USERNAME,
    ENV_WORLD,
    GameApiClient,
    download_fixtures,
    encode_gamedesign_request,
    encode_localization_request,
    parse_only,
    resolve_credentials,
    resolve_locale_arg,
    resolve_world,
    resolve_world_arg,
)


def test_encode_gamedesign_request_matches_known_bytes():
    assert encode_gamedesign_request() == bytes.fromhex("0a07696e76616c6964")


def test_encode_localization_request_matches_known_bytes():
    assert encode_localization_request(locale="en_DK") == bytes.fromhex(
        "0a05656e5f444b"
    )


def test_resolve_world_un0_maps_to_innogames_hosts():
    world = resolve_world("un0")
    assert world.login_url == "https://www.heroesgame.com/api/login"
    assert (
        world.account_play_url
        == "https://un0.heroesofhistorygame.com/core/api/account/play"
    )
    assert (
        world.game_url("game/startup")
        == "https://un1.heroesofhistorygame.com/game/startup"
    )
    assert "forgeofgames.com" not in world.login_url
    assert "forgeofgames.com" not in world.account_play_url
    assert "forgeofgames.com" not in world.game_url("game/gamedesign")


def test_resolve_world_aliases():
    assert resolve_world("un1").api_host == "un1"
    assert resolve_world("zz0").sign_in == "beta"
    assert resolve_world("zz1").account_host == "zz0"


def test_resolve_world_unknown():
    with pytest.raises(ValueError, match="unknown world"):
        resolve_world("xx9")


def test_parse_only_defaults_and_aliases():
    assert parse_only(None) == (
        game_api.FIXTURE_STARTUP,
        game_api.FIXTURE_GAMEDESIGN,
        game_api.FIXTURE_LOCA,
    )
    assert parse_only("gamedesign,loca") == (
        game_api.FIXTURE_GAMEDESIGN,
        game_api.FIXTURE_LOCA,
    )
    assert parse_only(["loca-compressed"]) == (game_api.FIXTURE_LOCA,)


def test_parse_only_rejects_unknown():
    with pytest.raises(ValueError, match="unknown fixture"):
        parse_only("wakeup")


def test_resolve_credentials_env_overwrites_flags():
    user, pwd = resolve_credentials(
        username="flag-user",
        password="flag-pass",
        environ={ENV_USERNAME: "env-user", ENV_PASSWORD: "env-pass"},
    )
    assert user == "env-user"
    assert pwd == "env-pass"


def test_resolve_credentials_flags_when_env_absent():
    user, pwd = resolve_credentials(
        username="flag-user",
        password="flag-pass",
        environ={},
    )
    assert user == "flag-user"
    assert pwd == "flag-pass"


def test_resolve_credentials_requires_both():
    with pytest.raises(ValueError, match="required"):
        resolve_credentials(username="only-user", password=None, environ={})


def test_resolve_world_and_locale_env_overwrite():
    assert resolve_world_arg("un0", environ={ENV_WORLD: "zz1"}) == "zz1"
    assert resolve_locale_arg("en_DK", environ={ENV_LOCALE: "de_DE"}) == "de_DE"


class _FakeClient(GameApiClient):
    """Scripted responses keyed by URL substring / method."""

    def __init__(self, responses: dict[tuple[str, str], tuple[int, dict, bytes]]):
        # Do not call super — no real opener.
        self.responses = responses
        self.calls: list[tuple[str, str, bytes | None]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        self.calls.append((method.upper(), url, data))
        assert "forgeofgames.com" not in url
        for (m, needle), payload in self.responses.items():
            if method.upper() == m and needle in url:
                return payload
        raise AssertionError(f"unexpected request {method} {url}")


def test_maybe_gunzip_respects_content_encoding():
    raw = b"hello-fixtures"
    assert game_api._maybe_gunzip(gzip.compress(raw), "gzip") == raw
    assert game_api._maybe_gunzip(raw, None) == raw


def test_download_fixtures_mocked_flow(tmp_path: Path):
    redirect_html = 'const clientVersion = "1.50.3";'
    startup_body = b"\x0a\x05start"
    gd_body = b"\x0a\x02gd"
    loca_body = b"\x0a\x04loca"

    client = _FakeClient(
        {
            ("POST", "/api/login"): (
                200,
                {},
                json.dumps(
                    {"redirectUrl": "https://www.heroesgame.com/play"}
                ).encode(),
            ),
            ("GET", "/play"): (200, {}, redirect_html.encode()),
            ("POST", "/core/api/account/play"): (
                200,
                {},
                json.dumps({"sessionId": "sess-1", "worldId": 0}).encode(),
            ),
            ("POST", "/game/startup"): (200, {}, startup_body),
            ("POST", "/game/gamedesign"): (200, {}, gd_body),
            ("POST", "/game/loca"): (200, {}, loca_body),
        }
    )

    result = download_fixtures(
        username="u",
        password="p",
        world="un0",
        output=tmp_path,
        force=True,
        client=client,
        environ={},
        download_xapk=False,
    )

    assert result.session.client_version == "1.50.3"
    assert result.session.session_id == "sess-1"
    assert result.out_dir == tmp_path / "un0" / "1.50.3"
    by_name = {f.name: f for f in result.files}
    assert by_name["startup"].path.read_bytes() == startup_body
    assert by_name["gamedesign"].path.read_bytes() == gd_body
    assert by_name["loca-compressed"].path.read_bytes() == b"\x0a\x04loca"

    gd_call = next(c for c in client.calls if c[1].endswith("/game/gamedesign"))
    assert gd_call[2] == encode_gamedesign_request()
    loca_call = next(c for c in client.calls if c[1].endswith("/game/loca"))
    assert loca_call[2] == encode_localization_request(locale="en_DK")
    startup_call = next(c for c in client.calls if c[1].endswith("/game/startup"))
    assert startup_call[2] == b""


def test_download_fixtures_skips_existing(tmp_path: Path):
    out = tmp_path / "un0" / "1.50.3"
    out.mkdir(parents=True)
    existing = out / "gamedesign"
    existing.write_bytes(b"already")

    redirect_html = 'const clientVersion = "1.50.3";'
    client = _FakeClient(
        {
            ("POST", "/api/login"): (
                200,
                {},
                json.dumps(
                    {"redirectUrl": "https://www.heroesgame.com/play"}
                ).encode(),
            ),
            ("GET", "/play"): (200, {}, redirect_html.encode()),
            ("POST", "/core/api/account/play"): (
                200,
                {},
                json.dumps({"sessionId": "sess-1"}).encode(),
            ),
        }
    )

    result = download_fixtures(
        username="u",
        password="p",
        world="un0",
        output=tmp_path,
        only="gamedesign",
        force=False,
        client=client,
        environ={},
        download_xapk=False,
    )
    assert len(result.files) == 1
    assert result.files[0].skipped is True
    assert result.files[0].path.read_bytes() == b"already"
    assert not any("/game/gamedesign" in url for _, url, _ in client.calls)


def test_download_fixtures_includes_game_xapk(tmp_path: Path, monkeypatch):
    redirect_html = 'const clientVersion = "1.50.3";'
    client = _FakeClient(
        {
            ("POST", "/api/login"): (
                200,
                {},
                json.dumps(
                    {"redirectUrl": "https://www.heroesgame.com/play"}
                ).encode(),
            ),
            ("GET", "/play"): (200, {}, redirect_html.encode()),
            ("POST", "/core/api/account/play"): (
                200,
                {},
                json.dumps({"sessionId": "sess-1"}).encode(),
            ),
            ("POST", "/game/gamedesign"): (200, {}, b"gd"),
        }
    )

    from xapk_to_proto.apkpure import DownloadResult, Release

    def fake_download_xapk(**kwargs):
        dest = Path(kwargs["output"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"xapk-bytes")
        return DownloadResult(
            release=Release(
                package="com.innogames.heroesofhistory",
                version=kwargs["version"],
                version_code=1,
                page_url="https://example",
            ),
            path=dest,
            url="https://example/xapk",
            size=10,
            skipped=False,
        )

    monkeypatch.setattr("xapk_to_proto.apkpure.download_xapk", fake_download_xapk)

    result = download_fixtures(
        username="u",
        password="p",
        world="un0",
        output=tmp_path,
        only="gamedesign",
        force=True,
        client=client,
        environ={},
        download_xapk=True,
    )
    assert result.xapk is not None
    assert result.xapk.path == tmp_path / "un0" / "1.50.3" / "game.xapk"
    assert result.xapk.path.read_bytes() == b"xapk-bytes"
    assert any(f.name == "game.xapk" for f in result.files)
