"""World/version path helpers for fixtures and pipeline output."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from xapk_to_proto.game_api import DEFAULT_WORLD, KNOWN_WORLDS

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


def resolve_run_paths(
    xapk: Path,
    *,
    world: str | None = None,
    version: str | None = None,
    output: Path | None = None,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
) -> ResolvedPaths:
    """Resolve world/version and the pipeline output directory.

    Resolution order for version:
    1. Explicit ``version``
    2. Parent dir when XAPK is under ``{world}/{version}/``
    3. Version embedded in the XAPK filename

    When ``output`` is omitted and a version is known, defaults to
    ``{output_root}/{world}/{version}``. Otherwise falls back to
    ``{xapk_stem}_protos`` next to the process cwd (legacy behaviour).
    """
    inferred_world, inferred_version = infer_world_version_from_path(xapk)
    filename_version = version_from_filename(xapk)

    resolved_world = world or inferred_world or DEFAULT_WORLD
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
