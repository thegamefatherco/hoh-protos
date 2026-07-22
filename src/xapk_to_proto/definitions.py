"""Decode captured server blobs (WrappedResponse / GameDesignResponse) to JSON.

Heroes of History server responses are ``WrappedResponse`` envelopes
(``communication.proto``):

    WrappedResponse.response       -> Any (StartupResponse / WakeupResponse / ...)
    WrappedResponse.root_context   -> PushContextDTO
        PushContextDTO.context_info -> Any (RootContextDTO, ...)
        PushContextDTO.messages     -> repeated Any (player state, configs, pushes)
        PushContextDTO.child_context -> repeated PushContextDTO (recursive)

Any ``Any`` whose payload is itself a ``GameDesignResponse`` is expanded so its
``content`` definitions are emitted individually instead of as one giant blob.
This module reuses the descriptor-pool plumbing from :mod:`gamedesign`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from google.protobuf import any_pb2, descriptor_pool

from xapk_to_proto.gamedesign import (
    GD_MESSAGE,
    DecodedEntry,
    _decode_any_entry,
    _message_class,
    _parse_gamedesign_message,
    _resolve_message_type_name,
    entry_to_protojson_dict,
    load_descriptor_pool,
    short_type_name,
)

WRAPPED_RESPONSE = "WrappedResponse"


@dataclass
class DefinitionsExportResult:
    source: str
    source_path: str
    total_entries: int
    type_counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _collect_context_anys(
    ctx, out: list[any_pb2.Any]
) -> None:
    """Flatten a PushContextDTO tree into its Any payloads (depth-first)."""
    if ctx.HasField("context_info"):
        out.append(ctx.context_info)
    out.extend(ctx.messages)
    for child in ctx.child_context:
        _collect_context_anys(child, out)


def _expand_any(
    any_msg: any_pb2.Any,
    pool: descriptor_pool.DescriptorPool,
    short_name_index: dict[str, str],
    entries: list[DecodedEntry],
    warnings: list[str],
) -> None:
    if not any_msg.type_url:
        return
    try:
        full_name = _resolve_message_type_name(
            pool, any_msg.type_url, short_name_index
        )
    except KeyError:
        warnings.append(f"unknown type: {any_msg.type_url}")
        return

    if full_name == GD_MESSAGE or full_name.endswith(".GameDesignResponse"):
        gd_cls = _message_class(pool, full_name)
        gd = gd_cls()
        try:
            gd.ParseFromString(any_msg.value)
        except Exception as exc:  # noqa: BLE001 - tolerate partial captures
            warnings.append(f"failed to parse {full_name}: {exc}")
            return
        for inner in gd.content:
            _expand_any(inner, pool, short_name_index, entries, warnings)
        return

    try:
        entries.append(_decode_any_entry(any_msg, pool, short_name_index))
    except Exception as exc:  # noqa: BLE001 - skip undecodable payloads
        warnings.append(f"failed to decode {full_name}: {exc}")


def decode_blob(
    data: bytes,
    pool: descriptor_pool.DescriptorPool,
    short_name_index: dict[str, str],
) -> tuple[list[DecodedEntry], list[str]]:
    """Decode a WrappedResponse (or bare GameDesignResponse) into typed entries."""
    entries: list[DecodedEntry] = []
    warnings: list[str] = []

    wr = _message_class(pool, WRAPPED_RESPONSE)()
    parsed = False
    try:
        wr.ParseFromString(data)
        parsed = True
    except Exception:  # noqa: BLE001 - fall back to bare GameDesignResponse
        parsed = False

    payloads: list[any_pb2.Any] = []
    if parsed and (wr.HasField("response") or wr.HasField("root_context")):
        if wr.HasField("response"):
            payloads.append(wr.response)
        if wr.HasField("root_context"):
            _collect_context_anys(wr.root_context, payloads)
    else:
        gd = _parse_gamedesign_message(data, pool)
        if gd is None:
            raise ValueError(
                "blob is neither a WrappedResponse nor a GameDesignResponse"
            )
        payloads.extend(gd.content)

    for any_msg in payloads:
        _expand_any(any_msg, pool, short_name_index, entries, warnings)

    return entries, list(dict.fromkeys(warnings))


def write_definitions_export(
    out_dir: Path,
    *,
    source: str,
    source_path: Path,
    entries: list[DecodedEntry],
    warnings: list[str],
) -> DefinitionsExportResult:
    out_dir.mkdir(parents=True, exist_ok=True)

    grouped: dict[str, list[dict]] = {}
    for entry in entries:
        grouped.setdefault(short_type_name(entry.type_name), []).append(
            entry_to_protojson_dict(entry)
        )

    for type_name, items in sorted(grouped.items()):
        (out_dir / f"{type_name}.json").write_text(
            json.dumps(items, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    type_counts = {name: len(items) for name, items in sorted(grouped.items())}
    manifest = {
        "source": source,
        "source_path": str(source_path),
        "total_entries": len(entries),
        "type_counts": type_counts,
        "warnings": warnings,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    return DefinitionsExportResult(
        source=source,
        source_path=str(source_path),
        total_entries=len(entries),
        type_counts=type_counts,
        warnings=warnings,
    )


def run_definitions_export(
    *,
    descriptors_pb: Path,
    out_dir: Path,
    input_path: Path,
    verbose: bool = False,
) -> DefinitionsExportResult:
    if not input_path.is_file():
        raise FileNotFoundError(f"definitions input not found: {input_path}")

    pool, short_name_index = load_descriptor_pool(descriptors_pb)
    data = input_path.read_bytes()
    if verbose:
        print(f"  decoding {input_path} ({len(data)} bytes)")
    entries, warnings = decode_blob(data, pool, short_name_index)
    return write_definitions_export(
        out_dir,
        source=input_path.stem,
        source_path=input_path.resolve(),
        entries=entries,
        warnings=warnings,
    )
