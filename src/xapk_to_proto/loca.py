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
import shutil
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from google.protobuf import message_factory

from xapk_to_proto.gamedesign import load_descriptor_pool
from xapk_to_proto.gamedesign_constants import unescape_csharp_string

WRAPPED_RESPONSE = "WrappedResponse"
COMPRESSED_LOCA = "CompressedLocaResponse"
LOCA_RESPONSE = "LocaResponse"

PREFIX_DIR = "by_prefix"
PREFIX_I18NEXT_DIR = "i18next"
PREFIX_ICU_DIR = "icu"
UNRESOLVED_PREFIX = "_unresolved"
PREFIX_DEPTH = 2

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

# C# / game placeholders: {0}, {0:d}, {1:s}, {0:%d}, {duration}, …
_PLACEHOLDER_RE = re.compile(r"\{([^{}]+)\}")
# Positional index with optional alignment and format spec.
_POSITIONAL_PLACEHOLDER_RE = re.compile(r"^(\d+)(?:,-?\d+)?(?::.*)?$")


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


def normalize_placeholder_name(inner: str) -> str:
    """Strip C# alignment/format specs from a placeholder body.

    ``0:d`` / ``0:%d`` / ``1,10:s`` → ``0`` / ``1``; named bodies like
    ``duration`` are returned unchanged (format after ``:`` stripped if present).
    """
    positional = _POSITIONAL_PLACEHOLDER_RE.match(inner)
    if positional:
        return positional.group(1)
    # Named arg with optional format: take name before first unescaped ':'.
    name_chars: list[str] = []
    escaped = False
    for ch in inner:
        if escaped:
            name_chars.append(ch)
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == ":":
            break
        name_chars.append(ch)
    return "".join(name_chars) if name_chars else inner


def strip_csharp_format_specs(text: str) -> str:
    """Rewrite ``{0:d}`` / ``{1:s}`` → ``{0}`` / ``{1}``; leave TMP tags alone."""

    def repl(match: re.Match[str]) -> str:
        return "{" + normalize_placeholder_name(match.group(1)) + "}"

    return _PLACEHOLDER_RE.sub(repl, text)


def to_i18next_placeholders(text: str) -> str:
    """Convert game placeholders to i18next ``{{name}}`` interpolation."""

    def repl(match: re.Match[str]) -> str:
        return "{{" + normalize_placeholder_name(match.group(1)) + "}}"

    return _PLACEHOLDER_RE.sub(repl, text)


def to_icu_placeholders(text: str) -> str:
    """Convert game placeholders to ICU ``{name}`` (strip C# format specs)."""
    return strip_csharp_format_specs(text)


def to_i18next_catalog(catalog: dict[str, list[str]]) -> dict[str, str]:
    """Flat react-i18next catalog: singular keys, ``_one``/``_other`` for plurals."""
    out: dict[str, str] = {}
    for key, values in catalog.items():
        if len(values) >= 2:
            out[f"{key}_one"] = to_i18next_placeholders(values[0])
            out[f"{key}_other"] = to_i18next_placeholders(values[1])
        elif values:
            out[key] = to_i18next_placeholders(values[0])
    return out


def _icu_escape_plural_branch(text: str) -> str:
    """Escape literal ``'`` for ICU nested in a plural branch; keep ``{args}``."""
    # ICU uses ASCII apostrophe for escaping. Double apostrophes.
    return text.replace("'", "''")


def to_icu_catalog(catalog: dict[str, list[str]]) -> dict[str, str]:
    """ICU MessageFormat catalog; plurals use ``{count, plural, one{…} other{…}}``."""
    out: dict[str, str] = {}
    for key, values in catalog.items():
        if len(values) >= 2:
            one = _icu_escape_plural_branch(to_icu_placeholders(values[0]))
            other = _icu_escape_plural_branch(to_icu_placeholders(values[1]))
            out[key] = (
                "{count, plural, "
                f"one {{{one}}} "
                f"other {{{other}}}"
                "}"
            )
        elif values:
            out[key] = to_icu_placeholders(values[0])
    return out


def _po_escape(text: str) -> str:
    """Escape a string for a gettext ``msgid`` / ``msgstr`` quoted value."""
    return (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def loca_key_prefix(key: str, *, depth: int = PREFIX_DEPTH) -> str:
    """Return the LocaKeys class path (first ``depth`` dotted segments).

    Unresolved hashes (``0x…``) and keys with fewer than ``depth`` segments
    land in ``_unresolved``.
    """
    if key.startswith("0x"):
        return UNRESOLVED_PREFIX
    parts = key.split(".")
    if len(parts) < depth:
        return UNRESOLVED_PREFIX
    return ".".join(parts[:depth])


def split_catalog_by_prefix(
    catalog: dict[str, list[str]],
    *,
    depth: int = PREFIX_DEPTH,
) -> dict[str, dict[str, list[str]]]:
    """Group catalog entries by ``loca_key_prefix``. Prefixes are sorted."""
    buckets: dict[str, dict[str, list[str]]] = {}
    for key, values in catalog.items():
        buckets.setdefault(loca_key_prefix(key, depth=depth), {})[key] = values
    return dict(sorted(buckets.items()))


def _safe_prefix_filename(prefix: str) -> str:
    return re.sub(r"[^\w.-]+", "_", prefix)


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _reset_prefix_dir(out_dir: Path) -> Path:
    prefix_dir = out_dir / PREFIX_DIR
    if prefix_dir.exists():
        shutil.rmtree(prefix_dir)
    prefix_dir.mkdir(parents=True, exist_ok=True)
    return prefix_dir


def _write_prefix_catalogs(
    prefix_dir: Path,
    buckets: dict[str, dict[str, list[str]]],
) -> tuple[int, dict[str, dict[str, Any]]]:
    i18next_dir = prefix_dir / PREFIX_I18NEXT_DIR
    icu_dir = prefix_dir / PREFIX_ICU_DIR
    i18next_dir.mkdir(parents=True, exist_ok=True)
    icu_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    prefixes: dict[str, dict[str, Any]] = {}
    for prefix, subset in buckets.items():
        safe = _safe_prefix_filename(prefix)
        _write_json(prefix_dir / f"{safe}.json", subset)
        _write_json(
            i18next_dir / f"{safe}.i18next.json", to_i18next_catalog(subset)
        )
        _write_json(icu_dir / f"{safe}.icu.json", to_icu_catalog(subset))
        written += 3
        prefixes[prefix] = {
            "entry_count": len(subset),
            "file": f"{PREFIX_DIR}/{safe}.json",
        }
    return written, prefixes


def to_gettext_po(catalog: dict[str, list[str]], *, locale: str = "en_DK") -> str:
    """Build a gettext .po file; ``msgctxt`` holds the loca key."""
    lines = [
        'msgid ""',
        'msgstr ""',
        f'"Language: {locale}\\n"',
        '"Content-Type: text/plain; charset=UTF-8\\n"',
        '"Content-Transfer-Encoding: 8bit\\n"',
        '"Plural-Forms: nplurals=2; plural=(n != 1);\\n"',
        "",
    ]
    for key, values in catalog.items():
        lines.append(f'msgctxt "{_po_escape(key)}"')
        if len(values) >= 2:
            one = to_icu_placeholders(values[0])
            other = to_icu_placeholders(values[1])
            lines.append(f'msgid "{_po_escape(one)}"')
            lines.append(f'msgid_plural "{_po_escape(other)}"')
            lines.append(f'msgstr[0] "{_po_escape(one)}"')
            lines.append(f'msgstr[1] "{_po_escape(other)}"')
        elif values:
            text = to_icu_placeholders(values[0])
            lines.append(f'msgid "{_po_escape(text)}"')
            lines.append(f'msgstr "{_po_escape(text)}"')
        else:
            continue
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
    templates: dict[str, str],
    warnings: list[str],
) -> LocaExportResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    files_written = 0

    locale = meta.get("locale") or "unknown"
    locale_safe = re.sub(r"[^\w.-]+", "_", locale)

    _write_json(out_dir / f"{locale_safe}.json", catalog)
    files_written += 1
    _write_json(out_dir / f"{locale_safe}.i18next.json", to_i18next_catalog(catalog))
    files_written += 1
    _write_json(out_dir / f"{locale_safe}.icu.json", to_icu_catalog(catalog))
    files_written += 1

    (out_dir / f"{locale_safe}.po").write_text(
        to_gettext_po(catalog, locale=locale_safe),
        encoding="utf-8",
    )
    files_written += 1

    prefix_dir = _reset_prefix_dir(out_dir)
    prefix_written, prefixes = _write_prefix_catalogs(
        prefix_dir, split_catalog_by_prefix(catalog)
    )
    files_written += prefix_written

    meta_out = {
        "locale": meta.get("locale", ""),
        "checksum": meta.get("checksum", ""),
        "version": meta.get("version", ""),
        "entry_count": len(catalog),
        "resolved_keys": meta.get("resolved_keys", 0),
        "unresolved_hashes": meta.get("unresolved_hashes", 0),
        "formats": [
            f"{locale_safe}.json",
            f"{locale_safe}.i18next.json",
            f"{locale_safe}.icu.json",
            f"{locale_safe}.po",
        ],
        "prefix_dir": PREFIX_DIR,
        "prefixes": prefixes,
        "templates": templates,
        "warnings": warnings,
    }
    _write_json(out_dir / "meta.json", meta_out)
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

    return write_loca_export(
        out_dir,
        catalog=catalog,
        meta=meta,
        templates=templates,
        warnings=warnings,
    )
