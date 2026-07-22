"""Tests for gamedesign JSON export with ProtoJSON @type wrappers."""

from __future__ import annotations

import json
from pathlib import Path

from xapk_to_proto.definitions import write_definitions_export
from xapk_to_proto.gamedesign import (
    DecodedEntry,
    _group_by_type,
    entry_to_protojson_dict,
    write_gamedesign_export,
)


def test_entry_to_protojson_dict_adds_type_url() -> None:
    entry = DecodedEntry(
        type_name="HeroDefinitionDTO",
        data={"definition_id": "hero.Example", "backdrop_asset_id": "backdrop"},
    )

    result = entry_to_protojson_dict(entry)

    assert result == {
        "@type": "type.googleapis.com/HeroDefinitionDTO",
        "definition_id": "hero.Example",
        "backdrop_asset_id": "backdrop",
    }


def test_entry_to_protojson_dict_type_url_not_overridden_by_data() -> None:
    entry = DecodedEntry(
        type_name="HeroDefinitionDTO",
        data={"@type": "type.googleapis.com/WrongType", "definition_id": "hero.Example"},
    )

    result = entry_to_protojson_dict(entry)

    assert result["@type"] == "type.googleapis.com/HeroDefinitionDTO"
    assert result["definition_id"] == "hero.Example"


def test_group_by_type_wraps_entries_with_type_url() -> None:
    entries = [
        DecodedEntry(
            type_name="HeroDefinitionDTO",
            data={"definition_id": "hero.A"},
        ),
        DecodedEntry(
            type_name="HeroDefinitionDTO",
            data={"definition_id": "hero.B"},
        ),
    ]

    grouped = _group_by_type(entries)

    assert grouped == {
        "HeroDefinitionDTO": [
            {
                "@type": "type.googleapis.com/HeroDefinitionDTO",
                "definition_id": "hero.A",
            },
            {
                "@type": "type.googleapis.com/HeroDefinitionDTO",
                "definition_id": "hero.B",
            },
        ],
    }


def test_write_gamedesign_export_includes_type_url(tmp_path: Path) -> None:
    hero_entries = [
        DecodedEntry(
            type_name="HeroDefinitionDTO",
            data={
                "definition_id": "hero.Example",
                "components": [
                    {
                        "@type": "type.googleapis.com/HeroSupportUnitComponentDTO",
                        "unit_type_definition_id": "unit_type.Cavalry",
                    }
                ],
            },
        ),
    ]

    write_gamedesign_export(
        tmp_path,
        source="test",
        source_path=None,
        checksum="abc",
        all_entries=hero_entries,
        hero_entries=hero_entries,
    )

    items = json.loads((tmp_path / "heroes" / "HeroDefinitionDTO.json").read_text())
    assert len(items) == 1
    assert items[0]["@type"] == "type.googleapis.com/HeroDefinitionDTO"
    assert items[0]["definition_id"] == "hero.Example"
    assert items[0]["components"][0]["@type"] == (
        "type.googleapis.com/HeroSupportUnitComponentDTO"
    )


def test_write_definitions_export_includes_type_url(tmp_path: Path) -> None:
    entries = [
        DecodedEntry(
            type_name="PlayerDTO",
            data={"player_id": "123"},
        ),
    ]

    write_definitions_export(
        tmp_path,
        source="startup",
        source_path=Path("/tmp/startup.raw"),
        entries=entries,
        warnings=[],
    )

    items = json.loads((tmp_path / "PlayerDTO.json").read_text())
    assert items == [
        {
            "@type": "type.googleapis.com/PlayerDTO",
            "player_id": "123",
        }
    ]
