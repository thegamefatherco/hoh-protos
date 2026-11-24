"""Tests for Il2CppDumper DummyDll soft-fail and dump validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from xapk_to_proto.dumper import (
    classify_dumper_result,
    is_known_dummy_dll_abort,
    list_dummy_dlls,
    validate_dump,
    warn_if_dummy_dll_missing,
)


def test_list_dummy_dlls_missing_dir(tmp_path: Path):
    assert list_dummy_dlls(tmp_path) == []


def test_list_dummy_dlls_empty_dir(tmp_path: Path):
    (tmp_path / "DummyDll").mkdir()
    assert list_dummy_dlls(tmp_path) == []


def test_list_dummy_dlls_finds_dlls(tmp_path: Path):
    folder = tmp_path / "DummyDll"
    folder.mkdir()
    (folder / "Assembly-CSharp.dll").write_bytes(b"MZ")
    (folder / "readme.txt").write_text("ignore", encoding="utf-8")
    dlls = list_dummy_dlls(tmp_path)
    assert [p.name for p in dlls] == ["Assembly-CSharp.dll"]


def test_is_known_dummy_dll_abort_detects_compressed_integer():
    assert is_known_dummy_dll_abort(
        "Generate dummy dll...\nInvalid compressed integer format\n"
    )
    assert not is_known_dummy_dll_abort("Generate dummy dll...\nDone!\n")


def test_classify_ok_with_dummy_dlls(tmp_path: Path):
    dump_cs = tmp_path / "dump.cs"
    dump_cs.write_text("// ok\n", encoding="utf-8")
    folder = tmp_path / "DummyDll"
    folder.mkdir()
    (folder / "Assembly-CSharp.dll").write_bytes(b"MZ")
    assert (
        classify_dumper_result(dump_cs=dump_cs, out_dir=tmp_path, combined="")
        == "ok"
    )


def test_classify_soft_skips_compressed_integer_abort(tmp_path: Path):
    dump_cs = tmp_path / "dump.cs"
    dump_cs.write_text("// ok\n", encoding="utf-8")
    (tmp_path / "DummyDll").mkdir()
    combined = (
        "Dumping...\nDone!\nGenerate struct...\nDone!\n"
        "Generate dummy dll...\nInvalid compressed integer format\n"
    )
    assert (
        classify_dumper_result(
            dump_cs=dump_cs, out_dir=tmp_path, combined=combined
        )
        == "ok_dummy_dll_skipped"
    )


def test_classify_hard_fails_empty_dummy_without_known_error(tmp_path: Path):
    dump_cs = tmp_path / "dump.cs"
    dump_cs.write_text("// ok\n", encoding="utf-8")
    (tmp_path / "DummyDll").mkdir()
    assert (
        classify_dumper_result(
            dump_cs=dump_cs, out_dir=tmp_path, combined="Dumping...\nDone!\n"
        )
        == "fail_dummy_dll"
    )


def test_classify_fail_no_dump(tmp_path: Path):
    dump_cs = tmp_path / "dump.cs"
    (tmp_path / "DummyDll").mkdir()
    assert (
        classify_dumper_result(
            dump_cs=dump_cs, out_dir=tmp_path, combined="Searching...\n"
        )
        == "fail_no_dump"
    )


def test_warn_if_dummy_dll_missing_empty(tmp_path: Path, capsys):
    (tmp_path / "DummyDll").mkdir()
    assert warn_if_dummy_dll_missing(tmp_path) is False
    err = capsys.readouterr().err
    assert "DummyDll missing or empty" in err


def test_warn_if_dummy_dll_missing_ok(tmp_path: Path, capsys):
    folder = tmp_path / "DummyDll"
    folder.mkdir()
    (folder / "Google.Protobuf.dll").write_bytes(b"MZ")
    assert warn_if_dummy_dll_missing(tmp_path) is True
    assert capsys.readouterr().err == ""


def test_failure_message_wasm_auto_scan_hint():
    from xapk_to_proto.dumper import _failure_message

    msg = _failure_message(
        "ERROR: Can't use auto mode to detect IL2CPP\nCodeRegistration missing",
        binary=Path("game.wasm"),
    )
    assert "WebGL/WASM" in msg
    assert "WebAssembly" in msg or "WASM" in msg


def test_failure_message_dump_without_dummy_dll(tmp_path: Path):
    from xapk_to_proto.dumper import _failure_message

    dump_cs = tmp_path / "dump.cs"
    dump_cs.write_text("// stub\n", encoding="utf-8")
    (tmp_path / "DummyDll").mkdir()
    msg = _failure_message(
        "Press any key to exit...\n",
        binary=Path("libil2cpp.so"),
        dump_cs=dump_cs,
        out_dir=tmp_path,
    )
    assert "DummyDll" in msg
    assert "Invalid compressed integer format" in msg
    assert "Press any key" not in msg


def test_validate_dump_rejects_image_list_only(tmp_path: Path):
    dump = tmp_path / "dump.cs"
    dump.write_text(
        "// Image 0: Google.Protobuf.dll - Google.Protobuf\n"
        "// Image 1: Sentry.System.Reflection.Metadata.dll\n"
        "public class Unrelated { }\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="public static \\*Reflection"):
        validate_dump(dump)


def test_validate_dump_rejects_missing_google_protobuf(tmp_path: Path):
    dump = tmp_path / "dump.cs"
    dump.write_text(
        "public static class FooReflection // TypeDefIndex: 1\n{\n}\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="Google.Protobuf"):
        validate_dump(dump)


def test_validate_dump_accepts_reflection_class(tmp_path: Path):
    dump = tmp_path / "dump.cs"
    dump.write_text(
        "// Image 0: Google.Protobuf.dll\n"
        "Namespace: InnoGames.Generated.Protobuf\n"
        "public static class AbsolutionReflection // TypeDefIndex: 42\n"
        "{\n"
        "}\n",
        encoding="utf-8",
    )
    validate_dump(dump)


def test_pipeline_fails_on_empty_merge(tmp_path: Path, monkeypatch, capsys):
    """Empty extract must fail before wirefix with a clear error."""
    from argparse import Namespace

    from xapk_to_proto import pipeline

    xapk = tmp_path / "game.xapk"
    xapk.write_bytes(b"PK")
    meta = tmp_path / "global-metadata.dat"
    meta.write_bytes(b"\x00" * 16)
    out = tmp_path / "out"
    il2cpp = out / "il2cpp"
    il2cpp.mkdir(parents=True)
    (il2cpp / "dump.cs").write_text(
        "// Image 0: Google.Protobuf.dll\n"
        "public static class FooReflection // TypeDefIndex: 1\n{\n}\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(pipeline, "extract_xapk", lambda *_a, **_k: tmp_path)
    monkeypatch.setattr(
        pipeline, "discover_il2cpp", lambda *_a, **_k: (tmp_path / "libil2cpp.so", meta)
    )
    monkeypatch.setattr(pipeline, "validate_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline.dumper, "validate_dump", lambda *_a, **_k: None)

    def fake_extract(_meta, _dump, out_path: Path):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"")
        return {"embedded": 0, "rebuilt": 0, "merged": 0, "missing_well_known": []}

    monkeypatch.setattr(pipeline.extract, "run", fake_extract)

    args = Namespace(
        xapk=xapk,
        world="un0",
        version="1.51.4",
        output=out,
        work_dir=None,
        skip_dump=True,
        verbose=False,
        keep_work=False,
    )
    rc = pipeline.run(args)
    assert rc == 1
    err = capsys.readouterr().err
    assert "no protobuf descriptors extracted" in err
    assert "DummyDll missing or empty" in err
