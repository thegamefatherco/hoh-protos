"""Tests for CompressedLocaResponse / LocaKeys extraction."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from xapk_to_proto.loca import (
    build_compressed_collection,
    build_display_maps,
    compress_loca_entry,
    decompress_loca_entry,
    encode_loca_values,
    fnv1a64,
    loca_keys_to_hash_map,
    parse_compressed_collection,
    parse_loca_keys,
    parse_loca_templates,
    parse_loca_values,
    resolve_catalog,
    run_loca_export,
)

from tests.fixture_paths import (
    FIXTURE_DESCRIPTORS,
    FIXTURE_DUMP_CS,
    FIXTURE_LOCA,
)

SAMPLE_DUMP = """\
// Namespace: 
public class LocaKeys.Base.Rarities // TypeDefIndex: 12190
{
	public const string RaritiesKey = "Base.Rarities";
	public const string Common = "Base.Rarities.Common";
	public const string Epic = "Base.Rarities.Epic";
	public const string Legendary = "Base.Rarities.Legendary";
	public const string Rare = "Base.Rarities.Rare";
	public const string Uncommon = "Base.Rarities.Uncommon";

	public void .ctor() { }
}

// Namespace: 
public class LocaKeys.Base.HeroClass // TypeDefIndex: 1
{
	public const string HeroClassKey = "Base.HeroClass";
	public const string Fighter = "Base.HeroClass.Fighter";
	public const string Support = "Base.HeroClass.Support";

	public void .ctor() { }
}

	private const string RarityLocaKey = "Base.Rarities.{0}";
	private const string HeroClassNameLocaKey = "Base.HeroClass.{0}";
	public const string UnitNameLocaKey = "Base.Units.{0}_Name";
"""


def test_fnv1a64_matches_known_rarity_key() -> None:
    # Verified against fixtures/un0/1.50.3/loca-compressed index.
    assert fnv1a64("Base.Rarities.Common") == 0x89878DE289EE15E4


def test_encode_parse_values_roundtrip() -> None:
    values = ["Common", "with \"quotes\""]
    assert parse_loca_values(encode_loca_values(values)) == values


def test_compress_decompress_entry_roundtrip() -> None:
    values = ["Legendary"]
    blob = compress_loca_entry(values, stream_position=0x3D62C)
    assert decompress_loca_entry(blob) == values


def test_build_and_parse_compressed_collection() -> None:
    entries = {
        "Base.Rarities.Common": ["Common"],
        "Base.Rarities.Rare": ["Rare"],
        "Base.Rarities.Epic": ["Epic"],
    }
    data = build_compressed_collection(entries)
    hashed = parse_compressed_collection(data)
    assert hashed[fnv1a64("Base.Rarities.Common")] == ["Common"]
    assert hashed[fnv1a64("Base.Rarities.Rare")] == ["Rare"]
    assert hashed[fnv1a64("Base.Rarities.Epic")] == ["Epic"]


def test_parse_loca_keys_and_templates(tmp_path: Path) -> None:
    dump = tmp_path / "dump.cs"
    dump.write_text(SAMPLE_DUMP, encoding="utf-8")

    classes, warnings = parse_loca_keys(dump)
    assert warnings == []
    assert [c.path for c in classes] == ["Base.Rarities", "Base.HeroClass"]
    rarities = classes[0]
    assert [c.member for c in rarities.constants] == [
        "RaritiesKey",
        "Common",
        "Epic",
        "Legendary",
        "Rare",
        "Uncommon",
    ]

    templates = parse_loca_templates(dump)
    assert templates["RarityLocaKey"] == "Base.Rarities.{0}"
    assert templates["UnitNameLocaKey"] == "Base.Units.{0}_Name"


def test_resolve_catalog_and_display_maps(tmp_path: Path) -> None:
    dump = tmp_path / "dump.cs"
    dump.write_text(SAMPLE_DUMP, encoding="utf-8")
    classes, _ = parse_loca_keys(dump)
    hash_to_key = loca_keys_to_hash_map(classes)

    hashed = {
        fnv1a64("Base.Rarities.Common"): ["Common"],
        fnv1a64("Base.Rarities.Rare"): ["Rare"],
        0xDEAD: ["orphan"],
    }
    catalog, unresolved = resolve_catalog(hashed, hash_to_key)
    assert unresolved == 1
    assert catalog["Base.Rarities.Common"] == ["Common"]
    assert catalog["0x000000000000dead"] == ["orphan"]

    maps = build_display_maps(classes, catalog)
    assert "Rarity" in maps
    assert 'COMMON: "Common"' in maps["Rarity"]
    assert 'RARE: "Rare"' in maps["Rarity"]


@pytest.mark.skipif(
    not FIXTURE_LOCA.is_file() or not FIXTURE_DESCRIPTORS.is_file(),
    reason="loca fixture or descriptors.pb missing",
)
@pytest.mark.skipif(not FIXTURE_DUMP_CS.is_file(), reason="dump.cs missing")
def test_golden_fixture_rarity_common(tmp_path: Path) -> None:
    out = tmp_path / "loca"
    result = run_loca_export(
        descriptors_pb=FIXTURE_DESCRIPTORS,
        dump_cs=FIXTURE_DUMP_CS,
        input_path=FIXTURE_LOCA,
        out_dir=out,
    )
    assert result.locale == "en_DK"
    assert result.entry_count > 10_000
    assert result.resolved_keys > 9_000

    catalog = json.loads((out / "en_DK.json").read_text(encoding="utf-8"))
    assert catalog["Base.Rarities.Common"] == ["Common"]
    assert catalog["Base.Rarities.Uncommon"] == ["Uncommon"]
    assert catalog["Base.Rarities.Rare"] == ["Rare"]
    assert catalog["Base.Rarities.Epic"] == ["Epic"]
    assert catalog["Base.Rarities.Legendary"] == ["Legendary"]

    rarity_ts = (out / "Rarity.ts").read_text(encoding="utf-8")
    assert "export const RarityDisplayName" in rarity_ts
    assert 'COMMON: "Common"' in rarity_ts
    assert 'LEGENDARY: "Legendary"' in rarity_ts

    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert meta["templates"]["RarityLocaKey"] == "Base.Rarities.{0}"
