"""Decode CompressedLocaResponse / LocaResponse blobs into English catalogs.

InnoGames ships localization as ``WrappedResponse`` → ``CompressedLocaResponse``
(or uncompressed ``LocaResponse``). Compressed payloads use
``LocalizationDataCollection`` layout:

    uint32 le entry_count
    repeated { uint64 fnv1a64(key); uint32 offset; uint32 length }
    payload entries: uint32 streamPosition + raw_deflate(values)

Indexed ``length`` is 4 bytes short of a complete deflate stream (DeflateStream
over-read). Decompress by reading ``length + 4`` bytes from the payload.
"""

from __future__ import annotations

import json
import re
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from google.protobuf import message_factory

from xapk_to_proto.gamedesign import load_descriptor_pool
from xapk_to_proto.gamedesign_constants import escape_ts_string, unescape_csharp_string

WRAPPED_RESPONSE = "WrappedResponse"
COMPRESSED_LOCA = "CompressedLocaResponse"
LOCA_RESPONSE = "LocaResponse"

FNV_OFFSET = 14695981039346656037
FNV_PRIME = 1099511628211

_LOCAKEYS_CLASS_RE = re.compile(
    r"^public class LocaKeys\.([\w.]+)\s*//[^\n]*\n\{",
    re.MULTILINE,
)

_STRING_CONST_RE = re.compile(
    r'^\s*public const string (\w+) = "((?:[^"\\]|\\.)*)";\s*$',
    re.MULTILINE,
)

_TEMPLATE_RE = re.compile(
    r'^\s*(?:private|public) const string (\w+LocaKey|LocalizedPortalError) = "([^"]+)";\s*$',
    re.MULTILINE,
)

# Proto-style display maps keyed by LocaKeys leaf group → TS export name.
# value: (export_name, optional key suffix to strip, e.g. "_Name")
_DISPLAY_MAP_GROUPS: dict[str, tuple[str, str]] = {
    "Base.Rarities": ("Rarity", ""),
    "Base.HeroClass": ("HeroClass", ""),
    "Base.AllianceRoles": ("AllianceRole", "_Name"),
    "Base.UnitTypes": ("UnitType", "_Name"),
    "Base.UnitColors": ("UnitColor", ""),
    "Base.Locales": ("Locale", ""),
}


@dataclass
class LocaKeyConstant:
    member: str
    key: str


@dataclass
class LocaKeysClass:
    path: str  # e.g. "Base.Rarities"
    constants: list[LocaKeyConstant] = field(default_factory=list)


@dataclass
class LocaExportResult:
    locale: str
    checksum: str
    version: str
    entry_count: int
    resolved_keys: int
    unresolved_hashes: int
    files_written: int
    out_dir: Path
    warnings: list[str] = field(default_factory=list)


def fnv1a64(text: str) -> int:
    """FNV-1a 64-bit over UTF-8 bytes (matches LocalizationDataCollection.Fnv1a64)."""
    h = FNV_OFFSET
    for byte in text.encode("utf-8"):
        h ^= byte
        h = (h * FNV_PRIME) & 0xFFFFFFFFFFFFFFFF
    return h


def read_7bit_encoded_int(buf: bytes, offset: int = 0) -> tuple[int, int]:
    result = 0
    shift = 0
    i = offset
    while True:
        if i >= len(buf):
            raise ValueError("truncated 7-bit int")
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, i
        shift += 7
        if shift > 35:
            raise ValueError("7-bit int overflow")


def write_7bit_encoded_int(value: int) -> bytes:
    out = bytearray()
    v = value
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def encode_loca_values(values: list[str]) -> bytes:
    """BinaryWriter-style serialization of a string array."""
    out = bytearray(write_7bit_encoded_int(len(values)))
    for value in values:
        raw = value.encode("utf-8")
        out.extend(write_7bit_encoded_int(len(raw)))
        out.extend(raw)
    return bytes(out)


def parse_loca_values(buf: bytes) -> list[str]:
    count, i = read_7bit_encoded_int(buf, 0)
    if count < 0 or count > 64:
        raise ValueError(f"implausible value count: {count}")
    values: list[str] = []
    for _ in range(count):
        length, i = read_7bit_encoded_int(buf, i)
        if length < 0 or i + length > len(buf):
            raise ValueError("implausible string length")
        values.append(buf[i : i + length].decode("utf-8"))
        i += length
    if i != len(buf):
        raise ValueError(f"trailing bytes after values ({i}/{len(buf)})")
    return values


def compress_loca_entry(values: list[str], stream_position: int = 0) -> bytes:
    """Encode like CompressEntry: u32 streamPosition + raw_deflate(values)."""
    raw = encode_loca_values(values)
    co = zlib.compressobj(zlib.Z_DEFAULT_COMPRESSION, zlib.DEFLATED, -15)
    deflated = co.compress(raw) + co.flush()
    return struct.pack("<I", stream_position & 0xFFFFFFFF) + deflated


def decompress_loca_entry(blob: bytes) -> list[str]:
    """Inflate a full entry (streamPosition + complete raw deflate)."""
    if len(blob) < 5:
        raise ValueError("loca entry too short")
    body = blob[4:]
    uncompressed = zlib.decompress(body, -15)
    return parse_loca_values(uncompressed)


def build_compressed_collection(
    entries: dict[str, list[str]],
) -> bytes:
    """Build a LocalizationDataCollection ``data`` blob for tests.

    Production packs entries so indexed ``length`` is 4 short of a full
    deflate stream; the next entry's ``streamPosition`` header supplies those
    4 bytes on over-read. Synthetic packs use the same overlap.
    """
    items = list(entries.items())
    if not items:
        return struct.pack("<I", 0)

    full_blobs: list[tuple[int, bytes]] = []
    stream_pos = 0
    for key, values in items:
        blob = compress_loca_entry(values, stream_position=stream_pos)
        full_blobs.append((fnv1a64(key), blob))
        # Next header overwrites this trailer; set it equal so over-read works.
        stream_pos = struct.unpack_from("<I", blob, len(blob) - 4)[0]

    payload = bytearray(full_blobs[0][1])
    offsets: list[tuple[int, int, int]] = [
        (full_blobs[0][0], 0, len(full_blobs[0][1]) - 4)
    ]
    for key_hash, blob in full_blobs[1:]:
        start = len(payload) - 4
        payload[start:] = blob
        offsets.append((key_hash, start, len(blob) - 4))

    index = bytearray(struct.pack("<I", len(offsets)))
    for key_hash, offset, length in offsets:
        index.extend(struct.pack("<QII", key_hash, offset, length))
    return bytes(index) + bytes(payload)


def parse_compressed_collection(
    data: bytes,
) -> dict[int, list[str]]:
    """Parse ``CompressedLocaResponse.data`` → ``{fnv_hash: values[]}``."""
    if len(data) < 4:
        raise ValueError("compressed loca data too short")
    count = struct.unpack_from("<I", data, 0)[0]
    index_end = 4 + count * 16
    if index_end > len(data):
        raise ValueError("loca index exceeds data length")
    payload = data[index_end:]

    result: dict[int, list[str]] = {}
    for i in range(count):
        key_hash, offset, length = struct.unpack_from("<QII", data, 4 + i * 16)
        end = offset + length + 4
        if end > len(payload):
            raise ValueError(
                f"entry {i} over-read past payload ({end} > {len(payload)})"
            )
        blob = payload[offset:end]
        result[key_hash] = decompress_loca_entry(blob)
    return result


def _class_body(text: str, open_brace_end: int) -> str | None:
    depth = 1
    i = open_brace_end
    while i < len(text):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace_end:i]
        i += 1
    return None


def parse_loca_keys(dump_cs: Path) -> tuple[list[LocaKeysClass], list[str]]:
    """Parse ``LocaKeys.*`` string constant classes from dump.cs."""
    text = dump_cs.read_text(encoding="utf-8", errors="replace")
    classes: list[LocaKeysClass] = []
    warnings: list[str] = []

    for match in _LOCAKEYS_CLASS_RE.finditer(text):
        path = match.group(1)
        body = _class_body(text, match.end())
        if body is None:
            warnings.append(f"unclosed LocaKeys class: {path}")
            continue
        constants: list[LocaKeyConstant] = []
        for const_match in _STRING_CONST_RE.finditer(body):
            member = const_match.group(1)
            raw = const_match.group(2)
            constants.append(
                LocaKeyConstant(member=member, key=unescape_csharp_string(raw))
            )
        if not constants:
            continue
        classes.append(LocaKeysClass(path=path, constants=constants))
    return classes, warnings


def parse_loca_templates(dump_cs: Path) -> dict[str, str]:
    """Parse ``*LocaKey = "Base....{0}..."`` templates from dump.cs."""
    text = dump_cs.read_text(encoding="utf-8", errors="replace")
    return {
        match.group(1): match.group(2)
        for match in _TEMPLATE_RE.finditer(text)
    }


def loca_keys_to_hash_map(
    classes: list[LocaKeysClass],
) -> dict[int, str]:
    """Build FNV hash → loca key string from parsed LocaKeys."""
    mapping: dict[int, str] = {}
    for cls in classes:
        for const in cls.constants:
            mapping[fnv1a64(const.key)] = const.key
    return mapping


def resolve_catalog(
    hashed: dict[int, list[str]],
    hash_to_key: dict[int, str],
) -> tuple[dict[str, list[str]], int]:
    """Resolve hashes to keys; unresolved stay as ``0x…`` hex strings."""
    catalog: dict[str, list[str]] = {}
    unresolved = 0
    for key_hash, values in hashed.items():
        key = hash_to_key.get(key_hash)
        if key is None:
            key = f"0x{key_hash:016x}"
            unresolved += 1
        catalog[key] = values
    return dict(sorted(catalog.items())), unresolved


def _leaf_display_members(
    cls: LocaKeysClass,
    *,
    key_suffix: str = "",
) -> list[tuple[LocaKeyConstant, str]]:
    """Return (const, display_member_name) for flat leaf keys in a LocaKeys group."""
    group_key = cls.path if cls.path.startswith("Base.") else f"Base.{cls.path}"
    out: list[tuple[LocaKeyConstant, str]] = []
    for const in cls.constants:
        if const.key == group_key:
            continue
        if not const.key.startswith(group_key + "."):
            continue
        last = const.key.rsplit(".", 1)[-1]
        if key_suffix:
            if not last.endswith(key_suffix):
                continue
            leaf = last[: -len(key_suffix)]
        else:
            if "_" in last:
                continue
            leaf = last
        if not leaf:
            continue
        out.append((const, leaf))
    return out


def _ts_member_name(member: str) -> str:
    """Convert PascalCase LocaKeys member to UPPER_SNAKE for display maps."""
    if member.isupper():
        return member
    chars: list[str] = []
    for i, ch in enumerate(member):
        if ch.isupper() and i > 0 and (
            member[i - 1].islower()
            or (i + 1 < len(member) and member[i + 1].islower())
        ):
            chars.append("_")
        chars.append(ch.upper())
    return "".join(chars)


def render_display_map_ts(
    export_name: str,
    entries: list[tuple[str, str]],
    *,
    source_group: str,
) -> str:
    lines = [
        "/**",
        f" * Auto-generated from LocaKeys.{source_group} + English loca.",
        " * Do not edit by hand.",
        " */",
        f"export const {export_name}DisplayName = {{",
    ]
    for member, display in entries:
        lines.append(f'  {member}: "{escape_ts_string(display)}",')
    lines.append("} as const;")
    lines.append("")
    lines.append(
        f"export type {export_name}DisplayNameKey = "
        f"keyof typeof {export_name}DisplayName;"
    )
    lines.append("")
    return "\n".join(lines)


def build_display_maps(
    classes: list[LocaKeysClass],
    catalog: dict[str, list[str]],
) -> dict[str, str]:
    """Return ``{ExportName: ts_source}`` for configured LocaKeys groups."""
    by_path = {cls.path: cls for cls in classes}
    files: dict[str, str] = {}
    for group_path, (export_name, key_suffix) in _DISPLAY_MAP_GROUPS.items():
        cls = by_path.get(group_path)
        if cls is None:
            continue
        entries: list[tuple[str, str]] = []
        for const, leaf in _leaf_display_members(cls, key_suffix=key_suffix):
            values = catalog.get(const.key)
            if not values:
                continue
            entries.append((_ts_member_name(leaf), values[0]))
        if not entries:
            continue
        files[export_name] = render_display_map_ts(
            export_name, entries, source_group=group_path
        )
    return files


def render_display_index_ts(export_names: list[str]) -> str:
    lines = [
        "/**",
        " * Auto-generated loca display-name barrel.",
        " * Do not edit by hand.",
        " */",
        "",
    ]
    for name in sorted(export_names):
        lines.append(
            f'export {{ {name}DisplayName, type {name}DisplayNameKey }} '
            f'from "./{name}";'
        )
    lines.append("")
    return "\n".join(lines)


def _decode_compressed_loca_message(
    data: bytes,
    pool,
) -> Any:
    """Return a parsed CompressedLocaResponse or LocaResponse message."""
    wr_cls = message_factory.GetMessageClass(
        pool.FindMessageTypeByName(WRAPPED_RESPONSE)
    )
    compressed_cls = message_factory.GetMessageClass(
        pool.FindMessageTypeByName(COMPRESSED_LOCA)
    )
    loca_cls = message_factory.GetMessageClass(
        pool.FindMessageTypeByName(LOCA_RESPONSE)
    )

    wr = wr_cls()
    try:
        wr.ParseFromString(data)
        if wr.HasField("response") and wr.response.type_url:
            type_name = wr.response.type_url.rsplit("/", 1)[-1]
            if type_name.endswith(COMPRESSED_LOCA) or type_name == COMPRESSED_LOCA:
                msg = compressed_cls()
                msg.ParseFromString(wr.response.value)
                return msg
            if type_name.endswith(LOCA_RESPONSE) or type_name == LOCA_RESPONSE:
                msg = loca_cls()
                msg.ParseFromString(wr.response.value)
                return msg
    except Exception:
        pass

    compressed = compressed_cls()
    try:
        compressed.ParseFromString(data)
        if compressed.data or compressed.locale:
            return compressed
    except Exception:
        pass

    loca = loca_cls()
    loca.ParseFromString(data)
    return loca


def extract_translations_from_message(
    msg: Any,
) -> tuple[dict[str, list[str]] | dict[int, list[str]], dict[str, Any], bool]:
    """Return (catalog_or_hashed, meta, is_hashed) from a loca protobuf message."""
    meta: dict[str, Any] = {
        "locale": getattr(msg, "locale", "") or "",
        "checksum": getattr(msg, "checksum", "") or "",
        "version": getattr(msg, "version", "") or "",
    }
    descriptor_name = msg.DESCRIPTOR.name

    if descriptor_name == COMPRESSED_LOCA:
        hashed = parse_compressed_collection(bytes(msg.data))
        return hashed, meta, True

    if descriptor_name == LOCA_RESPONSE:
        catalog: dict[str, list[str]] = {}
        for item in msg.translations:
            catalog[item.key] = list(item.values)
        return catalog, meta, False

    raise ValueError(f"unsupported loca message type: {descriptor_name}")


def write_loca_export(
    out_dir: Path,
    *,
    catalog: dict[str, list[str]],
    meta: dict[str, Any],
    display_maps: dict[str, str],
    templates: dict[str, str],
    warnings: list[str],
) -> LocaExportResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    files_written = 0

    locale = meta.get("locale") or "unknown"
    locale_safe = re.sub(r"[^\w.-]+", "_", locale)
    catalog_path = out_dir / f"{locale_safe}.json"
    catalog_path.write_text(
        json.dumps(catalog, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    files_written += 1

    meta_out = {
        "locale": meta.get("locale", ""),
        "checksum": meta.get("checksum", ""),
        "version": meta.get("version", ""),
        "entry_count": len(catalog),
        "resolved_keys": meta.get("resolved_keys", 0),
        "unresolved_hashes": meta.get("unresolved_hashes", 0),
        "templates": templates,
        "warnings": warnings,
    }
    (out_dir / "meta.json").write_text(
        json.dumps(meta_out, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    files_written += 1

    for export_name, source in sorted(display_maps.items()):
        (out_dir / f"{export_name}.ts").write_text(source, encoding="utf-8")
        files_written += 1

    if display_maps:
        (out_dir / "index.ts").write_text(
            render_display_index_ts(list(display_maps.keys())),
            encoding="utf-8",
        )
        files_written += 1

    return LocaExportResult(
        locale=locale,
        checksum=str(meta.get("checksum", "")),
        version=str(meta.get("version", "")),
        entry_count=len(catalog),
        resolved_keys=int(meta.get("resolved_keys", 0)),
        unresolved_hashes=int(meta.get("unresolved_hashes", 0)),
        files_written=files_written,
        out_dir=out_dir,
        warnings=warnings,
    )


def run_loca_export(
    *,
    descriptors_pb: Path,
    dump_cs: Path,
    input_path: Path,
    out_dir: Path,
    verbose: bool = False,
) -> LocaExportResult:
    if not input_path.is_file():
        raise FileNotFoundError(f"loca input not found: {input_path}")
    if not dump_cs.is_file():
        raise FileNotFoundError(f"dump.cs not found: {dump_cs}")

    pool, _ = load_descriptor_pool(descriptors_pb)
    data = input_path.read_bytes()
    if verbose:
        print(f"  decoding {input_path} ({len(data)} bytes)")

    msg = _decode_compressed_loca_message(data, pool)
    raw_catalog, meta, is_hashed = extract_translations_from_message(msg)
    warnings: list[str] = []

    loca_classes, key_warnings = parse_loca_keys(dump_cs)
    warnings.extend(key_warnings)
    templates = parse_loca_templates(dump_cs)
    hash_to_key = loca_keys_to_hash_map(loca_classes)

    if is_hashed:
        assert isinstance(raw_catalog, dict)
        catalog, unresolved = resolve_catalog(
            raw_catalog,  # type: ignore[arg-type]
            hash_to_key,
        )
        resolved = len(catalog) - unresolved
    else:
        catalog = dict(sorted(raw_catalog.items()))  # type: ignore[arg-type]
        unresolved = sum(1 for key in catalog if key.startswith("0x"))
        resolved = len(catalog) - unresolved

    if unresolved:
        warnings.append(f"{unresolved} loca entries could not be resolved via LocaKeys")

    meta["resolved_keys"] = resolved
    meta["unresolved_hashes"] = unresolved

    display_maps = build_display_maps(loca_classes, catalog)
    return write_loca_export(
        out_dir,
        catalog=catalog,
        meta=meta,
        display_maps=display_maps,
        templates=templates,
        warnings=warnings,
    )
