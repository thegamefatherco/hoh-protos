"""End-to-end XAPK → descriptors.pb + .proto pipeline."""

from __future__ import annotations

import shutil
import sys
from argparse import Namespace
from pathlib import Path

from google.protobuf import descriptor_pb2

from xapk_to_proto import dumper, emit, extract
from xapk_to_proto.xapk import discover_il2cpp, extract_xapk, validate_metadata


def log(msg: str) -> None:
    print(msg, flush=True)


def vlog(msg: str, verbose: bool) -> None:
    if verbose:
        print(msg, flush=True)


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
    print(
        f"Done: {proto_count} .proto files, {len(fds.file)} descriptor files, {msg_count} messages",
        flush=True,
    )
    print(f"  descriptors: {output / 'descriptors.pb'}", flush=True)
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

        log("[5/5] Emitting .proto files...")
        written = emit.run(descriptors, proto_dir)
        vlog(f"  wrote {written} files", args.verbose)

        summarize(output, proto_dir)

        if not args.keep_work and work_dir.exists():
            shutil.rmtree(work_dir)

        return 0
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
