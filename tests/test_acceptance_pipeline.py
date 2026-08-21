"""Acceptance checks against real fixtures / generated output.

Skipped automatically when local artifacts are missing. These catch broken
descriptors, decode mismatches, and empty proto trees early.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from google.protobuf import descriptor_pb2

from tests.fixture_paths import (
    FIXTURE_BUNDLE,
    FIXTURE_DESCRIPTORS,
    FIXTURE_DUMP_CS,
    FIXTURE_GAMEDESIGN,
    FIXTURE_LOCA,
    FIXTURE_PROTO_DIR,
    FIXTURE_XAPK,
    OUTPUT_DIR,
)
from xapk_to_proto import definitions, dumper, extract, loca
from xapk_to_proto.gamedesign import is_hero_related_type, load_descriptor_pool


pytestmark = pytest.mark.acceptance


def test_core_generate_artifacts_present() -> None:
    if not OUTPUT_DIR.is_dir():
        pytest.skip(f"missing generate output root {OUTPUT_DIR}")

    dump_cs = OUTPUT_DIR / "il2cpp" / "dump.cs"
    descriptors = OUTPUT_DIR / "descriptors.pb"
    bundle = OUTPUT_DIR / "descriptors_bundle.pb"
    proto_dir = OUTPUT_DIR / "proto"

    missing = [
        str(path)
        for path, ok in (
            (dump_cs, dump_cs.is_file()),
            (descriptors, descriptors.is_file()),
            (bundle, bundle.is_file()),
            (proto_dir, proto_dir.is_dir()),
        )
        if not ok
    ]
    assert not missing, f"incomplete generate output under {OUTPUT_DIR}: {missing}"


def test_dump_cs_exists_and_is_valid() -> None:
    if not FIXTURE_DUMP_CS.is_file():
        pytest.skip(f"missing {FIXTURE_DUMP_CS}")

    assert FIXTURE_DUMP_CS.stat().st_size > 100_000
    dumper.validate_dump(FIXTURE_DUMP_CS)
    protos = extract.parse_dump_cs(FIXTURE_DUMP_CS)
    assert len(protos) > 50


def test_descriptors_load_into_pool() -> None:
    if not FIXTURE_DESCRIPTORS.is_file():
        pytest.skip(f"missing {FIXTURE_DESCRIPTORS}")

    pool, short_index = load_descriptor_pool(FIXTURE_DESCRIPTORS)
    assert "GameDesignResponse" in short_index
    full_name = short_index["GameDesignResponse"].lstrip(".")
    pool.FindMessageTypeByName(full_name)
    fds = descriptor_pb2.FileDescriptorSet()
    fds.ParseFromString(FIXTURE_DESCRIPTORS.read_bytes())
    assert len(fds.file) > 50


def test_descriptors_bundle_includes_well_known() -> None:
    if not FIXTURE_BUNDLE.is_file():
        pytest.skip(f"missing {FIXTURE_BUNDLE}")
    fds = descriptor_pb2.FileDescriptorSet()
    fds.ParseFromString(FIXTURE_BUNDLE.read_bytes())
    names = {f.name for f in fds.file}
    assert "google/protobuf/any.proto" in names
    assert len(fds.file) > len(
        [n for n in names if not n.startswith("google/")]
    )


def test_proto_tree_is_nonempty_and_self_contained() -> None:
    if not FIXTURE_PROTO_DIR.is_dir():
        pytest.skip(f"missing {FIXTURE_PROTO_DIR}")
    if not FIXTURE_DESCRIPTORS.is_file():
        pytest.skip(f"missing {FIXTURE_DESCRIPTORS}")
    if not FIXTURE_BUNDLE.is_file():
        pytest.skip(f"missing {FIXTURE_BUNDLE}")

    protos = list(FIXTURE_PROTO_DIR.glob("**/*.proto"))
    assert len(protos) > 50
    for path in protos:
        assert path.stat().st_size > 0, path
    google = FIXTURE_PROTO_DIR / "google" / "protobuf"
    assert (google / "any.proto").is_file()
    assert (google / "timestamp.proto").is_file()

    fds = descriptor_pb2.FileDescriptorSet()
    fds.ParseFromString(FIXTURE_DESCRIPTORS.read_bytes())
    for fd in fds.file:
        rel = fd.name.replace("\\", "/")
        path = FIXTURE_PROTO_DIR / rel
        assert path.is_file(), f"missing emitted proto for {rel}"
        text = path.read_text(encoding="utf-8")
        assert "syntax =" in text, path
        assert "message " in text or "enum " in text, path

    load_descriptor_pool(FIXTURE_BUNDLE)


def test_gamedesign_fixture_decodes_full_catalog(tmp_path: Path) -> None:
    if not FIXTURE_DESCRIPTORS.is_file() or not FIXTURE_GAMEDESIGN.is_file():
        pytest.skip("missing descriptors or gamedesign fixture")
    out = tmp_path / "gamedesign"
    result = definitions.run_definitions_export(
        descriptors_pb=FIXTURE_DESCRIPTORS,
        out_dir=out,
        input_path=FIXTURE_GAMEDESIGN,
    )
    assert result.total_entries > 10_000
    type_files = {
        p.stem: json.loads(p.read_text(encoding="utf-8"))
        for p in out.glob("*.json")
        if p.name != "manifest.json"
    }
    assert len(type_files) > 50
    hero_types = [name for name in type_files if is_hero_related_type(name)]
    non_hero = [name for name in type_files if not is_hero_related_type(name)]
    assert hero_types, "expected hero-related definition types"
    assert non_hero, "full decode must include non-hero types (not hero-only export)"
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["total_entries"] == result.total_entries


def test_loca_fixture_decodes_catalog(tmp_path: Path) -> None:
    if not (
        FIXTURE_DESCRIPTORS.is_file()
        and FIXTURE_DUMP_CS.is_file()
        and FIXTURE_LOCA.is_file()
    ):
        pytest.skip("missing descriptors, dump.cs, or loca fixture")
    out = tmp_path / "loca"
    result = loca.run_loca_export(
        descriptors_pb=FIXTURE_DESCRIPTORS,
        dump_cs=FIXTURE_DUMP_CS,
        input_path=FIXTURE_LOCA,
        out_dir=out,
    )
    assert result.entry_count > 100
    assert result.resolved_keys > 0
    catalog = out / f"{result.locale}.json"
    if not catalog.is_file():
        catalog = out / "en_DK.json"
    assert catalog.is_file(), f"expected catalog under {out}: {list(out.iterdir())}"
    data = json.loads(catalog.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert len(data) == result.entry_count


def test_xapk_fixture_present_for_asset_smoke() -> None:
    if not FIXTURE_XAPK.is_file():
        pytest.skip(f"missing {FIXTURE_XAPK}")
    assert FIXTURE_XAPK.stat().st_size > 1_000_000
