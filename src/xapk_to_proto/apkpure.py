"""Download game XAPKs from APKPure via the ``apkeep`` CLI.

Uses [EFForg/apkeep](https://github.com/EFForg/apkeep) with ``-d apk-pure``.
APKPure serves this package as XAPK (``XAPKJ``); apkeep writes a ``.xapk`` file
which we rename to the caller's destination path.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PACKAGE = "com.innogames.heroesofhistory"
DEFAULT_ABI = "arm64-v8a"


@dataclass
class Release:
    package: str
    version: str


@dataclass
class DownloadResult:
    release: Release
    path: Path
    size: int
    skipped: bool = False


def resolve_output_path(output: Path | None, release: Release) -> Path:
    """Resolve ``-o`` into a concrete file path.

    Only a path ending in ``.xapk`` is treated as a filename; anything else is a
    directory that receives ``{package}_{version}.xapk``. A trailing separator
    cannot be used to signal a directory because ``Path`` normalizes it away.
    """
    default_name = f"{release.package}_{release.version}.xapk"
    if output is None:
        return Path(default_name).resolve()
    if output.suffix.lower() == ".xapk" and not output.is_dir():
        return output.resolve()
    return (output / default_name).resolve()


def _app_id(package: str, version: str | None) -> str:
    return f"{package}@{version}" if version else package


def _apkeep_missing_hint() -> str:
    if platform.system() == "Darwin":
        return "apkeep not found. Install with: brew install apkeep"
    return "apkeep not found. Run: hoh-protos setup"


def _run_apkeep(
    *,
    apkeep: str,
    app_id: str,
    abi: str,
    outdir: Path,
    verbose: bool,
) -> None:
    cmd = [
        apkeep,
        "-a",
        app_id,
        "-d",
        "apk-pure",
        "-o",
        f"arch={abi}",
        str(outdir),
    ]
    if verbose:
        print(f"  {' '.join(cmd)}", flush=True)
    try:
        # Inherit stdio so apkeep progress bars stream live on a TTY.
        proc = subprocess.run(cmd, check=False)
    except OSError as e:
        raise RuntimeError(f"failed to run apkeep: {e}") from e

    if proc.returncode != 0:
        raise RuntimeError(
            f"apkeep exited with status {proc.returncode} for {app_id}"
        )


def _find_downloaded_xapk(outdir: Path, package: str) -> Path:
    matches = sorted(outdir.glob("*.xapk"))
    if not matches:
        apk_matches = sorted(outdir.glob("*.apk"))
        if apk_matches:
            raise RuntimeError(
                f"apkeep downloaded APK(s) instead of XAPK: "
                f"{', '.join(p.name for p in apk_matches)} — "
                "this package requires the XAPK bundle"
            )
        raise RuntimeError(f"apkeep produced no .xapk under {outdir}")

    preferred = [p for p in matches if p.name.startswith(package)]
    return preferred[0] if preferred else matches[0]


def download_xapk(
    *,
    output: Path | None = None,
    package: str = DEFAULT_PACKAGE,
    version: str | None = None,
    abi: str = DEFAULT_ABI,
    force: bool = False,
    verbose: bool = False,
) -> DownloadResult:
    """Download an XAPK via ``apkeep``, renaming it to the resolved output path."""
    from xapk_to_proto import deps

    release = Release(package=package, version=version or "latest")
    dest = resolve_output_path(output, release)

    if dest.is_file() and not force:
        return DownloadResult(
            release=release,
            path=dest,
            size=dest.stat().st_size,
            skipped=True,
        )

    try:
        apkeep = deps.resolve_apkeep()
    except FileNotFoundError as e:
        raise RuntimeError(str(e) if str(e) else _apkeep_missing_hint()) from e

    app_id = _app_id(package, version)
    print(f"downloading {app_id} -> {dest}", flush=True)
    if verbose:
        print(f"  apkeep:  {apkeep}", flush=True)

    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hoh-apkeep-") as tmp:
        outdir = Path(tmp)
        _run_apkeep(
            apkeep=apkeep,
            app_id=app_id,
            abi=abi,
            outdir=outdir,
            verbose=verbose,
        )
        downloaded = _find_downloaded_xapk(outdir, package)
        if not zipfile.is_zipfile(downloaded):
            raise RuntimeError(
                f"downloaded file is not a valid archive: {downloaded}"
            )
        size = downloaded.stat().st_size
        dest.unlink(missing_ok=True)
        shutil.move(str(downloaded), str(dest))

    return DownloadResult(release=release, path=dest, size=size)
