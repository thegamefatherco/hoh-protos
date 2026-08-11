"""World/version path helpers for fixtures and pipeline output."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from xapk_to_proto.game_api import (
    DEFAULT_WORLD,
    ENV_WORLD,
    FIXTURE_GAMEDESIGN,
    FIXTURE_LOCA,
    FIXTURE_STARTUP,
    KNOWN_WORLDS,
)

DEFAULT_OUTPUT_ROOT = Path("output")
DEFAULT_FIXTURES_ROOT = Path("fixtures")
GAME_XAPK_NAME = "game.xapk"

# Semantic-ish client versions used by HoH (e.g. 1.50.3).
_VERSION_RE = re.compile(r"^\d+\.\d+(?:\.\d+)*$")
# Filename patterns: com.innogames.heroesofhistory_1.50.3.xapk or 1.50.3.xapk
_FILENAME_VERSION_RE = re.compile(
    r"(?:^|[_-])(\d+\.\d+(?:\.\d+)*)\.xapk$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ResolvedPaths:
    """Resolved world, version, and default output directory for a run."""

    world: str
    version: str | None
    output: Path


@dataclass(frozen=True)
class VersionLayout:
    """Canonical fixtures + output paths for a world/version pair."""

    world: str
    version: str
    fixtures_root: Path = DEFAULT_FIXTURES_ROOT
    output_root: Path = DEFAULT_OUTPUT_ROOT

    @property
    def fixtures(self) -> Path:
        return (self.fixtures_root / self.world / self.version).resolve()

    @property
    def root(self) -> Path:
        """Pipeline output directory: ``output/{world}/{version}/``."""
        return (self.output_root / self.world / self.version).resolve()

    @property
    def xapk(self) -> Path:
        return self.fixtures / GAME_XAPK_NAME

    @property
    def gamedesign(self) -> Path:
        return self.fixtures / FIXTURE_GAMEDESIGN

    @property
    def loca(self) -> Path:
        return self.fixtures / FIXTURE_LOCA

    @property
    def startup(self) -> Path:
        return self.fixtures / FIXTURE_STARTUP

    @property
    def descriptors(self) -> Path:
        return self.root / "descriptors.pb"

    @property
    def descriptors_bundle(self) -> Path:
        return self.root / "descriptors_bundle.pb"

    @property
    def dump_cs(self) -> Path:
        return self.root / "il2cpp" / "dump.cs"

    @property
    def proto(self) -> Path:
        return self.root / "proto"

    @property
    def assets(self) -> Path:
        return self.root / "assets"

    @property
    def unpacked(self) -> Path:
        return self.root / "unpacked"

    @property
    def asset_links(self) -> Path:
        return self.root / "asset_links"

    @property
    def gamedesign_out(self) -> Path:
        return self.root / "gamedesign"

    @property
    def loca_out(self) -> Path:
        return self.root / "loca"

    @property
    def startup_out(self) -> Path:
        return self.root / "startup"

    @property
    def constants(self) -> Path:
        return self.root / "gamedesign" / "constants"

    def existing_fixture_blobs(self) -> list[Path]:
        """Fixture blobs that exist on disk (startup / gamedesign / loca)."""
        return [p for p in (self.startup, self.gamedesign, self.loca) if p.is_file()]


def is_version_string(value: str) -> bool:
    return bool(_VERSION_RE.match(value))


def version_from_filename(path: Path) -> str | None:
    """Extract a client version from an XAPK filename, if present."""
    match = _FILENAME_VERSION_RE.search(path.name)
    return match.group(1) if match else None


def infer_world_version_from_path(
    xapk: Path,
    *,
    known_worlds: tuple[str, ...] = KNOWN_WORLDS,
) -> tuple[str | None, str | None]:
    """Infer ``(world, version)`` when ``xapk`` lives under ``…/{world}/{version}/``.

    Recognizes both ``fixtures/{world}/{version}/game.xapk`` and
    ``output/{world}/{version}/…`` layouts. Returns ``(None, None)`` pieces
    that cannot be inferred.
    """
    resolved = xapk.resolve()
    parent = resolved.parent
    version: str | None = None
    world: str | None = None

    if is_version_string(parent.name):
        version = parent.name
        grand = parent.parent
        if grand.name in known_worlds:
            world = grand.name

    if world is None and parent.name in known_worlds:
        world = parent.name

    return world, version


def resolve_world(
    world: str | None = None,
    *,
    environ: dict[str, str] | None = None,
) -> str:
    """Resolve world: explicit flag → ``HOH_WORLD`` → ``un0``."""
    env = os.environ if environ is None else environ
    explicit = (world or "").strip()
    if explicit:
        return explicit
    from_env = (env.get(ENV_WORLD) or "").strip()
    if from_env:
        return from_env
    return DEFAULT_WORLD


def version_layout(
    version: str,
    *,
    world: str | None = None,
    fixtures_root: Path = DEFAULT_FIXTURES_ROOT,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    environ: dict[str, str] | None = None,
) -> VersionLayout:
    """Build a :class:`VersionLayout` for ``world`` + ``version``."""
    return VersionLayout(
        world=resolve_world(world, environ=environ),
        version=version,
        fixtures_root=fixtures_root,
        output_root=output_root,
    )


def coalesce(explicit: Path | None, default: Path | None) -> Path | None:
    """Return ``explicit`` when set, otherwise ``default``."""
    return explicit if explicit is not None else default


def resolve_run_paths(
    xapk: Path,
    *,
    world: str | None = None,
    version: str | None = None,
    output: Path | None = None,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    environ: dict[str, str] | None = None,
) -> ResolvedPaths:
    """Resolve world/version and the pipeline output directory.

    Resolution order for version:
    1. Explicit ``version``
    2. Parent dir when XAPK is under ``{world}/{version}/``
    3. Version embedded in the XAPK filename

    Resolution order for world:
    1. Explicit ``world``
    2. Inferred from path
    3. ``HOH_WORLD`` / default ``un0``

    When ``output`` is omitted and a version is known, defaults to
    ``{output_root}/{world}/{version}``. Otherwise falls back to
    ``{xapk_stem}_protos`` next to the process cwd (legacy behaviour).
    """
    inferred_world, inferred_version = infer_world_version_from_path(xapk)
    filename_version = version_from_filename(xapk)

    resolved_world = world or inferred_world or resolve_world(None, environ=environ)
    resolved_version = version or inferred_version or filename_version

    if output is not None:
        return ResolvedPaths(
            world=resolved_world,
            version=resolved_version,
            output=output.resolve(),
        )

    if resolved_version is not None:
        return ResolvedPaths(
            world=resolved_world,
            version=resolved_version,
            output=(output_root / resolved_world / resolved_version).resolve(),
        )

    return ResolvedPaths(
        world=resolved_world,
        version=None,
        output=Path(f"{xapk.stem}_protos").resolve(),
    )


def fixtures_dir(
    world: str,
    version: str,
    *,
    root: Path = DEFAULT_FIXTURES_ROOT,
) -> Path:
    return (root / world / version).resolve()


def output_dir(
    world: str,
    version: str,
    *,
    root: Path = DEFAULT_OUTPUT_ROOT,
) -> Path:
    return (root / world / version).resolve()
