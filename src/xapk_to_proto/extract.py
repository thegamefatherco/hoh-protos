"""Extract Google.Protobuf FileDescriptorProtos from IL2CPP metadata and dump.cs."""

from __future__ import annotations

import base64
import re
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

from google.protobuf import descriptor_pb2

WELL_KNOWN: dict[str, str] = {
    "Google.Protobuf.WellKnownTypes.Timestamp": "google.protobuf.Timestamp",
    "Google.Protobuf.WellKnownTypes.Duration": "google.protobuf.Duration",
    "Google.Protobuf.WellKnownTypes.Any": "google.protobuf.Any",
    "Google.Protobuf.WellKnownTypes.Empty": "google.protobuf.Empty",
    "Google.Protobuf.WellKnownTypes.Struct": "google.protobuf.Struct",
    "Google.Protobuf.WellKnownTypes.Value": "google.protobuf.Value",
    "Google.Protobuf.WellKnownTypes.BoolValue": "google.protobuf.BoolValue",
    "Google.Protobuf.WellKnownTypes.Int32Value": "google.protobuf.Int32Value",
    "Google.Protobuf.WellKnownTypes.Int64Value": "google.protobuf.Int64Value",
    "Google.Protobuf.WellKnownTypes.UInt32Value": "google.protobuf.UInt32Value",
    "Google.Protobuf.WellKnownTypes.UInt64Value": "google.protobuf.UInt64Value",
    "Google.Protobuf.WellKnownTypes.FloatValue": "google.protobuf.FloatValue",
    "Google.Protobuf.WellKnownTypes.DoubleValue": "google.protobuf.DoubleValue",
    "Google.Protobuf.WellKnownTypes.StringValue": "google.protobuf.StringValue",
    "Google.Protobuf.WellKnownTypes.BytesValue": "google.protobuf.BytesValue",
    "Duration": "google.protobuf.Duration",
    "Timestamp": "google.protobuf.Timestamp",
    "Any": "google.protobuf.Any",
    "Struct": "google.protobuf.Struct",
    "Value": "google.protobuf.Value",
    "ListValue": "google.protobuf.ListValue",
}

_WELL_KNOWN_PREFIX = "Google.Protobuf.WellKnownTypes."

# Official google.protobuf well-known C# short names (excludes ambiguous names like
# Type, Field, Enum, Method, Syntax, Api, Mixin that collide with game types).
GOOGLE_WKT_SHORT_NAMES: frozenset[str] = frozenset(
    {
        "Timestamp",
        "Duration",
        "Any",
        "Empty",
        "Struct",
        "Value",
        "ListValue",
        "NullValue",
        "FieldMask",
        "SourceContext",
        "BoolValue",
        "Int32Value",
        "Int64Value",
        "UInt32Value",
        "UInt64Value",
        "FloatValue",
        "DoubleValue",
        "StringValue",
        "BytesValue",
    }
)

_missing_well_known: set[str] = set()

SCALAR: dict[str, int] = {
    "string": descriptor_pb2.FieldDescriptorProto.TYPE_STRING,
    "int": descriptor_pb2.FieldDescriptorProto.TYPE_INT32,
    "uint": descriptor_pb2.FieldDescriptorProto.TYPE_UINT32,
    "long": descriptor_pb2.FieldDescriptorProto.TYPE_INT64,
    "ulong": descriptor_pb2.FieldDescriptorProto.TYPE_UINT64,
    "float": descriptor_pb2.FieldDescriptorProto.TYPE_FLOAT,
    "double": descriptor_pb2.FieldDescriptorProto.TYPE_DOUBLE,
    "bool": descriptor_pb2.FieldDescriptorProto.TYPE_BOOL,
    "byte[]": descriptor_pb2.FieldDescriptorProto.TYPE_BYTES,
    "ByteString": descriptor_pb2.FieldDescriptorProto.TYPE_BYTES,
}


@dataclass
class EnumValue:
    name: str
    number: int


@dataclass
class EnumDef:
    name: str
    values: list[EnumValue] = field(default_factory=list)


@dataclass
class FieldDef:
    name: str
    number: int
    label: int
    type: int
    type_name: str = ""
    proto3_optional: bool = False
    oneof_index: int | None = None
    map_key_type: int | None = None
    map_key_type_name: str = ""
    map_value_type: int | None = None
    map_value_type_name: str = ""


@dataclass
class MessageDef:
    name: str
    fields: list[FieldDef] = field(default_factory=list)
    nested_enums: list[EnumDef] = field(default_factory=list)
    nested_messages: list["MessageDef"] = field(default_factory=list)
    oneofs: list[str] = field(default_factory=list)


@dataclass
class ProtoFile:
    name: str
    package: str
    messages: list[MessageDef] = field(default_factory=list)
    enums: list[EnumDef] = field(default_factory=list)
    dependencies: set[str] = field(default_factory=set)


def camel_to_snake(name: str) -> str:
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    s2 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1)
    return s2.replace("-", "_").lower()


def snake_to_pascal(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_"))


def reflection_to_proto_name(reflection_class: str) -> str:
    base = reflection_class.removesuffix("Reflection")
    return f"{camel_to_snake(base)}.proto"


def parse_metadata_literals(
    metadata_path: Path,
) -> tuple[dict[int, str], list[tuple[int, str]]]:
    data = metadata_path.read_bytes()
    lit_off, lit_size = struct.unpack_from("<II", data, 8)
    lit_data_off = struct.unpack_from("<I", data, 16)[0]
    by_idx: dict[int, str] = {}
    chunks: list[tuple[int, str]] = []
    for i in range(lit_size // 8):
        length, idx = struct.unpack_from("<II", data, lit_off + i * 8)
        if length == 0:
            continue
        raw = data[lit_data_off + idx : lit_data_off + idx + length]
        try:
            text = raw.decode("ascii")
        except UnicodeDecodeError:
            continue
        by_idx[i] = text
        if re.fullmatch(r"[A-Za-z0-9+/=]+", text) and 4 <= len(text) <= 120:
            chunks.append((i, text))
    return by_idx, chunks


def try_parse_fdp(b64_text: str) -> descriptor_pb2.FileDescriptorProto | None:
    for pad in ("", "=", "=="):
        try:
            raw = base64.b64decode(b64_text + pad, validate=False)
        except Exception:
            continue
        fd = descriptor_pb2.FileDescriptorProto()
        try:
            fd.ParseFromString(raw)
        except Exception:
            continue
        if fd.name and (fd.message_type or fd.enum_type or fd.service or fd.dependency):
            return fd
    return None


def chunk_proto_name(chunk: str) -> str | None:
    fd = try_parse_fdp(chunk)
    if fd and fd.name:
        return fd.name.strip()
    for pad in ("", "=", "=="):
        try:
            raw = base64.b64decode(chunk + pad, validate=False)
        except Exception:
            continue
        if len(raw) >= 2 and raw[0] == 0x0A:
            ln = raw[1]
            if ln and len(raw) >= 2 + ln:
                return raw[2 : 2 + ln].decode("utf-8", "replace").strip()
    return None


def extract_embedded_from_metadata(
    metadata_path: Path,
) -> dict[str, descriptor_pb2.FileDescriptorProto]:
    by_idx, chunks = parse_metadata_literals(metadata_path)
    starts = [(i, t) for i, t in chunks if t.startswith("Cg")]
    found: dict[str, descriptor_pb2.FileDescriptorProto] = {}

    # First pass: single-literal descriptors.
    for i, text in chunks:
        fd = try_parse_fdp(text)
        if fd and fd.name:
            found[fd.name] = fd

    # Second pass: two-chunk rows used by this game (index, index+83).
    for i, text in starts:
        j = i + 83
        if j not in by_idx:
            continue
        acc = text + by_idx[j]
        fd = try_parse_fdp(acc)
        if fd and fd.name:
            found[fd.name] = fd

    return found


def split_dump_sections(dump_text: str) -> list[tuple[str, str]]:
    pattern = re.compile(
        r"public static class (\w+Reflection)\s// TypeDefIndex: \d+\s*\{.*?\n\}",
        re.DOTALL,
    )
    sections: list[tuple[str, str]] = []
    matches = list(pattern.finditer(dump_text))
    for i, match in enumerate(matches):
        name = match.group(1)
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(dump_text)
        if (
            "InnoGames.Generated.Protobuf"
            not in dump_text[max(0, match.start() - 200) : match.start() + 200]
        ):
            # Keep only InnoGames protobuf reflections by checking nearby namespace markers.
            window = dump_text[max(0, match.start() - 4000) : match.start()]
            if "Namespace: InnoGames.Generated.Protobuf" not in window:
                continue
        sections.append((name, dump_text[start:end]))
    return sections


def parse_original_name(line: str) -> str | None:
    m = re.search(r'\[OriginalName\("([^"]+)"\)\]', line)
    return m.group(1) if m else None


def _reset_missing_well_known() -> None:
    _missing_well_known.clear()


def _is_well_known_candidate(type_name: str) -> bool:
    if type_name.startswith(_WELL_KNOWN_PREFIX):
        return True
    return type_name in GOOGLE_WKT_SHORT_NAMES


def _note_missing_well_known(type_name: str) -> None:
    name = type_name.strip()
    if name:
        _missing_well_known.add(name)


def _emit_missing_well_known_warning() -> list[str]:
    if not _missing_well_known:
        return []
    missing = sorted(_missing_well_known)
    print(
        f"warning: {len(missing)} C# type(s) missing from WELL_KNOWN in extract.py "
        "— add mappings to investigate:",
        file=sys.stderr,
    )
    for name in missing:
        print(f"  {name}", file=sys.stderr)
    return missing


def parse_csharp_nested_name(name: str) -> tuple[list[str], str]:
    """Split Foo.Types.Bar.Types.Baz into parent chain [Foo, Bar] and leaf Baz."""
    if ".Types." not in name:
        return [], name
    parts = name.split(".Types.")
    return parts[:-1], parts[-1]


def csharp_type_to_proto(type_name: str) -> tuple[int, str, str]:
    type_name = type_name.strip()
    if type_name.startswith("Nullable<") and type_name.endswith(">"):
        inner = type_name[len("Nullable<") : -1].strip()
        return csharp_type_to_proto(inner)
    if type_name.startswith("RepeatedField<"):
        inner = type_name[len("RepeatedField<") : -1]
        t, tn, dep = csharp_type_to_proto(inner)
        return t, tn, dep
    if type_name.startswith("MapField<"):
        inner = type_name[len("MapField<") : -1]
        key_t, val_t = inner.split(",", 1)
        kt, ktn, kd = csharp_type_to_proto(key_t.strip())
        vt, vtn, vd = csharp_type_to_proto(val_t.strip())
        return (
            descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE,
            f"Entry_{key_t.strip()}_{val_t.strip()}",
            kd or vd,
        )
    if type_name in SCALAR:
        return SCALAR[type_name], "", ""
    if type_name in WELL_KNOWN:
        return (
            descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE,
            WELL_KNOWN[type_name],
            WELL_KNOWN[type_name].split(".")[0]
            + "/"
            + WELL_KNOWN[type_name].split(".")[-1].lower()
            + ".proto",
        )
    if _is_well_known_candidate(type_name):
        _note_missing_well_known(type_name)
    if ".Types." in type_name:
        nested = type_name.split(".Types.", 1)[1]
        # Nested enums/messages are handled relative to their parent message.
        return descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE, nested, ""
    return descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE, type_name, ""


def _qualify(package: str, *parts: str) -> str:
    return "." + ".".join(
        p for p in ((*([package] if package else []), *parts)) if p
    )


def resolve_field_type_name(pf: ProtoFile, md: MessageDef, type_name: str) -> str:
    if not type_name:
        return ""
    if type_name.startswith("google.protobuf."):
        return f".{type_name}"
    nested_names = {e.name for e in md.nested_enums} | {
        m.name for m in md.nested_messages
    }
    if type_name in nested_names:
        return _qualify(pf.package, md.name, type_name)
    return _qualify(pf.package, type_name)


def _looks_like_protobuf_message(block: str) -> bool:
    """True if the class header implements IMessage or IMessage<T>.

    Unity 6000 / newer Il2CppDumper builds emit non-generic ``IMessage``.
    Older dumps use ``IMessage<T>``. Match only the class header (text before
    the first ``{``) so ``pb::Google.Protobuf.IMessage.Descriptor`` properties
    do not false-positive.
    """
    header, _, _ = block.partition("{")
    return bool(re.search(r"\bIMessage\b", header))


def parse_message_block(name: str, block: str) -> MessageDef | None:
    if not _looks_like_protobuf_message(block):
        return None
    msg = MessageDef(name=name)
    field_numbers: dict[int, str] = {}
    for m in re.finditer(r"public const int (\w+)FieldNumber = (\d+);", block):
        field_numbers[int(m.group(2))] = m.group(1)

    oneof_names: list[str] = []
    for m in re.finditer(r"public const int (\w+)FieldNumber = (\d+);", block):
        pass
    for m in re.finditer(r"public (\w+) (\w+); // 0x", block):
        if m.group(1) == "ParticipantOneofCase" or m.group(1).endswith("OneofCase"):
            continue
    for m in re.finditer(r"(\w+)OneofCase (\w+);", block):
        oneof_names.append(m.group(1))

    _CS_TYPE = r"(?:MapField|RepeatedField|Nullable)<[^>]+>|[\w<>,\.]+"
    for number, field_name in sorted(field_numbers.items()):
        pat = (
            rf"public const int {re.escape(field_name)}FieldNumber = {number};"
            rf"[\s\S]{{0,400}}?private (?:readonly )?({_CS_TYPE}) (\w+)_;"
        )
        m = re.search(pat, block)
        if not m:
            continue
        if camel_to_snake(m.group(2)) != camel_to_snake(field_name):
            continue
        cs_type = m.group(1)
        proto3_optional = False
        if cs_type.startswith("Nullable<") and cs_type.endswith(">"):
            proto3_optional = True
            cs_type = cs_type[len("Nullable<") : -1].strip()
        label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
        map_key_type = map_key_type_name = map_value_type = map_value_type_name = None
        if cs_type.startswith("RepeatedField<"):
            label = descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED
            cs_type = cs_type[len("RepeatedField<") : -1]
        if cs_type.startswith("MapField<"):
            label = descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED
            inner = cs_type[len("MapField<") : -1]
            key_t, val_t = [x.strip() for x in inner.split(",", 1)]
            map_key_type, map_key_type_name, _ = csharp_type_to_proto(key_t)
            map_value_type, map_value_type_name, _ = csharp_type_to_proto(val_t)
            cs_type = "MapEntry"
        p_type, type_name, _ = csharp_type_to_proto(cs_type)
        snake = camel_to_snake(field_name)
        msg.fields.append(
            FieldDef(
                name=snake,
                number=number,
                label=label,
                type=p_type,
                type_name=type_name
                if p_type == descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
                or p_type == descriptor_pb2.FieldDescriptorProto.TYPE_ENUM
                else "",
                map_key_type=map_key_type,
                map_key_type_name=map_key_type_name or "",
                map_value_type=map_value_type,
                map_value_type_name=map_value_type_name or "",
                proto3_optional=proto3_optional,
            )
        )
    return msg


def parse_enum_block(name: str, block: str) -> EnumDef | None:
    if "value__" not in block:
        return None
    enum_def = EnumDef(name=name)
    for m in re.finditer(
        r'\[OriginalName\("([^"]+)"\)\]\s*\n\s*public const [\w\.]+ (\w+) = (-?\d+);',
        block,
    ):
        enum_def.values.append(EnumValue(name=m.group(1), number=int(m.group(3))))
    if not enum_def.values:
        return None
    return enum_def


def parse_dump_cs(dump_path: Path) -> dict[str, ProtoFile]:
    text = dump_path.read_text(encoding="utf-8", errors="replace")
    protos: dict[str, ProtoFile] = {}
    for reflection_name, section in split_dump_sections(text):
        proto_name = reflection_to_proto_name(reflection_name)
        package = ""
        pf = ProtoFile(name=proto_name, package=package)
        class_pattern = re.compile(
            r"public (?:sealed )?class ([\w\.]+)(?: :|\s*\{)",
            re.MULTILINE,
        )
        enum_pattern = re.compile(r"public enum ([\w\.]+)(?: //|\s*\{)", re.MULTILINE)
        indices = []
        for m in class_pattern.finditer(section):
            indices.append(("class", m.start(), m.group(1)))
        for m in enum_pattern.finditer(section):
            indices.append(("enum", m.start(), m.group(1)))
        indices.sort(key=lambda x: x[1])

        def find_message(pf: ProtoFile, name: str) -> MessageDef | None:
            for msg in pf.messages:
                if msg.name == name:
                    return msg
            return None

        def ensure_message(pf: ProtoFile, name: str) -> MessageDef:
            existing = find_message(pf, name)
            if existing:
                return existing
            msg = MessageDef(name=name)
            pf.messages.append(msg)
            return msg

        def ensure_nested_message(pf: ProtoFile, parent_names: list[str]) -> MessageDef:
            if not parent_names:
                raise ValueError("empty nested parent path")
            parent = ensure_message(pf, parent_names[0])
            for nested_name in parent_names[1:]:
                child = next(
                    (m for m in parent.nested_messages if m.name == nested_name),
                    None,
                )
                if child is None:
                    child = MessageDef(name=nested_name)
                    parent.nested_messages.append(child)
                parent = child
            return parent

        def merge_nested_message(parent: MessageDef, incoming: MessageDef) -> None:
            existing = next(
                (m for m in parent.nested_messages if m.name == incoming.name),
                None,
            )
            if existing is None:
                parent.nested_messages.append(incoming)
                return
            if incoming.fields and not existing.fields:
                existing.fields = incoming.fields
            for ed in incoming.nested_enums:
                if not any(e.name == ed.name for e in existing.nested_enums):
                    existing.nested_enums.append(ed)
            for nm in incoming.nested_messages:
                merge_nested_message(existing, nm)

        deferred: list[tuple[str, str, str]] = []
        for i, (kind, pos, name) in enumerate(indices):
            if "<>c" in name or name.endswith(".Types"):
                continue
            # Protobuf C# nested types always use Parent.Types.Child. Plain
            # Parent.Child names are ordinary C# nested classes (and can be
            # false-positive IMessage hits in Il2CppDumper output).
            if "." in name and ".Types." not in name:
                continue
            end = indices[i + 1][1] if i + 1 < len(indices) else len(section)
            block = section[pos:end]
            if ".Types." in name:
                deferred.append((kind, name, block))
                continue
            if kind == "enum":
                enum_def = parse_enum_block(name, block)
                if enum_def:
                    pf.enums.append(enum_def)
            else:
                msg = parse_message_block(name, block)
                if msg and (msg.fields or _looks_like_protobuf_message(block)):
                    existing = find_message(pf, name)
                    if existing and not existing.fields:
                        existing.fields = msg.fields
                    elif not existing:
                        pf.messages.append(msg)

        for kind, full_name, block in deferred:
            parent_names, leaf_name = parse_csharp_nested_name(full_name)
            parent = ensure_nested_message(pf, parent_names)
            if kind == "enum":
                enum_def = parse_enum_block(leaf_name, block)
                if enum_def:
                    parent.nested_enums.append(enum_def)
            else:
                msg = parse_message_block(leaf_name, block)
                if msg:
                    msg.name = leaf_name
                    merge_nested_message(parent, msg)
        if pf.messages or pf.enums:
            protos[proto_name] = pf
    return protos


def add_deps_from_fields(pf: ProtoFile, type_to_file: dict[str, str]) -> None:
    google_deps = {
        "google.protobuf.Timestamp": "google/protobuf/timestamp.proto",
        "google.protobuf.Duration": "google/protobuf/duration.proto",
        "google.protobuf.Any": "google/protobuf/any.proto",
        "google.protobuf.Empty": "google/protobuf/empty.proto",
        "google.protobuf.Struct": "google/protobuf/struct.proto",
        "google.protobuf.Value": "google/protobuf/struct.proto",
        "google.protobuf.ListValue": "google/protobuf/struct.proto",
    }

    def note_type(type_name: str) -> None:
        if not type_name:
            return
        if type_name.startswith("google.protobuf."):
            dep = google_deps.get(type_name)
            if dep:
                pf.dependencies.add(dep)
            return
        dep_file = type_to_file.get(type_name)
        if dep_file and dep_file != pf.name:
            pf.dependencies.add(dep_file)

    def walk(msg: MessageDef) -> None:
        for fld in msg.fields:
            note_type(fld.type_name)
            note_type(fld.map_key_type_name)
            note_type(fld.map_value_type_name)
        for nested in msg.nested_messages:
            walk(nested)

    for msg in pf.messages:
        walk(msg)


def build_type_index(protos: dict[str, ProtoFile]) -> dict[str, str]:
    index: dict[str, str] = {}
    for fname, pf in protos.items():

        def walk(msg: MessageDef, prefix: str = "") -> None:
            index[f"{prefix}{msg.name}" if prefix else msg.name] = fname
            for nested in msg.nested_messages:
                walk(nested, f"{msg.name}.")
            for enum in msg.nested_enums:
                index[f"{msg.name}.{enum.name}"] = fname

        for msg in pf.messages:
            walk(msg)
        for enum in pf.enums:
            index[enum.name] = fname
    return index


def protofile_to_fdp(pf: ProtoFile) -> descriptor_pb2.FileDescriptorProto:
    fd = descriptor_pb2.FileDescriptorProto()
    fd.name = pf.name
    fd.package = pf.package
    fd.syntax = "proto3"
    fd.dependency.extend(sorted(pf.dependencies))

    def add_enum(
        ed: EnumDef, parent: descriptor_pb2.DescriptorProto | None = None
    ) -> None:
        target = parent.enum_type.add() if parent is not None else fd.enum_type.add()
        target.name = ed.name
        for val in ed.values:
            ev = target.value.add()
            ev.name = val.name
            ev.number = val.number

    def add_message(
        md: MessageDef,
        parent: descriptor_pb2.DescriptorProto | None = None,
        ancestor_path: tuple[str, ...] = (),
    ) -> None:
        target = (
            parent.nested_type.add() if parent is not None else fd.message_type.add()
        )
        target.name = md.name
        message_path = (*ancestor_path, md.name)
        for ed in md.nested_enums:
            add_enum(ed, target)
        for nested in md.nested_messages:
            add_message(nested, target, message_path)
        for fld in md.fields:
            f = target.field.add()
            f.name = fld.name
            f.number = fld.number
            f.label = fld.label
            nested_names = {e.name for e in md.nested_enums} | {
                m.name for m in md.nested_messages
            }
            if fld.type_name in nested_names and fld.type in (
                descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE,
                descriptor_pb2.FieldDescriptorProto.TYPE_ENUM,
            ):
                # Heuristic: nested enums often end with Mode/Type/Status; otherwise message.
                if any(
                    fld.type_name.endswith(s)
                    for s in ("Mode", "Type", "Status", "State", "Case")
                ):
                    f.type = descriptor_pb2.FieldDescriptorProto.TYPE_ENUM
            if fld.type_name.startswith("MapEntry"):
                entry_name = f"{snake_to_pascal(fld.name)}Entry"
                f.type = descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
                f.type_name = _qualify(pf.package, *message_path, entry_name)
                entry = target.nested_type.add()
                entry.name = entry_name
                entry.options.map_entry = True
                k = entry.field.add()
                k.name = "key"
                k.number = 1
                k.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
                k.type = (
                    fld.map_key_type or descriptor_pb2.FieldDescriptorProto.TYPE_STRING
                )
                if fld.map_key_type_name:
                    k.type_name = resolve_field_type_name(
                        pf, md, fld.map_key_type_name
                    )
                v = entry.field.add()
                v.name = "value"
                v.number = 2
                v.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
                v.type = (
                    fld.map_value_type
                    or descriptor_pb2.FieldDescriptorProto.TYPE_STRING
                )
                if fld.map_value_type_name:
                    v.type_name = resolve_field_type_name(
                        pf, md, fld.map_value_type_name
                    )
            else:
                f.type = fld.type
                tn = resolve_field_type_name(pf, md, fld.type_name)
                if tn:
                    f.type_name = tn
            if fld.proto3_optional:
                f.proto3_optional = True

    for ed in pf.enums:
        add_enum(ed)
    for md in pf.messages:
        add_message(md)
    return fd


def merge_fds(
    embedded: dict[str, descriptor_pb2.FileDescriptorProto],
    rebuilt: dict[str, descriptor_pb2.FileDescriptorProto],
) -> dict[str, descriptor_pb2.FileDescriptorProto]:
    out = dict(rebuilt)
    for name, fd in embedded.items():
        if name not in out:
            out[name] = fd
            continue
        if len(fd.message_type) > len(out[name].message_type):
            out[name] = fd
    return out


def run(
    metadata_path: Path, dump_path: Path, out_path: Path
) -> dict[str, int | list[str]]:
    if not metadata_path.exists():
        raise FileNotFoundError(f"metadata not found: {metadata_path}")
    if not dump_path.exists():
        raise FileNotFoundError(f"dump.cs not found: {dump_path}")

    out_path.parent.mkdir(parents=True, exist_ok=True)

    _reset_missing_well_known()
    embedded = extract_embedded_from_metadata(metadata_path)
    parsed = parse_dump_cs(dump_path)
    missing_well_known = _emit_missing_well_known_warning()
    type_index = build_type_index(parsed)
    for pf in parsed.values():
        add_deps_from_fields(pf, type_index)
    rebuilt = {name: protofile_to_fdp(pf) for name, pf in parsed.items()}
    merged = merge_fds(embedded, rebuilt)

    fds = descriptor_pb2.FileDescriptorSet()
    for name in sorted(merged.keys()):
        fds.file.add().CopyFrom(merged[name])

    out_path.write_bytes(fds.SerializeToString())
    return {
        "embedded": len(embedded),
        "rebuilt": len(rebuilt),
        "merged": len(merged),
        "missing_well_known": missing_well_known,
    }
