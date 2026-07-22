"""Repair known-bad descriptors from IL2CPP metadata before emitting .proto text."""

from __future__ import annotations

from google.protobuf import descriptor_pb2

LEGACY_PACKAGE_OBJECT = ".InnoGames.Generated.Protobuf.object"
LEGACY_PACKAGE_STRUCT = ".InnoGames.Generated.Protobuf.Struct"
PACKAGE_OBJECT = ".object"
PACKAGE_STRUCT = ".Struct"
GOOGLE_STRUCT = "google.protobuf.Struct"
GOOGLE_STRUCT_IMPORT = "google/protobuf/struct.proto"
GOOGLE_ANY = "google.protobuf.Any"
GOOGLE_ANY_IMPORT = "google/protobuf/any.proto"

_HERO_BATTLE_LOG_ENTRY: dict[str, str] = {
    "attack": "HeroBattleAttackLogEntry",
    "ability": "HeroAbilityLogEntry",
    "effect": "HeroEffectLogEntry",
    "effect_end": "HeroEffectEndLogEntry",
    "cleanse": "HeroAbilityEffectCleansedLogEntry",
    "battle_end": "HeroBattleEndLogEntry",
    "status_effect_interaction": "HeroStatusEffectInteractionLogEntry",
}

_HERO_EFFECT_LOG_ENTRY: dict[str, str] = {
    "non_targetable": "NonTargetable",
    "immunity": "Immunity",
    "unit_count_stat_change": "UnitCountStatChange",
    "damage_transfer": "DamageTransfer",
    "multi_lane_targetable": "MultiLaneTargetable",
    "charm": "Charm",
    "damage": "Damage",
    "ghosting": "Ghosting",
    "despawn_effect": "DespawnEffect",
}

_OBJECT_MESSAGE_MAP: dict[tuple[str, str], dict[str, str]] = {
    ("hero_battle_log.proto", "HeroBattleLogEntry"): _HERO_BATTLE_LOG_ENTRY,
    ("hero_battle_log.proto", "HeroEffectLogEntry"): _HERO_EFFECT_LOG_ENTRY,
}

_STRING_LIKE_OBJECT_FIELDS = frozenset(
    {
        "building_group",
        "building_definition_id",
        "battle_definition_id",
        "negotiation_definition_id",
    }
)


def _snake_to_pascal(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_"))


def _is_object_type(type_name: str) -> bool:
    return (
        type_name in (PACKAGE_OBJECT, LEGACY_PACKAGE_OBJECT)
        or type_name.endswith(".object")
    )


def _is_bad_struct_type(type_name: str) -> bool:
    return type_name in (
        PACKAGE_STRUCT,
        LEGACY_PACKAGE_STRUCT,
        ".Struct",
        "Struct",
    )


def _package_prefix(package: str) -> str:
    return f".{package}." if package else "."


def _qualify(package: str, message: str, parent: str | None) -> str:
    parts = [
        p
        for p in (
            *([package] if package else []),
            *([parent] if parent else []),
            message,
        )
        if p
    ]
    return "." + ".".join(parts)


def _collect_messages(
    fd: descriptor_pb2.FileDescriptorProto,
) -> dict[tuple[str | None, str], descriptor_pb2.DescriptorProto]:
    index: dict[tuple[str | None, str], descriptor_pb2.DescriptorProto] = {}

    def walk(msg: descriptor_pb2.DescriptorProto, parent: str | None) -> None:
        index[(parent, msg.name)] = msg
        for nested in msg.nested_type:
            if nested.options.map_entry:
                continue
            walk(nested, msg.name)

    for msg in fd.message_type:
        walk(msg, None)
    return index


def _resolve_object_field(
    fd: descriptor_pb2.FileDescriptorProto,
    parent_name: str | None,
    msg: descriptor_pb2.DescriptorProto,
    field: descriptor_pb2.FieldDescriptorProto,
    message_index: dict[tuple[str | None, str], descriptor_pb2.DescriptorProto],
    deps: set[str],
) -> str | None:
    explicit = _OBJECT_MESSAGE_MAP.get((fd.name, msg.name), {}).get(field.name)
    if explicit:
        if (msg.name, explicit) in message_index or (None, explicit) in message_index:
            return _qualify(fd.package, explicit, msg.name if (msg.name, explicit) in message_index else None)
        return _qualify(fd.package, explicit, None)

    if field.name in _STRING_LIKE_OBJECT_FIELDS or (
        field.name.endswith("_id") and field.name != "chest"
    ):
        return None

    if field.name == "player" and fd.name == "event_leaderboard.proto":
        deps.add("player.proto")
        return _qualify(fd.package, "PlayerInfoDTO", None)

    if field.name == "chest":
        return None

    pascal = _snake_to_pascal(field.name)
    if (msg.name, pascal) in message_index:
        return _qualify(fd.package, pascal, msg.name)
    if (None, pascal) in message_index:
        return _qualify(fd.package, pascal, None)
    for (p, name) in message_index:
        if name.lower() == pascal.lower():
            return _qualify(fd.package, name, p)
        if name.lower().endswith(pascal.lower()) and len(pascal) > 3:
            return _qualify(fd.package, name, p)
    return None


def _set_message_field(
    field: descriptor_pb2.FieldDescriptorProto,
    type_name: str,
    *,
    as_string: bool = False,
) -> None:
    if as_string:
        field.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING
        field.ClearField("type_name")
        return
    field.type = descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
    field.type_name = type_name


def _rebuild_oneof(
    msg: descriptor_pb2.DescriptorProto,
    fields: list[descriptor_pb2.FieldDescriptorProto],
    oneof_name: str,
) -> None:
    del msg.oneof_decl[:]
    oneof = msg.oneof_decl.add()
    oneof.name = oneof_name
    idx = 0
    for field in fields:
        field.oneof_index = idx


def _repair_message_objects(
    fd: descriptor_pb2.FileDescriptorProto,
    msg: descriptor_pb2.DescriptorProto,
    message_index: dict[tuple[str | None, str], descriptor_pb2.DescriptorProto],
    deps: set[str],
) -> None:
    object_fields = [f for f in msg.field if _is_object_type(f.type_name)]
    oneof_members: list[descriptor_pb2.FieldDescriptorProto] = []
    if object_fields:
        for field in object_fields:
            if field.name in _STRING_LIKE_OBJECT_FIELDS or (
                field.name.endswith("_id") and field.name != "chest"
            ):
                _set_message_field(field, "", as_string=True)
                continue
            resolved = _resolve_object_field(fd, None, msg, field, message_index, deps)
            if resolved:
                _set_message_field(field, resolved)
                oneof_members.append(field)
            else:
                deps.add(GOOGLE_ANY_IMPORT)
                _set_message_field(field, f".{GOOGLE_ANY}")
                oneof_members.append(field)
        if len(oneof_members) > 1:
            _rebuild_oneof(msg, oneof_members, "payload")

    for nested in msg.nested_type:
        if nested.options.map_entry:
            continue
        _repair_message_objects(fd, nested, message_index, deps)


def _index_nested_enums(
    fd: descriptor_pb2.FileDescriptorProto,
) -> dict[str, tuple[str, str]]:
    """Map enum short name -> (parent message name, enum name)."""
    found: dict[str, tuple[str, str]] = {}

    def walk(msg: descriptor_pb2.DescriptorProto) -> None:
        for enum in msg.enum_type:
            found[enum.name] = (msg.name, enum.name)
        for nested in msg.nested_type:
            if not nested.options.map_entry:
                walk(nested)

    for msg in fd.message_type:
        walk(msg)
    return found


def _repair_nested_enum_references(fd: descriptor_pb2.FileDescriptorProto) -> None:
    nested_enums = _index_nested_enums(fd)
    top_level_enums = {e.name for e in fd.enum_type}
    package_prefix = _package_prefix(fd.package)

    def walk(msg: descriptor_pb2.DescriptorProto) -> None:
        for field in msg.field:
            short = field.type_name.rsplit(".", 1)[-1] if field.type_name else ""
            if not short or short in top_level_enums:
                continue
            location = nested_enums.get(short)
            if not location and "." in field.type_name:
                location = nested_enums.get(field.type_name.rsplit(".", 2)[-1])
            if not location:
                continue
            parent, enum_name = location
            qualified = _qualify(fd.package, enum_name, parent)
            if field.type_name == f"{package_prefix}{short}":
                field.type = descriptor_pb2.FieldDescriptorProto.TYPE_ENUM
                field.type_name = qualified
        for nested in msg.nested_type:
            if not nested.options.map_entry:
                walk(nested)

    for msg in fd.message_type:
        walk(msg)


def _local_type_paths(
    fd: descriptor_pb2.FileDescriptorProto,
) -> tuple[set[str], set[str]]:
    """Collect full dotted paths (relative to package) of local messages and enums."""
    messages: set[str] = set()
    enums: set[str] = set()

    def walk(msg: descriptor_pb2.DescriptorProto, path: str) -> None:
        cur = f"{path}.{msg.name}" if path else msg.name
        messages.add(cur)
        for enum in msg.enum_type:
            enums.add(f"{cur}.{enum.name}")
        for nested in msg.nested_type:
            if not nested.options.map_entry:
                walk(nested, cur)

    for msg in fd.message_type:
        walk(msg, "")
    for enum in fd.enum_type:
        enums.add(enum.name)
    return messages, enums


def _repair_underqualified_local_types(
    fd: descriptor_pb2.FileDescriptorProto,
) -> None:
    """Fix intra-file refs that drop intermediate parents (e.g. deeply nested enums).

    IL2CPP metadata sometimes records ``.pkg.BatchedRequest.RequestMethod`` for a
    type whose real path is ``.pkg.BatchRequest.BatchedRequest.RequestMethod``.
    Emitting .proto text hides this (protoc resolves the short name lexically),
    but building the descriptor into a pool fails because the qualified name does
    not resolve. Rewrite such refs to the unique local path that matches the tail.
    """
    messages, enums = _local_type_paths(fd)
    prefix = _package_prefix(fd.package)
    all_paths = [(p, False) for p in messages] + [(p, True) for p in enums]

    def find(tail: str) -> tuple[str, bool] | None:
        matches = [
            (path, is_enum)
            for path, is_enum in all_paths
            if path == tail or path.endswith("." + tail)
        ]
        return matches[0] if len(matches) == 1 else None

    def walk(msg: descriptor_pb2.DescriptorProto) -> None:
        for field in msg.field:
            type_name = field.type_name
            if not type_name.startswith(prefix):
                continue
            tail = type_name[len(prefix) :]
            if tail in messages or tail in enums:
                continue
            match = find(tail)
            if match is None:
                continue
            path, is_enum = match
            field.type_name = f"{prefix}{path}"
            if is_enum:
                field.type = descriptor_pb2.FieldDescriptorProto.TYPE_ENUM

        for nested in msg.nested_type:
            if not nested.options.map_entry:
                walk(nested)

    for msg in fd.message_type:
        walk(msg)


def _normalize_csharp_type_name(type_name: str) -> str:
    return type_name.replace(".Types.", ".")


def build_nested_type_index(
    files: list[descriptor_pb2.FileDescriptorProto],
) -> tuple[
    dict[str, list[tuple[str, str, str]]],
    dict[str, list[tuple[str, str, str]]],
]:
    """Map short type name -> [(file, parent_message, nested_type_name)]."""
    messages: dict[str, list[tuple[str, str, str]]] = {}
    enums: dict[str, list[tuple[str, str, str]]] = {}

    def walk(
        file_name: str,
        msg: descriptor_pb2.DescriptorProto,
        parent: str | None,
    ) -> None:
        parent_name = parent or msg.name
        for enum in msg.enum_type:
            enums.setdefault(enum.name, []).append((file_name, parent_name, enum.name))
        for nested in msg.nested_type:
            if nested.options.map_entry:
                continue
            messages.setdefault(nested.name, []).append(
                (file_name, parent_name, nested.name)
            )
            walk(file_name, nested, msg.name)

    for fd in files:
        for msg in fd.message_type:
            walk(fd.name, msg, None)
    return messages, enums


def _repair_cross_file_nested_types(
    fd: descriptor_pb2.FileDescriptorProto,
    message_index: dict[str, list[tuple[str, str, str]]],
    enum_index: dict[str, list[tuple[str, str, str]]],
    deps: set[str],
) -> None:
    package_prefix = _package_prefix(fd.package)

    def resolve(short: str) -> tuple[str, str, str] | None:
        for index in (message_index, enum_index):
            candidates = index.get(short, [])
            if len(candidates) == 1:
                return candidates[0]
            if len(candidates) > 1:
                imported = [c for c in candidates if c[0] != fd.name]
                if len(imported) == 1:
                    return imported[0]
        return None

    def walk(msg: descriptor_pb2.DescriptorProto) -> None:
        for field in msg.field:
            if not field.type_name.startswith(package_prefix):
                continue
            short = field.type_name[len(package_prefix) :]
            if "." in short:
                continue
            match = resolve(short)
            if not match:
                continue
            file_name, parent, type_name = match
            if file_name != fd.name:
                deps.add(file_name)
            field.type_name = _qualify(fd.package, type_name, parent)
            if (file_name, parent, type_name) in [
                (c[0], c[1], c[2]) for c in enum_index.get(short, [])
            ]:
                field.type = descriptor_pb2.FieldDescriptorProto.TYPE_ENUM
        for nested in msg.nested_type:
            if not nested.options.map_entry:
                walk(nested)

    for msg in fd.message_type:
        walk(msg)


def build_nested_message_index(
    files: list[descriptor_pb2.FileDescriptorProto],
) -> dict[str, list[tuple[str, str, str]]]:
    messages, _ = build_nested_type_index(files)
    return messages


def repair_file_descriptor(
    fd: descriptor_pb2.FileDescriptorProto,
    *,
    nested_index: dict[str, list[tuple[str, str, str]]] | None = None,
    enum_index: dict[str, list[tuple[str, str, str]]] | None = None,
) -> None:
    """Normalize metadata quirks so protoc/buf accept the descriptor set."""
    deps = set(fd.dependency)
    message_index = _collect_messages(fd)

    for msg in fd.message_type:
        _repair_message_objects(fd, msg, message_index, deps)

    _repair_nested_enum_references(fd)

    def normalize_field_types(msg: descriptor_pb2.DescriptorProto) -> None:
        for field in msg.field:
            if field.type_name:
                field.type_name = _normalize_csharp_type_name(field.type_name)
        for nested in msg.nested_type:
            if not nested.options.map_entry:
                normalize_field_types(nested)

    for msg in fd.message_type:
        normalize_field_types(msg)

    _repair_nested_enum_references(fd)

    for msg in fd.message_type:
        def walk(m: descriptor_pb2.DescriptorProto) -> None:
            for field in m.field:
                if _is_bad_struct_type(field.type_name):
                    field.type_name = f".{GOOGLE_STRUCT}"
                    deps.add(GOOGLE_STRUCT_IMPORT)
            for nested in m.nested_type:
                if not nested.options.map_entry:
                    walk(nested)

        walk(msg)

    if nested_index is not None and enum_index is not None:
        _repair_cross_file_nested_types(fd, nested_index, enum_index, deps)

    _repair_underqualified_local_types(fd)

    def clear_invalid_proto3_optional(msg: descriptor_pb2.DescriptorProto) -> None:
        for field in msg.field:
            if field.proto3_optional and not field.HasField("oneof_index"):
                field.ClearField("proto3_optional")
        for nested in msg.nested_type:
            if not nested.options.map_entry:
                clear_invalid_proto3_optional(nested)

    for msg in fd.message_type:
        clear_invalid_proto3_optional(msg)

    fd.ClearField("dependency")
    for dep in sorted(deps):
        fd.dependency.append(dep)
