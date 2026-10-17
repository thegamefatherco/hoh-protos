"""Extract images (and optionally meshes) from Unity Addressables bundles.

The XAPK ships the complete Addressables bundle set under ``assets/aa/<platform>/``
(5,700+ bundles in a 1.48 build), so unpacking works fully offline and does not
depend on the CDN, whose bundle hashes churn between builds. Bundles downloaded
by :mod:`xapk_to_proto.assets` can be unpacked the same way.

Two facts about the bundle layout drive the design:

* A sprite's ``m_Name`` *is* the Addressables address the game data references
  (``icon_gold_ore_3``, ``icon_flat_chat``). The ``container`` paths inside these
  bundles are obfuscated single characters, so names are the only usable key.
* Sprites packed into a SpriteAtlas do not resolve their page through
  ``m_RD.texture`` — UnityPy goes through the atlas render-data map instead. Atlas
  pages are therefore recognised by Unity's ``sactx-`` naming convention rather
  than by reference counting.

Pass ``extract_meshes=True`` (CLI ``--meshes``) to also export raw ``Mesh``
objects as Wavefront OBJ beside the PNGs. Prefab hierarchy / materials are out
of scope; UnityPy only offers OBJ export.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import zipfile
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

try:
    import UnityPy
except ImportError:  # optional extra: pip install hoh-protos[assets]
    UnityPy = None

try:
    from UnityPy.export.MeshExporter import export_mesh as _export_mesh
except ImportError:  # UnityPy missing or too old
    _export_mesh = None

BUNDLE_SUFFIX = ".bundle"
EXTRACTED_DIRNAME = "extracted"
INDEX_FILENAME = "index.json"
IMAGE_TYPES = ("Sprite", "Texture2D")
MESH_TYPE = "Mesh"
INDEX_SCHEMA_VERSION = 2

# Unity names SpriteAtlas pages "sactx-<page>-<w>x<h>-<format>-<atlas>-<hash>".
ATLAS_PAGE_PREFIX = "sactx-"

DEFAULT_JOBS = os.cpu_count() or 4

_BUNDLE_NAME_RE = re.compile(
    r"^(?P<address>.+?)_[0-9a-f]{16}_"
    r"(?:assets|scenes|monoscripts|unitybuiltinassets)_all_[0-9a-f]{32}"
    rf"{re.escape(BUNDLE_SUFFIX)}$"
)
_UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class UnpackError(RuntimeError):
    pass


@dataclass
class ImageRecord:
    bundle: str
    address_prefix: str | None
    object_type: str
    name: str
    path: str
    width: int
    height: int


@dataclass
class MeshRecord:
    bundle: str
    address_prefix: str | None
    object_type: str
    name: str
    path: str
    vertex_count: int


@dataclass
class BundleOutcome:
    """Per-bundle worker result. Kept picklable for the process pool."""

    bundle: str
    records: list[ImageRecord] = field(default_factory=list)
    mesh_records: list[MeshRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unity_version: str = ""
    failed: bool = False


@dataclass
class UnpackResult:
    source: str
    out_dir: Path
    unity_version: str
    bundles_total: int
    bundles_selected: int
    bundles_skipped: int
    bundles_failed: int
    images_written: int
    meshes_written: int = 0
    records: list[ImageRecord] = field(default_factory=list)
    mesh_records: list[MeshRecord] = field(default_factory=list)
    # Bundles with neither images nor meshes — actors, prefabs, scenes. Recorded
    # so an address still resolves to its bundle even when there is nothing to
    # show.
    empty_bundles: list[str] = field(default_factory=list)
    # Bundles scanned with extract_meshes that yielded zero Mesh objects.
    mesh_empty_bundles: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class _PreviousBundle:
    """Resume state for one bundle, split by artifact kind."""

    images: list[ImageRecord] = field(default_factory=list)
    meshes: list[MeshRecord] = field(default_factory=list)
    image_scanned: bool = False
    mesh_scanned: bool = False


def require_unitypy() -> None:
    if UnityPy is None:
        raise UnpackError(
            "UnityPy is required to unpack asset bundles — "
            "install it with: pip install 'hoh-protos[assets]'"
        )


def bundle_address_prefix(name: str) -> str | None:
    """Recover the Addressables address a bundle was built from.

    Returns ``None`` for content-hash-named bundles (``<32hex>_monoscripts_…``),
    which carry no address.
    """
    match = _BUNDLE_NAME_RE.match(name)
    return match.group("address") if match else None


def safe_filename(name: str) -> str:
    cleaned = _UNSAFE_FILENAME.sub("_", name).strip().strip(".")
    return cleaned or "unnamed"


def assign_filenames(names: list[str]) -> list[str]:
    """Sanitise names and disambiguate collisions within one bundle."""
    used: dict[str, int] = {}
    out: list[str] = []
    for name in names:
        base = safe_filename(name)
        key = base.lower()
        seen = used.get(key, 0)
        used[key] = seen + 1
        out.append(base if seen == 0 else f"{base}__{seen + 1}")
    return out


def select_image_objects(
    candidates: list[tuple[str, str]],
    *,
    include_atlas_textures: bool = False,
) -> list[int]:
    """Pick which ``(type_name, object_name)`` pairs are worth exporting.

    Sprites always win: a ``Texture2D`` sharing a sprite's name is the untrimmed
    source of that sprite, and ``sactx-`` textures are atlas sheets holding
    hundreds of already-exported slices.
    """
    sprite_names = {
        name.lower() for type_name, name in candidates if type_name == "Sprite"
    }
    keep: list[int] = []
    for i, (type_name, name) in enumerate(candidates):
        if type_name not in IMAGE_TYPES:
            continue
        redundant = name.startswith(ATLAS_PAGE_PREFIX) or name.lower() in sprite_names
        if type_name == "Texture2D" and not include_atlas_textures and redundant:
            continue
        keep.append(i)
    return keep


def is_empty_texture(tex: object) -> bool:
    """True for stub Texture2Ds that would make UnityPy chase cwd as a resource.

    TMP font atlases often ship 0x0 Texture2Ds with empty ``image_data`` and an
    empty ``m_StreamData.path``. UnityPy then calls ``load_file("")`` against
    ``Environment.path`` (cwd) and raises ``IsADirectoryError``.
    """
    if not getattr(tex, "m_Width", 0) or not getattr(tex, "m_Height", 0):
        return True
    image_data = getattr(tex, "image_data", None) or b""
    if image_data:
        return False
    sd = getattr(tex, "m_StreamData", None)
    if sd is None:
        return True
    path = getattr(sd, "path", None) or ""
    size = getattr(sd, "size", 0) or 0
    return not path or not size


def unpack_bundle(
    data: bytes,
    bundle_name: str,
    out_root: Path,
    *,
    include_atlas_textures: bool = False,
    extract_meshes: bool = False,
) -> BundleOutcome:
    """Decode selected images (and optionally meshes) into ``out_root/<address>/``."""
    require_unitypy()
    outcome = BundleOutcome(bundle=bundle_name)
    try:
        env = UnityPy.load(data)
    except Exception as e:  # noqa: BLE001 - UnityPy raises arbitrary parse errors
        outcome.failed = True
        outcome.warnings.append(f"{bundle_name}: load failed: {type(e).__name__}: {e}")
        return outcome

    image_objects = []
    image_candidates: list[tuple[str, str]] = []
    mesh_objects = []
    mesh_names: list[str] = []
    for obj in env.objects:
        type_name = obj.type.name
        if type_name not in IMAGE_TYPES and not (
            extract_meshes and type_name == MESH_TYPE
        ):
            continue
        outcome.unity_version = obj.assets_file.unity_version
        try:
            name = obj.peek_name() or ""
        except Exception:  # noqa: BLE001 - an unnamed object is still exportable
            name = ""
        if type_name in IMAGE_TYPES:
            image_objects.append(obj)
            image_candidates.append((type_name, name))
        else:
            mesh_objects.append(obj)
            mesh_names.append(name)

    keep = select_image_objects(
        image_candidates, include_atlas_textures=include_atlas_textures
    )

    address = bundle_address_prefix(bundle_name)
    sub = address or bundle_name[: -len(BUNDLE_SUFFIX)]
    dest_dir = out_root / EXTRACTED_DIRNAME / safe_filename(sub)

    if keep:
        filenames = assign_filenames([image_candidates[i][1] for i in keep])
        for i, filename in zip(keep, filenames):
            obj = image_objects[i]
            type_name = image_candidates[i][0]
            try:
                parsed = obj.parse_as_object()
                if type_name == "Texture2D" and is_empty_texture(parsed):
                    outcome.warnings.append(
                        f"{bundle_name}: {type_name} {image_candidates[i][1]!r}: "
                        "empty texture"
                    )
                    continue
                image = parsed.image
            except Exception as e:  # noqa: BLE001 - one bad texture must not stop the run
                outcome.warnings.append(
                    f"{bundle_name}: {type_name} {image_candidates[i][1]!r}: "
                    f"decode failed: {type(e).__name__}: {e}"
                )
                continue
            dest = dest_dir / f"{filename}.png"
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                image.save(dest)
            except Exception as e:  # noqa: BLE001 - Pillow raises per-format errors
                outcome.warnings.append(
                    f"{bundle_name}: {image_candidates[i][1]!r}: write failed: {e}"
                )
                continue
            outcome.records.append(
                ImageRecord(
                    bundle=bundle_name,
                    address_prefix=address,
                    object_type=type_name,
                    name=image_candidates[i][1],
                    path=dest.relative_to(out_root).as_posix(),
                    width=image.width,
                    height=image.height,
                )
            )

    if extract_meshes and mesh_objects:
        if _export_mesh is None:
            outcome.warnings.append(
                f"{bundle_name}: MeshExporter unavailable; skipping meshes"
            )
            return outcome
        mesh_filenames = assign_filenames(mesh_names)
        for obj, name, filename in zip(mesh_objects, mesh_names, mesh_filenames):
            try:
                parsed = obj.parse_as_object()
                obj_text = _export_mesh(parsed, format="obj")
            except Exception as e:  # noqa: BLE001 - one bad mesh must not stop the run
                outcome.warnings.append(
                    f"{bundle_name}: Mesh {name!r}: "
                    f"export failed: {type(e).__name__}: {e}"
                )
                continue
            if not obj_text:
                outcome.warnings.append(
                    f"{bundle_name}: Mesh {name!r}: empty mesh"
                )
                continue
            dest = dest_dir / f"{filename}.obj"
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                dest.write_text(obj_text, encoding="utf-8")
            except Exception as e:  # noqa: BLE001 - filesystem errors
                outcome.warnings.append(
                    f"{bundle_name}: Mesh {name!r}: write failed: {e}"
                )
                continue
            vertex_count = sum(
                1 for line in obj_text.splitlines() if line.startswith("v ")
            )
            outcome.mesh_records.append(
                MeshRecord(
                    bundle=bundle_name,
                    address_prefix=address,
                    object_type=MESH_TYPE,
                    name=name,
                    path=dest.relative_to(out_root).as_posix(),
                    vertex_count=vertex_count,
                )
            )
    return outcome


class BundleSource:
    """A collection of bundles addressable by filename."""

    def names(self) -> list[str]:
        raise NotImplementedError

    def read(self, name: str) -> bytes:
        raise NotImplementedError

    def describe(self) -> str:
        raise NotImplementedError


@dataclass
class DirectorySource(BundleSource):
    directory: Path

    def names(self) -> list[str]:
        return sorted(p.name for p in self.directory.glob(f"*{BUNDLE_SUFFIX}"))

    def read(self, name: str) -> bytes:
        return (self.directory / name).read_bytes()

    def describe(self) -> str:
        return str(self.directory.resolve())


@dataclass
class XapkSource(BundleSource):
    """Streams bundles out of the asset-pack APK nested inside an XAPK.

    Zip handles are opened lazily and dropped when pickled so each pool worker
    builds its own, avoiding a ~900 MB unpack and cross-process handle sharing.
    """

    xapk: Path
    inner_apk: str
    members: dict[str, str]

    def __post_init__(self) -> None:
        self._outer: zipfile.ZipFile | None = None
        self._inner: zipfile.ZipFile | None = None

    def __getstate__(self) -> dict:
        return {"xapk": self.xapk, "inner_apk": self.inner_apk, "members": self.members}

    def __setstate__(self, state: dict) -> None:
        self.__dict__.update(state)
        self._outer = None
        self._inner = None

    def names(self) -> list[str]:
        return sorted(self.members)

    def read(self, name: str) -> bytes:
        if self._inner is None:
            self._outer = zipfile.ZipFile(self.xapk)
            self._inner = zipfile.ZipFile(self._outer.open(self.inner_apk))
        return self._inner.read(self.members[name])

    def describe(self) -> str:
        return f"{self.xapk.resolve()}!{self.inner_apk}"


def find_local_bundle_dir(root: Path) -> Path:
    """Locate the Addressables bundle directory in an extracted XAPK tree."""
    candidates = sorted(
        p
        for p in root.glob("**/assets/aa/*")
        if p.is_dir() and next(p.glob(f"*{BUNDLE_SUFFIX}"), None) is not None
    )
    if not candidates:
        raise FileNotFoundError(
            f"no Addressables bundle directory (assets/aa/<platform>) under {root}"
        )
    return candidates[0]


def xapk_bundle_source(xapk: Path) -> XapkSource:
    """Build a source over the ``assets/aa/**.bundle`` members inside an XAPK."""
    if not xapk.is_file():
        raise FileNotFoundError(f"xapk not found: {xapk}")

    with zipfile.ZipFile(xapk) as outer:
        inner_names = [n for n in outer.namelist() if n.endswith(".apk")]
        inner_names.sort(key=lambda n: "addressables" not in n.lower())
        for inner_name in inner_names:
            with outer.open(inner_name) as fh:
                try:
                    inner = zipfile.ZipFile(fh)
                except (zipfile.BadZipFile, OSError):
                    continue
                with inner:
                    members = {
                        m.rsplit("/", 1)[-1]: m
                        for m in inner.namelist()
                        if m.endswith(BUNDLE_SUFFIX) and "/aa/" in m
                    }
                if members:
                    return XapkSource(xapk=xapk, inner_apk=inner_name, members=members)

    raise FileNotFoundError(f"no assets/aa/**.bundle members found in {xapk}")


def select_bundles(
    names: list[str],
    *,
    only: tuple[str, ...] = (),
    skip: tuple[str, ...] = (),
) -> list[str]:
    """Filter bundle names. ``only`` takes precedence and disables ``skip``."""
    if only:
        return [n for n in names if any(term in n for term in only)]
    return [n for n in names if not n.startswith(tuple(skip))] if skip else list(names)


_WORKER_SOURCE: BundleSource | None = None
_WORKER_OUT: Path | None = None
_WORKER_ATLAS = False
_WORKER_MESHES = False


def _init_worker(
    source: BundleSource, out_root: Path, atlas: bool, meshes: bool = False
) -> None:
    global _WORKER_SOURCE, _WORKER_OUT, _WORKER_ATLAS, _WORKER_MESHES
    _WORKER_SOURCE = source
    _WORKER_OUT = out_root
    _WORKER_ATLAS = atlas
    _WORKER_MESHES = meshes


def _unpack_worker(name: str) -> BundleOutcome:
    assert _WORKER_SOURCE is not None and _WORKER_OUT is not None
    try:
        data = _WORKER_SOURCE.read(name)
    except Exception as e:  # noqa: BLE001 - report the bundle, keep the pool alive
        return BundleOutcome(
            bundle=name, failed=True, warnings=[f"{name}: read failed: {e}"]
        )
    return unpack_bundle(
        data,
        name,
        _WORKER_OUT,
        include_atlas_textures=_WORKER_ATLAS,
        extract_meshes=_WORKER_MESHES,
    )


def _index_has_mesh_capability(data: dict) -> bool:
    if data.get("schema_version", 1) >= 2:
        return True
    capabilities = data.get("capabilities") or []
    if "meshes" in capabilities:
        return True
    return "mesh_records" in data or "mesh_empty_bundles" in data


def _load_previous_state(out_dir: Path) -> dict[str, _PreviousBundle]:
    """Read an existing index so an interrupted run can resume per bundle."""
    index_path = out_dir / INDEX_FILENAME
    if not index_path.is_file():
        return {}
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}

    done: dict[str, _PreviousBundle] = {}
    for raw in data.get("records", []):
        try:
            record = ImageRecord(**raw)
        except TypeError:
            return {}
        # Legacy Windows indexes used backslashes; Path joins treat those as one
        # component on POSIX, so normalize before the existence check and keep
        # the POSIX form so the next write_index stays consistent.
        record.path = record.path.replace("\\", "/")
        if not (out_dir / record.path).is_file():
            continue
        entry = done.setdefault(record.bundle, _PreviousBundle())
        entry.images.append(record)
        entry.image_scanned = True

    for raw in data.get("mesh_records", []):
        try:
            record = MeshRecord(**raw)
        except TypeError:
            return {}
        record.path = record.path.replace("\\", "/")
        if not (out_dir / record.path).is_file():
            continue
        entry = done.setdefault(record.bundle, _PreviousBundle())
        entry.meshes.append(record)
        entry.mesh_scanned = True

    for bundle in data.get("empty_bundles", []):
        entry = done.setdefault(bundle, _PreviousBundle())
        entry.image_scanned = True

    for bundle in data.get("mesh_empty_bundles", []):
        entry = done.setdefault(bundle, _PreviousBundle())
        entry.mesh_scanned = True

    # Legacy image-only indexes never scanned for meshes; leave mesh_scanned False
    # so a later --meshes run re-queues those bundles.
    if not _index_has_mesh_capability(data):
        for entry in done.values():
            entry.mesh_scanned = False
    else:
        # unpack_bundle always scans images when it scans meshes, so a mesh-only
        # (or mesh-empty) bundle is also image-complete.
        for entry in done.values():
            if entry.mesh_scanned or entry.meshes:
                entry.image_scanned = True

    return done


def _load_previous_records(out_dir: Path) -> dict[str, list[ImageRecord]]:
    """Compatibility shim: image records only (tests + callers expecting the old shape)."""
    return {
        name: entry.images
        for name, entry in _load_previous_state(out_dir).items()
        if entry.image_scanned
    }


def _bundle_is_done(entry: _PreviousBundle, *, extract_meshes: bool) -> bool:
    if not entry.image_scanned:
        return False
    if extract_meshes and not entry.mesh_scanned:
        return False
    return True


def unpack_all(
    source: BundleSource,
    out_dir: Path,
    *,
    only: tuple[str, ...] = (),
    skip: tuple[str, ...] = (),
    jobs: int = DEFAULT_JOBS,
    include_atlas_textures: bool = False,
    extract_meshes: bool = False,
    clean: bool = False,
    dry_run: bool = False,
    verbose: bool = False,
) -> UnpackResult:
    """Unpack every selected bundle in ``source`` into ``out_dir``."""
    require_unitypy()
    names = source.names()
    if not names:
        raise ValueError(f"no bundles found in {source.describe()}")

    selected = select_bundles(names, only=only, skip=skip)
    result = UnpackResult(
        source=source.describe(),
        out_dir=out_dir,
        unity_version="",
        bundles_total=len(names),
        bundles_selected=len(selected),
        bundles_skipped=0,
        bundles_failed=0,
        images_written=0,
    )
    if not selected:
        result.warnings.append("filters matched no bundles")
        return result

    if dry_run:
        for name in selected:
            print(name, flush=True)
        return result

    if clean and out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    previous = {} if clean else _load_previous_state(out_dir)
    empty_bundles: list[str] = []
    mesh_empty_bundles: list[str] = []
    pending: list[str] = []
    for name in selected:
        entry = previous.get(name)
        if entry is not None and _bundle_is_done(entry, extract_meshes=extract_meshes):
            result.bundles_skipped += 1
            result.records.extend(entry.images)
            result.mesh_records.extend(entry.meshes)
            if not entry.images and not entry.meshes:
                empty_bundles.append(name)
            if extract_meshes and not entry.meshes:
                mesh_empty_bundles.append(name)
            elif not extract_meshes and entry.mesh_scanned and not entry.meshes:
                mesh_empty_bundles.append(name)
        else:
            pending.append(name)

    if result.bundles_skipped:
        print(f"  {result.bundles_skipped} already unpacked", flush=True)

    total = len(pending)
    for done, outcome in enumerate(
        _iter_outcomes(
            pending,
            source,
            out_dir,
            jobs=jobs,
            atlas=include_atlas_textures,
            meshes=extract_meshes,
        ),
        start=1,
    ):
        if outcome.failed:
            result.bundles_failed += 1
        if outcome.unity_version and not result.unity_version:
            result.unity_version = outcome.unity_version
        result.records.extend(outcome.records)
        result.warnings.extend(outcome.warnings)

        meshes = list(outcome.mesh_records)
        if not extract_meshes:
            # Preserve mesh artifacts from a prior --meshes run when re-scanning
            # for images only.
            prior = previous.get(outcome.bundle)
            if prior is not None and prior.meshes:
                meshes = list(prior.meshes)
                if prior.mesh_scanned and not prior.meshes:
                    mesh_empty_bundles.append(outcome.bundle)
        result.mesh_records.extend(meshes)

        if not outcome.failed:
            if not outcome.records and not meshes:
                empty_bundles.append(outcome.bundle)
            if extract_meshes and not outcome.mesh_records:
                mesh_empty_bundles.append(outcome.bundle)

        if verbose:
            print(
                f"  [{done}/{total}] {outcome.bundle} "
                f"({len(outcome.records)} image(s), {len(meshes)} mesh(es))",
                flush=True,
            )
        elif done % 250 == 0:
            print(f"  {done}/{total} bundles", flush=True)

    result.images_written = len(result.records)
    result.meshes_written = len(result.mesh_records)
    result.empty_bundles = empty_bundles
    result.mesh_empty_bundles = mesh_empty_bundles
    if result.bundles_failed:
        result.warnings.append(f"{result.bundles_failed} bundle(s) failed to load")
    return result


def _iter_outcomes(
    pending: list[str],
    source: BundleSource,
    out_dir: Path,
    *,
    jobs: int,
    atlas: bool,
    meshes: bool = False,
):
    if not pending:
        return
    pool = None
    if jobs > 1:
        try:
            pool = ProcessPoolExecutor(
                max_workers=jobs,
                initializer=_init_worker,
                initargs=(source, out_dir, atlas, meshes),
            )
        except (OSError, NotImplementedError, ValueError) as e:
            # Sandboxes and containers can forbid the semaphores a pool needs.
            print(f"  process pool unavailable ({e}); unpacking serially", flush=True)

    if pool is None:
        _init_worker(source, out_dir, atlas, meshes)
        for name in pending:
            yield _unpack_worker(name)
        return

    with pool:
        yield from pool.map(_unpack_worker, pending, chunksize=8)


def build_index(result: UnpackResult) -> dict:
    """Build the sidecar index: name lookup for sprites, prefix lookup for the rest."""
    by_name: dict[str, list[str]] = {}
    by_mesh_name: dict[str, list[str]] = {}
    by_bundle_prefix: dict[str, dict] = {}

    def _prefix_entry(address: str, bundle: str) -> dict:
        return by_bundle_prefix.setdefault(
            address.lower(),
            {"bundle": bundle, "images": [], "meshes": []},
        )

    for record in result.records:
        if record.name:
            by_name.setdefault(record.name.lower(), []).append(record.path)
        if record.address_prefix:
            entry = _prefix_entry(record.address_prefix, record.bundle)
            entry["images"].append(record.path)

    for record in result.mesh_records:
        if record.name:
            by_mesh_name.setdefault(record.name.lower(), []).append(record.path)
        if record.address_prefix:
            entry = _prefix_entry(record.address_prefix, record.bundle)
            entry["meshes"].append(record.path)

    for bundle in result.empty_bundles:
        prefix = bundle_address_prefix(bundle)
        if prefix:
            _prefix_entry(prefix, bundle)

    for bundle in result.mesh_empty_bundles:
        prefix = bundle_address_prefix(bundle)
        if prefix:
            _prefix_entry(prefix, bundle)

    # Legacy consumers expect images-only entries; keep meshes key always present.
    for entry in by_bundle_prefix.values():
        entry.setdefault("images", [])
        entry.setdefault("meshes", [])

    duplicates = {name: paths for name, paths in by_name.items() if len(paths) > 1}
    return {
        "schema_version": INDEX_SCHEMA_VERSION,
        "capabilities": ["images", "meshes"],
        "source": result.source,
        "unity_version": result.unity_version,
        "bundles_total": result.bundles_total,
        "bundles_selected": result.bundles_selected,
        "bundles_skipped": result.bundles_skipped,
        "bundles_failed": result.bundles_failed,
        "images": result.images_written,
        "meshes": result.meshes_written,
        "duplicate_names": len(duplicates),
        "by_name": {k: sorted(v) for k, v in sorted(by_name.items())},
        "by_mesh_name": {k: sorted(v) for k, v in sorted(by_mesh_name.items())},
        "by_bundle_prefix": dict(sorted(by_bundle_prefix.items())),
        "records": [asdict(r) for r in result.records],
        "mesh_records": [asdict(r) for r in result.mesh_records],
        "empty_bundles": sorted(result.empty_bundles),
        "mesh_empty_bundles": sorted(set(result.mesh_empty_bundles)),
        "warnings": result.warnings[:200],
    }


def write_index(result: UnpackResult) -> Path:
    path = result.out_dir / INDEX_FILENAME
    path.write_text(
        json.dumps(build_index(result), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def run_unpack(
    *,
    xapk: Path | None = None,
    bundles: Path | None = None,
    out_dir: Path,
    only: tuple[str, ...] = (),
    skip: tuple[str, ...] = (),
    jobs: int = DEFAULT_JOBS,
    include_atlas_textures: bool = False,
    extract_meshes: bool = False,
    clean: bool = False,
    dry_run: bool = False,
    verbose: bool = False,
) -> UnpackResult:
    """Unpack bundles from an XAPK or a directory and write ``index.json``."""
    if bundles is not None:
        if not bundles.is_dir():
            raise FileNotFoundError(f"bundle directory not found: {bundles}")
        source: BundleSource = DirectorySource(bundles)
    elif xapk is not None:
        source = xapk_bundle_source(xapk)
    else:
        raise ValueError("either an xapk or a bundle directory is required")

    result = unpack_all(
        source,
        out_dir,
        only=only,
        skip=skip,
        jobs=jobs,
        include_atlas_textures=include_atlas_textures,
        extract_meshes=extract_meshes,
        clean=clean,
        dry_run=dry_run,
        verbose=verbose,
    )
    if not dry_run:
        write_index(result)
    return result
