"""Join asset-reference fields in decoded game data to extracted image files.

Game data points at art through plain strings, and those strings mean two
different things:

* ``icon_pantheon_vital_ascent`` is a **sprite name** living inside a shared
  SpriteAtlas bundle. It resolves to a PNG only after :mod:`xapk_to_proto.unpack`
  has run, which is why these fields look unresolvable against a raw catalog.
* ``Unit_QueenBoudicca`` is an **Addressables address** whose bundle holds a
  prefab, not an image. It resolves to a bundle and legitimately has no PNG.

The resolver reports those outcomes separately (``image`` vs ``bundle_only``)
rather than collapsing the second into a failure.

Which fields to inspect is derived from the descriptor set instead of a fixed
list, so new builds pick up new fields automatically. This module deliberately
does not import UnityPy - it only reads the index that unpacking produced.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from google.protobuf.descriptor import Descriptor, FieldDescriptor

from xapk_to_proto.gamedesign import load_descriptor_pool

LINKS_FILENAME = "links.json"
REPORT_FILENAME = "report.json"
INDEX_FILENAME = "index.json"

STATUS_IMAGE = "image"
STATUS_BUNDLE_ONLY = "bundle_only"
STATUS_DEFINITION_REF = "definition_ref"
STATUS_MISS = "miss"

STATUSES = (STATUS_IMAGE, STATUS_BUNDLE_ONLY, STATUS_DEFINITION_REF, STATUS_MISS)

MAX_SAMPLE_MISSES = 10

# Names that identify a record in the report; the first present one wins.
_ID_KEYS = ("id", "definition_id", "definitionId")

_ASSET_FIELD_RE = re.compile(
    r"asset|icon|sprite|image|portrait|banner|backdrop|texture|thumbnail|avatar",
    re.IGNORECASE,
)

# Fields whose names carry no hint that they hold an address.
_EXTRA_ASSET_FIELDS: dict[str, tuple[str, ...]] = {
    "BattleFieldAssetSetDefinitionDTO": ("start", "middle", "end"),
}

# Fields that match the pattern but hold something else. asset_ids here carries
# gamedesign definition ids such as "hero_battle_ability.JoanOfArc_Passive".
_DENY_ASSET_FIELDS: dict[str, tuple[str, ...]] = {
    "HeroPassiveAbilityDisplayComponentDTO": ("asset_ids",),
}

# A "<something>_definition_id" field points at another definition by id, never
# at an Addressables address, whatever the rest of its name suggests.
_DEFINITION_REF_SUFFIX = "_definition_id"

# Gamedesign ids are namespaced ("resource.agate", "battlefield_asset_set.X");
# Addressables addresses never contain a dot. Values shaped like this live in a
# different namespace, so they are reported apart from genuine lookup failures.
_GAMEDESIGN_ID_RE = re.compile(r"^[A-Za-z0-9_]+\.[A-Za-z0-9_|.\-]+$")


@dataclass
class Resolution:
    value: str
    status: str
    image: str | None = None
    bundle: str | None = None


@dataclass
class FieldStats:
    image: int = 0
    bundle_only: int = 0
    definition_ref: int = 0
    miss: int = 0
    sample_misses: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.image + self.bundle_only + self.definition_ref + self.miss


@dataclass
class LinkResult:
    index_source: str
    sources: list[str] = field(default_factory=list)
    links: dict[str, list[dict]] = field(default_factory=dict)
    field_stats: dict[str, FieldStats] = field(default_factory=dict)
    records_scanned: int = 0
    types_scanned: int = 0
    warnings: list[str] = field(default_factory=list)

    def totals(self) -> dict[str, int]:
        totals = {
            "records_with_assets": sum(len(v) for v in self.links.values()),
            "records_scanned": self.records_scanned,
            "types_scanned": self.types_scanned,
            "values": sum(s.total for s in self.field_stats.values()),
        }
        for status in STATUSES:
            totals[status] = sum(getattr(s, status) for s in self.field_stats.values())
        return totals


@dataclass
class AssetIndex:
    by_name: dict[str, list[str]]
    by_bundle_prefix: dict[str, dict]
    source: str = ""

    @classmethod
    def load(cls, path: Path) -> AssetIndex:
        if path.is_dir():
            path = path / INDEX_FILENAME
        if not path.is_file():
            raise FileNotFoundError(f"asset index not found: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            by_name=data.get("by_name", {}),
            by_bundle_prefix=data.get("by_bundle_prefix", {}),
            source=data.get("source", str(path)),
        )

    def resolve(self, value: str) -> Resolution:
        key = value.strip().lower()
        if not key:
            return Resolution(value=value, status=STATUS_MISS)
        images = self.by_name.get(key)
        if images:
            return Resolution(value=value, status=STATUS_IMAGE, image=images[0])
        entry = self.by_bundle_prefix.get(key)
        if entry is not None:
            bundle_images = entry.get("images") or []
            return Resolution(
                value=value,
                status=STATUS_IMAGE if bundle_images else STATUS_BUNDLE_ONLY,
                image=bundle_images[0] if bundle_images else None,
                bundle=entry.get("bundle"),
            )
        if _GAMEDESIGN_ID_RE.match(value.strip()):
            return Resolution(value=value, status=STATUS_DEFINITION_REF)
        return Resolution(value=value, status=STATUS_MISS)


def asset_fields_from_pool(
    pool, short_name_index: dict[str, str]
) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    """Find string fields that hold asset references.

    Returns ``(by_full_name, by_short_name)`` - the first drives the walk, the
    second is the human-readable view written into the report.
    """
    by_full: dict[str, tuple[str, ...]] = {}
    by_short: dict[str, tuple[str, ...]] = {}

    for short_name, full_name in sorted(short_name_index.items()):
        try:
            desc = pool.FindMessageTypeByName(full_name.lstrip("."))
        except KeyError:
            continue
        denied = _DENY_ASSET_FIELDS.get(short_name, ())
        extra = _EXTRA_ASSET_FIELDS.get(short_name, ())
        names = [
            f.name
            for f in desc.fields
            if f.type == FieldDescriptor.TYPE_STRING
            and f.name not in denied
            and not f.name.endswith(_DEFINITION_REF_SUFFIX)
            and (_ASSET_FIELD_RE.search(f.name) or f.name in extra)
        ]
        if names:
            by_full[desc.full_name] = tuple(names)
            by_short[short_name] = tuple(names)

    return by_full, by_short


def asset_fields_from_descriptors(
    descriptors_pb: Path,
) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    pool, short_name_index = load_descriptor_pool(descriptors_pb)
    return asset_fields_from_pool(pool, short_name_index)


def _descriptor_for_type_url(pool, short_name_index: dict[str, str], url: str):
    short = url.rsplit("/", 1)[-1].rsplit(".", 1)[-1]
    full = short_name_index.get(short)
    if full is None:
        return None
    try:
        return pool.FindMessageTypeByName(full.lstrip("."))
    except KeyError:
        return None


def _record_id(node: dict) -> str | None:
    for key in _ID_KEYS:
        value = node.get(key)
        if isinstance(value, str) and value:
            return value
    return None


class _Walker:
    """Walks ProtoJSON records, tracking the message type at each depth."""

    def __init__(self, pool, short_name_index, asset_fields, index: AssetIndex):
        self.pool = pool
        self.short_name_index = short_name_index
        self.asset_fields = asset_fields
        self.index = index

    def walk(
        self,
        node: Any,
        desc: Descriptor | None,
        path: str,
        found: dict[str, Resolution],
    ) -> None:
        if isinstance(node, list):
            for i, item in enumerate(node):
                self.walk(item, desc, f"{path}[{i}]", found)
            return
        if not isinstance(node, dict):
            return

        type_url = node.get("@type")
        if isinstance(type_url, str):
            resolved = _descriptor_for_type_url(
                self.pool, self.short_name_index, type_url
            )
            if resolved is not None:
                desc = resolved
        if desc is None:
            return

        wanted = self.asset_fields.get(desc.full_name, ())
        for key, value in node.items():
            if key == "@type":
                continue
            fd = desc.fields_by_name.get(key)
            if fd is None:
                continue
            child_path = f"{path}.{key}" if path else key
            if fd.type == FieldDescriptor.TYPE_STRING:
                if key in wanted:
                    self._record(value, child_path, found)
            elif fd.message_type is not None:
                self._walk_message(fd, value, child_path, found)

    def _walk_message(
        self, fd, value: Any, path: str, found: dict[str, Resolution]
    ) -> None:
        message_type = fd.message_type
        if message_type.GetOptions().map_entry:
            value_fd = message_type.fields_by_name.get("value")
            if value_fd is None or not isinstance(value, dict):
                return
            for map_key, map_value in value.items():
                self.walk(map_value, value_fd.message_type, f"{path}[{map_key}]", found)
            return
        self.walk(value, message_type, path, found)

    def _record(self, value: Any, path: str, found: dict[str, Resolution]) -> None:
        values = value if isinstance(value, list) else [value]
        for i, item in enumerate(values):
            if not isinstance(item, str) or not item.strip():
                continue
            key = path if len(values) == 1 else f"{path}[{i}]"
            found[key] = self.index.resolve(item)


_INDEX_SUFFIX_RE = re.compile(r"\[[^\]]*\]")


def _field_key(type_name: str, path: str) -> str:
    """Collapse list/map indices so stats group per field, not per element."""
    return f"{type_name}.{_INDEX_SUFFIX_RE.sub('', path)}"


def link_definitions(
    definition_dirs: list[Path],
    index: AssetIndex,
    descriptors_pb: Path,
    *,
    verbose: bool = False,
) -> LinkResult:
    """Resolve every asset field in the decoded definition JSON under each dir."""
    pool, short_name_index = load_descriptor_pool(descriptors_pb)
    asset_fields, _ = asset_fields_from_pool(pool, short_name_index)
    walker = _Walker(pool, short_name_index, asset_fields, index)

    result = LinkResult(index_source=index.source)
    for directory in definition_dirs:
        if not directory.is_dir():
            result.warnings.append(f"not a directory, skipped: {directory}")
            continue
        result.sources.append(str(directory.resolve()))
        for json_path in sorted(directory.glob("*.json")):
            if json_path.name == "manifest.json":
                continue
            type_name = json_path.stem
            desc = _descriptor_for_type_url(pool, short_name_index, type_name)
            if desc is None:
                result.warnings.append(f"unknown type, skipped: {json_path.name}")
                continue
            try:
                records = json.loads(json_path.read_text(encoding="utf-8"))
            except (ValueError, OSError) as e:
                result.warnings.append(f"{json_path.name}: unreadable: {e}")
                continue
            if not isinstance(records, list):
                continue

            result.types_scanned += 1
            linked = _link_records(records, desc, type_name, walker, result)
            if linked:
                result.links.setdefault(type_name, []).extend(linked)
            if verbose:
                print(
                    f"  {type_name}: {len(linked)}/{len(records)} record(s) "
                    "with asset references",
                    flush=True,
                )
    return result


def _link_records(
    records: list,
    desc: Descriptor,
    type_name: str,
    walker: _Walker,
    result: LinkResult,
) -> list[dict]:
    linked: list[dict] = []
    for i, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        result.records_scanned += 1
        found: dict[str, Resolution] = {}
        walker.walk(record, desc, "", found)
        if not found:
            continue
        for path, resolution in found.items():
            stats = result.field_stats.setdefault(
                _field_key(type_name, path), FieldStats()
            )
            setattr(stats, resolution.status, getattr(stats, resolution.status) + 1)
            if (
                resolution.status == STATUS_MISS
                and len(stats.sample_misses) < MAX_SAMPLE_MISSES
                and resolution.value not in stats.sample_misses
            ):
                stats.sample_misses.append(resolution.value)
        linked.append(
            {
                "id": _record_id(record),
                "index": i,
                "fields": {
                    path: {
                        "value": res.value,
                        "status": res.status,
                        "image": res.image,
                        "bundle": res.bundle,
                    }
                    for path, res in sorted(found.items())
                },
            }
        )
    return linked


def build_report(result: LinkResult) -> dict:
    misses: Counter[str] = Counter()
    for records in result.links.values():
        for record in records:
            for info in record["fields"].values():
                if info["status"] == STATUS_MISS:
                    misses[info["value"]] += 1

    return {
        "index_source": result.index_source,
        "sources": result.sources,
        "totals": result.totals(),
        "fields": {
            key: {
                **{status: getattr(stats, status) for status in STATUSES},
                "total": stats.total,
                "sample_misses": stats.sample_misses,
            }
            for key, stats in sorted(result.field_stats.items())
        },
        "top_unresolved_values": [
            {"value": value, "count": count} for value, count in misses.most_common(50)
        ],
        "warnings": result.warnings,
    }


def write_link_export(result: LinkResult, out_dir: Path) -> LinkResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / LINKS_FILENAME).write_text(
        json.dumps(
            {k: v for k, v in sorted(result.links.items())},
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (out_dir / REPORT_FILENAME).write_text(
        json.dumps(build_report(result), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return result


def run_link_export(
    *,
    index_path: Path,
    descriptors_pb: Path,
    definition_dirs: list[Path],
    out_dir: Path,
    verbose: bool = False,
) -> LinkResult:
    """Resolve asset fields against the unpack index and write links + report."""
    if not definition_dirs:
        raise ValueError("at least one definitions directory is required")
    index = AssetIndex.load(index_path)
    result = link_definitions(definition_dirs, index, descriptors_pb, verbose=verbose)
    return write_link_export(result, out_dir)


__all__ = [
    "STATUSES",
    "AssetIndex",
    "LinkResult",
    "Resolution",
    "asset_fields_from_descriptors",
    "asset_fields_from_pool",
    "build_report",
    "link_definitions",
    "run_link_export",
]
