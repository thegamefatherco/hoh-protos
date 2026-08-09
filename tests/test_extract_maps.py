"""Tests for MapField<K, V> detection in C# message parsing."""

from __future__ import annotations

from google.protobuf import descriptor_pb2

from xapk_to_proto.extract import (
    FieldDef,
    MessageDef,
    ProtoFile,
    add_deps_from_fields,
    parse_message_block,
    protofile_to_fdp,
    snake_to_pascal,
)

CHEAT_PUSH_BLOCK = """
public sealed class CheatPush : IMessage<CheatPush> {
    public const int DataFieldNumber = 1;
    private static readonly MapField.Codec<string, Value> _map_data_codec;
    private readonly MapField<string, Value> data_;
}
"""

UNLOCKED_CITY_DTO_BLOCK = """
public sealed class UnlockedCityDTO : IMessage<UnlockedCityDTO> {
    public const int IdFieldNumber = 1;
    private readonly long id_;
    public const int DefinitionIdFieldNumber = 2;
    private readonly string definitionId_;
    public const int BuildingLimitsFieldNumber = 3;
    private readonly BuildingLimitsDTO buildingLimits_;
    public const int PlacedBuildingAmountsFieldNumber = 4;
    private static readonly MapField.Codec<string, int> _map_placedBuildingAmounts_codec;
    private readonly MapField<string, int> placedBuildingAmounts_;
    public const int WorkersFieldNumber = 5;
    private readonly RepeatedField<WorkerDTO> workers_;
}
"""

# Unity 6000 / Windows81 Il2CppDumper: non-generic IMessage + Descriptor property
# that must not be mistaken for the interface gate.
GAME_DESIGN_RESPONSE_BLOCK = """
public sealed class GameDesignResponse : IUiData, IEventSystemHandler, IVersioned, IMessage, IAsyncStateMachine
{
	private static readonly MessageParser<GameDesignResponse> _parser; // 0x0
	private UnknownFieldSet _unknownFields; // 0x10
	public const int ChecksumFieldNumber = 1;
	private string checksum_; // 0x18
	public const int ContentFieldNumber = 2;
	private static readonly FieldCodec<Any> _repeated_content_codec; // 0x8
	private readonly RepeatedField<Any> content_; // 0x20

	private MessageDescriptor pb::Google.Protobuf.IMessage.Descriptor { get; }
	public string Checksum { get; set; }
	public RepeatedField<Any> Content { get; }
}
"""

HERO_UNIT_STAT_CONFIG_BLOCK = """
public sealed class HeroUnitStatConfigDefinitionDTO : IUiData, IEventSystemHandler, IVersioned, IMessage, IAsyncStateMachine
{
	public const int DefinitionIdFieldNumber = 1;
	private string definitionId_; // 0x18
	public const int MinFieldNumber = 5;
	private static readonly FieldCodec<Nullable<double>> _single_min_codec; // 0x8
	private Nullable<double> min_; // 0x38

	private MessageDescriptor pb::Google.Protobuf.IMessage.Descriptor { get; }
}
"""


def _field_by_number(msg: MessageDef, number: int) -> FieldDef:
    return next(f for f in msg.fields if f.number == number)


def test_map_field_with_spaces_in_generic() -> None:
    msg = parse_message_block("UnlockedCityDTO", UNLOCKED_CITY_DTO_BLOCK)
    assert msg is not None
    placed = _field_by_number(msg, 4)
    assert placed.name == "placed_building_amounts"
    assert placed.label == descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED
    assert placed.map_key_type == descriptor_pb2.FieldDescriptorProto.TYPE_STRING
    assert placed.map_value_type == descriptor_pb2.FieldDescriptorProto.TYPE_INT32
    assert placed.type_name == "MapEntry"


def test_repeated_field_unchanged() -> None:
    msg = parse_message_block("UnlockedCityDTO", UNLOCKED_CITY_DTO_BLOCK)
    assert msg is not None
    workers = _field_by_number(msg, 5)
    assert workers.name == "workers"
    assert workers.label == descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED
    assert workers.type_name == "WorkerDTO"
    assert workers.map_key_type is None


def test_adjacent_map_and_repeated_fields_differ() -> None:
    msg = parse_message_block("UnlockedCityDTO", UNLOCKED_CITY_DTO_BLOCK)
    assert msg is not None
    placed = _field_by_number(msg, 4)
    workers = _field_by_number(msg, 5)
    assert placed.type_name != workers.type_name
    assert placed.map_key_type is not None
    assert workers.map_key_type is None


def test_snake_to_pascal_for_map_entry_names() -> None:
    assert snake_to_pascal("abilities_used") == "AbilitiesUsed"
    assert snake_to_pascal("placed_building_amounts") == "PlacedBuildingAmounts"


def test_map_field_with_well_known_value_type() -> None:
    msg = parse_message_block("CheatPush", CHEAT_PUSH_BLOCK)
    assert msg is not None
    data = _field_by_number(msg, 1)
    assert data.name == "data"
    assert data.map_key_type == descriptor_pb2.FieldDescriptorProto.TYPE_STRING
    assert data.map_value_type == descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
    assert data.map_value_type_name == "google.protobuf.Value"

    pf = ProtoFile(name="cheat.proto", package="", messages=[msg])
    add_deps_from_fields(pf, {})
    assert "google/protobuf/struct.proto" in pf.dependencies

    fd = protofile_to_fdp(pf)
    cheat_push = next(m for m in fd.message_type if m.name == "CheatPush")
    entry = next(n for n in cheat_push.nested_type if n.options.map_entry)
    value_field = next(f for f in entry.field if f.name == "value")
    assert value_field.type_name == ".google.protobuf.Value"


def test_protofile_to_fdp_emits_map_entry() -> None:
    msg = parse_message_block("UnlockedCityDTO", UNLOCKED_CITY_DTO_BLOCK)
    assert msg is not None
    pf = ProtoFile(name="city.proto", package="city", messages=[msg])
    fd = protofile_to_fdp(pf)

    unlocked = next(m for m in fd.message_type if m.name == "UnlockedCityDTO")
    placed_field = next(f for f in unlocked.field if f.name == "placed_building_amounts")
    assert placed_field.type == descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE

    map_entries = [n for n in unlocked.nested_type if n.options.map_entry]
    assert len(map_entries) == 1
    entry = map_entries[0]
    assert entry.field[0].name == "key"
    assert entry.field[0].type == descriptor_pb2.FieldDescriptorProto.TYPE_STRING
    assert entry.field[1].name == "value"
    assert entry.field[1].type == descriptor_pb2.FieldDescriptorProto.TYPE_INT32


def test_bare_imessage_parses_gamedesign_response() -> None:
    msg = parse_message_block("GameDesignResponse", GAME_DESIGN_RESPONSE_BLOCK)
    assert msg is not None
    assert len(msg.fields) == 2

    checksum = _field_by_number(msg, 1)
    assert checksum.name == "checksum"
    assert checksum.type == descriptor_pb2.FieldDescriptorProto.TYPE_STRING
    assert checksum.label == descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL

    content = _field_by_number(msg, 2)
    assert content.name == "content"
    assert content.label == descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED
    assert content.type == descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
    assert content.type_name == "google.protobuf.Any"


def test_bare_imessage_parses_nullable_wrapper_field() -> None:
    msg = parse_message_block(
        "HeroUnitStatConfigDefinitionDTO", HERO_UNIT_STAT_CONFIG_BLOCK
    )
    assert msg is not None
    assert len(msg.fields) == 2

    definition_id = _field_by_number(msg, 1)
    assert definition_id.name == "definition_id"
    assert definition_id.type == descriptor_pb2.FieldDescriptorProto.TYPE_STRING

    min_field = _field_by_number(msg, 5)
    assert min_field.name == "min"
    assert min_field.proto3_optional is True
    assert min_field.type == descriptor_pb2.FieldDescriptorProto.TYPE_DOUBLE


def test_descriptor_property_alone_is_not_a_message() -> None:
    """IMessage.Descriptor in the body must not satisfy the gate without a header match."""
    block = """
public sealed class NotAProto {
	private MessageDescriptor pb::Google.Protobuf.IMessage.Descriptor { get; }
	public const int ChecksumFieldNumber = 1;
	private string checksum_;
}
"""
    assert parse_message_block("NotAProto", block) is None
