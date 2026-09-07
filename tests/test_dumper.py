"""Tests for Il2CppDumper early-kill stability and dump validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from xapk_to_proto.dumper import dump_size_stable, validate_dump


def test_dump_size_stable_below_min_resets():
    should_stop, last, since = dump_size_stable(
        500_000, -1, None, now=100.0, min_bytes=1_000_000, stable_seconds=3.0
    )
    assert should_stop is False
    assert last == 500_000
    assert since is None


def test_dump_size_stable_growing_resets_timer():
    should_stop, last, since = dump_size_stable(
        2_000_000, 1_500_000, 90.0, now=100.0, min_bytes=1_000_000, stable_seconds=3.0
    )
    assert should_stop is False
    assert last == 2_000_000
    assert since == 100.0


def test_dump_size_stable_first_observation_above_min():
    should_stop, last, since = dump_size_stable(
        2_000_000, -1, None, now=100.0, min_bytes=1_000_000, stable_seconds=3.0
    )
    assert should_stop is False
    assert last == 2_000_000
    assert since == 100.0


def test_dump_size_stable_not_yet_elapsed():
    should_stop, last, since = dump_size_stable(
        2_000_000, 2_000_000, 98.0, now=100.0, min_bytes=1_000_000, stable_seconds=3.0
    )
    assert should_stop is False
    assert last == 2_000_000
    assert since == 98.0


def test_dump_size_stable_terminates_after_stable_period():
    should_stop, last, since = dump_size_stable(
        2_000_000, 2_000_000, 97.0, now=100.0, min_bytes=1_000_000, stable_seconds=3.0
    )
    assert should_stop is True
    assert last == 2_000_000
    assert since == 97.0


def test_dump_size_stable_sequence_waits_for_plateau():
    """Simulate growing then flat sizes; terminate only after stable_seconds."""
    last = -1
    since = None
    timeline = [
        (0.0, 100_000),
        (0.5, 800_000),
        (1.0, 1_200_000),
        (1.5, 5_000_000),
        (2.0, 10_000_000),
        (2.5, 10_000_000),
        (3.0, 10_000_000),
        (3.5, 10_000_000),
        (5.5, 10_000_000),
    ]
    stopped_at: float | None = None
    for now, size in timeline:
        should_stop, last, since = dump_size_stable(
            size, last, since, now, min_bytes=1_000_000, stable_seconds=3.0
        )
        if should_stop:
            stopped_at = now
            break
    assert stopped_at == 5.5


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
    (out / "il2cpp").mkdir(parents=True)
    (out / "il2cpp" / "dump.cs").write_text(
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
