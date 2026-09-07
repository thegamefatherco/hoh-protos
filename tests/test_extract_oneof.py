"""Tests for reconstructing proto oneofs from C# *OneofCase enums."""

from __future__ import annotations

from pathlib import Path

from google.protobuf import descriptor_pb2

from xapk_to_proto.emit import emit_message
from xapk_to_proto.extract import parse_dump_cs, protofile_to_fdp

_DUMP = """
// Namespace: InnoGames.Generated.Protobuf
public static class HeroBattleLogReflection // TypeDefIndex: 1
{
}

// Namespace:
public enum HeroBattleLogEntry.LogOneofCase // TypeDefIndex: 2
{
	public int value__; // 0x0
	public const HeroBattleLogEntry.LogOneofCase None = 0;
	public const HeroBattleLogEntry.LogOneofCase Attack = 10;
	public const HeroBattleLogEntry.LogOneofCase Ability = 11;
	public const HeroBattleLogEntry.LogOneofCase Effect = 12;
}

public sealed class HeroBattleAttackLogEntry : IMessage
{
	public const int DamageFieldNumber = 1;
	private float damage_; // 0x10
}

public sealed class HeroAbilityLogEntry : IMessage
{
	public const int AbilityIdFieldNumber = 1;
	private int abilityId_; // 0x10
}

public enum HeroEffectLogEntry.EffectOneofCase // TypeDefIndex: 3
{
	public int value__; // 0x0
	public const HeroEffectLogEntry.EffectOneofCase None = 0;
	public const HeroEffectLogEntry.EffectOneofCase AbilityBlock = 100;
	public const HeroEffectLogEntry.EffectOneofCase Aura = 101;
}

public sealed class HeroEffectLogEntry.Types.AbilityBlock : IMessage
{
}

public sealed class HeroEffectLogEntry.Types.Aura : IMessage
{
}

public sealed class HeroEffectLogEntry : IMessage
{
	public const int EffectIdFieldNumber = 4;
	private int effectId_; // 0x10
	public const int AbilityBlockFieldNumber = 100;
	public const int AuraFieldNumber = 101;
	private object effect_; // 0x18
	private HeroEffectLogEntry.EffectOneofCase effectCase_; // 0x20

	public int EffectId { get; set; }
	public HeroEffectLogEntry.Types.AbilityBlock AbilityBlock { get; set; }
	public HeroEffectLogEntry.Types.Aura Aura { get; set; }
	public HeroEffectLogEntry.EffectOneofCase EffectCase { get; }
}

public sealed class HeroBattleLogEntry : IMessage
{
	public const int MillisSinceWaveStartFieldNumber = 1;
	private long millisSinceWaveStart_; // 0x18
	public const int AttackFieldNumber = 10;
	public const int AbilityFieldNumber = 11;
	public const int EffectFieldNumber = 12;
	private object log_; // 0x20
	private HeroBattleLogEntry.LogOneofCase logCase_; // 0x28

	public long MillisSinceWaveStart { get; set; }
	public HeroBattleAttackLogEntry Attack { get; set; }
	public HeroAbilityLogEntry Ability { get; set; }
	public HeroEffectLogEntry Effect { get; set; }
	public HeroBattleLogEntry.LogOneofCase LogCase { get; }
}
"""


def test_parse_dump_reconstructs_oneof_from_oneof_case(tmp_path: Path) -> None:
    dump_path = tmp_path / "dump.cs"
    dump_path.write_text(_DUMP, encoding="utf-8")

    protos = parse_dump_cs(dump_path)
    assert "hero_battle_log.proto" in protos
    pf = protos["hero_battle_log.proto"]

    entry = next(m for m in pf.messages if m.name == "HeroBattleLogEntry")
    assert entry.oneofs == ["log"]
    by_number = {f.number: f for f in entry.fields}
    assert by_number[1].name == "millis_since_wave_start"
    assert by_number[1].oneof_index is None
    assert by_number[10].name == "attack"
    assert by_number[10].type_name == "HeroBattleAttackLogEntry"
    assert by_number[10].oneof_index == 0
    assert by_number[11].name == "ability"
    assert by_number[11].oneof_index == 0
    assert by_number[12].name == "effect"
    assert by_number[12].oneof_index == 0

    # *OneofCase must not become a proto enum
    assert not any(e.name.endswith("OneofCase") for e in pf.enums)
    assert not any(e.name.endswith("OneofCase") for e in entry.nested_enums)

    effect = next(m for m in pf.messages if m.name == "HeroEffectLogEntry")
    assert effect.oneofs == ["effect"]
    effect_fields = {f.number: f for f in effect.fields}
    assert effect_fields[4].name == "effect_id"
    assert effect_fields[4].oneof_index is None
    assert effect_fields[100].name == "ability_block"
    assert effect_fields[100].type_name == "AbilityBlock"
    assert effect_fields[100].oneof_index == 0
    assert effect_fields[101].name == "aura"
    assert effect_fields[101].oneof_index == 0
    nested = {m.name for m in effect.nested_messages}
    assert "AbilityBlock" in nested
    assert "Aura" in nested


def test_protofile_to_fdp_and_emit_oneof(tmp_path: Path) -> None:
    dump_path = tmp_path / "dump.cs"
    dump_path.write_text(_DUMP, encoding="utf-8")

    pf = parse_dump_cs(dump_path)["hero_battle_log.proto"]
    fd = protofile_to_fdp(pf)

    entry = next(m for m in fd.message_type if m.name == "HeroBattleLogEntry")
    assert [o.name for o in entry.oneof_decl] == ["log"]
    attack = next(f for f in entry.field if f.number == 10)
    assert attack.name == "attack"
    assert attack.HasField("oneof_index")
    assert attack.oneof_index == 0
    assert attack.type == descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE

    enum_names = [e.name for e in fd.enum_type]
    assert "LogOneofCase" not in enum_names
    for msg in fd.message_type:
        assert not any(e.name.endswith("OneofCase") for e in msg.enum_type)

    text = "\n".join(emit_message(entry, "", fd.package))
    assert "oneof log {" in text
    assert "HeroBattleAttackLogEntry attack = 10;" in text
    assert "HeroAbilityLogEntry ability = 11;" in text
    assert "HeroEffectLogEntry effect = 12;" in text
    assert "millis_since_wave_start = 1;" in text

    effect = next(m for m in fd.message_type if m.name == "HeroEffectLogEntry")
    effect_text = "\n".join(emit_message(effect, "", fd.package))
    assert "oneof effect {" in effect_text
    assert "AbilityBlock ability_block = 100;" in effect_text
    assert "Aura aura = 101;" in effect_text


_NESTED_TYPES_ONEOF_DUMP = """
// Namespace: InnoGames.Generated.Protobuf
public static class CrmReflection // TypeDefIndex: 1
{
}

public enum CrmOfferResurfacingDTO.Types.TriggerDTO.ValueOneofCase // TypeDefIndex: 2
{
	public int value__; // 0x0
	public const CrmOfferResurfacingDTO.Types.TriggerDTO.ValueOneofCase None = 0;
	public const CrmOfferResurfacingDTO.Types.TriggerDTO.ValueOneofCase Event = 2;
	public const CrmOfferResurfacingDTO.Types.TriggerDTO.ValueOneofCase Interval = 3;
}

public sealed class CrmOfferResurfacingDTO.Types.TriggerDTO : IMessage
{
	public const int EventFieldNumber = 2;
	public const int IntervalFieldNumber = 3;
	private object value_; // 0x20
	private CrmOfferResurfacingDTO.Types.TriggerDTO.ValueOneofCase valueCase_; // 0x28

	public string Event { get; set; }
	public int Interval { get; set; }
	public CrmOfferResurfacingDTO.Types.TriggerDTO.ValueOneofCase ValueCase { get; }
}

public sealed class CrmOfferResurfacingDTO : IMessage
{
	public const int IdFieldNumber = 1;
	private string id_; // 0x10
}
"""


def test_nested_types_oneof_case_attaches_to_nested_message(tmp_path: Path) -> None:
    dump_path = tmp_path / "dump.cs"
    dump_path.write_text(_NESTED_TYPES_ONEOF_DUMP, encoding="utf-8")

    pf = parse_dump_cs(dump_path)["crm.proto"]
    parent = next(m for m in pf.messages if m.name == "CrmOfferResurfacingDTO")
    assert parent.oneofs == []
    assert not any("." in name for name in parent.oneofs)

    trigger = next(m for m in parent.nested_messages if m.name == "TriggerDTO")
    assert trigger.oneofs == ["value"]
    by_number = {f.number: f for f in trigger.fields}
    assert by_number[2].name == "event"
    assert by_number[2].oneof_index == 0
    assert by_number[3].name == "interval"
    assert by_number[3].oneof_index == 0

    fd = protofile_to_fdp(pf)
    parent_fd = next(m for m in fd.message_type if m.name == "CrmOfferResurfacingDTO")
    assert list(parent_fd.oneof_decl) == []
    trigger_fd = next(m for m in parent_fd.nested_type if m.name == "TriggerDTO")
    assert [o.name for o in trigger_fd.oneof_decl] == ["value"]
