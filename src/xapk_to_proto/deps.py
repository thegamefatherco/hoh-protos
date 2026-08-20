"""Cache and resolve external dependencies (dotnet, Il2CppDumper, apkeep)."""

from __future__ import annotations

import os
import platform
import shutil
import stat
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.request import urlretrieve

import platformdirs

# Windows81 fork: metadata v35–v39 (Unity 6000.x). Stock Perfare stops at older versions.
DEFAULT_DUMPER_REPO = "Windows81/Il2CppDumper"
DEFAULT_DUMPER_VERSION = "v20260329T093452Z"
DEFAULT_DUMPER_ASSET = "Il2CppDumper-CLI-20260329T093452Z_0507132.zip"
# Windows81 CLI targets net9.0; DOTNET_ROLL_FORWARD cannot substitute an 8.0 runtime.
DOTNET_CHANNEL = "9.0"
DOTNET_INSTALL_URL = "https://dot.net/v1/dotnet-install.sh"
DOTNET_MANUAL_HINT = "Install .NET 9 from https://dot.net"

# EFForg/apkeep — no official macOS release assets; use Homebrew there.
DEFAULT_APKEEP_VERSION = "1.0.0"
APKEEP_REPO = "EFForg/apkeep"


@dataclass(frozen=True)
class DepResult:
    name: str
    status: str  # "ok" | "missing" | "skipped" | "failed"
    detail: str
    hint: str | None = None


def cache_dir() -> Path:
    return Path(platformdirs.user_cache_dir("hoh-protos"))


def dotnet_cache_dir() -> Path:
    return cache_dir() / "dotnet"


def dumper_cache_dir() -> Path:
    return cache_dir() / "Il2CppDumper"


def apkeep_cache_dir() -> Path:
    return cache_dir() / "apkeep"


def dumper_repo() -> str:
    return os.environ.get("IL2CPP_DUMPER_REPO", DEFAULT_DUMPER_REPO)


def dumper_version() -> str:
    return os.environ.get("IL2CPP_DUMPER_VERSION", DEFAULT_DUMPER_VERSION)


def dumper_asset() -> str:
    return os.environ.get("IL2CPP_DUMPER_ASSET", DEFAULT_DUMPER_ASSET)


def dumper_url(
    *,
    repo: str | None = None,
    version: str | None = None,
    asset: str | None = None,
) -> str:
    repo = repo if repo is not None else dumper_repo()
    version = version if version is not None else dumper_version()
    asset = asset if asset is not None else dumper_asset()
    return f"https://github.com/{repo}/releases/download/{version}/{asset}"


def find_dumper_dll(root: Path) -> Path | None:
    """Locate Il2CppDumper.dll under *root* (zip layouts may nest one level)."""
    direct = root / "Il2CppDumper.dll"
    if direct.is_file():
        return direct
    matches = sorted(root.rglob("Il2CppDumper.dll"))
    return matches[0] if matches else None


def resolve_dotnet() -> list[str]:
    env = os.environ.get("XAPK_TO_PROTO_DOTNET")
    if env:
        path = Path(env)
        if path.is_file():
            return [str(path)]
        raise FileNotFoundError(f"XAPK_TO_PROTO_DOTNET not found: {env}")

    bundled = dotnet_cache_dir() / "dotnet"
    if bundled.is_file():
        return [str(bundled)]

    exe = shutil.which("dotnet")
    if exe:
        return [exe]

    if platform.system() == "Windows":
        raise FileNotFoundError(f"dotnet not found. {DOTNET_MANUAL_HINT}")
    raise FileNotFoundError("dotnet not found. Run: hoh-protos setup")


def resolve_dumper_dll() -> Path:
    env = os.environ.get("XAPK_TO_PROTO_DUMPER")
    if env:
        path = Path(env)
        if path.is_file():
            return path
        raise FileNotFoundError(f"XAPK_TO_PROTO_DUMPER not found: {env}")

    dll = find_dumper_dll(dumper_cache_dir())
    if dll is not None:
        return dll

    raise FileNotFoundError("Il2CppDumper not found. Run: hoh-protos setup")


def apkeep_version() -> str:
    return os.environ.get("XAPK_TO_PROTO_APKEEP_VERSION", DEFAULT_APKEEP_VERSION)


def apkeep_release_asset() -> str | None:
    """GitHub release asset name for this platform, or None on Darwin (use brew)."""
    system = platform.system()
    machine = platform.machine().lower()

    if system == "Darwin":
        return None
    if system == "Windows":
        return "apkeep-x86_64-pc-windows-msvc.exe"
    if system == "Linux":
        if machine in ("aarch64", "arm64"):
            return "apkeep-aarch64-unknown-linux-gnu"
        if machine in ("armv7l", "armv7"):
            return "apkeep-armv7-unknown-linux-gnueabihf"
        if machine in ("i386", "i686", "x86"):
            return "apkeep-i686-unknown-linux-gnu"
        return "apkeep-x86_64-unknown-linux-gnu"
    return None


def apkeep_url(*, version: str | None = None, asset: str | None = None) -> str:
    version = version if version is not None else apkeep_version()
    asset = asset if asset is not None else apkeep_release_asset()
    if asset is None:
        raise RuntimeError("no apkeep release asset for this platform (use brew on macOS)")
    return f"https://github.com/{APKEEP_REPO}/releases/download/{version}/{asset}"


def cached_apkeep_path() -> Path:
    asset = apkeep_release_asset()
    name = "apkeep.exe" if asset and asset.endswith(".exe") else "apkeep"
    return apkeep_cache_dir() / name


def _apkeep_missing_message() -> str:
    if platform.system() == "Darwin":
        return "apkeep not found. Install with: brew install apkeep"
    return "apkeep not found. Run: hoh-protos setup"


def _apkeep_missing_hint() -> str:
    if platform.system() == "Darwin":
        return "brew install apkeep"
    return "hoh-protos setup"


def _dotnet_missing_hint() -> str:
    if platform.system() == "Windows":
        return DOTNET_MANUAL_HINT
    return "hoh-protos setup"


def resolve_apkeep() -> str:
    """Return path to an ``apkeep`` executable.

    Order: ``XAPK_TO_PROTO_APKEEP`` → cached release binary → ``PATH``.
    """
    env = os.environ.get("XAPK_TO_PROTO_APKEEP")
    if env:
        path = Path(env)
        if path.is_file():
            return str(path)
        raise FileNotFoundError(f"XAPK_TO_PROTO_APKEEP not found: {env}")

    cached = cached_apkeep_path()
    if cached.is_file():
        return str(cached)

    exe = shutil.which("apkeep")
    if exe:
        return exe

    raise FileNotFoundError(_apkeep_missing_message())


def find_apkeep_on_path() -> str | None:
    return shutil.which("apkeep")


def has_netcore_runtime(dotnet_root: Path, major: str = "9") -> bool:
    """True if *dotnet_root* has a Microsoft.NETCore.App runtime for *major*.x."""
    shared = dotnet_root / "shared" / "Microsoft.NETCore.App"
    if not shared.is_dir():
        return False
    prefix = f"{major}."
    return any(p.is_dir() and p.name.startswith(prefix) for p in shared.iterdir())


def _system_dotnet_with_runtime(major: str = "9") -> Path | None:
    """Return a PATH/DOTNET_ROOT ``dotnet`` that has Microsoft.NETCore.App *major*.x."""
    exe = shutil.which("dotnet")
    if not exe:
        return None
    exe_path = Path(exe)

    roots: list[Path] = []
    env_root = os.environ.get("DOTNET_ROOT")
    if env_root:
        roots.append(Path(env_root))
    roots.append(exe_path.resolve().parent)

    for root in roots:
        if has_netcore_runtime(root, major):
            return exe_path
    return None


def find_dotnet_with_runtime(major: str = "9") -> Path | None:
    """Locate a usable ``dotnet`` with Microsoft.NETCore.App *major*.x (no install)."""
    env = os.environ.get("XAPK_TO_PROTO_DOTNET")
    if env:
        path = Path(env)
        if path.is_file():
            return path
        return None

    dest = dotnet_cache_dir()
    bundled = dest / "dotnet"
    if bundled.is_file() and has_netcore_runtime(dest, major):
        return bundled

    return _system_dotnet_with_runtime(major)


def install_dotnet(*, force: bool = False, verbose: bool = False) -> Path | None:
    """Install or locate .NET 9.

    Returns the ``dotnet`` path on success. On Windows, never runs the bash
    installer — returns ``None`` when no usable net9 host is present (caller
    treats that as skipped/missing).
    """
    dest = dotnet_cache_dir()
    dotnet_bin = dest / "dotnet"
    if dotnet_bin.is_file() and not force and has_netcore_runtime(dest, "9"):
        return dotnet_bin

    if not force:
        system = _system_dotnet_with_runtime("9")
        if system is not None:
            if verbose:
                print(f"using system dotnet: {system}", flush=True)
            return system

    if platform.system() == "Windows":
        if verbose:
            print(f"skipping .NET auto-install on Windows — {DOTNET_MANUAL_HINT}", flush=True)
        return None

    if force and dest.exists():
        shutil.rmtree(dest)

    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "dotnet-install.sh"
        urlretrieve(DOTNET_INSTALL_URL, script)
        script.chmod(0o755)
        cmd = [
            "bash",
            str(script),
            "--channel",
            DOTNET_CHANNEL,
            "--install-dir",
            str(dest),
        ]
        if verbose:
            print(" ".join(cmd), flush=True)
        subprocess.run(cmd, check=True)

    if not dotnet_bin.is_file():
        raise RuntimeError("dotnet install failed")
    if not has_netcore_runtime(dest, "9"):
        raise RuntimeError(
            f"dotnet install did not provide Microsoft.NETCore.App 9.x under {dest}"
        )
    return dotnet_bin


def install_dumper(*, force: bool = False, verbose: bool = False) -> Path:
    existing = find_dumper_dll(dumper_cache_dir())
    if existing is not None and not force:
        return existing

    if force and dumper_cache_dir().exists():
        shutil.rmtree(dumper_cache_dir())

    url = dumper_url()
    dumper_cache_dir().mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / "dumper.zip"
        if verbose:
            print(f"Downloading {url}", flush=True)
        urlretrieve(url, zip_path)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(dumper_cache_dir())

    dll = find_dumper_dll(dumper_cache_dir())
    if dll is None:
        raise RuntimeError("Il2CppDumper install failed — Il2CppDumper.dll not found in archive")
    return dll


def install_apkeep(*, force: bool = False, verbose: bool = False) -> str | None:
    """Install or locate ``apkeep``.

    On Darwin there are no official release binaries — resolve from PATH (Homebrew)
    and return ``None`` when missing (caller should print the brew hint).
    On Linux/Windows, download the pinned GitHub release asset into the user cache.
    """
    if not force:
        try:
            return resolve_apkeep()
        except FileNotFoundError:
            pass

    if platform.system() == "Darwin":
        exe = find_apkeep_on_path()
        if exe:
            return exe
        return None

    dest = cached_apkeep_path()
    if dest.is_file() and not force:
        return str(dest)

    if force and apkeep_cache_dir().exists():
        shutil.rmtree(apkeep_cache_dir())

    apkeep_cache_dir().mkdir(parents=True, exist_ok=True)
    url = apkeep_url()
    if verbose:
        print(f"Downloading {url}", flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "apkeep-download"
        urlretrieve(url, raw)
        shutil.move(str(raw), str(dest))

    dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    if not dest.is_file():
        raise RuntimeError("apkeep install failed")
    return str(dest)


def _check_dotnet() -> DepResult:
    env = os.environ.get("XAPK_TO_PROTO_DOTNET")
    if env:
        path = Path(env)
        if path.is_file():
            return DepResult(".NET", "ok", str(path))
        return DepResult(
            ".NET",
            "missing",
            f"XAPK_TO_PROTO_DOTNET not found: {env}",
            hint=_dotnet_missing_hint(),
        )

    found = find_dotnet_with_runtime("9")
    if found is not None:
        return DepResult(".NET", "ok", str(found))
    return DepResult(
        ".NET",
        "missing",
        "Microsoft.NETCore.App 9.x not found",
        hint=_dotnet_missing_hint(),
    )


def _check_dumper() -> DepResult:
    try:
        dll = resolve_dumper_dll()
        return DepResult("Il2CppDumper", "ok", str(dll))
    except FileNotFoundError as e:
        return DepResult("Il2CppDumper", "missing", str(e), hint="hoh-protos setup")


def _check_apkeep() -> DepResult:
    try:
        path = resolve_apkeep()
        return DepResult("apkeep", "ok", path)
    except FileNotFoundError as e:
        return DepResult("apkeep", "missing", str(e), hint=_apkeep_missing_hint())


def check_deps() -> list[DepResult]:
    """Resolve-only status for external tools needed by the CLI."""
    return [_check_dotnet(), _check_dumper(), _check_apkeep()]


def print_dep_results(results: list[DepResult]) -> None:
    width = max((len(r.status) for r in results), default=0)
    for r in results:
        line = f"{r.status:<{width}}  {r.name}  {r.detail}"
        print(line, flush=True)
        if r.hint and r.status != "ok":
            print(f"{'':<{width}}  hint: {r.hint}", flush=True)


def any_required_missing(results: list[DepResult]) -> bool:
    return any(r.status in ("missing", "failed") for r in results)


def any_failed(results: list[DepResult]) -> bool:
    return any(r.status == "failed" for r in results)


def setup(*, force: bool = False, verbose: bool = False) -> list[DepResult]:
    """Install each dependency independently; return per-dep status."""
    results: list[DepResult] = []

    try:
        dotnet_bin = install_dotnet(force=force, verbose=verbose)
        if dotnet_bin is not None:
            results.append(DepResult(".NET", "ok", str(dotnet_bin)))
        else:
            results.append(
                DepResult(
                    ".NET",
                    "skipped",
                    "auto-install not supported on Windows",
                    hint=DOTNET_MANUAL_HINT,
                )
            )
    except Exception as e:
        results.append(DepResult(".NET", "failed", str(e), hint=_dotnet_missing_hint()))

    try:
        dll = install_dumper(force=force, verbose=verbose)
        results.append(DepResult("Il2CppDumper", "ok", str(dll)))
    except Exception as e:
        results.append(
            DepResult("Il2CppDumper", "failed", str(e), hint="hoh-protos setup --force")
        )

    try:
        apkeep = install_apkeep(force=force, verbose=verbose)
        if apkeep:
            results.append(DepResult("apkeep", "ok", apkeep))
        else:
            results.append(
                DepResult(
                    "apkeep",
                    "missing",
                    "not found on PATH",
                    hint=_apkeep_missing_hint(),
                )
            )
    except Exception as e:
        results.append(
            DepResult("apkeep", "failed", str(e), hint=_apkeep_missing_hint())
        )

    print_dep_results(results)

    if not any_required_missing(results) and not any(
        r.status == "skipped" for r in results
    ):
        print("", flush=True)
        print("Ready. Example:", flush=True)
        print('  hoh-protos "/path/to/game.xapk" -o ./output', flush=True)
    elif any(r.status == "skipped" for r in results) and not any_failed(results):
        # Windows .NET skipped but other deps may be ok — still useful.
        if not any(r.status in ("missing", "failed") for r in results if r.name != ".NET"):
            print("", flush=True)
            print("Cached tools ready. Install .NET 9 manually before running Il2CppDumper.", flush=True)

    return results
