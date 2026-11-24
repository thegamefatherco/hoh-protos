"""Il2CppDumper integration."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from xapk_to_proto import deps
from xapk_to_proto.xapk import vlog

_READKEY_NOISE = re.compile(
    r"Cannot read keys when either application does not have a console|"
    r"Press any key to exit",
    re.IGNORECASE,
)
_VERSION_ERROR = re.compile(
    r"not a supported version\[(\d+)\]",
    re.IGNORECASE,
)
# Matches extract.split_dump_sections; image-list "Reflection" alone is not enough.
_REFLECTION_CLASS = re.compile(
    r"public static class \w+Reflection\s*// TypeDefIndex:",
)
_COMPRESSED_INT_ERROR = re.compile(
    r"Invalid compressed integer format",
    re.IGNORECASE,
)

DUMMY_DLL_DIRNAME = "DummyDll"
_POLL_INTERVAL = 0.5
# DummyDll generation runs after dump.cs; allow a long wall clock for large games.
_DUMPER_TIMEOUT = 1800


def _filter_dumper_output(text: str) -> str:
    """Drop Console.ReadKey noise; keep the real failure lines."""
    lines = []
    for line in text.splitlines():
        if _READKEY_NOISE.search(line):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def dummy_dll_dir(out_dir: Path) -> Path:
    return out_dir / DUMMY_DLL_DIRNAME


def list_dummy_dlls(out_dir: Path) -> list[Path]:
    """Return ``*.dll`` files under ``out_dir/DummyDll`` (non-recursive)."""
    folder = dummy_dll_dir(out_dir)
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.glob("*.dll") if p.is_file())


def is_known_dummy_dll_abort(combined: str) -> bool:
    """True when Il2CppDumper aborted DummyDll after a known upstream bug."""
    return bool(_COMPRESSED_INT_ERROR.search(combined or ""))


def classify_dumper_result(
    *,
    dump_cs: Path,
    out_dir: Path,
    combined: str,
) -> str:
    """Classify Il2CppDumper exit artifacts.

    Returns one of: ``ok``, ``ok_dummy_dll_skipped``, ``fail_no_dump``,
    ``fail_dummy_dll``.
    """
    dump_ok = dump_cs.is_file()
    dlls = list_dummy_dlls(out_dir)
    if dump_ok and dlls:
        return "ok"
    if dump_ok and is_known_dummy_dll_abort(combined):
        return "ok_dummy_dll_skipped"
    if dump_ok and not dlls:
        return "fail_dummy_dll"
    return "fail_no_dump"


def _failure_message(
    combined: str,
    *,
    binary: Path | None = None,
    dump_cs: Path | None = None,
    out_dir: Path | None = None,
) -> str:
    filtered = _filter_dumper_output(combined)
    match = _VERSION_ERROR.search(filtered)
    if match:
        ver = match.group(1)
        return (
            f"Il2CppDumper failed — metadata version {ver} is not supported by this "
            f"dumper build. Run `hoh-protos setup --force` to install a v39-capable "
            f"fork, or set XAPK_TO_PROTO_DUMPER / IL2CPP_DUMPER_* overrides.\n\n"
            f"{filtered}"
        )
    if "Microsoft.NETCore.App" in filtered and "You must install or update .NET" in filtered:
        return (
            "Il2CppDumper failed — .NET 9 runtime missing from the cached host. "
            "Run `hoh-protos setup` (installs channel 9.0; use --force to replace an "
            "old .NET 8 cache).\n\n"
            f"{filtered}"
        )
    is_wasm = binary is not None and binary.suffix.lower() == ".wasm"
    auto_scan_hint = ""
    if is_wasm and (
        "ERROR: Can't use auto mode to detect IL2CPP" in filtered
        or "CodeRegistration" in filtered
        or "auto mode" in filtered.lower()
    ):
        auto_scan_hint = (
            "\n\nWebGL/WASM note: Il2CppDumper auto-scan often fails on Unity 6 "
            "WebAssembly builds. Manual CodeRegistration/MetadataRegistration "
            "flags are not supported in this CLI yet — try the Android XAPK "
            "path (`hoh-protos run` without --from-wasm) if available, or a "
            "dumper fork with improved WASM scanning."
        )

    dump_ok = dump_cs is not None and dump_cs.is_file()
    dlls = list_dummy_dlls(out_dir) if out_dir is not None else []
    if dump_ok and not dlls:
        return (
            "Il2CppDumper failed — dump.cs was created but DummyDll/ has no .dll "
            "assemblies (DummyDll generation did not finish). A common upstream "
            "cause is `Invalid compressed integer format` during DummyDll export "
            "on some Unity 6 / metadata v39 builds; that case is soft-failed when "
            "detected. Otherwise re-run without interrupting the dumper."
            f"{auto_scan_hint}\n\n{filtered}"
            if filtered
            else (
                "Il2CppDumper failed — dump.cs was created but DummyDll/ has no "
                ".dll assemblies. A common upstream cause is "
                "`Invalid compressed integer format` during DummyDll export."
                f"{auto_scan_hint}"
            )
        )

    if filtered:
        return (
            f"Il2CppDumper failed — dump.cs was not created{auto_scan_hint}\n\n"
            f"{filtered}"
        )
    return f"Il2CppDumper failed — dump.cs was not created{auto_scan_hint}"


def _drain_pipe(pipe, chunks: list[str], tee: bool) -> None:
    try:
        for line in iter(pipe.readline, b""):
            text = line.decode(errors="replace")
            chunks.append(text)
            if tee:
                sys.stderr.write(text)
                sys.stderr.flush()
    finally:
        pipe.close()


def _terminate_dumper(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


def warn_if_dummy_dll_missing(out_dir: Path) -> bool:
    """Warn when DummyDll is missing/empty. Returns True if assemblies exist."""
    dlls = list_dummy_dlls(out_dir)
    if dlls:
        return True
    folder = dummy_dll_dir(out_dir)
    print(
        f"warning: DummyDll missing or empty ({folder}) — protobuf extract will "
        "use dump.cs; AssetRipper needs managed assemblies and will export stub "
        "scripts without them",
        file=sys.stderr,
        flush=True,
    )
    return False


def run_il2cpp_dumper(
    libil2cpp: Path,
    metadata: Path,
    out_dir: Path,
    verbose: bool,
) -> None:
    """Run Il2CppDumper until it exits; require dump.cs (DummyDll is best-effort)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    dummy_dll_dir(out_dir).mkdir(parents=True, exist_ok=True)
    dotnet = deps.resolve_dotnet()
    dumper = deps.resolve_dumper_dll()
    env = os.environ.copy()
    env["DOTNET_ROLL_FORWARD"] = "LatestMajor"
    cmd = [*dotnet, str(dumper), str(libil2cpp), str(metadata), str(out_dir)]
    vlog(f"  {' '.join(cmd)}", verbose)

    dump_cs = out_dir / "dump.cs"
    proc = subprocess.Popen(
        cmd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    threads = [
        threading.Thread(
            target=_drain_pipe,
            args=(proc.stdout, stdout_chunks, verbose),
            daemon=True,
        ),
        threading.Thread(
            target=_drain_pipe,
            args=(proc.stderr, stderr_chunks, verbose),
            daemon=True,
        ),
    ]
    for t in threads:
        t.start()

    deadline = time.time() + _DUMPER_TIMEOUT
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        time.sleep(_POLL_INTERVAL)

    if proc.poll() is None:
        _terminate_dumper(proc)

    for t in threads:
        t.join(timeout=5)

    combined = "".join(stdout_chunks) + "".join(stderr_chunks)
    outcome = classify_dumper_result(
        dump_cs=dump_cs, out_dir=out_dir, combined=combined
    )
    if outcome == "ok":
        vlog(f"  DummyDll: {len(list_dummy_dlls(out_dir))} assembly(ies)", verbose)
        return
    if outcome == "ok_dummy_dll_skipped":
        print(
            "warning: Il2CppDumper wrote dump.cs but DummyDll export aborted "
            "(Invalid compressed integer format / upstream DummyAssemblyGenerator). "
            "Continuing with dump.cs for protobuf extract; DummyDll/ will be empty.",
            file=sys.stderr,
            flush=True,
        )
        return

    raise RuntimeError(
        _failure_message(
            combined,
            binary=libil2cpp,
            dump_cs=dump_cs,
            out_dir=out_dir,
        )
    )


def validate_dump(dump_cs: Path) -> None:
    text = dump_cs.read_text(encoding="utf-8", errors="replace")
    if "Google.Protobuf" not in text:
        raise RuntimeError(
            "dump.cs has no Google.Protobuf types — this game may not use Google.Protobuf"
        )
    if not _REFLECTION_CLASS.search(text):
        raise RuntimeError(
            "dump.cs has no public static *Reflection classes — "
            "protobuf schemas likely unavailable (dump may be truncated)"
        )
