"""Rewrite reserved enum value names so multi-language codegen stays valid.

Only enum **values** are renamed (append ``_`` until unique). Field, message,
enum type, and service names are left unchanged. Wire numbers are preserved.
"""

from __future__ import annotations

import keyword

from google.protobuf import descriptor_pb2

# Python keywords plus cross-language / protobuf identifiers that commonly
# break or confuse codegen. Match is exact and case-sensitive.
_EXTRA_RESERVED: frozenset[str] = frozenset(
    {
        "default",
        "export",
        "function",
        "implements",
        "interface",
        "package",
        "private",
        "protected",
        "public",
        "static",
        "type",
        "enum",
        "message",
        "oneof",
        "option",
        "reserved",
        "repeated",
        "required",
        "optional",
        "syntax",
        "import",
        "extend",
        "extensions",
        "service",
        "rpc",
        "returns",
        "stream",
        "map",
        "float",
        "double",
        "int32",
        "int64",
        "uint32",
        "uint64",
        "sint32",
        "sint64",
        "fixed32",
        "fixed64",
        "sfixed32",
        "sfixed64",
        "bool",
        "string",
        "bytes",
        "true",
        "false",
        "null",
    }
)

RESERVED_ENUM_VALUE_NAMES: frozenset[str] = frozenset(keyword.kwlist) | _EXTRA_RESERVED


def rewrite_reserved_enum_value_name(name: str, used: set[str]) -> str:
    """Return ``name`` or a ``_``-suffixed variant unique within ``used``."""
    if name not in RESERVED_ENUM_VALUE_NAMES:
        return name
    candidate = name + "_"
    while candidate in used:
        candidate += "_"
    return candidate


def _rewrite_enum_values(enum: descriptor_pb2.EnumDescriptorProto) -> int:
    used = {v.name for v in enum.value}
    renamed = 0
    for val in enum.value:
        new_name = rewrite_reserved_enum_value_name(val.name, used - {val.name})
        if new_name != val.name:
            used.discard(val.name)
            used.add(new_name)
            val.name = new_name
            renamed += 1
    return renamed


def _rewrite_message_enums(msg: descriptor_pb2.DescriptorProto) -> int:
    renamed = 0
    for enum in msg.enum_type:
        renamed += _rewrite_enum_values(enum)
    for nested in msg.nested_type:
        if nested.options.map_entry:
            continue
        renamed += _rewrite_message_enums(nested)
    return renamed


def rewrite_file_enum_values(fd: descriptor_pb2.FileDescriptorProto) -> int:
    """Rename reserved enum values in ``fd``. Return number of renames."""
    renamed = 0
    for enum in fd.enum_type:
        renamed += _rewrite_enum_values(enum)
    for msg in fd.message_type:
        renamed += _rewrite_message_enums(msg)
    return renamed
