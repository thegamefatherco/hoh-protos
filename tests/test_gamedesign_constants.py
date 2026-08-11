"""Tests for GameDesign *Constants → TypeScript string enum export."""

from __future__ import annotations

from pathlib import Path

import pytest

from xapk_to_proto.gamedesign_constants import (
    ConstantsClass,
    StringConstant,
    escape_ts_string,
    enum_name_from_class,
    parse_gamedesign_constants,
    render_enum_ts,
    render_index_ts,
    run_gamedesign_constants_export,
    unescape_csharp_string,
    write_gamedesign_constants_ts,
)

from tests.fixture_paths import FIXTURE_DUMP_CS

SAMPLE_DUMP = """\
// Namespace: InnoGames.Game.UI
public static class UiConstants // TypeDefIndex: 1
{
	public const string ShouldIgnore = "ui.ignore";
}

// Namespace: InnoGames.Generated.GameDesign
public static class EquipmentRarityConstants // TypeDefIndex: 11886
{
	// Fields
	public const string EquipmentRarity2 = "equipment_rarity.2";
	public const string EquipmentRarity3 = "equipment_rarity.3";
	public const string EquipmentRarity4 = "equipment_rarity.4";
	public const string EquipmentRarity5 = "equipment_rarity.5";
	public static readonly Dictionary<EquipmentRarityConstants.AsEnum, string> ByEnumMapping; // 0x0
	public static readonly Dictionary<string, EquipmentRarityConstants.AsEnum> ByValueMapping; // 0x8

	// Methods
	public static EquipmentRarityConstants.AsEnum ParseString(string stringValue) { }
}

// Namespace: InnoGames.Generated.GameDesign
public static class ShopOfferFocusBackgroundConstants // TypeDefIndex: 11851
{
}

// Namespace: InnoGames.Generated.GameDesign
public static class ResourceConstants // TypeDefIndex: 11811
{
	public const string BuildingPiece = "resource.BuildingPiece|Building_BronzeAge_Collectable_Amphitheatre_1";
	public const string Quoted = "has \\"quote\\" inside";
}
"""


def test_enum_name_from_class_strips_suffix() -> None:
    assert enum_name_from_class("EquipmentRarityConstants") == "EquipmentRarity"
    assert enum_name_from_class("Resource") == "Resource"


def test_unescape_and_escape_roundtrip() -> None:
    assert unescape_csharp_string(r'has \"quote\" inside') == 'has "quote" inside'
    assert escape_ts_string('has "quote" inside') == r'has \"quote\" inside'
    assert escape_ts_string("a|b") == "a|b"
    assert escape_ts_string("back\\slash") == r"back\\slash"


def test_parse_extracts_string_consts_only(tmp_path: Path) -> None:
    dump = tmp_path / "dump.cs"
    dump.write_text(SAMPLE_DUMP, encoding="utf-8")

    classes, warnings = parse_gamedesign_constants(dump)

    assert [c.class_name for c in classes] == [
        "EquipmentRarityConstants",
        "ResourceConstants",
    ]
    assert classes[0].enum_name == "EquipmentRarity"
    assert [(c.member, c.value) for c in classes[0].constants] == [
        ("EquipmentRarity2", "equipment_rarity.2"),
        ("EquipmentRarity3", "equipment_rarity.3"),
        ("EquipmentRarity4", "equipment_rarity.4"),
        ("EquipmentRarity5", "equipment_rarity.5"),
    ]
    assert classes[1].constants[0].value == (
        "resource.BuildingPiece|Building_BronzeAge_Collectable_Amphitheatre_1"
    )
    assert classes[1].constants[1].value == 'has "quote" inside'
    assert any("ShopOfferFocusBackgroundConstants" in w for w in warnings)
    assert all("UiConstants" not in w for w in warnings)
    assert all(c.class_name != "UiConstants" for c in classes)


def test_render_enum_ts_escapes_pipe_and_quotes() -> None:
    cls = ConstantsClass(
        class_name="ResourceConstants",
        enum_name="Resource",
        constants=[
            StringConstant(
                "BuildingPiece",
                "resource.BuildingPiece|Building_X",
            ),
            StringConstant("Quoted", 'has "quote"'),
        ],
    )
    text = render_enum_ts(cls)
    assert "export enum Resource {" in text
    assert 'BuildingPiece = "resource.BuildingPiece|Building_X",' in text
    assert r'Quoted = "has \"quote\"",' in text
    assert "EquipmentRarityConstants" not in text
    assert "ResourceConstants" in text


def test_write_produces_files_and_index(tmp_path: Path) -> None:
    classes = [
        ConstantsClass(
            class_name="EquipmentRarityConstants",
            enum_name="EquipmentRarity",
            constants=[
                StringConstant("EquipmentRarity2", "equipment_rarity.2"),
                StringConstant("EquipmentRarity3", "equipment_rarity.3"),
            ],
        ),
        ConstantsClass(
            class_name="AgeConstants",
            enum_name="Age",
            constants=[StringConstant("Bronze", "age.bronze")],
        ),
    ]
    out = tmp_path / "constants"
    result = write_gamedesign_constants_ts(classes, out)

    assert result.enum_count == 2
    assert result.files_written == 3
    rarity = (out / "EquipmentRarity.ts").read_text(encoding="utf-8")
    assert 'EquipmentRarity2 = "equipment_rarity.2",' in rarity
    assert "export enum EquipmentRarity {" in rarity

    index = (out / "index.ts").read_text(encoding="utf-8")
    assert index == render_index_ts(classes)
    assert 'export { EquipmentRarity } from "./EquipmentRarity";' in index
    assert 'export { Age } from "./Age";' in index


def test_run_end_to_end(tmp_path: Path) -> None:
    dump = tmp_path / "dump.cs"
    dump.write_text(SAMPLE_DUMP, encoding="utf-8")
    out = tmp_path / "out"

    result = run_gamedesign_constants_export(dump, out)

    assert result.enum_count == 2
    assert (out / "EquipmentRarity.ts").is_file()
    assert (out / "Resource.ts").is_file()
    assert (out / "index.ts").is_file()
    assert not (out / "ShopOfferFocusBackground.ts").exists()
    assert any("ShopOfferFocusBackgroundConstants" in w for w in result.warnings)


def test_run_missing_dump_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        run_gamedesign_constants_export(tmp_path / "missing.cs", tmp_path / "out")


def test_integration_un0_dump_cs() -> None:
    if not FIXTURE_DUMP_CS.is_file():
        pytest.skip(f"fixture not found: {FIXTURE_DUMP_CS}")

    classes, warnings = parse_gamedesign_constants(FIXTURE_DUMP_CS)
    assert len(classes) == 55
    assert any("ShopOfferFocusBackgroundConstants" in w for w in warnings)

    names = {c.enum_name for c in classes}
    assert "EquipmentRarity" in names
    assert len(names) == len(classes)

    rarity = next(c for c in classes if c.enum_name == "EquipmentRarity")
    assert rarity.constants[0].value == "equipment_rarity.2"
