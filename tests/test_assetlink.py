"""Tests for asset-field discovery and the data-to-image resolver (no UnityPy)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from google.protobuf import descriptor_pb2

from xapk_to_proto.assetlink import (
    STATUS_BUNDLE_ONLY,
    STATUS_DEFINITION_REF,
    STATUS_IMAGE,
    STATUS_MISS,
    AssetIndex,
    asset_fields_from_descriptors,
    build_report,
    link_definitions,
)

_STRING = descriptor_pb2.FieldDescriptorProto.TYPE_STRING
_MESSAGE = descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
_REPEATED = descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED


def _message(name: str, fields: list[tuple[str, int, str | None, bool]]):
    """Build a DescriptorProto from (name, type, type_name, repeated) tuples."""
    msg = descriptor_pb2.DescriptorProto()
    msg.name = name
    for i, (field_name, field_type, type_name, repeated) in enumerate(fields, start=1):
        f = msg.field.add()
        f.name = field_name
        f.number = i
        f.type = field_type
        if type_name:
            f.type_name = type_name
        if repeated:
            f.label = _REPEATED
    return msg


def _write_descriptors(tmp_path: Path) -> Path:
    """A packageless descriptor set shaped like the real game protos."""
    fd = descriptor_pb2.FileDescriptorProto()
    fd.name = "test_assets.proto"
    fd.dependency.append("google/protobuf/any.proto")

    fd.message_type.append(
        _message(
            "HeroUnitDefinitionDTO",
            [
                ("id", _STRING, None, False),
                ("asset_id", _STRING, None, False),
                ("unit_icon_override", _STRING, None, False),
                ("power", _STRING, None, False),
            ],
        )
    )
    fd.message_type.append(
        _message(
            "BattleFieldAssetSetDefinitionDTO",
            [
                ("id", _STRING, None, False),
                ("start", _STRING, None, False),
                ("middle", _STRING, None, False),
                ("end", _STRING, None, False),
            ],
        )
    )
    fd.message_type.append(
        _message(
            "HeroPassiveAbilityDisplayComponentDTO",
            [("asset_ids", _STRING, None, True)],
        )
    )
    fd.message_type.append(
        _message(
            "HeroBattleDefinitionDTO",
            [("battlefield_asset_set_definition_id", _STRING, None, False)],
        )
    )
    fd.message_type.append(
        _message("NestedArtDTO", [("banner_asset_id", _STRING, None, False)])
    )
    fd.message_type.append(
        _message(
            "EncounterDefinitionDTO",
            [
                ("id", _STRING, None, False),
                ("nested", _MESSAGE, ".NestedArtDTO", False),
                ("components", _MESSAGE, ".google.protobuf.Any", True),
            ],
        )
    )

    fds = descriptor_pb2.FileDescriptorSet()
    fds.file.append(fd)
    path = tmp_path / "descriptors.pb"
    path.write_bytes(fds.SerializeToString())
    return path


def _index() -> AssetIndex:
    return AssetIndex(
        by_name={"icon_gold": ["extracted/atlas/icon_gold.png"]},
        by_bundle_prefix={
            "unit_boudicca": {"bundle": "unit_boudicca_x.bundle", "images": []},
            "hero_backdrop": {
                "bundle": "hero_backdrop_x.bundle",
                "images": ["extracted/hero_backdrop/bg.png"],
            },
        },
        source="test-index",
    )


@pytest.fixture
def descriptors(tmp_path):
    return _write_descriptors(tmp_path)


def test_asset_fields_are_discovered_from_field_names(descriptors):
    _, by_short = asset_fields_from_descriptors(descriptors)
    assert by_short["HeroUnitDefinitionDTO"] == ("asset_id", "unit_icon_override")


def test_asset_fields_include_the_battlefield_positional_overrides(descriptors):
    _, by_short = asset_fields_from_descriptors(descriptors)
    assert by_short["BattleFieldAssetSetDefinitionDTO"] == ("start", "middle", "end")


def test_asset_fields_exclude_denied_definition_id_lists(descriptors):
    _, by_short = asset_fields_from_descriptors(descriptors)
    assert "HeroPassiveAbilityDisplayComponentDTO" not in by_short


def test_asset_fields_exclude_cross_references_to_other_definitions(descriptors):
    _, by_short = asset_fields_from_descriptors(descriptors)
    assert "HeroBattleDefinitionDTO" not in by_short


def test_resolve_prefers_an_exact_sprite_name_match():
    resolution = _index().resolve("Icon_Gold")
    assert resolution.status == STATUS_IMAGE
    assert resolution.image == "extracted/atlas/icon_gold.png"


def test_resolve_reports_prefab_addresses_as_bundle_only():
    resolution = _index().resolve("Unit_Boudicca")
    assert resolution.status == STATUS_BUNDLE_ONLY
    assert resolution.bundle == "unit_boudicca_x.bundle"
    assert resolution.image is None


def test_resolve_uses_bundle_images_when_the_address_owns_art():
    resolution = _index().resolve("hero_backdrop")
    assert resolution.status == STATUS_IMAGE
    assert resolution.image == "extracted/hero_backdrop/bg.png"


def test_resolve_reports_unknown_values_as_a_miss():
    assert _index().resolve("nothing_here").status == STATUS_MISS


def test_resolve_separates_namespaced_gamedesign_ids_from_real_misses():
    assert _index().resolve("resource.agate").status == STATUS_DEFINITION_REF
    assert (
        _index().resolve("battlefield_asset_set.Grass_River").status
        == STATUS_DEFINITION_REF
    )


def test_resolve_still_prefers_a_real_match_over_the_gamedesign_id_shape():
    index = _index()
    index.by_name["merge_event_bucket.worldfair_gold"] = ["extracted/a/b.png"]
    assert index.resolve("merge_event_bucket.WorldFair_Gold").status == STATUS_IMAGE


def test_link_definitions_resolves_top_level_fields(tmp_path, descriptors):
    defs = tmp_path / "defs"
    defs.mkdir()
    (defs / "HeroUnitDefinitionDTO.json").write_text(
        json.dumps(
            [
                {
                    "id": "unit.boudicca",
                    "asset_id": "Unit_Boudicca",
                    "unit_icon_override": "icon_gold",
                }
            ]
        ),
        encoding="utf-8",
    )

    result = link_definitions([defs], _index(), descriptors)

    fields = result.links["HeroUnitDefinitionDTO"][0]["fields"]
    assert fields["asset_id"]["status"] == STATUS_BUNDLE_ONLY
    assert fields["unit_icon_override"]["image"] == "extracted/atlas/icon_gold.png"
    assert result.links["HeroUnitDefinitionDTO"][0]["id"] == "unit.boudicca"


def test_link_definitions_descends_into_nested_messages_and_any_payloads(
    tmp_path, descriptors
):
    defs = tmp_path / "defs"
    defs.mkdir()
    (defs / "EncounterDefinitionDTO.json").write_text(
        json.dumps(
            [
                {
                    "id": "encounter.1",
                    "nested": {"banner_asset_id": "icon_gold"},
                    "components": [
                        {
                            "@type": "type.googleapis.com/HeroUnitDefinitionDTO",
                            "asset_id": "icon_gold",
                        }
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )

    result = link_definitions([defs], _index(), descriptors)

    fields = result.links["EncounterDefinitionDTO"][0]["fields"]
    assert fields["nested.banner_asset_id"]["status"] == STATUS_IMAGE
    assert fields["components[0].asset_id"]["status"] == STATUS_IMAGE


def test_link_definitions_ignores_fields_that_only_look_like_assets(
    tmp_path, descriptors
):
    defs = tmp_path / "defs"
    defs.mkdir()
    (defs / "HeroPassiveAbilityDisplayComponentDTO.json").write_text(
        json.dumps([{"asset_ids": ["hero_battle_ability.JoanOfArc_Passive"]}]),
        encoding="utf-8",
    )

    result = link_definitions([defs], _index(), descriptors)

    assert result.links == {}


def test_link_definitions_skips_manifests_and_unknown_types(tmp_path, descriptors):
    defs = tmp_path / "defs"
    defs.mkdir()
    (defs / "manifest.json").write_text(
        json.dumps({"total_entries": 0}), encoding="utf-8"
    )
    (defs / "NotAThingDTO.json").write_text(
        json.dumps([{"asset_id": "x"}]), encoding="utf-8"
    )

    result = link_definitions([defs], _index(), descriptors)

    assert result.links == {}
    assert any("NotAThingDTO" in w for w in result.warnings)


def test_report_counts_each_status_and_samples_misses(tmp_path, descriptors):
    defs = tmp_path / "defs"
    defs.mkdir()
    (defs / "HeroUnitDefinitionDTO.json").write_text(
        json.dumps(
            [
                {"id": "a", "asset_id": "icon_gold"},
                {"id": "b", "asset_id": "Unit_Boudicca"},
                {"id": "c", "asset_id": "missing_thing"},
            ]
        ),
        encoding="utf-8",
    )

    report = build_report(link_definitions([defs], _index(), descriptors))

    stats = report["fields"]["HeroUnitDefinitionDTO.asset_id"]
    assert (stats[STATUS_IMAGE], stats[STATUS_BUNDLE_ONLY], stats[STATUS_MISS]) == (
        1,
        1,
        1,
    )
    assert stats["sample_misses"] == ["missing_thing"]
    assert report["totals"]["values"] == 3


def test_report_counts_gamedesign_ids_apart_from_misses(tmp_path, descriptors):
    defs = tmp_path / "defs"
    defs.mkdir()
    (defs / "HeroUnitDefinitionDTO.json").write_text(
        json.dumps(
            [
                {"id": "a", "asset_id": "resource.agate"},
                {"id": "b", "asset_id": "missing_thing"},
            ]
        ),
        encoding="utf-8",
    )

    report = build_report(link_definitions([defs], _index(), descriptors))

    stats = report["fields"]["HeroUnitDefinitionDTO.asset_id"]
    assert stats[STATUS_DEFINITION_REF] == 1
    assert stats[STATUS_MISS] == 1
    assert stats["sample_misses"] == ["missing_thing"]


def test_asset_index_load_accepts_a_directory(tmp_path):
    (tmp_path / "index.json").write_text(
        json.dumps(
            {"by_name": {"a": ["p.png"]}, "by_bundle_prefix": {}, "source": "s"}
        ),
        encoding="utf-8",
    )
    assert AssetIndex.load(tmp_path).by_name == {"a": ["p.png"]}


def test_asset_index_load_reports_a_missing_index(tmp_path):
    with pytest.raises(FileNotFoundError):
        AssetIndex.load(tmp_path / "nope")
