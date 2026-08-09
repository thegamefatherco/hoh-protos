"""XAPK extraction and IL2CPP binary discovery."""

from __future__ import annotations

import shutil
import struct
import zipfile
from pathlib import Path

METADATA_MAGIC = 0xFAB11BAF


def vlog(msg: str, verbose: bool) -> None:
    if verbose:
        print(msg, flush=True)


def extract_zip(archive: Path, dest: Path, verbose: bool) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    vlog(f"  unzip {archive} -> {dest}", verbose)
    with zipfile.ZipFile(archive, "r") as zf:
        zf.extractall(dest)


def extract_xapk(xapk: Path, work_dir: Path, verbose: bool) -> Path:
    xapk_root = work_dir / "xapk"
    if xapk_root.exists():
        shutil.rmtree(xapk_root)
    extract_zip(xapk, xapk_root, verbose)

    for candidate in sorted(xapk_root.rglob("*.apk")):
        if candidate.is_file() and zipfile.is_zipfile(candidate):
            dest = candidate.parent / candidate.stem
            if not dest.is_dir():
                extract_zip(candidate, dest, verbose)

    return xapk_root


def find_file(root: Path, patterns: list[str]) -> Path | None:
    for pattern in patterns:
        matches = sorted(root.glob(pattern))
        if matches:
            return matches[0]
    return None


def discover_il2cpp(root: Path) -> tuple[Path, Path]:
    metadata = find_file(
        root,
        ["**/assets/bin/Data/Managed/Metadata/global-metadata.dat"],
    )
    il2cpp = find_file(
        root,
        [
            "**/lib/arm64-v8a/libil2cpp.so",
            "**/lib/armeabi-v7a/libil2cpp.so",
            "**/libil2cpp.so",
        ],
    )
    if metadata is None:
        raise FileNotFoundError(
            "global-metadata.dat not found — not a Unity IL2CPP build?"
        )
    if il2cpp is None:
        raise FileNotFoundError(
            "libil2cpp.so not found — ensure the XAPK includes a native config split "
            "(e.g. config.arm64_v8a.apk)"
        )
    return il2cpp, metadata


def read_metadata_version(metadata: Path) -> int:
    """Return the IL2CPP global-metadata version (uint32 after the magic)."""
    data = metadata.read_bytes()
    if len(data) < 8:
        raise ValueError(f"metadata too small ({len(data)} bytes): {metadata}")
    magic = struct.unpack_from("<I", data, 0)[0]
    if magic != METADATA_MAGIC:
        raise ValueError(
            f"Unsupported or encrypted metadata (magic {magic:#x}, expected {METADATA_MAGIC:#x})"
        )
    return struct.unpack_from("<I", data, 4)[0]


def validate_metadata(metadata: Path) -> int:
    """Validate magic and return metadata version."""
    version = read_metadata_version(metadata)
    if version >= 35:
        print(
            f"  metadata version {version} (Unity 6000.x+) — requires a v39-capable Il2CppDumper",
            flush=True,
        )
    return version
