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

DEFAULT_DUMPER_VERSION = "v6.7.46"
DOTNET_INSTALL_URL = "https://dot.net/v1/dotnet-install.sh"


def cache_dir() -> Path:
    return Path(platformdirs.user_cache_dir("hoh-protos"))


def dotnet_cache_dir() -> Path:
    return cache_dir() / "dotnet"


def dumper_cache_dir() -> Path:
    return cache_dir() / "Il2CppDumper"


def dumper_version() -> str:
    return os.environ.get("IL2CPP_DUMPER_VERSION", DEFAULT_DUMPER_VERSION)


def dumper_url() -> str:
    version = dumper_version()
    return (
        f"https://github.com/Perfare/Il2CppDumper/releases/download/"
        f"{version}/Il2CppDumper-net6-{version}.zip"
    )


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

    dll = dumper_cache_dir() / "Il2CppDumper.dll"
    if dll.is_file():
        return dll

    raise FileNotFoundError("Il2CppDumper not found. Run: hoh-protos setup")


def install_dotnet(*, force: bool = False, verbose: bool = False) -> Path:
    dest = dotnet_cache_dir()
    dotnet_bin = dest / "dotnet"
    if dotnet_bin.is_file() and not force:
        return dotnet_bin

    if force and dest.exists():
        shutil.rmtree(dest)

    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "dotnet-install.sh"
        urlretrieve(DOTNET_INSTALL_URL, script)
        script.chmod(0o755)
        cmd = ["bash", str(script), "--channel", "8.0", "--install-dir", str(dest)]
        if verbose:
            print(" ".join(cmd), flush=True)
        subprocess.run(cmd, check=True)

    if not dotnet_bin.is_file():
        raise RuntimeError("dotnet install failed")
    return dotnet_bin


def install_dumper(*, force: bool = False, verbose: bool = False) -> Path:
    dll = dumper_cache_dir() / "Il2CppDumper.dll"
    if dll.is_file() and not force:
        return dll

    if force and dumper_cache_dir().exists():
        shutil.rmtree(dumper_cache_dir())

    url = dumper_url()
    dumper_cache_dir().mkdir(parents=True, exist_ok=True)
    zip_path = Path(tempfile.mkstemp(suffix=".zip")[1])
    try:
        if verbose:
            print(f"Downloading {url}", flush=True)
        urlretrieve(url, zip_path)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(dumper_cache_dir())
    finally:
        zip_path.unlink(missing_ok=True)

    if not dll.is_file():
        raise RuntimeError("Il2CppDumper install failed")
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
