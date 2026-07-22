"""Render .proto files from a FileDescriptorSet (descriptors.pb)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from google.protobuf import descriptor_pb2

from xapk_to_proto.data import load_well_known_fds
from xapk_to_proto.repair import build_nested_type_index, repair_file_descriptor

TYPE_NAMES = {
    descriptor_pb2.FieldDescriptorProto.TYPE_DOUBLE: "double",
    descriptor_pb2.FieldDescriptorProto.TYPE_FLOAT: "float",
    descriptor_pb2.FieldDescriptorProto.TYPE_INT64: "int64",
    descriptor_pb2.FieldDescriptorProto.TYPE_UINT64: "uint64",
    descriptor_pb2.FieldDescriptorProto.TYPE_INT32: "int32",
    descriptor_pb2.FieldDescriptorProto.TYPE_FIXED64: "fixed64",
    descriptor_pb2.FieldDescriptorProto.TYPE_FIXED32: "fixed32",
    descriptor_pb2.FieldDescriptorProto.TYPE_BOOL: "bool",
    descriptor_pb2.FieldDescriptorProto.TYPE_STRING: "string",
    descriptor_pb2.FieldDescriptorProto.TYPE_BYTES: "bytes",
    descriptor_pb2.FieldDescriptorProto.TYPE_UINT32: "uint32",
    descriptor_pb2.FieldDescriptorProto.TYPE_ENUM: "enum",
    descriptor_pb2.FieldDescriptorProto.TYPE_SFIXED32: "sfixed32",
    descriptor_pb2.FieldDescriptorProto.TYPE_SFIXED64: "sfixed64",
    descriptor_pb2.FieldDescriptorProto.TYPE_SINT32: "sint32",
    descriptor_pb2.FieldDescriptorProto.TYPE_SINT64: "sint64",
}

LABEL_PREFIX = {
    descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL: "",
    descriptor_pb2.FieldDescriptorProto.LABEL_REQUIRED: "",
    descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED: "repeated ",
}


def _field_type_name(
    field: descriptor_pb2.FieldDescriptorProto,
    msg: descriptor_pb2.DescriptorProto,
    package: str,
) -> str:
    type_str = short_type(field.type_name, package)
    if msg.enum_type:
        local_enums = {enum.name for enum in msg.enum_type}
        tail = type_str.rsplit(".", 1)[-1]
        if tail in local_enums:
            return tail
    return type_str


def short_type(type_name: str, package: str = "") -> str:
    if not type_name:
        return ""
    if type_name.startswith("."):
        type_name = type_name[1:]
    if type_name.startswith("google.protobuf."):
        return type_name
    type_name = type_name.replace(".Types.", ".")
    prefix = f"{package}." if package else ""
    if prefix and type_name.startswith(prefix):
        return type_name[len(prefix) :]
    if not prefix:
        # No package: keep Parent.Child paths for nested types.
        return type_name
    return type_name.rsplit(".", 1)[-1]


def dep_import(dep: str) -> str:
    return dep if dep.endswith(".proto") else f"{dep}.proto"


def emit_enum(enum: descriptor_pb2.EnumDescriptorProto, indent: str) -> list[str]:
    lines = [f"{indent}enum {enum.name} {{"]
    for val in enum.value:
        lines.append(f"{indent}  {val.name} = {val.number};")
    lines.append(f"{indent}}}")
    return lines


def _emit_field_line(
    field: descriptor_pb2.FieldDescriptorProto,
    msg: descriptor_pb2.DescriptorProto,
    indent: str,
    package: str,
) -> str | None:
    if field.type == descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE:
        map_entry = next(
            (
                n
                for n in msg.nested_type
                if n.name == field.type_name.rsplit(".", 1)[-1]
            ),
            None,
        )
        if map_entry and map_entry.options.map_entry:
            key = map_entry.field[0]
            val = map_entry.field[1]
            key_t = TYPE_NAMES.get(key.type, short_type(key.type_name, package))
            val_t = TYPE_NAMES.get(val.type, short_type(val.type_name, package))
            return f"{indent}  map<{key_t}, {val_t}> {field.name} = {field.number};"
    if field.proto3_optional:
        prefix = "optional "
    else:
        prefix = LABEL_PREFIX.get(field.label, "")
    if (
        field.type in TYPE_NAMES
        and field.type != descriptor_pb2.FieldDescriptorProto.TYPE_ENUM
    ):
        type_str = TYPE_NAMES[field.type]
    else:
        type_str = _field_type_name(field, msg, package)
    return f"{indent}  {prefix}{type_str} {field.name} = {field.number};"


def emit_message(
    msg: descriptor_pb2.DescriptorProto, indent: str, package: str
) -> list[str]:
    lines = [f"{indent}message {msg.name} {{"]
    oneof_groups: dict[int, list[descriptor_pb2.FieldDescriptorProto]] = {}
    regular: list[descriptor_pb2.FieldDescriptorProto] = []

    for field in msg.field:
        if field.HasField("oneof_index"):
            oneof_groups.setdefault(field.oneof_index, []).append(field)
        else:
            regular.append(field)

    for nested_enum in msg.enum_type:
        lines.extend(emit_enum(nested_enum, indent + "  "))
    for nested_msg in msg.nested_type:
        if nested_msg.options.map_entry:
            continue
        lines.extend(emit_message(nested_msg, indent + "  ", package))

    for field in sorted(regular, key=lambda f: f.number):
        line = _emit_field_line(field, msg, indent, package)
        if line:
            lines.append(line)

    for idx, oneof in enumerate(msg.oneof_decl):
        lines.append(f"{indent}  oneof {oneof.name} {{")
        for field in sorted(oneof_groups.get(idx, []), key=lambda f: f.number):
            line = _emit_field_line(field, msg, indent, package)
            if line:
                lines.append(line)
        lines.append(f"{indent}  }}")

    lines.append(f"{indent}}}")
    return lines


def emit_service(
    service: descriptor_pb2.ServiceDescriptorProto, indent: str, package: str
) -> list[str]:
    lines = [f"{indent}service {service.name} {{"]
    for method in service.method:
        in_t = short_type(method.input_type, package)
        out_t = short_type(method.output_type, package)
        lines.append(f"{indent}  rpc {method.name}({in_t}) returns ({out_t});")
    lines.append(f"{indent}}}")
    return lines


def emit_file(fd: descriptor_pb2.FileDescriptorProto) -> str:
    lines: list[str] = []
    if fd.syntax:
        lines.append(f'syntax = "{fd.syntax}";')
        lines.append("")
    if fd.package:
        lines.append(f"package {fd.package};")
        lines.append("")
    for dep in fd.dependency:
        lines.append(f'import "{dep_import(dep)}";')
    if fd.dependency:
        lines.append("")
    for enum in fd.enum_type:
        lines.extend(emit_enum(enum, ""))
        lines.append("")
    for msg in fd.message_type:
        lines.extend(emit_message(msg, "", fd.package))
        lines.append("")
    for service in fd.service:
        lines.extend(emit_service(service, "", fd.package))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


@dataclass(frozen=True)
class EmitResult:
    game_files: int
    well_known_files: int

    @property
    def total(self) -> int:
        return self.game_files + self.well_known_files


def emit_well_known(out_dir: Path) -> int:
    written = 0
    for fd in load_well_known_fds().file:
        rel = fd.name.replace("\\", "/")
        target = out_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(emit_file(fd), encoding="utf-8")
        written += 1
    return written


def write_descriptor_bundle(game_pb: Path, out_path: Path) -> int:
    bundle = descriptor_pb2.FileDescriptorSet()
    fds = descriptor_pb2.FileDescriptorSet()
    fds.ParseFromString(game_pb.read_bytes())
    for fd in fds.file:
        bundle.file.add().CopyFrom(fd)

    existing = {fd.name for fd in bundle.file}
    for fd in load_well_known_fds().file:
        if fd.name not in existing:
            bundle.file.add().CopyFrom(fd)
            existing.add(fd.name)

    out_path.write_bytes(bundle.SerializeToString())
    return len(bundle.file)


def run(inp: Path, out_dir: Path) -> EmitResult:
    if not inp.exists():
        raise FileNotFoundError(f"input not found: {inp}")

    out_dir.mkdir(parents=True, exist_ok=True)

    fds = descriptor_pb2.FileDescriptorSet()
    fds.ParseFromString(inp.read_bytes())
    nested_index, enum_index = build_nested_type_index(list(fds.file))

    written = 0
    for fd in fds.file:
        rel = fd.name.replace("\\", "/")
        target = out_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        repaired = descriptor_pb2.FileDescriptorProto()
        repaired.CopyFrom(fd)
        repair_file_descriptor(
            repaired, nested_index=nested_index, enum_index=enum_index
        )
        target.write_text(emit_file(repaired), encoding="utf-8")
        written += 1

    well_known_written = emit_well_known(out_dir)
    return EmitResult(game_files=written, well_known_files=well_known_written)
