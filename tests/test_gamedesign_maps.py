"""Tests for gamedesign decoding over protobuf map fields."""

from __future__ import annotations

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
from google.protobuf.descriptor_database import DescriptorDatabase

from xapk_to_proto.gamedesign import _normalize_any_type_urls


def _minimal_map_pool() -> descriptor_pool.DescriptorPool:
    sub = descriptor_pb2.DescriptorProto()
    sub.name = "SubMessage"

    parent = descriptor_pb2.DescriptorProto()
    parent.name = "ParentMessage"
    map_entry = parent.nested_type.add()
    map_entry.name = "CountsEntry"
    map_entry.options.map_entry = True
    key = map_entry.field.add()
    key.name = "key"
    key.number = 1
    key.type = descriptor_pb2.FieldDescriptorProto.TYPE_INT32
    val = map_entry.field.add()
    val.name = "value"
    val.number = 2
    val.type = descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
    val.type_name = ".test.SubMessage"
    counts = parent.field.add()
    counts.name = "counts"
    counts.number = 1
    counts.label = descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED
    counts.type = descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
    counts.type_name = ".test.ParentMessage.CountsEntry"

    fd = descriptor_pb2.FileDescriptorProto()
    fd.name = "test_maps.proto"
    fd.package = "test"
    fd.message_type.append(sub)
    fd.message_type.append(parent)

    db = DescriptorDatabase()
    db.Add(fd)
    return descriptor_pool.DescriptorPool(db)


def test_normalize_any_type_urls_handles_int_keyed_maps() -> None:
    pool = _minimal_map_pool()
    parent_cls = message_factory.GetMessageClass(pool.FindMessageTypeByName("test.ParentMessage"))
    sub_cls = message_factory.GetMessageClass(pool.FindMessageTypeByName("test.SubMessage"))
    msg = parent_cls()
    msg.counts[1].CopyFrom(sub_cls())  # type: ignore[attr-defined]

    _normalize_any_type_urls(msg, pool, {})


def test_normalize_any_type_urls_handles_string_keyed_scalar_maps() -> None:
    parent_desc = descriptor_pb2.DescriptorProto()
    parent_desc.name = "ScalarMapMessage"
    map_entry = parent_desc.nested_type.add()
    map_entry.name = "ItemsEntry"
    map_entry.options.map_entry = True
    map_entry.field.add(
        name="key", number=1, type=descriptor_pb2.FieldDescriptorProto.TYPE_STRING
    )
    map_entry.field.add(
        name="value", number=2, type=descriptor_pb2.FieldDescriptorProto.TYPE_INT32
    )
    field = parent_desc.field.add()
    field.name = "items"
    field.number = 1
    field.label = descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED
    field.type = descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
    field.type_name = ".ScalarMapMessage.ItemsEntry"
    fd = descriptor_pb2.FileDescriptorProto()
    fd.name = "scalar_map.proto"
    fd.message_type.append(parent_desc)
    db = DescriptorDatabase()
    db.Add(fd)
    pool = descriptor_pool.DescriptorPool(db)
    msg_cls = message_factory.GetMessageClass(
        pool.FindMessageTypeByName("ScalarMapMessage")
    )
    msg = msg_cls()
    msg.items["building.example"] = 3  # type: ignore[attr-defined]

    _normalize_any_type_urls(msg, pool, {})
