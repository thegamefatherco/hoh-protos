"""Discover, decode, and export hero-related GameDesign protobuf data."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from google.protobuf import any_pb2, descriptor_pb2, descriptor_pool, json_format, message_factory
from google.protobuf.descriptor_database import DescriptorDatabase
from google.protobuf.message import Message

from xapk_to_proto.data import load_well_known_fds
from xapk_to_proto.repair import build_nested_type_index, repair_file_descriptor

GD_MESSAGE = "GameDesignResponse"

MIN_CANDIDATE_SIZE = 50
MAX_CANDIDATE_SIZE = 80_000_000
MIN_GD_ENTRIES = 5

PRIORITY_GLOB_PATTERNS = (
    "**/gamedesign*",
    "**/GameDesign*",
    "**/gd_*",
    "**/GD_*",
    "**/cache/**",
    "**/StreamingAssets/**",
    "**/assets/bin/Data/**",
)

HERO_TYPE_PREFIXES = (
    "Hero",
    "BattleAbility",
    "BattleStatusEffect",
    "BattleEffect",
    "BattleConfig",
    "Battlefield",
    "HeroUnit",
    "UnitStat",
    "UnitSpawn",
    "Formula",
    "ActionComponent",
    "AttackCalculation",
    "PowerLevel",
    "SpawnSquad",
    "DamageEffect",
    "StatChange",
    "ManualTrigger",
    "ConditionalTrigger",
    "BattleVfx",
)

_DUMP_CS_PATH_RE = re.compile(
    r'"(?:[^"\\]|\\.)*(?:gamedesign|GameDesign|gd_cache|/cache/)(?:[^"\\]|\\.)*"',
    re.IGNORECASE,
)


@dataclass
class DecodedEntry:
    type_name: str
    data: dict[str, Any]


@dataclass
class GameDesignExportResult:
    source: str
    source_path: str | None
    checksum: str
    total_entries: int
    hero_entries: int
    type_counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _build_short_name_index_from_files(
    files: list[descriptor_pb2.FileDescriptorProto],
) -> dict[str, str]:
    """Map bare message short names to fully-qualified pool names."""
    index: dict[str, str] = {}

    def walk(
        package: str,
        parent: str | None,
        msg: descriptor_pb2.DescriptorProto,
    ) -> None:
        full_name = f"{package}.{parent + '.' if parent else ''}{msg.name}"
        if msg.name not in index:
            index[msg.name] = full_name
        for nested in msg.nested_type:
            if nested.options.map_entry:
                continue
            walk(package, f"{parent}.{msg.name}" if parent else msg.name, nested)

    for fd in files:
        for msg in fd.message_type:
            walk(fd.package, None, msg)
    return index


def load_descriptor_pool(
    descriptors_pb: Path,
) -> tuple[descriptor_pool.DescriptorPool, dict[str, str]]:
    if not descriptors_pb.is_file():
        raise FileNotFoundError(f"descriptors.pb not found: {descriptors_pb}")

    fds = descriptor_pb2.FileDescriptorSet()
    fds.ParseFromString(descriptors_pb.read_bytes())
    existing_names = {fd.name for fd in fds.file}

    db = DescriptorDatabase()
    for fd in load_well_known_fds().file:
        if fd.name not in existing_names:
            db.Add(fd)
    nested_index, enum_index = build_nested_type_index(list(fds.file))
    repaired_files: list[descriptor_pb2.FileDescriptorProto] = []
    for fd in fds.file:
        repaired = descriptor_pb2.FileDescriptorProto()
        repaired.CopyFrom(fd)
        repair_file_descriptor(
            repaired, nested_index=nested_index, enum_index=enum_index
        )
        repaired_files.append(repaired)
        db.Add(repaired)

    return descriptor_pool.DescriptorPool(db), _build_short_name_index_from_files(
        repaired_files
    )


def _message_class(
    pool: descriptor_pool.DescriptorPool, full_name: str
) -> type[Message]:
    return message_factory.GetMessageClass(pool.FindMessageTypeByName(full_name))


def type_url_to_full_name(type_url: str) -> str:
    if not type_url:
        raise ValueError("empty type_url")
    if "/" in type_url:
        return type_url.rsplit("/", 1)[-1]
    return type_url


def short_type_name(full_name: str) -> str:
    return full_name.rsplit(".", 1)[-1]


def is_hero_related_type(full_name: str) -> bool:
    short = short_type_name(full_name)
    return any(short.startswith(prefix) for prefix in HERO_TYPE_PREFIXES)


def _paths_from_dump_cs(dump_cs: Path) -> list[Path]:
    if not dump_cs.is_file():
        return []
    text = dump_cs.read_text(encoding="utf-8", errors="replace")
    hints: list[str] = []
    for match in _DUMP_CS_PATH_RE.finditer(text):
        raw = match.group(0).strip('"')
        raw = raw.replace("\\/", "/").replace("\\\\", "\\")
        if "/" in raw or raw.endswith((".pb", ".bin", ".bytes", ".dat")):
            hints.append(raw)
    return [Path(h) for h in dict.fromkeys(hints)]


def _candidate_files(xapk_root: Path, dump_cs: Path | None) -> list[Path]:
    seen: set[Path] = set()
    ordered: list[Path] = []

    def add(path: Path) -> None:
        resolved = path.resolve()
        if resolved in seen or not resolved.is_file():
            return
        seen.add(resolved)
        ordered.append(resolved)

    if dump_cs is not None:
        for hint in _paths_from_dump_cs(dump_cs):
            add(xapk_root / hint)
            add(hint)

    for pattern in PRIORITY_GLOB_PATTERNS:
        for path in sorted(xapk_root.glob(pattern)):
            if path.is_file():
                add(path)

    for path in sorted(xapk_root.rglob("*")):
        if not path.is_file():
            continue
        size = path.stat().st_size
        if size < MIN_CANDIDATE_SIZE or size > MAX_CANDIDATE_SIZE:
            continue
        add(path)

    return ordered


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


def _unwrap_gamedesign_envelope(
    data: bytes, pool: descriptor_pool.DescriptorPool
) -> Message | None:
    """Peel a server-response envelope: outer message field -> Any(GameDesignResponse)."""
    gd_cls = _message_class(pool, GD_MESSAGE)
    offset = 0
    while offset < len(data):
        try:
            tag, offset = _read_varint(data, offset)
        except ValueError:
            break
        wire_type = tag & 0x7
        if wire_type != 2:
            break
        try:
            length, offset = _read_varint(data, offset)
        except ValueError:
            break
        chunk = data[offset : offset + length]
        offset += length
        if not chunk:
            continue
        any_msg = any_pb2.Any()
        try:
            any_msg.ParseFromString(chunk)
        except Exception:
            continue
        if not any_msg.type_url.endswith("/GameDesignResponse"):
            continue
        gd = gd_cls()
        try:
            gd.ParseFromString(any_msg.value)
        except Exception:
            continue
        if gd.content:
            return gd
    return None


def _parse_gamedesign_message(
    data: bytes, pool: descriptor_pool.DescriptorPool
) -> Message | None:
    gd_cls = _message_class(pool, GD_MESSAGE)
    msg = gd_cls()
    try:
        msg.ParseFromString(data)
    except Exception:
        msg = gd_cls()
    if msg.content:
        return msg
    return _unwrap_gamedesign_envelope(data, pool)


def _resolve_message_type_name(
    pool: descriptor_pool.DescriptorPool,
    type_url: str,
    short_name_index: dict[str, str],
) -> str:
    full_name = type_url_to_full_name(type_url)
    try:
        pool.FindMessageTypeByName(full_name)
        return full_name
    except KeyError:
        pass
    short = short_type_name(full_name)
    resolved = short_name_index.get(short)
    if resolved is None:
        raise KeyError(f"unknown gamedesign type: {type_url}")
    return resolved


def _normalize_any_payload(
    any_msg: any_pb2.Any,
    pool: descriptor_pool.DescriptorPool,
    short_name_index: dict[str, str],
) -> bool:
    if not any_msg.type_url:
        return False
    try:
        full_name = _resolve_message_type_name(
            pool, any_msg.type_url, short_name_index
        )
    except KeyError:
        return False
    any_msg.type_url = f"type.googleapis.com/{full_name}"
    inner_cls = _message_class(pool, full_name)
    inner = inner_cls()
    inner.ParseFromString(any_msg.value)
    _normalize_any_type_urls(inner, pool, short_name_index)
    any_msg.value = inner.SerializeToString()
    return True


def _normalize_any_type_urls(
    msg: Message,
    pool: descriptor_pool.DescriptorPool,
    short_name_index: dict[str, str],
) -> None:
    """Rewrite bare Any type_urls and normalize nested payloads for MessageToDict."""
    for field, value in msg.ListFields():
        if field.message_type is None:
            continue
        if field.message_type.full_name == "google.protobuf.Any":
            if field.is_repeated:
                kept: list[any_pb2.Any] = []
                for any_msg in value:
                    if _normalize_any_payload(any_msg, pool, short_name_index):
                        kept.append(any_msg)
                repeated = getattr(msg, field.name)
                del repeated[:]
                repeated.extend(kept)
            else:
                if not _normalize_any_payload(value, pool, short_name_index):
                    msg.ClearField(field.name)
            continue
        if field.message_type.GetOptions().map_entry:
            value_desc = field.message_type.fields_by_name["value"]
            if value_desc.message_type is None:
                continue
            if value_desc.message_type.full_name == "google.protobuf.Any":
                for key in list(value.keys()):
                    if not _normalize_any_payload(value[key], pool, short_name_index):
                        del value[key]
            else:
                for map_value in value.values():
                    _normalize_any_type_urls(map_value, pool, short_name_index)
            continue
        values = value if field.is_repeated else [value]
        for nested in values:
            if isinstance(nested, Message):
                _normalize_any_type_urls(nested, pool, short_name_index)


def _decode_any_entry(
    any_msg: any_pb2.Any,
    pool: descriptor_pool.DescriptorPool,
    short_name_index: dict[str, str],
) -> DecodedEntry:
    full_name = _resolve_message_type_name(
        pool, any_msg.type_url, short_name_index
    )
    inner_cls = _message_class(pool, full_name)
    inner = inner_cls()
    inner.ParseFromString(any_msg.value)
    _normalize_any_type_urls(inner, pool, short_name_index)
    return DecodedEntry(
        type_name=full_name,
        data=message_to_dict(inner, pool),
    )


def try_parse_gamedesign(
    data: bytes, pool: descriptor_pool.DescriptorPool
) -> Message | None:
    msg = _parse_gamedesign_message(data, pool)
    if msg is None or len(msg.content) < MIN_GD_ENTRIES:
        return None
    return msg


def discover_gamedesign_blobs(
    xapk_root: Path,
    pool: descriptor_pool.DescriptorPool,
    dump_cs: Path | None = None,
) -> list[Path]:
    hits: list[tuple[int, Path]] = []
    for path in _candidate_files(xapk_root, dump_cs):
        gd = try_parse_gamedesign(path.read_bytes(), pool)
        if gd is not None:
            hits.append((len(gd.content), path))
    hits.sort(key=lambda item: item[0], reverse=True)
    return [path for _, path in hits]


def message_to_dict(msg: Message, pool: descriptor_pool.DescriptorPool) -> dict[str, Any]:
    return json_format.MessageToDict(
        msg,
        preserving_proto_field_name=True,
        always_print_fields_with_no_presence=False,
        descriptor_pool=pool,
    )


def entry_to_protojson_dict(entry: DecodedEntry) -> dict[str, Any]:
    """Wrap a decoded entry as ProtoJSON suitable for ``google.protobuf.Any`` parsing."""
    return {
        **entry.data,
        "@type": f"type.googleapis.com/{entry.type_name}",
    }


def decode_gamedesign(
    data: bytes,
    pool: descriptor_pool.DescriptorPool,
    short_name_index: dict[str, str],
) -> tuple[str, list[DecodedEntry]]:
    gd = _parse_gamedesign_message(data, pool)
    if gd is None:
        raise ValueError("data is not a valid GameDesignResponse")

    entries = [
        _decode_any_entry(any_msg, pool, short_name_index)
        for any_msg in gd.content
    ]
    return gd.checksum, entries


def filter_hero_definitions(entries: list[DecodedEntry]) -> list[DecodedEntry]:
    return [entry for entry in entries if is_hero_related_type(entry.type_name)]


def _group_by_type(entries: list[DecodedEntry]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        key = short_type_name(entry.type_name)
        grouped.setdefault(key, []).append(entry_to_protojson_dict(entry))
    return grouped


def write_gamedesign_export(
    out_dir: Path,
    *,
    source: str,
    source_path: Path | None,
    checksum: str,
    all_entries: list[DecodedEntry],
    hero_entries: list[DecodedEntry],
    warnings: list[str] | None = None,
) -> GameDesignExportResult:
    heroes_dir = out_dir / "heroes"
    heroes_dir.mkdir(parents=True, exist_ok=True)

    grouped = _group_by_type(hero_entries)
    type_counts = {name: len(items) for name, items in grouped.items()}
    for type_name, items in sorted(grouped.items()):
        (heroes_dir / f"{type_name}.json").write_text(
            json.dumps(items, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    all_counts: dict[str, int] = {}
    for entry in all_entries:
        short = short_type_name(entry.type_name)
        all_counts[short] = all_counts.get(short, 0) + 1

    manifest = {
        "source": source,
        "source_path": str(source_path) if source_path else None,
        "checksum": checksum,
        "total_entries": len(all_entries),
        "hero_entries": len(hero_entries),
        "hero_type_counts": type_counts,
        "all_type_counts": all_counts,
        "warnings": warnings or [],
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    return GameDesignExportResult(
        source=source,
        source_path=str(source_path) if source_path else None,
        checksum=checksum,
        total_entries=len(all_entries),
        hero_entries=len(hero_entries),
        type_counts=type_counts,
        warnings=warnings or [],
    )


def run_gamedesign_export(
    *,
    descriptors_pb: Path,
    out_dir: Path,
    xapk_root: Path | None = None,
    dump_cs: Path | None = None,
    input_path: Path | None = None,
    verbose: bool = False,
) -> GameDesignExportResult:
    pool, short_name_index = load_descriptor_pool(descriptors_pb)
    warnings: list[str] = []

    source = "not-found"
    source_path: Path | None = None
    data: bytes | None = None

    if input_path is not None:
        if not input_path.is_file():
            raise FileNotFoundError(f"gamedesign input not found: {input_path}")
        source = "external-input"
        source_path = input_path.resolve()
        data = input_path.read_bytes()
    elif xapk_root is not None:
        candidates = discover_gamedesign_blobs(xapk_root, pool, dump_cs)
        if candidates:
            source = "bundled"
            source_path = candidates[0]
            data = source_path.read_bytes()
            if verbose:
                print(f"  found GameDesignResponse: {source_path} ({len(data)} bytes)")
        else:
            warnings.append(
                "No GameDesignResponse blob found in XAPK; "
                "hero gamedesign is likely server-delivered. "
                "Use --gamedesign-input with a captured cache/API blob."
            )
            if verbose:
                print("  no bundled GameDesignResponse found in XAPK")

    if data is None:
        return write_gamedesign_export(
            out_dir,
            source=source,
            source_path=source_path,
            checksum="",
            all_entries=[],
            hero_entries=[],
            warnings=warnings,
        )

    checksum, all_entries = decode_gamedesign(data, pool, short_name_index)
    hero_entries = filter_hero_definitions(all_entries)
    return write_gamedesign_export(
        out_dir,
        source=source,
        source_path=source_path,
        checksum=checksum,
        all_entries=all_entries,
        hero_entries=hero_entries,
        warnings=warnings,
    )
