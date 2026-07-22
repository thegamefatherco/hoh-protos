"""End-to-end XAPK → descriptors.pb + .proto pipeline."""

from __future__ import annotations

import shutil
import sys
from argparse import Namespace
from pathlib import Path

from google.protobuf import descriptor_pb2

from xapk_to_proto import (
    definitions,
    dumper,
    emit,
    extract,
    gamedesign,
    gamedesign_constants,
    loca,
    wirefix,
)
from xapk_to_proto.xapk import discover_il2cpp, extract_xapk, validate_metadata


def log(msg: str) -> None:
    print(msg, flush=True)


def vlog(msg: str, verbose: bool) -> None:
    if verbose:
        print(msg, flush=True)


def _resolve_wire_fix_input(
    args: Namespace,
    xapk_root: Path,
    descriptors: Path,
) -> Path | None:
    explicit = getattr(args, "wire_fix_input", None)
    if explicit is not None:
        path = Path(explicit).resolve()
        return path if path.is_file() else None

    gd_input = getattr(args, "gamedesign_input", None)
    if gd_input is not None:
        path = Path(gd_input).resolve()
        return path if path.is_file() else None

    if not getattr(args, "gamedesign", False):
        return None

    try:
        pool, _ = gamedesign.load_descriptor_pool(descriptors)
        candidates = gamedesign.discover_gamedesign_blobs(xapk_root, pool)
        return candidates[0] if candidates else None
    except (FileNotFoundError, ValueError):
        return None


def check_python_deps() -> None:
    try:
        import google.protobuf  # noqa: F401
    except ImportError as e:
        raise RuntimeError(
            "Python protobuf package missing. Reinstall: pip install hoh-protos"
        ) from e


def summarize(output: Path, proto_dir: Path) -> None:
    fds = descriptor_pb2.FileDescriptorSet()
    fds.ParseFromString((output / "descriptors.pb").read_bytes())
    msg_count = sum(len(f.message_type) for f in fds.file)
    proto_count = len(list(proto_dir.glob("**/*.proto")))
    google_proto_count = len(list((proto_dir / "google").glob("**/*.proto")))
    bundle_path = output / "descriptors_bundle.pb"
    print(
        f"Done: {proto_count} .proto files ({google_proto_count} well-known), "
        f"{len(fds.file)} game descriptor files, {msg_count} messages",
        flush=True,
    )
    print(f"  descriptors: {output / 'descriptors.pb'}", flush=True)
    if bundle_path.is_file():
        print(f"  bundle:      {bundle_path}", flush=True)
    print(f"  proto dir:   {proto_dir}", flush=True)


def run(args: Namespace) -> int:
    xapk = args.xapk.resolve()
    if not xapk.is_file():
        print(f"xapk not found: {xapk}", file=sys.stderr)
        return 1

    output = (args.output or Path(f"{xapk.stem}_protos")).resolve()
    work_dir = (args.work_dir or output / ".work").resolve()
    il2cpp_out = output / "il2cpp"
    proto_dir = output / "proto"
    descriptors = output / "descriptors.pb"
    dump_cs = il2cpp_out / "dump.cs"

    try:
        check_python_deps()
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 1

    output.mkdir(parents=True, exist_ok=True)

    try:
        log("[1/5] Extracting XAPK...")
        xapk_root = extract_xapk(xapk, work_dir, args.verbose)

        log("[2/5] Locating IL2CPP binaries...")
        libil2cpp, metadata = discover_il2cpp(xapk_root)
        validate_metadata(metadata)
        vlog(f"  libil2cpp: {libil2cpp}", args.verbose)
        vlog(f"  metadata:  {metadata}", args.verbose)

        if args.skip_dump and dump_cs.is_file():
            log("[3/5] Skipping Il2CppDumper (--skip-dump, dump.cs exists)")
        else:
            log("[3/5] Running Il2CppDumper...")
            dumper.run_il2cpp_dumper(libil2cpp, metadata, il2cpp_out, args.verbose)

        if not dump_cs.is_file():
            print("dump.cs missing after Il2CppDumper", file=sys.stderr)
            return 1
        dumper.validate_dump(dump_cs)

        log("[4/5] Extracting protobuf descriptors...")
        stats = extract.run(metadata, dump_cs, descriptors)
        vlog(
            f"  embedded={stats['embedded']} rebuilt={stats['rebuilt']} merged={stats['merged']}",
            args.verbose,
        )
        missing = stats.get("missing_well_known") or []
        if missing:
            vlog(f"  missing_well_known: {', '.join(missing)}", args.verbose)

        wire_fix_input = _resolve_wire_fix_input(args, xapk_root, descriptors)
        if wire_fix_input is not None:
            log("[4b/5] Correcting wire types from sample blob...")
            wf_report = wirefix.run_wirefix(
                descriptors,
                wire_fix_input,
                verbose=args.verbose,
            )
            vlog(
                f"  wirefix: {wf_report.fixed_count} correction(s) "
                f"in {wf_report.iterations} iteration(s)",
                args.verbose,
            )

        log("[5/5] Emitting .proto files...")
        emit_result = emit.run(descriptors, proto_dir)
        bundle_count = emit.write_descriptor_bundle(
            descriptors, output / "descriptors_bundle.pb"
        )
        vlog(
            f"  wrote {emit_result.game_files} game + "
            f"{emit_result.well_known_files} well-known .proto files",
            args.verbose,
        )
        vlog(f"  descriptors_bundle.pb: {bundle_count} files", args.verbose)

        summarize(output, proto_dir)

        if getattr(args, "gamedesign_constants", False):
            gc_out = (
                getattr(args, "gamedesign_constants_out", None)
                or output / "gamedesign" / "constants"
            ).resolve()
            log("[5b/6] Emitting GameDesign string enums...")
            gc_result = gamedesign_constants.run_gamedesign_constants_export(
                dump_cs=dump_cs,
                out_dir=gc_out,
            )
            for warning in gc_result.warnings:
                print(f"warning: {warning}", file=sys.stderr)
            print(
                f"  gamedesign/constants: {gc_result.enum_count} enums "
                f"-> {gc_out}",
                flush=True,
            )

        if getattr(args, "gamedesign", False) or getattr(args, "gamedesign_input", None):
            gd_out = (args.gamedesign_out or output / "gamedesign").resolve()
            log("[6/6] Exporting hero gamedesign...")
            gd_input = (
                args.gamedesign_input.resolve()
                if getattr(args, "gamedesign_input", None)
                else None
            )
            result = gamedesign.run_gamedesign_export(
                descriptors_pb=descriptors,
                out_dir=gd_out,
                xapk_root=xapk_root if gd_input is None else None,
                dump_cs=dump_cs if gd_input is None else None,
                input_path=gd_input,
                verbose=args.verbose,
            )
            vlog(
                f"  source={result.source} hero_entries={result.hero_entries}",
                args.verbose,
            )
            for warning in result.warnings:
                print(f"warning: {warning}", file=sys.stderr)
            print(f"  gamedesign: {gd_out / 'manifest.json'}", flush=True)

        defs_inputs = getattr(args, "definitions_input", None)
        if defs_inputs:
            defs_out = (
                getattr(args, "definitions_out", None) or output / "definitions"
            ).resolve()
            log("[+] Decoding definition blobs...")
            for blob in defs_inputs:
                blob = Path(blob).resolve()
                if not blob.is_file():
                    print(f"warning: definitions input not found: {blob}", file=sys.stderr)
                    continue
                dest = defs_out / blob.stem
                defs_result = definitions.run_definitions_export(
                    descriptors_pb=descriptors,
                    out_dir=dest,
                    input_path=blob,
                    verbose=args.verbose,
                )
                for warning in defs_result.warnings:
                    print(
                        f"warning [{defs_result.source}]: {warning}", file=sys.stderr
                    )
                print(
                    f"  definitions[{defs_result.source}]: "
                    f"{defs_result.total_entries} entries -> {dest}",
                    flush=True,
                )

        loca_input = getattr(args, "loca_input", None)
        if loca_input is not None:
            loca_path = Path(loca_input).resolve()
            loca_out = (
                getattr(args, "loca_out", None) or output / "loca"
            ).resolve()
            if not loca_path.is_file():
                print(f"warning: loca input not found: {loca_path}", file=sys.stderr)
            else:
                log("[+] Decoding loca blob...")
                loca_result = loca.run_loca_export(
                    descriptors_pb=descriptors,
                    dump_cs=dump_cs,
                    input_path=loca_path,
                    out_dir=loca_out,
                    verbose=args.verbose,
                )
                for warning in loca_result.warnings:
                    print(f"warning: {warning}", file=sys.stderr)
                print(
                    f"  loca[{loca_result.locale}]: {loca_result.entry_count} entries "
                    f"-> {loca_out}",
                    flush=True,
                )

        if not args.keep_work and work_dir.exists():
            shutil.rmtree(work_dir)

        return 0
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
