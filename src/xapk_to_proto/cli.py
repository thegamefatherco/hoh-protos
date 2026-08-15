"""Command-line interface for hoh-protos."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from xapk_to_proto import (
    apkpure,
    assetlink,
    assets,
    definitions,
    deps,
    emit,
    extract,
    game_api,
    gamedesign_constants,
    loca,
    unpack,
    wirefix,
)
from xapk_to_proto.paths import VersionLayout, coalesce, version_layout
from xapk_to_proto.pipeline import run as pipeline_run

_SUBCOMMANDS = frozenset(
    {
        "setup",
        "extract",
        "emit",
        "run",
        "gamedesign",
        "gamedesign-constants",
        "definitions",
        "loca",
        "wirefix",
        "download-xapk",
        "download-fixtures",
        "download-assets",
        "unpack-assets",
        "link-assets",
    }
)

# Sentinel string for ``--xapk`` with nargs='?' meaning "use layout default".
_XAPK_LAYOUT_DEFAULT = "__layout__"


def _normalize_argv(argv: list[str]) -> list[str]:
    if argv and not argv[0].startswith("-") and argv[0] not in _SUBCOMMANDS:
        return ["run", *argv]
    return argv


def _add_world_version_args(
    p: argparse.ArgumentParser,
    *,
    version_required_hint: str = "required unless all path flags are given",
) -> None:
    p.add_argument(
        "--world",
        default=None,
        help=(
            f"Fixture/output world key (default: {game_api.DEFAULT_WORLD} or "
            f"${game_api.ENV_WORLD}; known: {', '.join(game_api.KNOWN_WORLDS)})"
        ),
    )
    p.add_argument(
        "--version",
        default=None,
        help=(
            "Client version for default fixtures/output paths "
            f"({version_required_hint})"
        ),
    )


def _layout_from_args(args: argparse.Namespace) -> VersionLayout | None:
    version = getattr(args, "version", None)
    if not version:
        return None
    return version_layout(version, world=getattr(args, "world", None))


def _resolve_xapk_flag(value: str | Path | None, layout: VersionLayout | None) -> Path | None:
    """Resolve ``--xapk`` which may be a path or the layout-default sentinel."""
    if value is None:
        return None
    if value == _XAPK_LAYOUT_DEFAULT or value == Path(_XAPK_LAYOUT_DEFAULT):
        if layout is None:
            return None
        return layout.xapk
    return Path(value)


def _add_extract_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "extract",
        help="Extract descriptors.pb from metadata + dump.cs",
        description="Extract Google.Protobuf FileDescriptorProtos from IL2CPP artifacts.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  hoh-protos extract --metadata global-metadata.dat --version 1.50.3\n"
            "  hoh-protos extract --metadata global-metadata.dat "
            "--dump-cs output/un0/1.50.3/il2cpp/dump.cs "
            "--out output/un0/1.50.3/descriptors.pb\n"
        ),
    )
    _add_world_version_args(p)
    p.add_argument(
        "--metadata", type=Path, required=True, help="global-metadata.dat path"
    )
    p.add_argument(
        "--dump-cs",
        type=Path,
        default=None,
        help="Il2CppDumper dump.cs path (default: output/{world}/{version}/il2cpp/dump.cs)",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output descriptors.pb path (default: output/{world}/{version}/descriptors.pb)",
    )


def _add_emit_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "emit",
        help="Render .proto files from descriptors.pb",
        description="Render .proto files from a FileDescriptorSet.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  hoh-protos emit --version 1.50.3\n"
            "  hoh-protos emit --in output/un0/1.50.3/descriptors.pb "
            "--out-dir output/un0/1.50.3/proto\n"
        ),
    )
    _add_world_version_args(p)
    p.add_argument(
        "--in",
        dest="inp",
        type=Path,
        default=None,
        help="descriptors.pb path (default: output/{world}/{version}/descriptors.pb)",
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Output directory for .proto files (default: output/{world}/{version}/proto)",
    )


def _add_setup_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "setup",
        help="Download dotnet, Il2CppDumper, and apkeep into user cache",
        description=(
            "Install external dependencies (dotnet + Il2CppDumper; apkeep on "
            "Linux/Windows). On macOS install apkeep with: brew install apkeep"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("Examples:\n  hoh-protos setup\n  hoh-protos setup --force\n"),
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if already cached",
    )
    p.add_argument("-v", "--verbose", action="store_true")


def _add_run_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "run",
        help="Full pipeline: XAPK → descriptors.pb + .proto files",
        description="Extract .proto schemas from a Unity IL2CPP + Google.Protobuf XAPK.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Requires Unity IL2CPP + Google.Protobuf. Run `hoh-protos setup` once first.\n\n"
            "With --version, the XAPK and fixture blobs default under "
            "fixtures/{world}/{version}/. Providing --gamedesign-input (or the "
            "fixture default) decodes full definitions, runs wirefix, and emits "
            "GameDesign constants.\n\n"
            "Examples:\n"
            "  hoh-protos run --version 1.50.3\n"
            "  hoh-protos run fixtures/un0/1.50.3/game.xapk\n"
            "  hoh-protos run --version 1.50.3 --world zz0\n"
        ),
    )
    p.add_argument(
        "xapk",
        type=Path,
        nargs="?",
        default=None,
        help=(
            "Path to .xapk file (default: fixtures/{world}/{version}/game.xapk "
            "when --version is set)"
        ),
    )
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help=(
            "Output directory (default: output/{world}/{version}/ when version "
            "is known, else {xapk_stem}_protos/)"
        ),
    )
    _add_world_version_args(
        p,
        version_required_hint="inferred from XAPK path/filename when omitted",
    )
    p.add_argument(
        "--work-dir",
        type=Path,
        default=None,
        help="Extraction scratch directory (default: {output}/.work)",
    )
    p.add_argument(
        "--skip-dump",
        action="store_true",
        help="Skip Il2CppDumper if dump.cs already exists in output",
    )
    p.add_argument(
        "--keep-work",
        action="store_true",
        help="Do not delete work directory on success",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument(
        "--gamedesign-input",
        type=Path,
        default=None,
        help=(
            "GameDesignResponse blob: wirefix + full per-type JSON under "
            "{output}/gamedesign + TypeScript constants "
            "(default: fixtures/{world}/{version}/gamedesign when present)"
        ),
    )
    p.add_argument(
        "--gamedesign-out",
        type=Path,
        default=None,
        help="Gamedesign output directory (default: {output}/gamedesign)",
    )
    p.add_argument(
        "--startup-input",
        type=Path,
        default=None,
        help=(
            "WrappedResponse startup blob: decode full definitions under "
            "{output}/startup "
            "(default: fixtures/{world}/{version}/startup when present)"
        ),
    )
    p.add_argument(
        "--wire-fix-input",
        type=Path,
        default=None,
        help=(
            "GameDesignResponse blob for wire-type correction before .proto emit. "
            "Defaults to --gamedesign-input when set."
        ),
    )
    p.add_argument(
        "--definitions-input",
        type=Path,
        action="append",
        default=None,
        metavar="BLOB",
        help=(
            "Decode an extra WrappedResponse/GameDesignResponse blob into "
            "{output}/{blob-stem}/. Repeatable. Prefer --gamedesign-input / "
            "--startup-input for the common fixtures."
        ),
    )
    p.add_argument(
        "--gamedesign-constants",
        action="store_true",
        help=(
            "Emit TypeScript string enums from GameDesign *Constants in dump.cs. "
            "Implied by --gamedesign-input."
        ),
    )
    p.add_argument(
        "--gamedesign-constants-out",
        type=Path,
        default=None,
        help=(
            "GameDesign constants TS output directory "
            "(default: {output}/gamedesign/constants)"
        ),
    )
    p.add_argument(
        "--loca-input",
        type=Path,
        default=None,
        help=(
            "CompressedLocaResponse / LocaResponse blob to decode into "
            "an English catalog under {output}/loca "
            "(default: fixtures/{world}/{version}/loca-compressed when present)"
        ),
    )
    p.add_argument(
        "--loca-out",
        type=Path,
        default=None,
        help="Loca output directory (default: {output}/loca)",
    )
    p.add_argument(
        "--assets",
        action="store_true",
        help=(
            "Download Addressables asset bundles from the CDN using the "
            "catalog found in the XAPK"
        ),
    )
    p.add_argument(
        "--assets-out",
        type=Path,
        default=None,
        help="Assets output directory (default: {output}/assets)",
    )
    p.add_argument(
        "--unpack-assets",
        action="store_true",
        help=(
            "Extract images from the Addressables bundles shipped inside the "
            "XAPK into {output}/unpacked (needs the assets extra)"
        ),
    )
    p.add_argument(
        "--unpack-assets-out",
        type=Path,
        default=None,
        help="Unpacked images output directory (default: {output}/unpacked)",
    )
    p.add_argument(
        "--link-assets",
        action="store_true",
        help=(
            "Resolve asset fields in decoded definitions against the unpack "
            "index; uses --gamedesign-input / --startup-input outputs and "
            "requires --unpack-assets (or --link-assets-index)"
        ),
    )
    p.add_argument(
        "--link-assets-index",
        type=Path,
        default=None,
        help="Asset index.json to link against (default: the unpacked output)",
    )
    p.add_argument(
        "--link-assets-out",
        type=Path,
        default=None,
        help="Asset links output directory (default: {output}/asset_links)",
    )


def _add_gamedesign_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "gamedesign",
        help="Decode full gamedesign JSON from a GameDesignResponse blob",
        description=(
            "Decode a GameDesignResponse protobuf blob into per-type JSON files. "
            "Alias of `definitions` for a single gamedesign input."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  hoh-protos gamedesign --version 1.50.3\n"
            "  hoh-protos gamedesign --descriptors output/un0/1.50.3/descriptors.pb "
            "--input fixtures/un0/1.50.3/gamedesign "
            "--out-dir output/un0/1.50.3/gamedesign\n"
        ),
    )
    _add_world_version_args(p)
    p.add_argument(
        "--descriptors",
        type=Path,
        default=None,
        help="descriptors.pb path (default: output/{world}/{version}/descriptors.pb)",
    )
    p.add_argument(
        "--input",
        type=Path,
        default=None,
        help="GameDesignResponse blob (default: fixtures/{world}/{version}/gamedesign)",
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help=(
            "Output directory for per-type JSON and manifest.json "
            "(default: output/{world}/{version}/gamedesign)"
        ),
    )
    p.add_argument("-v", "--verbose", action="store_true")


def _add_gamedesign_constants_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "gamedesign-constants",
        help="Emit TypeScript string enums from GameDesign *Constants in dump.cs",
        description=(
            "Parse InnoGames.Generated.GameDesign *Constants static classes from "
            "Il2CppDumper dump.cs and emit TypeScript string enums."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  hoh-protos gamedesign-constants --version 1.50.3\n"
            "  hoh-protos gamedesign-constants "
            "--dump-cs output/un0/1.50.3/il2cpp/dump.cs "
            "--out-dir output/un0/1.50.3/gamedesign/constants\n"
        ),
    )
    _add_world_version_args(p)
    p.add_argument(
        "--dump-cs",
        type=Path,
        default=None,
        help="Il2CppDumper dump.cs path (default: output/{world}/{version}/il2cpp/dump.cs)",
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help=(
            "Output directory for *.ts enums and index.ts "
            "(default: output/{world}/{version}/gamedesign/constants)"
        ),
    )


def _add_wirefix_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "wirefix",
        help="Correct wire types in descriptors.pb using a GameDesignResponse sample",
        description=(
            "Detect fields whose declared wire class disagrees with captured payload "
            "bytes and rewrite types in descriptors.pb before .proto emission."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  hoh-protos wirefix --version 1.50.3\n"
            "  hoh-protos wirefix --descriptors output/un0/1.50.3/descriptors.pb "
            "--input fixtures/un0/1.50.3/gamedesign\n"
        ),
    )
    _add_world_version_args(p)
    p.add_argument(
        "--descriptors",
        type=Path,
        default=None,
        help="descriptors.pb path (default: output/{world}/{version}/descriptors.pb)",
    )
    p.add_argument(
        "--input",
        type=Path,
        default=None,
        help="GameDesignResponse blob (default: fixtures/{world}/{version}/gamedesign)",
    )
    p.add_argument("-v", "--verbose", action="store_true")


def _add_definitions_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "definitions",
        help="Decode captured WrappedResponse/GameDesignResponse blobs to JSON",
        description=(
            "Decode one or more captured server blobs (startup/wakeup/gamedesign) "
            "into per-type JSON files, one output subdirectory per source."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  hoh-protos definitions --version 1.50.3\n"
            "  hoh-protos definitions --descriptors output/un0/1.50.3/descriptors.pb "
            "--out-dir output/un0/1.50.3 "
            "--input fixtures/un0/1.50.3/startup "
            "--input fixtures/un0/1.50.3/gamedesign\n"
        ),
    )
    _add_world_version_args(p)
    p.add_argument(
        "--descriptors",
        type=Path,
        default=None,
        help="descriptors.pb path (default: output/{world}/{version}/descriptors.pb)",
    )
    p.add_argument(
        "--input",
        type=Path,
        action="append",
        default=None,
        metavar="BLOB",
        help=(
            "Captured blob to decode (repeatable; default with --version: "
            "existing startup/gamedesign/loca-compressed under fixtures/)"
        ),
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help=(
            "Output directory; a subdir named after each blob is created "
            "(default: output/{world}/{version}/)"
        ),
    )
    p.add_argument("-v", "--verbose", action="store_true")


def _add_loca_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "loca",
        help="Decode CompressedLocaResponse into English catalogs",
        description=(
            "Decode a captured CompressedLocaResponse (or LocaResponse) blob, "
            "resolve FNV hashes via LocaKeys in dump.cs, and emit raw JSON, "
            "i18next JSON, ICU MessageFormat JSON, and a gettext .po file."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  hoh-protos loca --version 1.50.3\n"
            "  hoh-protos loca --descriptors output/un0/1.50.3/descriptors.pb "
            "--dump-cs output/un0/1.50.3/il2cpp/dump.cs "
            "--input fixtures/un0/1.50.3/loca-compressed "
            "--out-dir output/un0/1.50.3/loca\n"
        ),
    )
    _add_world_version_args(p)
    p.add_argument(
        "--descriptors",
        type=Path,
        default=None,
        help="descriptors.pb path (default: output/{world}/{version}/descriptors.pb)",
    )
    p.add_argument(
        "--dump-cs",
        type=Path,
        default=None,
        help="Il2CppDumper dump.cs path (default: output/{world}/{version}/il2cpp/dump.cs)",
    )
    p.add_argument(
        "--input",
        type=Path,
        default=None,
        help=(
            "CompressedLocaResponse / LocaResponse blob "
            "(default: fixtures/{world}/{version}/loca-compressed)"
        ),
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help=(
            "Output directory for raw/i18next/ICU JSON and gettext .po "
            "(default: output/{world}/{version}/loca)"
        ),
    )
    p.add_argument("-v", "--verbose", action="store_true")


def _add_download_xapk_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "download-xapk",
        help="Download the game XAPK from APKPure via apkeep (latest or a specific version)",
        description=(
            "Download an XAPK from APKPure using the apkeep CLI "
            "(https://github.com/EFForg/apkeep). On macOS install with "
            "`brew install apkeep`; on Linux/Windows, `hoh-protos setup` caches "
            "a release binary. With --version and no -o, writes "
            "fixtures/{world}/{version}/game.xapk. An existing destination is "
            "left alone unless you pass --force."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  hoh-protos download-xapk --version 1.50.3\n"
            "  hoh-protos download-xapk --version 1.50.3 --world zz0\n"
            "  hoh-protos download-xapk -o ./custom/game.xapk\n"
        ),
    )
    _add_world_version_args(
        p,
        version_required_hint="required unless -o is given",
    )
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help=(
            "Output path. A *.xapk path is used as the filename; anything else "
            "is a directory that receives {package}_{version}.xapk. "
            "With --version and no -o: fixtures/{world}/{version}/game.xapk"
        ),
    )
    p.add_argument(
        "--package",
        default=apkpure.DEFAULT_PACKAGE,
        help=f"Android package name (default: {apkpure.DEFAULT_PACKAGE})",
    )
    p.add_argument(
        "--abi",
        default=apkpure.DEFAULT_ABI,
        help=(
            f"Native ABI split to request (default: {apkpure.DEFAULT_ABI}; "
            "the pipeline needs arm64-v8a or armeabi-v7a)"
        ),
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if the destination file already exists",
    )
    p.add_argument("-v", "--verbose", action="store_true")


def _add_download_assets_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "download-assets",
        help="Download Addressables asset bundles listed in the game catalog",
        description=(
            "Parse bundle names out of the Addressables catalog and download them "
            "from the InnoGames CDN. Bundles already present are skipped, so an "
            "interrupted run can be resumed by re-running the same command."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "The CDN only keeps bundle hashes for recent builds, so a stale catalog "
            "produces mostly 404s. Use --dry-run to inspect URLs first.\n\n"
            "Examples:\n"
            "  hoh-protos download-assets --version 1.50.3\n"
            "  hoh-protos download-assets --xapk fixtures/un0/1.50.3/game.xapk "
            "-o output/un0/1.50.3/assets\n"
            "  hoh-protos download-assets --catalog catalog.bin -o ./assets "
            "--only hero --jobs 16\n"
        ),
    )
    _add_world_version_args(p)
    src = p.add_mutually_exclusive_group(required=False)
    src.add_argument(
        "--catalog",
        type=Path,
        help="Addressables catalog.bin path",
    )
    src.add_argument(
        "--xapk",
        type=Path,
        help=(
            "XAPK to read assets/aa/catalog.bin from (no full unpack; "
            "default with --version: fixtures/{world}/{version}/game.xapk)"
        ),
    )
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help=(
            "Output directory for .bundle files and manifest.json "
            "(default: output/{world}/{version}/assets)"
        ),
    )
    p.add_argument(
        "--cdn-root",
        default=assets.CDN_ROOT,
        help=f"CDN base URL (default: {assets.CDN_ROOT})",
    )
    p.add_argument(
        "--only",
        action="append",
        default=None,
        metavar="TERM",
        help=(
            "Download only bundles whose filename contains TERM "
            "(repeatable; disables --skip)"
        ),
    )
    p.add_argument(
        "--skip",
        action="append",
        default=None,
        metavar="PREFIX",
        help=(
            "Skip bundles whose filename starts with PREFIX (repeatable; "
            f"default: {', '.join(assets.DEFAULT_SKIP)})"
        ),
    )
    p.add_argument(
        "--jobs",
        type=int,
        default=assets.DEFAULT_JOBS,
        help=f"Parallel downloads (default: {assets.DEFAULT_JOBS})",
    )
    p.add_argument(
        "--retries",
        type=int,
        default=assets.DEFAULT_RETRIES,
        help=(
            f"Attempts per bundle for transient failures "
            f"(default: {assets.DEFAULT_RETRIES}; 404s are never retried)"
        ),
    )
    p.add_argument(
        "--clean",
        action="store_true",
        help="Delete the output directory before downloading",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the resolved bundle URLs without downloading",
    )
    p.add_argument(
        "--unpack",
        action="store_true",
        help="Extract images from the downloaded bundles once the download finishes",
    )
    p.add_argument(
        "--unpack-out",
        type=Path,
        default=None,
        help=(
            "Output directory for extracted images "
            "(default: output/{world}/{version}/unpacked, else OUTPUT/unpacked)"
        ),
    )
    p.add_argument("-v", "--verbose", action="store_true")


def _add_unpack_assets_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "unpack-assets",
        help="Extract images from Addressables bundles and index them by name",
        description=(
            "Decode Sprite and Texture2D objects out of Unity asset bundles into "
            "PNGs, and write an index.json mapping asset names and Addressables "
            "addresses to the extracted files."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "The XAPK ships the complete bundle set under assets/aa/<platform>/, "
            "so --xapk needs no network and covers far more than a CDN pull.\n"
            "Requires the assets extra: pip install 'hoh-protos[assets]'\n\n"
            "Examples:\n"
            "  hoh-protos unpack-assets --version 1.50.3 --only spriteatlas\n"
            "  hoh-protos unpack-assets --version 1.50.3 --xapk\n"
            "  hoh-protos unpack-assets --xapk fixtures/un0/1.50.3/game.xapk "
            "-o output/un0/1.50.3/unpacked\n"
        ),
    )
    _add_world_version_args(p)
    src = p.add_mutually_exclusive_group(required=False)
    src.add_argument(
        "--xapk",
        nargs="?",
        const=_XAPK_LAYOUT_DEFAULT,
        default=None,
        help=(
            "XAPK to stream assets/aa/**.bundle from (no full unpack). "
            "Pass --xapk alone with --version to use fixtures/.../game.xapk"
        ),
    )
    src.add_argument(
        "--bundles",
        type=Path,
        help=(
            "Directory of .bundle files (default with --version: "
            "output/{world}/{version}/assets)"
        ),
    )
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help=(
            "Output directory for extracted/ and index.json "
            "(default: output/{world}/{version}/unpacked)"
        ),
    )
    p.add_argument(
        "--only",
        action="append",
        default=None,
        metavar="TERM",
        help=(
            "Unpack only bundles whose filename contains TERM "
            "(repeatable; disables --skip)"
        ),
    )
    p.add_argument(
        "--skip",
        action="append",
        default=None,
        metavar="PREFIX",
        help="Skip bundles whose filename starts with PREFIX (repeatable)",
    )
    p.add_argument(
        "--jobs",
        type=int,
        default=unpack.DEFAULT_JOBS,
        help=(
            "Parallel worker processes for texture decoding "
            f"(default: {unpack.DEFAULT_JOBS})"
        ),
    )
    p.add_argument(
        "--include-atlas-textures",
        action="store_true",
        help=(
            "Also export atlas sheet textures (sactx-*) and textures shadowed by "
            "a sprite of the same name; skipped by default as redundant"
        ),
    )
    p.add_argument(
        "--clean",
        action="store_true",
        help="Delete the output directory first instead of resuming",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the selected bundle names without extracting",
    )
    p.add_argument("-v", "--verbose", action="store_true")


def _add_link_assets_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "link-assets",
        help="Resolve asset fields in decoded definitions to extracted images",
        description=(
            "Join asset-reference string fields (asset_id, backdrop_asset_id, "
            "icon, ...) in decoded definition JSON against the unpack index. "
            "Which fields to inspect is derived from the descriptor set."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Each value resolves to one of: image (a PNG), bundle_only (an "
            "address whose bundle holds a prefab, not art), or miss.\n\n"
            "Examples:\n"
            "  hoh-protos link-assets --version 1.50.3\n"
            "  hoh-protos link-assets --index output/un0/1.50.3/unpacked/index.json \\\n"
            "    --descriptors output/un0/1.50.3/descriptors.pb \\\n"
            "    --definitions output/un0/1.50.3/gamedesign "
            "-o output/un0/1.50.3/asset_links\n"
        ),
    )
    _add_world_version_args(p)
    p.add_argument(
        "--index",
        type=Path,
        default=None,
        help=(
            "index.json written by unpack-assets (or its directory; "
            "default: output/{world}/{version}/unpacked)"
        ),
    )
    p.add_argument(
        "--descriptors",
        type=Path,
        default=None,
        help="descriptors.pb path (default: output/{world}/{version}/descriptors.pb)",
    )
    p.add_argument(
        "--definitions",
        type=Path,
        action="append",
        default=None,
        metavar="DIR",
        help=(
            "Directory of decoded per-type JSON from `definitions` (repeatable; "
            "default with --version: gamedesign/ and startup/ under output/)"
        ),
    )
    p.add_argument(
        "-o",
        "--out-dir",
        type=Path,
        default=None,
        help=(
            "Output directory for links.json and report.json "
            "(default: output/{world}/{version}/asset_links)"
        ),
    )
    p.add_argument("-v", "--verbose", action="store_true")


def _add_download_fixtures_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "download-fixtures",
        help="Download startup/gamedesign/loca fixtures from InnoGames",
        description=(
            "Log into Heroes of History, open a browser play session, and save "
            "raw protobuf responses (startup, gamedesign, loca-compressed) plus "
            "the matching XAPK as game.xapk under fixtures/{world}/{clientVersion}/. "
            "Talks only to InnoGames / APKPure hosts; nothing is uploaded."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Credentials: --username/--password, or env HOH_USERNAME/HOH_PASSWORD "
            "(env overwrites flags). Optional HOH_WORLD / HOH_LOCALE likewise "
            "overwrite --world / --locale.\n\n"
            "Examples:\n"
            "  hoh-protos download-fixtures --username USER --password PASS\n"
            "  hoh-protos download-fixtures --world zz0 --only gamedesign,loca\n"
            "  HOH_USERNAME=… HOH_PASSWORD=… hoh-protos download-fixtures -v\n"
        ),
    )
    p.add_argument(
        "--username",
        default=None,
        help=f"Game username (overwritten by {game_api.ENV_USERNAME})",
    )
    p.add_argument(
        "--password",
        default=None,
        help=f"Game password (overwritten by {game_api.ENV_PASSWORD})",
    )
    p.add_argument(
        "--world",
        default=game_api.DEFAULT_WORLD,
        help=(
            f"Fixture world key (default: {game_api.DEFAULT_WORLD}; "
            f"known: {', '.join(game_api.KNOWN_WORLDS)}; "
            f"overwritten by {game_api.ENV_WORLD})"
        ),
    )
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=game_api.DEFAULT_OUTPUT,
        help=(
            "Fixtures root directory; files go under "
            "{output}/{world}/{clientVersion}/ "
            f"(default: {game_api.DEFAULT_OUTPUT})"
        ),
    )
    p.add_argument(
        "--only",
        default=None,
        metavar="LIST",
        help=(
            "Comma-separated fixtures to download: startup, gamedesign, loca "
            "(default: all three)"
        ),
    )
    p.add_argument(
        "--locale",
        default=game_api.DEFAULT_LOCALE,
        help=(
            f"Locale for the loca request (default: {game_api.DEFAULT_LOCALE}; "
            f"overwritten by {game_api.ENV_LOCALE})"
        ),
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if the destination file already exists",
    )
    p.add_argument(
        "--skip-xapk",
        action="store_true",
        help="Do not download game.xapk from APKPure into the fixture folder",
    )
    p.add_argument("-v", "--verbose", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hoh-protos",
        description="Extract .proto schemas from Unity IL2CPP + Google.Protobuf XAPK files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Run `hoh-protos setup` once before the main pipeline.\n\n"
            "Examples:\n"
            "  hoh-protos setup\n"
            "  hoh-protos download-xapk --version 1.50.3\n"
            "  hoh-protos run --version 1.50.3\n"
            "  hoh-protos download-assets --version 1.50.3\n"
        ),
    )
    sub = parser.add_subparsers(dest="command")

    _add_setup_parser(sub)
    _add_extract_parser(sub)
    _add_emit_parser(sub)
    _add_run_parser(sub)
    _add_gamedesign_parser(sub)
    _add_gamedesign_constants_parser(sub)
    _add_wirefix_parser(sub)
    _add_definitions_parser(sub)
    _add_loca_parser(sub)
    _add_download_xapk_parser(sub)
    _add_download_fixtures_parser(sub)
    _add_download_assets_parser(sub)
    _add_unpack_assets_parser(sub)
    _add_link_assets_parser(sub)

    return parser


def _err_need_version_or_paths(*flags: str) -> str:
    joined = ", ".join(flags)
    return (
        f"error: provide --version to use default paths, or pass {joined} explicitly"
    )


def _resolve_extract_args(args: argparse.Namespace) -> str | None:
    layout = _layout_from_args(args)
    args.dump_cs = coalesce(args.dump_cs, layout.dump_cs if layout else None)
    args.out = coalesce(args.out, layout.descriptors if layout else None)
    missing = []
    if args.dump_cs is None:
        missing.append("--dump-cs")
    if args.out is None:
        missing.append("--out")
    if missing:
        return _err_need_version_or_paths(*missing)
    return None


def _resolve_emit_args(args: argparse.Namespace) -> str | None:
    layout = _layout_from_args(args)
    args.inp = coalesce(args.inp, layout.descriptors if layout else None)
    args.out_dir = coalesce(args.out_dir, layout.proto if layout else None)
    missing = []
    if args.inp is None:
        missing.append("--in")
    if args.out_dir is None:
        missing.append("--out-dir")
    if missing:
        return _err_need_version_or_paths(*missing)
    return None


def _resolve_run_args(args: argparse.Namespace) -> str | None:
    layout = _layout_from_args(args)
    if args.xapk is None:
        if layout is None:
            return "error: provide an XAPK path or --version"
        args.xapk = layout.xapk
    elif layout is None:
        # Infer layout from an explicit XAPK under fixtures/{world}/{version}/.
        from xapk_to_proto.paths import infer_world_version_from_path, resolve_world

        inferred_world, inferred_version = infer_world_version_from_path(args.xapk)
        version = args.version or inferred_version
        if version:
            world = args.world or inferred_world or resolve_world(None)
            layout = version_layout(version, world=world)

    if layout is not None:
        if args.gamedesign_input is None and layout.gamedesign.is_file():
            args.gamedesign_input = layout.gamedesign
        if args.loca_input is None and layout.loca.is_file():
            args.loca_input = layout.loca
        if args.startup_input is None and layout.startup.is_file():
            args.startup_input = layout.startup
    return None


def _resolve_gamedesign_args(args: argparse.Namespace) -> str | None:
    layout = _layout_from_args(args)
    args.descriptors = coalesce(
        args.descriptors, layout.descriptors if layout else None
    )
    args.input = coalesce(args.input, layout.gamedesign if layout else None)
    args.out_dir = coalesce(args.out_dir, layout.gamedesign_out if layout else None)
    missing = []
    if args.descriptors is None:
        missing.append("--descriptors")
    if args.input is None:
        missing.append("--input")
    if args.out_dir is None:
        missing.append("--out-dir")
    if missing:
        return _err_need_version_or_paths(*missing)
    return None


def _resolve_gamedesign_constants_args(args: argparse.Namespace) -> str | None:
    layout = _layout_from_args(args)
    args.dump_cs = coalesce(args.dump_cs, layout.dump_cs if layout else None)
    args.out_dir = coalesce(args.out_dir, layout.constants if layout else None)
    missing = []
    if args.dump_cs is None:
        missing.append("--dump-cs")
    if args.out_dir is None:
        missing.append("--out-dir")
    if missing:
        return _err_need_version_or_paths(*missing)
    return None


def _resolve_wirefix_args(args: argparse.Namespace) -> str | None:
    layout = _layout_from_args(args)
    args.descriptors = coalesce(
        args.descriptors, layout.descriptors if layout else None
    )
    args.input = coalesce(args.input, layout.gamedesign if layout else None)
    missing = []
    if args.descriptors is None:
        missing.append("--descriptors")
    if args.input is None:
        missing.append("--input")
    if missing:
        return _err_need_version_or_paths(*missing)
    return None


def _resolve_definitions_args(args: argparse.Namespace) -> str | None:
    layout = _layout_from_args(args)
    args.descriptors = coalesce(
        args.descriptors, layout.descriptors if layout else None
    )
    args.out_dir = coalesce(args.out_dir, layout.root if layout else None)
    if args.input is None and layout is not None:
        blobs = layout.existing_fixture_blobs()
        # definitions decodes startup/gamedesign style WrappedResponse blobs;
        # skip loca-compressed (different message type).
        args.input = [
            p
            for p in blobs
            if p.name in (game_api.FIXTURE_STARTUP, game_api.FIXTURE_GAMEDESIGN)
        ]
    missing = []
    if args.descriptors is None:
        missing.append("--descriptors")
    if not args.input:
        missing.append("--input")
    if args.out_dir is None:
        missing.append("--out-dir")
    if missing:
        return _err_need_version_or_paths(*missing)
    return None


def _resolve_loca_args(args: argparse.Namespace) -> str | None:
    layout = _layout_from_args(args)
    args.descriptors = coalesce(
        args.descriptors, layout.descriptors if layout else None
    )
    args.dump_cs = coalesce(args.dump_cs, layout.dump_cs if layout else None)
    args.input = coalesce(args.input, layout.loca if layout else None)
    args.out_dir = coalesce(args.out_dir, layout.loca_out if layout else None)
    missing = []
    if args.descriptors is None:
        missing.append("--descriptors")
    if args.dump_cs is None:
        missing.append("--dump-cs")
    if args.input is None:
        missing.append("--input")
    if args.out_dir is None:
        missing.append("--out-dir")
    if missing:
        return _err_need_version_or_paths(*missing)
    return None


def _resolve_download_xapk_args(args: argparse.Namespace) -> str | None:
    if args.output is None and args.version:
        layout = version_layout(args.version, world=args.world)
        args.output = layout.xapk
    if args.output is None:
        return _err_need_version_or_paths("--output")
    return None


def _resolve_download_assets_args(args: argparse.Namespace) -> str | None:
    layout = _layout_from_args(args)
    if args.catalog is None and args.xapk is None and layout is not None:
        args.xapk = layout.xapk
    args.output = coalesce(args.output, layout.assets if layout else None)
    if layout is not None:
        args.unpack_out = coalesce(args.unpack_out, layout.unpacked)
    if args.catalog is None and args.xapk is None:
        return _err_need_version_or_paths("--catalog", "--xapk")
    if args.output is None:
        return _err_need_version_or_paths("--output")
    return None


def _resolve_unpack_assets_args(args: argparse.Namespace) -> str | None:
    layout = _layout_from_args(args)
    xapk = _resolve_xapk_flag(args.xapk, layout)
    bundles = args.bundles
    if xapk is None and bundles is None and layout is not None:
        bundles = layout.assets
    args.xapk = xapk
    args.bundles = bundles
    args.output = coalesce(args.output, layout.unpacked if layout else None)
    if args.xapk is None and args.bundles is None:
        return _err_need_version_or_paths("--xapk", "--bundles")
    if args.output is None:
        return _err_need_version_or_paths("--output")
    return None


def _resolve_link_assets_args(args: argparse.Namespace) -> str | None:
    layout = _layout_from_args(args)
    args.index = coalesce(args.index, layout.unpacked if layout else None)
    args.descriptors = coalesce(
        args.descriptors, layout.descriptors if layout else None
    )
    args.out_dir = coalesce(args.out_dir, layout.asset_links if layout else None)
    if args.definitions is None and layout is not None:
        defs = []
        if layout.gamedesign_out.is_dir():
            defs.append(layout.gamedesign_out)
        if layout.startup_out.is_dir():
            defs.append(layout.startup_out)
        args.definitions = defs or None
    missing = []
    if args.index is None:
        missing.append("--index")
    if args.descriptors is None:
        missing.append("--descriptors")
    if not args.definitions:
        missing.append("--definitions")
    if args.out_dir is None:
        missing.append("--out-dir")
    if missing:
        return _err_need_version_or_paths(*missing)
    return None


def main(argv: list[str] | None = None) -> int:
    argv = _normalize_argv(list(sys.argv[1:] if argv is None else argv))
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "setup":
        try:
            deps.setup(force=args.force, verbose=args.verbose)
        except (RuntimeError, subprocess.CalledProcessError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        return 0

    if args.command == "extract":
        err = _resolve_extract_args(args)
        if err:
            print(err, file=sys.stderr)
            return 2
        try:
            stats = extract.run(
                args.metadata.resolve(),
                args.dump_cs.resolve(),
                args.out.resolve(),
            )
        except FileNotFoundError as e:
            print(e, file=sys.stderr)
            return 1
        print(f"embedded descriptors: {stats['embedded']}")
        print(f"dump.cs proto files: {stats['rebuilt']}")
        print(f"merged descriptors: {stats['merged']}")
        missing = stats.get("missing_well_known") or []
        if missing:
            print(f"missing WELL_KNOWN mappings: {len(missing)} (see stderr)")
        print(f"wrote {args.out.resolve()}")
        return 0

    if args.command == "emit":
        err = _resolve_emit_args(args)
        if err:
            print(err, file=sys.stderr)
            return 2
        try:
            emit_result = emit.run(args.inp.resolve(), args.out_dir.resolve())
        except FileNotFoundError as e:
            print(e, file=sys.stderr)
            return 1
        print(
            f"wrote {emit_result.game_files} game + "
            f"{emit_result.well_known_files} well-known .proto files to "
            f"{args.out_dir.resolve()}"
        )
        return 0

    if args.command == "run":
        err = _resolve_run_args(args)
        if err:
            print(err, file=sys.stderr)
            return 2
        return pipeline_run(args)

    if args.command == "gamedesign":
        err = _resolve_gamedesign_args(args)
        if err:
            print(err, file=sys.stderr)
            return 2
        try:
            result = definitions.run_definitions_export(
                descriptors_pb=args.descriptors.resolve(),
                out_dir=args.out_dir.resolve(),
                input_path=args.input.resolve(),
                verbose=args.verbose,
            )
        except (FileNotFoundError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        for warning in result.warnings:
            print(f"warning: {warning}", file=sys.stderr)
        print(f"source: {result.source}")
        print(f"entries: {result.total_entries}")
        print(f"wrote {args.out_dir.resolve() / 'manifest.json'}")
        return 0

    if args.command == "gamedesign-constants":
        err = _resolve_gamedesign_constants_args(args)
        if err:
            print(err, file=sys.stderr)
            return 2
        try:
            result = gamedesign_constants.run_gamedesign_constants_export(
                dump_cs=args.dump_cs.resolve(),
                out_dir=args.out_dir.resolve(),
            )
        except FileNotFoundError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        for warning in result.warnings:
            print(f"warning: {warning}", file=sys.stderr)
        print(
            f"wrote {result.enum_count} enums "
            f"({result.files_written} files) to {result.out_dir}"
        )
        return 0

    if args.command == "wirefix":
        err = _resolve_wirefix_args(args)
        if err:
            print(err, file=sys.stderr)
            return 2
        try:
            report = wirefix.run_wirefix(
                args.descriptors.resolve(),
                args.input.resolve(),
                verbose=args.verbose,
            )
        except (FileNotFoundError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        print(
            f"wirefix: {report.fixed_count} correction(s) "
            f"in {report.iterations} iteration(s)"
        )
        print(f"wrote {args.descriptors.resolve()}")
        return 0

    if args.command == "definitions":
        err = _resolve_definitions_args(args)
        if err:
            print(err, file=sys.stderr)
            return 2
        out_root = args.out_dir.resolve()
        for blob in args.input:
            blob = blob.resolve()
            dest = out_root / blob.stem
            try:
                result = definitions.run_definitions_export(
                    descriptors_pb=args.descriptors.resolve(),
                    out_dir=dest,
                    input_path=blob,
                    verbose=args.verbose,
                )
            except (FileNotFoundError, ValueError) as e:
                print(f"error: {e}", file=sys.stderr)
                return 1
            for warning in result.warnings:
                print(f"warning [{result.source}]: {warning}", file=sys.stderr)
            print(f"{result.source}: {result.total_entries} entries -> {dest}")
        return 0

    if args.command == "loca":
        err = _resolve_loca_args(args)
        if err:
            print(err, file=sys.stderr)
            return 2
        try:
            result = loca.run_loca_export(
                descriptors_pb=args.descriptors.resolve(),
                dump_cs=args.dump_cs.resolve(),
                input_path=args.input.resolve(),
                out_dir=args.out_dir.resolve(),
                verbose=args.verbose,
            )
        except (FileNotFoundError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        for warning in result.warnings:
            print(f"warning: {warning}", file=sys.stderr)
        print(
            f"loca[{result.locale}]: {result.entry_count} entries "
            f"({result.resolved_keys} resolved, {result.unresolved_hashes} unresolved) "
            f"-> {result.out_dir}"
        )
        return 0

    if args.command == "download-xapk":
        err = _resolve_download_xapk_args(args)
        if err:
            print(err, file=sys.stderr)
            return 1
        try:
            dl = apkpure.download_xapk(
                output=args.output,
                package=args.package,
                version=args.version,
                abi=args.abi,
                force=args.force,
                verbose=args.verbose,
            )
        except RuntimeError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        if dl.skipped:
            print(f"already downloaded: {dl.path} (use --force to replace)")
            return 0
        print(f"{dl.release.package} {dl.release.version}: {dl.size} bytes")
        print(f"wrote {dl.path}")
        return 0

    if args.command == "download-fixtures":
        try:
            result = game_api.download_fixtures(
                username=args.username,
                password=args.password,
                world=args.world,
                output=args.output,
                only=args.only,
                locale=args.locale,
                force=args.force,
                verbose=args.verbose,
                download_xapk=not args.skip_xapk,
            )
        except (RuntimeError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        print(
            f"world {result.session.world.key} "
            f"clientVersion {result.session.client_version}"
        )
        print(f"out {result.out_dir}")
        for item in result.files:
            status = "skipped" if item.skipped else "wrote"
            print(f"  {status} {item.name}: {item.path} ({item.size} bytes)")
        return 0

    if args.command == "download-assets":
        err = _resolve_download_assets_args(args)
        if err:
            print(err, file=sys.stderr)
            return 2
        try:
            assets_result = assets.run_assets_download(
                catalog=args.catalog.resolve() if args.catalog else None,
                xapk=args.xapk.resolve() if args.xapk else None,
                out_dir=args.output.resolve(),
                cdn_root=args.cdn_root,
                only=tuple(args.only or ()),
                skip=tuple(args.skip) if args.skip else assets.DEFAULT_SKIP,
                jobs=args.jobs,
                retries=args.retries,
                clean=args.clean,
                dry_run=args.dry_run,
                verbose=args.verbose,
            )
        except (FileNotFoundError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        for warning in assets_result.warnings:
            print(f"warning: {warning}", file=sys.stderr)
        if args.dry_run:
            print(
                f"{assets_result.selected} of {assets_result.total_bundles} "
                "bundle(s) selected (dry run)",
                file=sys.stderr,
            )
            return 0
        print(
            f"assets: {assets_result.downloaded} downloaded, "
            f"{assets_result.skipped_existing} already present, "
            f"{len(assets_result.failed)} failed "
            f"-> {assets_result.out_dir}"
        )
        if args.unpack:
            unpack_out = (
                args.unpack_out.resolve()
                if args.unpack_out
                else assets_result.out_dir / "unpacked"
            )
            try:
                unpack_result = unpack.run_unpack(
                    bundles=assets_result.out_dir,
                    out_dir=unpack_out,
                    verbose=args.verbose,
                )
            except (FileNotFoundError, ValueError, unpack.UnpackError) as e:
                print(f"error: {e}", file=sys.stderr)
                return 1
            _print_unpack_result(unpack_result)
        return 0

    if args.command == "unpack-assets":
        err = _resolve_unpack_assets_args(args)
        if err:
            print(err, file=sys.stderr)
            return 2
        try:
            unpack_result = unpack.run_unpack(
                xapk=args.xapk.resolve() if args.xapk else None,
                bundles=args.bundles.resolve() if args.bundles else None,
                out_dir=args.output.resolve(),
                only=tuple(args.only or ()),
                skip=tuple(args.skip or ()),
                jobs=args.jobs,
                include_atlas_textures=args.include_atlas_textures,
                clean=args.clean,
                dry_run=args.dry_run,
                verbose=args.verbose,
            )
        except (FileNotFoundError, ValueError, unpack.UnpackError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        if args.dry_run:
            print(
                f"{unpack_result.bundles_selected} of "
                f"{unpack_result.bundles_total} bundle(s) selected (dry run)",
                file=sys.stderr,
            )
            return 0
        _print_unpack_result(unpack_result)
        return 0

    if args.command == "link-assets":
        err = _resolve_link_assets_args(args)
        if err:
            print(err, file=sys.stderr)
            return 2
        try:
            link_result = assetlink.run_link_export(
                index_path=args.index.resolve(),
                descriptors_pb=args.descriptors.resolve(),
                definition_dirs=[d.resolve() for d in args.definitions],
                out_dir=args.out_dir.resolve(),
                verbose=args.verbose,
            )
        except (FileNotFoundError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        _print_link_result(link_result, args.out_dir.resolve())
        return 0

    parser.print_help()
    return 0


def _print_unpack_result(result: unpack.UnpackResult) -> None:
    for warning in result.warnings[:20]:
        print(f"warning: {warning}", file=sys.stderr)
    extra = len(result.warnings) - 20
    if extra > 0:
        print(f"warning: ... and {extra} more", file=sys.stderr)
    print(
        f"unpacked: {result.images_written} image(s) from "
        f"{result.bundles_selected - result.bundles_skipped} bundle(s) "
        f"({result.bundles_skipped} already done, {result.bundles_failed} failed) "
        f"-> {result.out_dir}"
    )


def _print_link_result(result: assetlink.LinkResult, out_dir: Path) -> None:
    for warning in result.warnings[:20]:
        print(f"warning: {warning}", file=sys.stderr)
    totals = result.totals()
    print(
        f"linked: {totals['values']} asset reference(s) across "
        f"{totals['records_with_assets']} record(s) in "
        f"{totals['types_scanned']} type(s)"
    )
    print(
        f"  {totals['image']} image, {totals['bundle_only']} bundle-only, "
        f"{totals['definition_ref']} gamedesign id, {totals['miss']} unresolved"
    )
    print(f"wrote {out_dir / assetlink.REPORT_FILENAME}")
