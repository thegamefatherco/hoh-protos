"""Shared fixture/output paths for tests."""

from __future__ import annotations

from pathlib import Path

WORLD = "un0"
VERSION = "1.50.3"

FIXTURES_DIR = Path("fixtures") / WORLD / VERSION
OUTPUT_DIR = Path("output") / WORLD / VERSION

# Prefer the versioned layout; fall back to legacy unversioned output/un0 for
# local trees that have not been regenerated yet.
_LEGACY_OUTPUT = Path("output") / WORLD


def _first_existing(*candidates: Path) -> Path:
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


FIXTURE_XAPK = FIXTURES_DIR / "game.xapk"
FIXTURE_GAMEDESIGN = FIXTURES_DIR / "gamedesign"
FIXTURE_LOCA = FIXTURES_DIR / "loca-compressed"
FIXTURE_STARTUP = FIXTURES_DIR / "startup"

FIXTURE_DESCRIPTORS = _first_existing(
    OUTPUT_DIR / "descriptors.pb",
    _LEGACY_OUTPUT / "descriptors.pb",
)
FIXTURE_DUMP_CS = _first_existing(
    OUTPUT_DIR / "il2cpp" / "dump.cs",
    _LEGACY_OUTPUT / "il2cpp" / "dump.cs",
)
FIXTURE_PROTO_DIR = _first_existing(
    OUTPUT_DIR / "proto",
    _LEGACY_OUTPUT / "proto",
)
FIXTURE_BUNDLE = _first_existing(
    OUTPUT_DIR / "descriptors_bundle.pb",
    _LEGACY_OUTPUT / "descriptors_bundle.pb",
)
