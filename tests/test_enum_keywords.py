"""Tests for reserved enum value renaming."""

from __future__ import annotations

from google.protobuf import descriptor_pb2

from xapk_to_proto.emit import emit_file
from xapk_to_proto.enum_keywords import (
    rewrite_file_enum_values,
    rewrite_reserved_enum_value_name,
)
from xapk_to_proto.repair import repair_file_descriptor


def _enum_fd(
    *,
    enum_name: str = "CastEffectMovementPattern",
    values: list[tuple[str, int]] | None = None,
) -> descriptor_pb2.FileDescriptorProto:
    fd = descriptor_pb2.FileDescriptorProto()
    fd.name = "gamedesign_enums.proto"
    fd.syntax = "proto3"
    enum = fd.enum_type.add()
    enum.name = enum_name
    for name, number in values or [
        ("PROTO_DEFAULT", 0),
        ("Line", 1),
        ("None", 2),
        ("Spiral", 3),
    ]:
        ev = enum.value.add()
        ev.name = name
        ev.number = number
    return fd


def test_rewrite_reserved_enum_value_name_appends_underscore() -> None:
    assert rewrite_reserved_enum_value_name("None", set()) == "None_"
    assert rewrite_reserved_enum_value_name("Line", set()) == "Line"


def test_rewrite_reserved_enum_value_name_collision() -> None:
    assert rewrite_reserved_enum_value_name("None", {"None_"}) == "None__"


def test_rewrite_file_enum_values_renames_none() -> None:
    fd = _enum_fd()
    renamed = rewrite_file_enum_values(fd)
    assert renamed == 1
    names = [v.name for v in fd.enum_type[0].value]
    assert names == ["PROTO_DEFAULT", "Line", "None_", "Spiral"]
    assert fd.enum_type[0].value[2].number == 2


def test_rewrite_file_enum_values_idempotent() -> None:
    fd = _enum_fd()
    assert rewrite_file_enum_values(fd) == 1
    assert rewrite_file_enum_values(fd) == 0
    assert [v.name for v in fd.enum_type[0].value][2] == "None_"


def test_rewrite_file_enum_values_collision_with_existing_suffix() -> None:
    fd = _enum_fd(values=[("None_", 0), ("None", 1)])
    assert rewrite_file_enum_values(fd) == 1
    assert [v.name for v in fd.enum_type[0].value] == ["None_", "None__"]


def test_field_named_from_unchanged() -> None:
    fd = descriptor_pb2.FileDescriptorProto()
    fd.name = "fields.proto"
    fd.syntax = "proto3"
    msg = fd.message_type.add()
    msg.name = "Baz"
    field = msg.field.add()
    field.name = "from"
    field.number = 1
    field.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    field.type = descriptor_pb2.FieldDescriptorProto.TYPE_INT32

    assert rewrite_file_enum_values(fd) == 0
    assert msg.field[0].name == "from"


def test_nested_enum_rewritten() -> None:
    fd = descriptor_pb2.FileDescriptorProto()
    fd.name = "labyrinth_event.proto"
    fd.syntax = "proto3"
    msg = fd.message_type.add()
    msg.name = "LabyrinthNodeDefinitionDTO"
    enum = msg.enum_type.add()
    enum.name = "LabyrinthNodeTag"
    for name, number in (("None", 0), ("Bridge", 1)):
        ev = enum.value.add()
        ev.name = name
        ev.number = number

    assert rewrite_file_enum_values(fd) == 1
    assert [v.name for v in enum.value] == ["None_", "Bridge"]


def test_repair_emits_none_underscore() -> None:
    fd = _enum_fd()
    repair_file_descriptor(fd)
    text = emit_file(fd)
    assert "None_ = 2;" in text
    assert "None = 2;" not in text
    assert "Line = 1;" in text
