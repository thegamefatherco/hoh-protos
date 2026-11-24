"""Stage an AssetRipper-ready input folder from DummyDll + asset bundles.

AssetRipper only rebuilds real MonoBehaviour scripts when the managed
assemblies are imported together with the bundles; otherwise it reports the
``Unknown`` scripting backend and emits dummy classes. The staged layout is::

    {out}/Managed/*.dll      # copied from il2cpp/DummyDll
    {out}/bundles/*.bundle   # symlinked (or copied) Addressables bundles

Drag both subfolders into AssetRipper at the same time.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from xapk_to_proto.dumper import list_dummy_dlls

MANAGED_DIRNAME = "Managed"
BUNDLES_DIRNAME = "bundles"
BUNDLE_SUFFIX = ".bundle"


class StageError(RuntimeError):
    pass


@dataclass(frozen=True)
class StageResult:
    out_dir: Path
    managed_dir: Path
    bundles_dir: Path
    assemblies: int
    bundles: int
    linked: bool


def _reset_dir(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    path.mkdir(parents=True)


def _try_symlink(src: Path, dest: Path) -> bool:
    try:
        os.symlink(os.path.relpath(src, dest.parent), dest)
    except (OSError, NotImplementedError):
        return False
    return True


def stage_input(
    *,
    il2cpp_dir: Path,
    bundles_dir: Path,
    out_dir: Path,
    link_bundles: bool = True,
) -> StageResult:
    """Copy DummyDll assemblies and link bundles under ``out_dir``.

    ``il2cpp_dir`` is the Il2CppDumper output folder (contains ``DummyDll/``).
    Bundles are symlinked when ``link_bundles`` is set and the platform allows
    it; otherwise they are copied. Pass ``link_bundles=False`` when the source
    directory is scratch space that will be deleted after the run.
    """
    dlls = list_dummy_dlls(il2cpp_dir)
    if not dlls:
        raise StageError(
            f"no assemblies in {il2cpp_dir / 'DummyDll'} — Il2CppDumper did not "
            "export DummyDll (see the dumper warning above; run `hoh-protos "
            "setup --force` to install the patched dumper). AssetRipper would "
            "only produce dummy scripts without them."
        )
    if not bundles_dir.is_dir():
        raise StageError(
            f"bundle directory not found: {bundles_dir} "
            "(run with --assets to download Addressables bundles first)"
        )
    bundles = sorted(p for p in bundles_dir.glob(f"*{BUNDLE_SUFFIX}") if p.is_file())
    if not bundles:
        raise StageError(f"no *{BUNDLE_SUFFIX} files under {bundles_dir}")

    out_dir = out_dir.resolve()
    managed = out_dir / MANAGED_DIRNAME
    staged_bundles = out_dir / BUNDLES_DIRNAME
    _reset_dir(managed)
    _reset_dir(staged_bundles)

    for dll in dlls:
        shutil.copy2(dll, managed / dll.name)

    linked = link_bundles
    for bundle in bundles:
        dest = staged_bundles / bundle.name
        if linked and _try_symlink(bundle.resolve(), dest):
            continue
        linked = False
        shutil.copy2(bundle, dest)

    return StageResult(
        out_dir=out_dir,
        managed_dir=managed,
        bundles_dir=staged_bundles,
        assemblies=len(dlls),
        bundles=len(bundles),
        linked=linked,
    )
