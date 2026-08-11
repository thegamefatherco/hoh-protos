"""Tests for data-driven wire-type correction."""

from __future__ import annotations

import shutil
import struct
from pathlib import Path

import pytest
from google.protobuf import descriptor_pb2

from tests.fixture_paths import FIXTURE_DESCRIPTORS, FIXTURE_GAMEDESIGN
from xapk_to_proto import gamedesign, wirefix

FT = descriptor_pb2.FieldDescriptorProto


def _encode_double_value(value: float) -> bytes:
    payload = struct.pack("<d", value)
    tag = (1 << 3) | 1
    return bytes([tag]) + payload


def _encode_varint(value: int) -> bytes:
    out = bytearray()
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value & 0x7F)
    return bytes(out)


def test_correct_field_maps_double_wrapper() -> None:
    field = descriptor_pb2.FieldDescriptorProto()
    field.name = "min"
    field.number = 5
    field.type = FT.TYPE_DOUBLE

    sample = _encode_double_value(40.0)
    new_type, new_type_name, deps = wirefix.correct_field(
        field,
        wirefix.WIRE_LEN,
        sample,
        nested_enum_names=frozenset(),
    )

    assert new_type == FT.TYPE_MESSAGE
    assert new_type_name == ".google.protobuf.DoubleValue"
    assert field.type == FT.TYPE_MESSAGE
    assert field.type_name == ".google.protobuf.DoubleValue"
    assert wirefix.GOOGLE_WRAPPERS_IMPORT in deps


def test_correct_field_maps_message_to_nested_enum() -> None:
    field = descriptor_pb2.FieldDescriptorProto()
    field.name = "mode"
    field.number = 2
    field.type = FT.TYPE_MESSAGE
    field.type_name = ".SampleDTO.Mode"

    new_type, new_type_name, deps = wirefix.correct_field(
        field,
        wirefix.WIRE_VARINT,
        _encode_varint(1),
        nested_enum_names=frozenset({"Mode"}),
    )

    assert new_type == FT.TYPE_ENUM
    assert new_type_name == ".SampleDTO.Mode"
    assert field.type == FT.TYPE_ENUM
    assert deps == set()


def test_apply_corrections_updates_descriptor_set() -> None:
    fd = descriptor_pb2.FileDescriptorProto()
    fd.name = "sample.proto"
    fd.syntax = "proto3"
    msg = fd.message_type.add()
    msg.name = "SampleDTO"
    enum = msg.enum_type.add()
    enum.name = "Mode"
    enum.value.add(name="ALPHA", number=0)
    enum.value.add(name="BETA", number=1)
    fld = msg.field.add()
    fld.name = "mode"
    fld.number = 2
    fld.label = FT.LABEL_OPTIONAL
    fld.type = FT.TYPE_MESSAGE
    fld.type_name = "Mode"

    fds = descriptor_pb2.FileDescriptorSet()
    fds.file.add().CopyFrom(fd)

    mismatch = wirefix.Mismatch(
        message_full_name="SampleDTO",
        field_number=2,
        field_name="mode",
        declared_type=FT.TYPE_MESSAGE,
        observed_wire_type=wirefix.WIRE_VARINT,
        sample_bytes=_encode_varint(1),
    )
    applied = wirefix.apply_corrections(fds, [mismatch])

    assert len(applied) == 1
    corrected = fds.file[0].message_type[0].field[0]
    assert corrected.type == FT.TYPE_ENUM
    assert corrected.type_name == ".Mode"


@pytest.mark.skipif(
    not FIXTURE_DESCRIPTORS.is_file() or not FIXTURE_GAMEDESIGN.is_file(),
    reason="requires generated descriptors and gamedesign fixture",
)
def test_run_wirefix_on_fixture_reaches_fixed_point(tmp_path: Path) -> None:
    descriptors = tmp_path / "descriptors.pb"
    shutil.copy(FIXTURE_DESCRIPTORS, descriptors)

    report = wirefix.run_wirefix(
        descriptors,
        FIXTURE_GAMEDESIGN,
        verbose=False,
    )

    pool, short_idx = gamedesign.load_descriptor_pool(descriptors)
    gd = gamedesign._parse_gamedesign_message(
        FIXTURE_GAMEDESIGN.read_bytes(),
        pool,
    )
    assert gd is not None
    remaining = wirefix.detect_mismatches(pool, short_idx, gd)
    assert remaining == []

    # Fresh extracts typically need ≥1 correction; regenerated trees may already
    # be fixed (fixed_count == 0). Either way the DoubleValue mapping must hold.
    if report.fixed_count >= 1:
        assert any(
            corr.message_full_name == "HeroUnitStatConfigDefinitionDTO"
            and corr.field_name == "min"
            and corr.new_type_name == ".google.protobuf.DoubleValue"
            for corr in report.corrections
        )

    desc = pool.FindMessageTypeByName("HeroUnitStatConfigDefinitionDTO")
    min_field = desc.fields_by_name["min"]
    assert min_field.message_type.full_name == "google.protobuf.DoubleValue"

    (tmp_path / "hero_unit.proto").write_text(
        _emit_single_file(descriptors, "hero_unit.proto"),
        encoding="utf-8",
    )
    text = (tmp_path / "hero_unit.proto").read_text(encoding="utf-8")
    assert "google.protobuf.DoubleValue min = 5" in text


def _emit_single_file(descriptors: Path, name: str) -> str:
    from xapk_to_proto import emit
    from xapk_to_proto.repair import build_nested_type_index, repair_file_descriptor

    fds = descriptor_pb2.FileDescriptorSet()
    fds.ParseFromString(descriptors.read_bytes())
    nested_index, enum_index = build_nested_type_index(list(fds.file))
    for fd in fds.file:
        if fd.name == name:
            repaired = descriptor_pb2.FileDescriptorProto()
            repaired.CopyFrom(fd)
            repair_file_descriptor(
                repaired, nested_index=nested_index, enum_index=enum_index
            )
            return emit.emit_file(repaired)
    raise AssertionError(f"{name} not in descriptor set")
