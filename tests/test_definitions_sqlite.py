"""Tests for definitions SQLite export alongside per-type JSON."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from xapk_to_proto.cli import build_parser
from xapk_to_proto.definitions import (
    sqlite_path_for,
    write_definitions_export,
)
from xapk_to_proto.gamedesign import DecodedEntry


def _sample_entries() -> list[DecodedEntry]:
    return [
        DecodedEntry(
            type_name="PlayerDTO",
            data={"player_id": "123"},
        ),
        DecodedEntry(
            type_name="HeroDefinitionDTO",
            data={"definition_id": "hero.A"},
        ),
        DecodedEntry(
            type_name="HeroDefinitionDTO",
            data={"definition_id": "hero.B"},
        ),
    ]


def test_sqlite_path_for_is_sibling_db() -> None:
    assert sqlite_path_for(Path("/tmp/out/gamedesign")) == Path(
        "/tmp/out/gamedesign.db"
    )


def test_write_definitions_export_skips_sqlite_by_default(tmp_path: Path) -> None:
    out_dir = tmp_path / "startup"
    write_definitions_export(
        out_dir,
        source="startup",
        source_path=Path("/tmp/startup.raw"),
        entries=_sample_entries(),
        warnings=[],
    )

    assert (out_dir / "manifest.json").is_file()
    assert not sqlite_path_for(out_dir).exists()


def test_write_definitions_export_sqlite_matches_json(tmp_path: Path) -> None:
    out_dir = tmp_path / "gamedesign"
    warnings = ["unknown type: type.googleapis.com/Missing"]
    result = write_definitions_export(
        out_dir,
        source="gamedesign",
        source_path=Path("/tmp/gamedesign.raw"),
        entries=_sample_entries(),
        warnings=warnings,
        sqlite=True,
    )

    db_path = sqlite_path_for(out_dir)
    assert result.sqlite_path == str(db_path.resolve())
    assert db_path.is_file()

    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    hero_json = json.loads(
        (out_dir / "HeroDefinitionDTO.json").read_text(encoding="utf-8")
    )
    player_json = json.loads((out_dir / "PlayerDTO.json").read_text(encoding="utf-8"))

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT source, source_path, total_entries, type_counts, warnings "
            "FROM manifest"
        ).fetchone()
        assert row is not None
        assert row[0] == manifest["source"]
        assert row[1] == manifest["source_path"]
        assert row[2] == manifest["total_entries"]
        assert json.loads(row[3]) == manifest["type_counts"]
        assert json.loads(row[4]) == warnings

        db_entries = conn.execute(
            "SELECT type, json FROM entries ORDER BY id"
        ).fetchall()
    finally:
        conn.close()

    assert len(db_entries) == 3
    by_type: dict[str, list[dict]] = {}
    for type_name, payload in db_entries:
        by_type.setdefault(type_name, []).append(json.loads(payload))

    assert by_type["HeroDefinitionDTO"] == hero_json
    assert by_type["PlayerDTO"] == player_json


def test_write_definitions_export_sqlite_replaces_existing(tmp_path: Path) -> None:
    out_dir = tmp_path / "gamedesign"
    db_path = sqlite_path_for(out_dir)
    write_definitions_export(
        out_dir,
        source="gamedesign",
        source_path=Path("/tmp/a"),
        entries=_sample_entries(),
        warnings=[],
        sqlite=True,
    )
    first_mtime = db_path.stat().st_mtime_ns

    write_definitions_export(
        out_dir,
        source="gamedesign",
        source_path=Path("/tmp/b"),
        entries=[
            DecodedEntry(type_name="PlayerDTO", data={"player_id": "only"}),
        ],
        warnings=[],
        sqlite=True,
    )

    assert db_path.stat().st_mtime_ns >= first_mtime
    conn = sqlite3.connect(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        source_path = conn.execute(
            "SELECT source_path FROM manifest"
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 1
    assert source_path == str(Path("/tmp/b"))


def test_cli_sqlite_flag_on_relevant_commands() -> None:
    parser = build_parser()
    for command in ("gamedesign", "definitions", "run"):
        args = parser.parse_args([command, "--sqlite"])
        assert args.sqlite is True
        args_off = parser.parse_args([command])
        assert args_off.sqlite is False
