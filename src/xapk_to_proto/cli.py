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
    gamedesign,
    gamedesign_constants,
    loca,
    unpack,
    wirefix,
)
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
        "download-assets",
        "unpack-assets",
        "link-assets",
    }
)


def _normalize_argv(argv: list[str]) -> list[str]:
    if argv and not argv[0].startswith("-") and argv[0] not in _SUBCOMMANDS:
        return ["run", *argv]
    return argv


def _add_extract_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "extract",
        help="Extract descriptors.pb from metadata + dump.cs",
        description="Extract Google.Protobuf FileDescriptorProtos from IL2CPP artifacts.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  hoh-protos extract --metadata global-metadata.dat --dump-cs dump.cs --out descriptors.pb\n"
        ),
    )
    p.add_argument(
        "--metadata", type=Path, required=True, help="global-metadata.dat path"
    )
    p.add_argument(
        "--dump-cs", type=Path, required=True, help="Il2CppDumper dump.cs path"
    )
    p.add_argument("--out", type=Path, required=True, help="Output descriptors.pb path")


def _add_emit_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "emit",
        help="Render .proto files from descriptors.pb",
        description="Render .proto files from a FileDescriptorSet.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("Examples:\n  hoh-protos emit --in descriptors.pb --out-dir ./proto\n"),
    )
    p.add_argument(
        "--in", dest="inp", type=Path, required=True, help="descriptors.pb path"
    )
    p.add_argument(
        "--out-dir", type=Path, required=True, help="Output directory for .proto files"
    )


def _add_setup_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "setup",
        help="Download dotnet runtime and Il2CppDumper into user cache",
        description="Install external dependencies (dotnet + Il2CppDumper).",
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
            "Examples:\n"
            '  hoh-protos run "/path/to/game.xapk" -o ./output\n'
            "  hoh-protos run game.xapk -o ./output --skip-dump -v\n"
        ),
    )
    p.add_argument("xapk", type=Path, help="Path to .xapk file")
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output directory (default: {xapk_stem}_protos/)",
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
        "--gamedesign",
        action="store_true",
        help="Discover and export hero gamedesign JSON after schema extraction",
    )
    p.add_argument(
        "--gamedesign-input",
        type=Path,
        default=None,
        help="Decode hero gamedesign from a captured GameDesignResponse blob",
    )
    p.add_argument(
        "--gamedesign-out",
        type=Path,
        default=None,
        help="Gamedesign output directory (default: {output}/gamedesign)",
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
            "Decode a captured WrappedResponse/GameDesignResponse blob "
            "(e.g. startup.raw, wakeup.raw, gamedesign) into per-type JSON. "
            "Repeatable; one output dir per source is created."
        ),
    )
    p.add_argument(
        "--definitions-out",
        type=Path,
        default=None,
        help="Definitions output directory (default: {output}/definitions)",
    )
    p.add_argument(
        "--gamedesign-constants",
        action="store_true",
        help=(
            "Emit TypeScript string enums from GameDesign *Constants classes "
            "in dump.cs"
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
            "an English catalog under {output}/loca"
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
            "Resolve asset fields in the decoded definitions against the unpack "
            "index; requires --definitions-input and --unpack-assets"
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
        help="Decode hero gamedesign JSON from a GameDesignResponse blob",
        description="Decode GameDesignResponse protobuf data into hero-related JSON files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  hoh-protos gamedesign --descriptors output/descriptors.pb "
            "--input gd.bin --out-dir output/gamedesign\n"
        ),
    )
    p.add_argument(
        "--descriptors",
        type=Path,
        required=True,
        help="descriptors.pb path from schema extraction",
    )
    p.add_argument(
        "--input",
        type=Path,
        required=True,
        help="GameDesignResponse binary blob",
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="Output directory for manifest.json and heroes/*.json",
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
            "  hoh-protos gamedesign-constants --dump-cs output/il2cpp/dump.cs "
            "--out-dir output/gamedesign/constants\n"
        ),
    )
    p.add_argument(
        "--dump-cs",
        type=Path,
        required=True,
        help="Il2CppDumper dump.cs path",
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="Output directory for *.ts enums and index.ts",
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
            "  hoh-protos wirefix --descriptors output/descriptors.pb "
            "--input fixtures/un0/gamedesign\n"
        ),
    )
    p.add_argument(
        "--descriptors",
        type=Path,
        required=True,
        help="descriptors.pb path from schema extraction",
    )
    p.add_argument(
        "--input",
        type=Path,
        required=True,
        help="GameDesignResponse binary blob",
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
            "  hoh-protos definitions --descriptors output/descriptors.pb "
            "--out-dir output/definitions "
            "--input fixtures/startup.raw --input fixtures/wakeup.raw "
            "--input fixtures/gamedesign\n"
        ),
    )
    p.add_argument(
        "--descriptors",
        type=Path,
        required=True,
        help="descriptors.pb path from schema extraction",
    )
    p.add_argument(
        "--input",
        type=Path,
        action="append",
        required=True,
        metavar="BLOB",
        help="Captured blob to decode (repeatable)",
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="Output directory; a subdir named after each blob is created",
    )
    p.add_argument("-v", "--verbose", action="store_true")


def _add_loca_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "loca",
        help="Decode CompressedLocaResponse into an English key→string catalog",
        description=(
            "Decode a captured CompressedLocaResponse (or LocaResponse) blob, "
            "resolve FNV hashes via LocaKeys in dump.cs, and emit JSON plus "
            "typed display-name maps (e.g. RarityDisplayName)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  hoh-protos loca --descriptors output/un0/descriptors.pb "
            "--dump-cs output/un0/il2cpp/dump.cs "
            "--input fixtures/un0/loca-compressed "
            "--out-dir output/un0/loca\n"
        ),
    )
    p.add_argument(
        "--descriptors",
        type=Path,
        required=True,
        help="descriptors.pb path from schema extraction",
    )
    p.add_argument(
        "--dump-cs",
        type=Path,
        required=True,
        help="Il2CppDumper dump.cs path (for LocaKeys)",
    )
    p.add_argument(
        "--input",
        type=Path,
        required=True,
        help="CompressedLocaResponse / LocaResponse blob",
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="Output directory for catalog JSON and display-name TS maps",
    )
    p.add_argument("-v", "--verbose", action="store_true")


def _add_download_xapk_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "download-xapk",
        help="Download the game XAPK from APKPure (latest or a specific version)",
        description=(
            "Resolve a release on APKPure and download its XAPK. Partial downloads "
            "resume automatically."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  hoh-protos download-xapk -o ./fixtures\n"
            "  hoh-protos download-xapk 1.49.8 -o ./fixtures/1.49.8.xapk\n"
        ),
    )
    p.add_argument(
        "version",
        nargs="?",
        default=None,
        help="Version name to download (default: latest)",
    )
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help=(
            "Output path. A *.xapk path is used as the filename; anything else "
            "is a directory that receives {package}_{version}.xapk "
            "(default: current directory)"
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
            "  hoh-protos download-assets --xapk game.xapk -o ./assets\n"
            "  hoh-protos download-assets --catalog catalog.bin -o ./assets "
            "--only hero --jobs 16\n"
        ),
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--catalog",
        type=Path,
        help="Addressables catalog.bin path",
    )
    src.add_argument(
        "--xapk",
        type=Path,
        help="XAPK to read assets/aa/catalog.bin from (no full unpack)",
    )
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        required=True,
        help="Output directory for .bundle files and manifest.json",
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
        help="Output directory for extracted images (default: OUTPUT/unpacked)",
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
            "  hoh-protos unpack-assets --xapk game.xapk -o ./output/unpacked\n"
            "  hoh-protos unpack-assets --bundles ./output/assets "
            "-o ./output/unpacked --only spriteatlas\n"
        ),
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--xapk",
        type=Path,
        help="XAPK to stream assets/aa/**.bundle from (no full unpack)",
    )
    src.add_argument(
        "--bundles",
        type=Path,
        help="Directory of .bundle files, e.g. the download-assets output",
    )
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        required=True,
        help="Output directory for extracted/ and index.json",
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
            "Example:\n"
            "  hoh-protos link-assets --index ./output/unpacked/index.json \\\n"
            "    --descriptors ./output/descriptors.pb \\\n"
            "    --definitions ./output/definitions/startup -o ./output/asset_links\n"
        ),
    )
    p.add_argument(
        "--index",
        type=Path,
        required=True,
        help="index.json written by unpack-assets (or its directory)",
    )
    p.add_argument(
        "--descriptors",
        type=Path,
        required=True,
        help="descriptors.pb used to discover asset fields and message types",
    )
    p.add_argument(
        "--definitions",
        type=Path,
        action="append",
        required=True,
        metavar="DIR",
        help="Directory of decoded per-type JSON from `definitions` (repeatable)",
    )
    p.add_argument(
        "-o",
        "--out-dir",
        type=Path,
        required=True,
        help="Output directory for links.json and report.json",
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
            "  hoh-protos download-xapk -o ./fixtures\n"
            '  hoh-protos "/path/to/game.xapk" -o ./output\n'
            "  hoh-protos extract --metadata meta.dat --dump-cs dump.cs --out out.pb\n"
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
    _add_download_assets_parser(sub)
    _add_unpack_assets_parser(sub)
    _add_link_assets_parser(sub)

    return parser


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
        return pipeline_run(args)

    if args.command == "gamedesign":
        try:
            result = gamedesign.run_gamedesign_export(
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
        print(f"hero entries: {result.hero_entries} / {result.total_entries}")
        print(f"wrote {args.out_dir.resolve() / 'manifest.json'}")
        return 0

    if args.command == "gamedesign-constants":
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

    if args.command == "download-assets":
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
