"""Il2CppDumper integration."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from xapk_to_proto import deps
from xapk_to_proto.xapk import vlog


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
        stdout=subprocess.PIPE if not verbose else None,
        stderr=subprocess.PIPE if not verbose else None,
    )

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

    if dump_cs.is_file():
        return

    stdout = proc.stdout.read().decode() if proc.stdout else ""
    stderr = proc.stderr.read().decode() if proc.stderr else ""
    if stdout:
        print(stdout, file=sys.stderr)
    if stderr:
        print(stderr, file=sys.stderr)
    raise RuntimeError("Il2CppDumper failed — dump.cs was not created")


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
