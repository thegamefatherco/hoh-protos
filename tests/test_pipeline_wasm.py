"""Pipeline --from-wasm branch (mocked dump/extract; no real Il2CppDumper)."""

from __future__ import annotations

import struct
from argparse import Namespace
from pathlib import Path

from xapk_to_proto import pipeline
from xapk_to_proto.paths import (
    GAME_CATALOG_NAME,
    GAME_DATA_NAME,
    GAME_METADATA_NAME,
    GAME_WASM_NAME,
)
from xapk_to_proto.xapk import METADATA_MAGIC
from tests.test_wasm import _pack_unity_web_data


def _fake_metadata() -> bytes:
    return struct.pack("<II", METADATA_MAGIC, 39) + b"\x00" * 64


def test_run_from_wasm_uses_catalog_for_assets(tmp_path: Path, monkeypatch):
    fixtures = tmp_path / "fixtures" / "zz0" / "1.54.0"
    fixtures.mkdir(parents=True)
    (fixtures / GAME_WASM_NAME).write_bytes(b"\x00asm")
    meta = _fake_metadata()
    (fixtures / GAME_DATA_NAME).write_bytes(
        _pack_unity_web_data({"Il2CppData/Metadata/global-metadata.dat": meta})
    )
    catalog = fixtures / GAME_CATALOG_NAME
    catalog.write_bytes(b"catalog")

    out = tmp_path / "output" / "zz0" / "1.54.0"
    monkeypatch.chdir(tmp_path)

    dump_calls: list[tuple[Path, Path]] = []

    def fake_dumper(binary, metadata, out_dir, verbose):
        dump_calls.append((binary, metadata))
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "dump.cs").write_text(
            "Google.Protobuf\npublic static class FooReflection // TypeDefIndex: 1\n",
            encoding="utf-8",
        )

    def fake_extract(metadata, dump_cs, descriptors):
        descriptors.parent.mkdir(parents=True, exist_ok=True)
        # Minimal empty FileDescriptorSet
        from google.protobuf import descriptor_pb2

        fds = descriptor_pb2.FileDescriptorSet()
        f = fds.file.add()
        f.name = "dummy.proto"
        f.package = "test"
        descriptors.write_bytes(fds.SerializeToString())
        return {"embedded": 0, "rebuilt": 1, "merged": 1, "missing_well_known": []}

    def fake_emit(descriptors, proto_dir):
        proto_dir.mkdir(parents=True, exist_ok=True)
        (proto_dir / "dummy.proto").write_text("syntax = \"proto3\";\n")

        class R:
            game_files = 1
            well_known_files = 0

        return R()

    def fake_bundle(descriptors, bundle_path):
        bundle_path.write_bytes(descriptors.read_bytes())
        return 1

    assets_calls: list[dict] = []

    def fake_assets_download(**kwargs):
        assets_calls.append(kwargs)

        class R:
            downloaded = 0
            skipped_existing = 0
            failed: list = []
            warnings: list = []

        return R()

    monkeypatch.setattr("xapk_to_proto.pipeline.dumper.run_il2cpp_dumper", fake_dumper)
    monkeypatch.setattr("xapk_to_proto.pipeline.dumper.validate_dump", lambda p: None)
    monkeypatch.setattr("xapk_to_proto.pipeline.extract.run", fake_extract)
    monkeypatch.setattr("xapk_to_proto.pipeline.emit.run", fake_emit)
    monkeypatch.setattr(
        "xapk_to_proto.pipeline.emit.write_descriptor_bundle", fake_bundle
    )
    monkeypatch.setattr(
        "xapk_to_proto.pipeline.assets.run_assets_download", fake_assets_download
    )

    args = Namespace(
        from_wasm=True,
        world="zz0",
        version="1.54.0",
        output=None,
        work_dir=None,
        skip_dump=False,
        keep_work=True,
        verbose=False,
        username=None,
        password=None,
        gamedesign_input=None,
        gamedesign_out=None,
        gamedesign_constants=False,
        gamedesign_constants_out=None,
        startup_input=None,
        wire_fix_input=None,
        definitions_input=None,
        loca_input=None,
        loca_out=None,
        assets=True,
        assets_out=None,
        unpack_assets=False,
        unpack_assets_out=None,
        link_assets=False,
        link_assets_index=None,
        link_assets_out=None,
        fixtures_dir=fixtures,
        catalog=catalog,
        xapk=None,
    )
    rc = pipeline.run(args)
    assert rc == 0
    assert dump_calls
    assert dump_calls[0][0].name == GAME_WASM_NAME
    assert dump_calls[0][1].name == GAME_METADATA_NAME
    assert len(assets_calls) == 1
    assert assets_calls[0]["catalog"] == catalog
    assert (out / "descriptors.pb").is_file()
