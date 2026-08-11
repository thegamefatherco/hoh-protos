"""Tests for self-contained proto emission with Google well-known types."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from google.protobuf import descriptor_pb2

from xapk_to_proto.data import load_well_known_fds
from xapk_to_proto.emit import emit_well_known, run, write_descriptor_bundle

from tests.fixture_paths import FIXTURE_DESCRIPTORS

WELL_KNOWN_FILES = (
    "google/protobuf/any.proto",
    "google/protobuf/timestamp.proto",
    "google/protobuf/duration.proto",
    "google/protobuf/struct.proto",
    "google/protobuf/empty.proto",
    "google/protobuf/wrappers.proto",
)


@pytest.fixture
def descriptors_pb(tmp_path: Path) -> Path:
    if not FIXTURE_DESCRIPTORS.is_file():
        pytest.skip(f"fixture not found: {FIXTURE_DESCRIPTORS}")
    dest = tmp_path / "descriptors.pb"
    shutil.copy(FIXTURE_DESCRIPTORS, dest)
    return dest


def test_emit_writes_google_well_known_protos(
    descriptors_pb: Path, tmp_path: Path
) -> None:
    out_dir = tmp_path / "proto"
    result = run(descriptors_pb, out_dir)

    assert result.well_known_files == len(WELL_KNOWN_FILES)
    for rel in WELL_KNOWN_FILES:
        path = out_dir / rel
        assert path.is_file(), f"missing {rel}"

    any_proto = (out_dir / "google/protobuf/any.proto").read_text(encoding="utf-8")
    assert "package google.protobuf;" in any_proto
    assert "message Any {" in any_proto


def test_emit_well_known_only(tmp_path: Path) -> None:
    out_dir = tmp_path / "google-only"
    written = emit_well_known(out_dir)

    assert written == len(WELL_KNOWN_FILES)
    wrappers = (out_dir / "google/protobuf/wrappers.proto").read_text(encoding="utf-8")
    assert "message DoubleValue {" in wrappers


def test_write_descriptor_bundle_includes_game_and_well_known(
    descriptors_pb: Path, tmp_path: Path
) -> None:
    bundle_path = tmp_path / "descriptors_bundle.pb"
    count = write_descriptor_bundle(descriptors_pb, bundle_path)

    bundle = descriptor_pb2.FileDescriptorSet()
    bundle.ParseFromString(bundle_path.read_bytes())
    bundle_names = {fd.name for fd in bundle.file}

    game = descriptor_pb2.FileDescriptorSet()
    game.ParseFromString(descriptors_pb.read_bytes())
    game_names = {fd.name for fd in game.file}
    well_known_names = {fd.name for fd in load_well_known_fds().file}

    assert game_names <= bundle_names
    assert well_known_names <= bundle_names
    assert count == len(bundle_names)
    assert count == len(game_names) + len(well_known_names)
