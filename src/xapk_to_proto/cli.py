"""Command-line interface for hoh-protos."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from xapk_to_proto import deps, emit, extract
from xapk_to_proto.pipeline import run as pipeline_run

_SUBCOMMANDS = frozenset({"setup", "extract", "emit", "run"})


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hoh-protos",
        description="Extract .proto schemas from Unity IL2CPP + Google.Protobuf XAPK files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Run `hoh-protos setup` once before the main pipeline.\n\n"
            "Examples:\n"
            "  hoh-protos setup\n"
            '  hoh-protos "/path/to/game.xapk" -o ./output\n'
            "  hoh-protos extract --metadata meta.dat --dump-cs dump.cs --out out.pb\n"
        ),
    )
    sub = parser.add_subparsers(dest="command")

    _add_setup_parser(sub)
    _add_extract_parser(sub)
    _add_emit_parser(sub)
    _add_run_parser(sub)

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
            written = emit.run(args.inp.resolve(), args.out_dir.resolve())
        except FileNotFoundError as e:
            print(e, file=sys.stderr)
            return 1
        print(f"wrote {written} .proto files to {args.out_dir.resolve()}")
        return 0

    if args.command == "run":
        return pipeline_run(args)

    parser.print_help()
    return 0
