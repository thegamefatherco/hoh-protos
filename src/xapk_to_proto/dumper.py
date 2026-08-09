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


def _filter_dumper_output(text: str) -> str:
    """Drop Console.ReadKey noise; keep the real failure lines."""
    lines = []
    for line in text.splitlines():
        if _READKEY_NOISE.search(line):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _failure_message(combined: str) -> str:
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
    if filtered:
        return f"Il2CppDumper failed — dump.cs was not created\n\n{filtered}"
    return "Il2CppDumper failed — dump.cs was not created"


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


def run_il2cpp_dumper(
    libil2cpp: Path,
    metadata: Path,
    out_dir: Path,
    verbose: bool,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
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

    deadline = time.time() + 600
    while time.time() < deadline:
        if dump_cs.is_file() and dump_cs.stat().st_size > 500_000:
            time.sleep(2)
            if proc.poll() is not None:
                break
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
            break
        if proc.poll() is not None:
            break
        time.sleep(1)

    if proc.poll() is None:
        proc.kill()
        proc.wait(timeout=10)

    for t in threads:
        t.join(timeout=5)

    if dump_cs.is_file():
        return

    combined = "".join(stdout_chunks) + "".join(stderr_chunks)
    raise RuntimeError(_failure_message(combined))


def validate_dump(dump_cs: Path) -> None:
    text = dump_cs.read_text(encoding="utf-8", errors="replace")
    if "Google.Protobuf" not in text:
        raise RuntimeError(
            "dump.cs has no Google.Protobuf types — this game may not use Google.Protobuf"
        )
    if "Reflection" not in text:
        raise RuntimeError(
            "dump.cs has no *Reflection classes — protobuf schemas likely unavailable"
        )
