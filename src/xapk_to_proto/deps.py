"""Cache and resolve external dependencies (dotnet, Il2CppDumper)."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import zipfile
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


def cache_dir() -> Path:
    return Path(platformdirs.user_cache_dir("hoh-protos"))


def dotnet_cache_dir() -> Path:
    return cache_dir() / "dotnet"


def dumper_cache_dir() -> Path:
    return cache_dir() / "Il2CppDumper"


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


def install_dotnet(*, force: bool = False, verbose: bool = False) -> Path:
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


def setup(*, force: bool = False, verbose: bool = False) -> None:
    print("==> .NET runtime", flush=True)
    dotnet_bin = install_dotnet(force=force, verbose=verbose)
    print(f"  {dotnet_bin}", flush=True)

    print("==> Il2CppDumper", flush=True)
    dll = install_dumper(force=force, verbose=verbose)
    print(f"  {dll}", flush=True)

    print("", flush=True)
    print("Ready. Example:", flush=True)
    print('  hoh-protos "/path/to/game.xapk" -o ./output', flush=True)
