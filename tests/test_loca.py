"""Tests for CompressedLocaResponse / LocaKeys extraction."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from xapk_to_proto.loca import (
    PREFIX_DIR,
    PREFIX_I18NEXT_DIR,
    PREFIX_ICU_DIR,
    UNRESOLVED_PREFIX,
    build_compressed_collection,
    compress_loca_entry,
    decompress_loca_entry,
    encode_loca_values,
    fnv1a64,
    loca_key_prefix,
    loca_keys_to_hash_map,
    normalize_placeholder_name,
    parse_compressed_collection,
    parse_loca_keys,
    parse_loca_templates,
    parse_loca_values,
    resolve_catalog,
    run_loca_export,
    split_catalog_by_prefix,
    strip_csharp_format_specs,
    to_gettext_po,
    to_i18next_catalog,
    to_i18next_placeholders,
    to_icu_catalog,
    write_loca_export,
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

SAMPLE_CATALOG = {
    "Base.Rarities.Common": ["Common"],
    "Base.QuestlinesPanel.FinishPvpBattleInTierTask": [
        "Finish <b>{0:d}</b> Tier {1:s} battle in the <b>{2:s}</b>.",
        "Finish <b>{0:d}</b> Tier {1:s} battles in the <b>{2:s}</b>.",
    ],
    "Base.AllianceQuarterPanel.QuarterNameAndLevel": [
        "{0}<alpha=#60><space=0.5em>Lv {1}",
    ],
    "Base.Abilities.example_Desc": [
        "Deal {dmg_percentage} damage over {duration}.",
    ],
}


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


def test_resolve_catalog(tmp_path: Path) -> None:
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


def test_normalize_and_strip_placeholders() -> None:
    assert normalize_placeholder_name("0") == "0"
    assert normalize_placeholder_name("0:d") == "0"
    assert normalize_placeholder_name("1:s") == "1"
    assert normalize_placeholder_name("0:%d") == "0"
    assert normalize_placeholder_name("duration") == "duration"
    assert normalize_placeholder_name("0:d\\:hh\\:mm\\:ss") == "0"

    text = "Finish <b>{0:d}</b> Tier {1:s} in {duration}"
    assert strip_csharp_format_specs(text) == (
        "Finish <b>{0}</b> Tier {1} in {duration}"
    )
    assert to_i18next_placeholders(text) == (
        "Finish <b>{{0}}</b> Tier {{1}} in {{duration}}"
    )


def test_to_i18next_catalog() -> None:
    out = to_i18next_catalog(SAMPLE_CATALOG)
    assert out["Base.Rarities.Common"] == "Common"
    assert out["Base.QuestlinesPanel.FinishPvpBattleInTierTask_one"] == (
        "Finish <b>{{0}}</b> Tier {{1}} battle in the <b>{{2}}</b>."
    )
    assert out["Base.QuestlinesPanel.FinishPvpBattleInTierTask_other"] == (
        "Finish <b>{{0}}</b> Tier {{1}} battles in the <b>{{2}}</b>."
    )
    assert "Base.QuestlinesPanel.FinishPvpBattleInTierTask" not in out
    assert out["Base.AllianceQuarterPanel.QuarterNameAndLevel"] == (
        "{{0}}<alpha=#60><space=0.5em>Lv {{1}}"
    )
    assert out["Base.Abilities.example_Desc"] == (
        "Deal {{dmg_percentage}} damage over {{duration}}."
    )


def test_to_icu_catalog() -> None:
    out = to_icu_catalog(SAMPLE_CATALOG)
    assert out["Base.Rarities.Common"] == "Common"
    plural = out["Base.QuestlinesPanel.FinishPvpBattleInTierTask"]
    assert plural.startswith("{count, plural, one {")
    assert "Finish <b>{0}</b> Tier {1} battle" in plural
    assert "Finish <b>{0}</b> Tier {1} battles" in plural
    assert out["Base.AllianceQuarterPanel.QuarterNameAndLevel"] == (
        "{0}<alpha=#60><space=0.5em>Lv {1}"
    )


def test_loca_key_prefix() -> None:
    assert loca_key_prefix("Base.Rarities.Common") == "Base.Rarities"
    assert loca_key_prefix("Base.Abilities.hero_battle_ability.Abacus_Name") == (
        "Base.Abilities"
    )
    assert loca_key_prefix("0x000000000000dead") == UNRESOLVED_PREFIX
    assert loca_key_prefix("lonely") == UNRESOLVED_PREFIX


def test_split_catalog_by_prefix() -> None:
    catalog = {
        **SAMPLE_CATALOG,
        "0x000000000000dead": ["orphan"],
        "lonely": ["no dots"],
    }
    buckets = split_catalog_by_prefix(catalog)
    assert list(buckets) == [
        "Base.Abilities",
        "Base.AllianceQuarterPanel",
        "Base.QuestlinesPanel",
        "Base.Rarities",
        UNRESOLVED_PREFIX,
    ]
    assert buckets["Base.Rarities"] == {"Base.Rarities.Common": ["Common"]}
    assert buckets[UNRESOLVED_PREFIX] == {
        "0x000000000000dead": ["orphan"],
        "lonely": ["no dots"],
    }


def test_write_loca_export_splits_by_prefix(tmp_path: Path) -> None:
    catalog = {
        **SAMPLE_CATALOG,
        "0x000000000000dead": ["orphan"],
    }
    result = write_loca_export(
        tmp_path,
        catalog=catalog,
        meta={"locale": "en_DK", "checksum": "x", "version": "v1"},
        templates={"RarityLocaKey": "Base.Rarities.{0}"},
        warnings=[],
    )
    prefix_count = 5  # four Base.* groups + _unresolved
    assert result.files_written == 5 + 3 * prefix_count

    rarities = json.loads(
        (tmp_path / PREFIX_DIR / "Base.Rarities.json").read_text(encoding="utf-8")
    )
    assert rarities == {"Base.Rarities.Common": ["Common"]}

    i18next = json.loads(
        (
            tmp_path / PREFIX_DIR / PREFIX_I18NEXT_DIR / "Base.Rarities.i18next.json"
        ).read_text(encoding="utf-8")
    )
    assert i18next["Base.Rarities.Common"] == "Common"

    icu = json.loads(
        (tmp_path / PREFIX_DIR / PREFIX_ICU_DIR / "Base.Rarities.icu.json").read_text(
            encoding="utf-8"
        )
    )
    assert icu["Base.Rarities.Common"] == "Common"

    unresolved = json.loads(
        (tmp_path / PREFIX_DIR / f"{UNRESOLVED_PREFIX}.json").read_text(
            encoding="utf-8"
        )
    )
    assert unresolved == {"0x000000000000dead": ["orphan"]}
    assert not list((tmp_path / PREFIX_DIR).glob("*.i18next.json"))
    assert not list((tmp_path / PREFIX_DIR).glob("*.icu.json"))
    assert not list((tmp_path / PREFIX_DIR).glob("*.po"))

    meta = json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))
    assert meta["prefix_dir"] == PREFIX_DIR
    assert meta["prefixes"]["Base.Rarities"] == {
        "entry_count": 1,
        "file": "by_prefix/Base.Rarities.json",
    }
    assert meta["prefixes"][UNRESOLVED_PREFIX]["entry_count"] == 1


def test_write_loca_export_wipes_stale_prefix_files(tmp_path: Path) -> None:
    stale = tmp_path / PREFIX_DIR / "Stale.Group.json"
    stale.parent.mkdir()
    stale.write_text("{}", encoding="utf-8")
    write_loca_export(
        tmp_path,
        catalog={"Base.Rarities.Common": ["Common"]},
        meta={"locale": "en_DK"},
        templates={},
        warnings=[],
    )
    assert not stale.exists()
    assert (tmp_path / PREFIX_DIR / "Base.Rarities.json").is_file()


def test_to_gettext_po() -> None:
    po = to_gettext_po(SAMPLE_CATALOG, locale="en_DK")
    assert 'msgctxt "Base.Rarities.Common"' in po
    assert 'msgid "Common"' in po
    assert 'msgstr "Common"' in po
    assert 'msgctxt "Base.QuestlinesPanel.FinishPvpBattleInTierTask"' in po
    assert 'msgid "Finish <b>{0}</b> Tier {1} battle in the <b>{2}</b>."' in po
    assert 'msgid_plural "Finish <b>{0}</b> Tier {1} battles in the <b>{2}</b>."' in po
    assert 'msgstr[0]' in po
    assert 'msgstr[1]' in po
    assert "<alpha=#60>" in po
    assert "Plural-Forms: nplurals=2" in po


@pytest.mark.skipif(
    not FIXTURE_LOCA.is_file() or not FIXTURE_DESCRIPTORS.is_file(),
    reason="loca fixture or descriptors.pb missing",
)
@pytest.mark.skipif(not FIXTURE_DUMP_CS.is_file(), reason="dump.cs missing")
def test_golden_fixture_multi_format_export(tmp_path: Path) -> None:
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
    assert result.files_written >= 5

    catalog = json.loads((out / "en_DK.json").read_text(encoding="utf-8"))
    assert catalog["Base.Rarities.Common"] == ["Common"]
    assert catalog["Base.Rarities.Uncommon"] == ["Uncommon"]
    assert catalog["Base.Rarities.Rare"] == ["Rare"]
    assert catalog["Base.Rarities.Epic"] == ["Epic"]
    assert catalog["Base.Rarities.Legendary"] == ["Legendary"]

    i18next = json.loads((out / "en_DK.i18next.json").read_text(encoding="utf-8"))
    assert i18next["Base.Rarities.Common"] == "Common"
    assert isinstance(i18next["Base.Rarities.Common"], str)

    icu = json.loads((out / "en_DK.icu.json").read_text(encoding="utf-8"))
    assert icu["Base.Rarities.Common"] == "Common"

    po = (out / "en_DK.po").read_text(encoding="utf-8")
    assert 'msgctxt "Base.Rarities.Common"' in po

    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert meta["templates"]["RarityLocaKey"] == "Base.Rarities.{0}"
    assert meta["formats"] == [
        "en_DK.json",
        "en_DK.i18next.json",
        "en_DK.icu.json",
        "en_DK.po",
    ]
    assert meta["prefix_dir"] == PREFIX_DIR
    assert meta["prefixes"]["Base.Rarities"]["file"] == (
        "by_prefix/Base.Rarities.json"
    )
    assert meta["prefixes"]["Base.Rarities"]["entry_count"] > 0
    assert result.files_written == 5 + 3 * len(meta["prefixes"])

    rarities_i18next = json.loads(
        (out / PREFIX_DIR / PREFIX_I18NEXT_DIR / "Base.Rarities.i18next.json").read_text(
            encoding="utf-8"
        )
    )
    assert rarities_i18next["Base.Rarities.Common"] == "Common"
    assert (out / PREFIX_DIR / PREFIX_ICU_DIR / "Base.Rarities.icu.json").is_file()
    assert not list((out / PREFIX_DIR).glob("*.i18next.json"))
    assert not list((out / PREFIX_DIR).glob("*.icu.json"))

    assert not list(out.glob("*.ts"))
