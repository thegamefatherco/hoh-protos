"""Tests for API catalog extraction and synthetic gRPC service emission."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from xapk_to_proto.api_catalog import (
    build_endpoints,
    group_to_package,
    group_to_service_name,
    index_message_proto_files,
    parse_request_mapping_factories,
    path_group_key,
    path_to_call,
    rpc_name_for,
    run_emit_api,
    title_case_kebab,
)

SAMPLE_DUMP = """\
// Namespace: InnoGames.Generated.Network
public static class RequestMapping // TypeDefIndex: 12497
{
	public static NetworkRequest<HeroBattleStatsResponse> BattleHeroStats(HeroBattleStatsRequest request) { }
	public static NetworkAction BattleHeroCancel(HeroCancelBattleRequest request) { }
	public static NetworkRequest<StartupResponse> Startup() { }
	public static NetworkAction Cheat(CheatDTO cheat) { }
	public static NetworkAction DevCheat(CheatDTO cheat) { }
	public static NetworkRequest<StartupResponse> DevCheatStartup(StartupCheatRequest request) { }
	public static NetworkRequest<EventRewardLaneCollectResponse> EventRewardLaneCollectAll(EventRewardLaneCollectAllRequest request) { }
}
"""

SAMPLE_STRINGLITERAL = [
    {"value": "/game/battle/hero/stats", "address": "0x1"},
    {"value": "/game/battle/hero/cancel", "address": "0x2"},
    {"value": "/game/startup", "address": "0x3"},
    {"value": "/game/cheat", "address": "0x4"},
    {"value": "/dev/cheat", "address": "0x5"},
    {"value": "/dev/cheat/startup", "address": "0x6"},
    {"value": "/game/event/reward-lane/collectAll", "address": "0x7"},
    {"value": "/dev/worlds", "address": "0x8"},
    {"value": "/other/ignore", "address": "0x9"},
]


def test_title_case_and_path_to_call() -> None:
    assert title_case_kebab("start-and-finish") == "StartAndFinish"
    assert path_to_call("/game/battle/hero/stats") == "BattleHeroStats"
    assert path_to_call("/game/event/reward-lane/collectAll") == (
        "EventRewardLaneCollectAll"
    )
    assert path_to_call("/dev/cheat") == "DevCheat"
    assert path_to_call("/dev/cheat/startup") == "DevCheatStartup"
    assert path_to_call("/game/cheat") == "Cheat"


def test_group_and_rpc_naming() -> None:
    assert path_group_key("/game/battle/hero/stats") == "battle"
    assert path_group_key("/dev/cheat") == "dev"
    assert path_group_key("/game/daily-bonus/collect") == "daily-bonus"
    assert group_to_service_name("battle") == "BattleService"
    assert group_to_package("daily-bonus") == "hoh.api.daily_bonus.v1"
    assert rpc_name_for("BattleHeroStats", "BattleService") == "HeroStats"
    assert rpc_name_for("Absolution", "AbsolutionService") == "Absolution"
    assert rpc_name_for("DevCheat", "DevService") == "Cheat"


def test_parse_factories(tmp_path: Path) -> None:
    dump = tmp_path / "dump.cs"
    dump.write_text(SAMPLE_DUMP, encoding="utf-8")
    factories = parse_request_mapping_factories(dump)
    assert factories["BattleHeroStats"].kind == "request"
    assert factories["BattleHeroStats"].request_type == "HeroBattleStatsRequest"
    assert factories["BattleHeroStats"].response_type == "HeroBattleStatsResponse"
    assert factories["BattleHeroCancel"].kind == "action"
    assert factories["BattleHeroCancel"].response_type is None
    assert factories["Startup"].request_type is None


def test_build_endpoints_joins_and_warns(tmp_path: Path) -> None:
    dump = tmp_path / "dump.cs"
    dump.write_text(SAMPLE_DUMP, encoding="utf-8")
    factories = parse_request_mapping_factories(dump)

    paths = [
        e["value"]
        for e in SAMPLE_STRINGLITERAL
        if e["value"].startswith(("/game/", "/dev/"))
    ]
    endpoints, warnings = build_endpoints(paths, factories)
    by_path = {ep.path: ep for ep in endpoints}

    assert by_path["/game/battle/hero/stats"].rpc == "HeroStats"
    assert by_path["/game/battle/hero/stats"].service == "BattleService"
    assert by_path["/game/battle/hero/cancel"].kind == "action"
    assert by_path["/game/startup"].request_type is None
    assert by_path["/dev/cheat"].call == "DevCheat"
    assert by_path["/dev/cheat"].service == "DevService"
    assert any("no RequestMapping factory" in w and "/dev/worlds" in w for w in warnings)


def test_run_emit_api_writes_catalog_and_protos(tmp_path: Path) -> None:
    dump = tmp_path / "dump.cs"
    dump.write_text(SAMPLE_DUMP, encoding="utf-8")

    stringliteral = tmp_path / "stringliteral.json"
    stringliteral.write_text(json.dumps(SAMPLE_STRINGLITERAL), encoding="utf-8")

    proto_dir = tmp_path / "proto"
    proto_dir.mkdir()
    (proto_dir / "hero_battle.proto").write_text(
        'syntax = "proto3";\n'
        "message HeroBattleStatsRequest {}\n"
        "message HeroBattleStatsResponse {}\n"
        "message HeroCancelBattleRequest {}\n",
        encoding="utf-8",
    )
    (proto_dir / "startup.proto").write_text(
        'syntax = "proto3";\n'
        "message StartupResponse {}\n"
        "message StartupCheatRequest {}\n",
        encoding="utf-8",
    )
    (proto_dir / "cheat.proto").write_text(
        'syntax = "proto3";\nmessage CheatDTO {}\n',
        encoding="utf-8",
    )
    (proto_dir / "event.proto").write_text(
        'syntax = "proto3";\n'
        "message EventRewardLaneCollectAllRequest {}\n"
        "message EventRewardLaneCollectResponse {}\n",
        encoding="utf-8",
    )
    (proto_dir / "google" / "protobuf").mkdir(parents=True)
    (proto_dir / "google" / "protobuf" / "empty.proto").write_text(
        'syntax = "proto3";\npackage google.protobuf;\nmessage Empty {}\n',
        encoding="utf-8",
    )

    catalog = tmp_path / "api" / "endpoints.json"
    result = run_emit_api(
        dump_cs=dump,
        stringliteral=stringliteral,
        proto_dir=proto_dir,
        catalog_path=catalog,
    )

    assert result.endpoint_count == 7
    assert catalog.is_file()
    payload = json.loads(catalog.read_text(encoding="utf-8"))
    assert payload["content_type"] == "application/x-protobuf"
    assert len(payload["endpoints"]) == 7

    battle = (proto_dir / "api" / "battle.proto").read_text(encoding="utf-8")
    assert "package hoh.api.battle.v1;" in battle
    assert "service BattleService {" in battle
    assert "rpc HeroStats(HeroBattleStatsRequest) returns (HeroBattleStatsResponse);" in (
        battle
    )
    assert "rpc HeroCancel(HeroCancelBattleRequest) returns (google.protobuf.Empty);" in (
        battle
    )
    assert 'import "hero_battle.proto";' in battle
    assert 'import "google/protobuf/empty.proto";' in battle

    startup = (proto_dir / "api" / "startup.proto").read_text(encoding="utf-8")
    assert "rpc Startup(google.protobuf.Empty) returns (StartupResponse);" in startup

    index = index_message_proto_files(proto_dir)
    assert index["HeroBattleStatsRequest"] == "hero_battle.proto"


def test_run_emit_api_requires_inputs(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="dump.cs"):
        run_emit_api(
            dump_cs=tmp_path / "missing.cs",
            stringliteral=tmp_path / "sl.json",
            proto_dir=tmp_path / "proto",
            catalog_path=tmp_path / "api" / "endpoints.json",
        )


def test_rpc_renamed_when_colliding_with_message_type(tmp_path: Path) -> None:
    """Short RPC names must not equal a message type (protobuf rejects it)."""
    dump = tmp_path / "dump.cs"
    dump.write_text(
        "// Namespace: InnoGames.Generated.Network\n"
        "public static class RequestMapping\n"
        "{\n"
        "\tpublic static NetworkAction WonderCancelContributionRequest("
        "CancelContributionRequest request) { }\n"
        "\tpublic static NetworkAction WonderCreateContributionRequest("
        "CreateContributionRequest request) { }\n"
        "\tpublic static NetworkAction WonderBuyChest(BuyChestRequest request) { }\n"
        "}\n",
        encoding="utf-8",
    )
    stringliteral = tmp_path / "stringliteral.json"
    stringliteral.write_text(
        json.dumps(
            [
                {"value": "/game/wonder/cancel-contribution-request"},
                {"value": "/game/wonder/create-contribution-request"},
                {"value": "/game/wonder/buy-chest"},
            ]
        ),
        encoding="utf-8",
    )
    proto_dir = tmp_path / "proto"
    proto_dir.mkdir()
    (proto_dir / "wonder.proto").write_text(
        'syntax = "proto3";\n'
        "message CancelContributionRequest {}\n"
        "message CreateContributionRequest {}\n"
        "message BuyChestRequest {}\n",
        encoding="utf-8",
    )
    (proto_dir / "google" / "protobuf").mkdir(parents=True)
    (proto_dir / "google" / "protobuf" / "empty.proto").write_text(
        'syntax = "proto3";\npackage google.protobuf;\nmessage Empty {}\n',
        encoding="utf-8",
    )

    catalog = tmp_path / "api" / "endpoints.json"
    result = run_emit_api(
        dump_cs=dump,
        stringliteral=stringliteral,
        proto_dir=proto_dir,
        catalog_path=catalog,
    )
    assert result.endpoint_count == 3
    assert any("CancelContributionRequest ->" in w for w in result.warnings)

    wonder = (proto_dir / "api" / "wonder.proto").read_text(encoding="utf-8")
    assert "rpc WonderCancelContributionRequest(CancelContributionRequest)" in wonder
    assert "rpc WonderCreateContributionRequest(CreateContributionRequest)" in wonder
    # Non-colliding short name kept.
    assert "rpc BuyChest(BuyChestRequest)" in wonder
    assert "rpc CancelContributionRequest(CancelContributionRequest)" not in wonder

    payload = json.loads(catalog.read_text(encoding="utf-8"))
    by_path = {e["path"]: e for e in payload["endpoints"]}
    assert (
        by_path["/game/wonder/cancel-contribution-request"]["rpc"]
        == "WonderCancelContributionRequest"
    )
    assert by_path["/game/wonder/buy-chest"]["rpc"] == "BuyChest"
