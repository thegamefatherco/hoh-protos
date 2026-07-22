"""Data-driven wire-type correction for reconstructed protobuf descriptors."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

from google.protobuf import any_pb2, descriptor_pb2, descriptor_pool, message_factory
from google.protobuf.descriptor import FieldDescriptor as PFD
from google.protobuf.message import Message
from google.protobuf.unknown_fields import UnknownFieldSet

from xapk_to_proto import gamedesign

FT = descriptor_pb2.FieldDescriptorProto

VARINT_TYPES = frozenset(
    {
        FT.TYPE_INT32,
        FT.TYPE_INT64,
        FT.TYPE_UINT32,
        FT.TYPE_UINT64,
        FT.TYPE_SINT32,
        FT.TYPE_SINT64,
        FT.TYPE_BOOL,
        FT.TYPE_ENUM,
    }
)
I64_TYPES = frozenset({FT.TYPE_FIXED64, FT.TYPE_SFIXED64, FT.TYPE_DOUBLE})
I32_TYPES = frozenset({FT.TYPE_FIXED32, FT.TYPE_SFIXED32, FT.TYPE_FLOAT})
LEN_TYPES = frozenset({FT.TYPE_STRING, FT.TYPE_BYTES, FT.TYPE_MESSAGE, FT.TYPE_GROUP})

GOOGLE_WRAPPERS_IMPORT = "google/protobuf/wrappers.proto"
WRAPPER_BY_SCALAR: dict[int, str] = {
    FT.TYPE_DOUBLE: ".google.protobuf.DoubleValue",
    FT.TYPE_FLOAT: ".google.protobuf.FloatValue",
    FT.TYPE_INT64: ".google.protobuf.Int64Value",
    FT.TYPE_UINT64: ".google.protobuf.UInt64Value",
    FT.TYPE_INT32: ".google.protobuf.Int32Value",
    FT.TYPE_UINT32: ".google.protobuf.UInt32Value",
    FT.TYPE_BOOL: ".google.protobuf.BoolValue",
    FT.TYPE_STRING: ".google.protobuf.StringValue",
    FT.TYPE_BYTES: ".google.protobuf.BytesValue",
}

WIRE_VARINT = 0
WIRE_I64 = 1
WIRE_LEN = 2
WIRE_I32 = 5


@dataclass
class Mismatch:
    message_full_name: str
    field_number: int
    field_name: str
    declared_type: int
    observed_wire_type: int
    sample_bytes: bytes


@dataclass
class Correction:
    message_full_name: str
    field_number: int
    field_name: str
    old_type: int
    new_type: int
    new_type_name: str = ""


@dataclass
class WireFixReport:
    iterations: int
    corrections: list[Correction] = field(default_factory=list)

    @property
    def fixed_count(self) -> int:
        return len(self.corrections)


def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
    shift = 0
    value = 0
    while offset < len(data):
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, offset
        shift += 7
    raise ValueError("truncated varint")


def _expected_wire_types(fd: PFD) -> frozenset[int]:
    if fd.type in VARINT_TYPES:
        return frozenset({WIRE_VARINT, WIRE_LEN}) if fd.is_repeated else frozenset({WIRE_VARINT})
    if fd.type in I64_TYPES:
        return frozenset({WIRE_I64, WIRE_LEN}) if fd.is_repeated else frozenset({WIRE_I64})
    if fd.type in LEN_TYPES:
        return frozenset({WIRE_LEN})
    if fd.type in I32_TYPES:
        return frozenset({WIRE_I32, WIRE_LEN}) if fd.is_repeated else frozenset({WIRE_I32})
    return frozenset()



def _detect_wrapper_scalar(sample: bytes) -> int | None:
    """If sample looks like a google wrapper message, return inner scalar TYPE_*."""
    if not sample:
        return None
    try:
        offset = 0
        tag, offset = _read_varint(sample, offset)
        field_no = tag >> 3
        wire = tag & 7
        if field_no != 1:
            return None
        if wire == WIRE_I64:
            if offset + 8 > len(sample):
                return None
            return FT.TYPE_DOUBLE
        if wire == WIRE_I32:
            if offset + 4 > len(sample):
                return None
            return FT.TYPE_FLOAT
        if wire == WIRE_VARINT:
            _, offset = _read_varint(sample, offset)
            if offset != len(sample):
                return None
            return FT.TYPE_INT64
        if wire == WIRE_LEN:
            length, offset = _read_varint(sample, offset)
            end = offset + length
            if end != len(sample):
                return None
            chunk = sample[offset:end]
            try:
                chunk.decode("utf-8")
                return FT.TYPE_STRING
            except UnicodeDecodeError:
                return FT.TYPE_BYTES
    except ValueError:
        return None
    return None


def _varint_fallback(declared_type: int) -> int:
    if declared_type in VARINT_TYPES:
        return declared_type
    if declared_type == FT.TYPE_ENUM:
        return FT.TYPE_ENUM
    return FT.TYPE_INT64


def _i64_fallback(declared_type: int) -> int:
    if declared_type in I64_TYPES:
        return declared_type
    return FT.TYPE_DOUBLE


def _i32_fallback(declared_type: int) -> int:
    if declared_type in I32_TYPES:
        return declared_type
    return FT.TYPE_FLOAT


def correct_field(
    field_proto: descriptor_pb2.FieldDescriptorProto,
    observed_wire: int,
    sample_bytes: bytes,
    *,
    nested_enum_names: frozenset[str],
) -> tuple[int, str, set[str]]:
    """Return (new_type, new_type_name, extra_dependencies)."""
    declared = field_proto.type
    deps: set[str] = set()

    if observed_wire == WIRE_LEN:
        inner = _detect_wrapper_scalar(sample_bytes)
        if inner is not None:
            field_proto.type = FT.TYPE_MESSAGE
            field_proto.type_name = WRAPPER_BY_SCALAR[inner]
            deps.add(GOOGLE_WRAPPERS_IMPORT)
            return FT.TYPE_MESSAGE, WRAPPER_BY_SCALAR[inner], deps
        if declared in (FT.TYPE_MESSAGE, FT.TYPE_GROUP):
            if field_proto.type_name:
                return declared, field_proto.type_name, deps
            field_proto.type = FT.TYPE_BYTES
            field_proto.ClearField("type_name")
            return FT.TYPE_BYTES, "", deps
        field_proto.type = FT.TYPE_BYTES
        field_proto.ClearField("type_name")
        return FT.TYPE_BYTES, "", deps

    if observed_wire == WIRE_VARINT:
        if declared in (FT.TYPE_MESSAGE, FT.TYPE_GROUP):
            short = field_proto.type_name.rsplit(".", 1)[-1] if field_proto.type_name else ""
            if short in nested_enum_names:
                field_proto.type = FT.TYPE_ENUM
                if not field_proto.type_name.startswith("."):
                    field_proto.type_name = f".{field_proto.type_name.lstrip('.')}"
                return FT.TYPE_ENUM, field_proto.type_name, deps
            field_proto.type = _varint_fallback(declared)
            field_proto.ClearField("type_name")
            return field_proto.type, "", deps
        new_type = _varint_fallback(declared)
        field_proto.type = new_type
        if new_type != FT.TYPE_ENUM:
            field_proto.ClearField("type_name")
        return new_type, field_proto.type_name, deps

    if observed_wire == WIRE_I64:
        new_type = _i64_fallback(declared)
        field_proto.type = new_type
        field_proto.ClearField("type_name")
        return new_type, "", deps

    if observed_wire == WIRE_I32:
        new_type = _i32_fallback(declared)
        field_proto.type = new_type
        field_proto.ClearField("type_name")
        return new_type, "", deps

    return declared, field_proto.type_name, deps


def _collect_nested_enum_names(msg: descriptor_pb2.DescriptorProto) -> frozenset[str]:
    names: set[str] = set()

    def walk(m: descriptor_pb2.DescriptorProto) -> None:
        for enum in m.enum_type:
            names.add(enum.name)
        for nested in m.nested_type:
            if not nested.options.map_entry:
                walk(nested)

    walk(msg)
    return frozenset(names)


def _index_messages(
    fds: descriptor_pb2.FileDescriptorSet,
) -> dict[str, tuple[descriptor_pb2.FileDescriptorProto, descriptor_pb2.DescriptorProto]]:
    index: dict[
        str, tuple[descriptor_pb2.FileDescriptorProto, descriptor_pb2.DescriptorProto]
    ] = {}

    def walk(
        fd: descriptor_pb2.FileDescriptorProto,
        msg: descriptor_pb2.DescriptorProto,
        path: tuple[str, ...],
    ) -> None:
        full_name = ".".join(path) if path else msg.name
        index[full_name] = (fd, msg)
        parent = (*path, msg.name) if path else (msg.name,)
        for nested in msg.nested_type:
            if nested.options.map_entry:
                continue
            walk(fd, nested, parent)

    for fd in fds.file:
        for msg in fd.message_type:
            walk(fd, msg, ())
    return index


def apply_corrections(
    fds: descriptor_pb2.FileDescriptorSet,
    mismatches: list[Mismatch],
) -> list[Correction]:
    index = _index_messages(fds)
    applied: list[Correction] = []
    seen: set[tuple[str, int]] = set()

    for mismatch in mismatches:
        key = (mismatch.message_full_name, mismatch.field_number)
        if key in seen:
            continue
        seen.add(key)

        entry = index.get(mismatch.message_full_name)
        if entry is None:
            continue
        fd, msg = entry
        field_proto = next(
            (fld for fld in msg.field if fld.number == mismatch.field_number),
            None,
        )
        if field_proto is None:
            continue

        old_type = field_proto.type
        old_type_name = field_proto.type_name
        nested_enums = _collect_nested_enum_names(msg)
        new_type, new_type_name, deps = correct_field(
            field_proto,
            mismatch.observed_wire_type,
            mismatch.sample_bytes,
            nested_enum_names=nested_enums,
        )
        for dep in deps:
            if dep not in fd.dependency:
                fd.dependency.append(dep)

        if new_type != old_type or new_type_name != old_type_name:
            applied.append(
                Correction(
                    message_full_name=mismatch.message_full_name,
                    field_number=mismatch.field_number,
                    field_name=mismatch.field_name,
                    old_type=old_type,
                    new_type=new_type,
                    new_type_name=new_type_name,
                )
            )

    return applied


def _walk_message(
    msg: Message,
    pool: descriptor_pool.DescriptorPool,
    short_name_index: dict[str, str],
    mismatches: dict[tuple[str, int], Mismatch],
) -> None:
    desc = msg.DESCRIPTOR
    by_number = {f.number: f for f in desc.fields}
    for unknown in UnknownFieldSet(msg):
        field_desc = by_number.get(unknown.field_number)
        if field_desc is None:
            continue
        if unknown.wire_type in _expected_wire_types(field_desc):
            continue
        sample = unknown.data if isinstance(unknown.data, bytes) else b""
        key = (desc.full_name, unknown.field_number)
        if key not in mismatches:
            mismatches[key] = Mismatch(
                message_full_name=desc.full_name,
                field_number=unknown.field_number,
                field_name=field_desc.name,
                declared_type=field_desc.type,
                observed_wire_type=unknown.wire_type,
                sample_bytes=sample,
            )

    for field, value in msg.ListFields():
        if field.type != PFD.TYPE_MESSAGE:
            continue
        if field.message_type.GetOptions().map_entry:
            value_desc = field.message_type.fields_by_name["value"]
            if value_desc.type == PFD.TYPE_MESSAGE:
                for map_value in value.values():
                    _walk_message(map_value, pool, short_name_index, mismatches)
            continue
        if field.message_type.full_name == "google.protobuf.Any":
            if field.is_repeated:
                for any_msg in value:
                    _walk_any_payload(any_msg, pool, short_name_index, mismatches)
            else:
                _walk_any_payload(value, pool, short_name_index, mismatches)
            continue
        if field.is_repeated:
            for item in value:
                _walk_message(item, pool, short_name_index, mismatches)
        else:
            _walk_message(value, pool, short_name_index, mismatches)


def _walk_any_payload(
    any_msg: any_pb2.Any,
    pool: descriptor_pool.DescriptorPool,
    short_name_index: dict[str, str],
    mismatches: dict[tuple[str, int], Mismatch],
) -> None:
    if not any_msg.type_url:
        return
    try:
        full_name = gamedesign._resolve_message_type_name(
            pool, any_msg.type_url, short_name_index
        )
    except KeyError:
        return
    inner_cls = message_factory.GetMessageClass(pool.FindMessageTypeByName(full_name))
    inner = inner_cls()
    try:
        inner.ParseFromString(any_msg.value)
    except Exception:
        return
    _walk_message(inner, pool, short_name_index, mismatches)


def detect_mismatches(
    pool: descriptor_pool.DescriptorPool,
    short_name_index: dict[str, str],
    gd_msg: Message,
) -> list[Mismatch]:
    mismatches: dict[tuple[str, int], Mismatch] = {}
    for any_msg in gd_msg.content:
        _walk_any_payload(any_msg, pool, short_name_index, mismatches)
    return list(mismatches.values())


def run_wirefix(
    descriptors_pb: Path,
    sample_blob: Path,
    *,
    max_iters: int = 5,
    verbose: bool = False,
) -> WireFixReport:
    if not descriptors_pb.is_file():
        raise FileNotFoundError(f"descriptors.pb not found: {descriptors_pb}")
    if not sample_blob.is_file():
        raise FileNotFoundError(f"wire-fix sample not found: {sample_blob}")

    data = sample_blob.read_bytes()
    report = WireFixReport(iterations=0)
    corrections_by_key: dict[tuple[str, int], Correction] = {}

    for iteration in range(1, max_iters + 1):
        pool, short_name_index = gamedesign.load_descriptor_pool(descriptors_pb)
        gd_msg = gamedesign._parse_gamedesign_message(data, pool)
        if gd_msg is None:
            raise ValueError("sample blob is not a valid GameDesignResponse")

        mismatches = detect_mismatches(pool, short_name_index, gd_msg)
        if not mismatches:
            report.iterations = iteration - 1 if iteration > 1 else 0
            break

        fds = descriptor_pb2.FileDescriptorSet()
        fds.ParseFromString(descriptors_pb.read_bytes())
        applied = apply_corrections(fds, mismatches)
        if not applied:
            report.iterations = iteration
            if verbose:
                print(
                    f"wirefix: iteration {iteration}: {len(mismatches)} mismatch(es) "
                    "but none could be applied",
                    file=sys.stderr,
                )
            break

        descriptors_pb.write_bytes(fds.SerializeToString())
        for corr in applied:
            corrections_by_key[(corr.message_full_name, corr.field_number)] = corr
            if verbose:
                type_names = {v: k[5:] for k, v in vars(FT).items() if k.startswith("TYPE_")}
                old = type_names.get(corr.old_type, str(corr.old_type))
                new = type_names.get(corr.new_type, str(corr.new_type))
                extra = f" -> {corr.new_type_name}" if corr.new_type_name else ""
                print(
                    f"wirefix: {corr.message_full_name}.{corr.field_name} "
                    f"#{corr.field_number}: {old} -> {new}{extra}",
                    file=sys.stderr,
                )

        report.iterations = iteration

        pool2, short_name_index2 = gamedesign.load_descriptor_pool(descriptors_pb)
        gd_msg2 = gamedesign._parse_gamedesign_message(data, pool2)
        if gd_msg2 is None:
            break
        remaining = detect_mismatches(pool2, short_name_index2, gd_msg2)
        if not remaining:
            break
    else:
        if report.iterations == 0:
            report.iterations = max_iters

    report.corrections = list(corrections_by_key.values())
    return report
