"""Download Addressables asset bundles listed in the game's catalog.

The Addressables ``catalog.bin`` shipped inside the XAPK stores bundle filenames
as length-prefixed strings with no host information, so bundle names are
recovered by scanning for ``.bundle`` terminators and walking back to the
preceding length prefix. The CDN root is therefore a constant rather than
something the catalog can tell us; ``bundles/WebGL/`` serves the Android bundles
too (``bundles/Android/`` returns 404).
"""

from __future__ import annotations

import gzip
import json
import shutil
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

CDN_ROOT = "https://heczz.innogamescdn.com/bundles/WebGL/"
CATALOG_MEMBER = "assets/aa/catalog.bin"
DEFAULT_SKIP: tuple[str, ...] = ("vfx", "pfx")
DEFAULT_JOBS = 10
DEFAULT_RETRIES = 2

USER_AGENT = "Mozilla/5.0"

_START_MARKER = bytes.fromhex("000000")
_END_MARKER = b".bundle"
_CHUNK = 1 << 16

# 404 dominates when a catalog is stale: the CDN only keeps bundle hashes for
# recent builds. Those are permanent, so only transient statuses are retried.
_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class DownloadError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass
class AssetsResult:
    catalog_source: str
    cdn_root: str
    out_dir: Path
    total_bundles: int
    selected: int
    downloaded: int
    skipped_existing: int
    failed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def parse_catalog(data: bytes) -> list[str]:
    """Extract bundle filenames from raw catalog bytes, in catalog order."""
    names: list[str] = []
    search_start = 0
    while (end := data.find(_END_MARKER, search_start)) != -1:
        start = data.rfind(_START_MARKER, 0, end)
        if start != -1:
            name = data[start + len(_START_MARKER) : end + len(_END_MARKER)].decode(
                "utf-8", errors="ignore"
            )
            names.append(name)
        search_start = end + len(_END_MARKER)
    return list(dict.fromkeys(names))


def find_catalog(root: Path) -> Path:
    """Locate ``catalog.bin`` inside an already-extracted XAPK tree."""
    matches = sorted(root.glob(f"**/{CATALOG_MEMBER}"))
    if not matches:
        raise FileNotFoundError(
            f"{CATALOG_MEMBER} not found under {root} — "
            "the build may not ship an Addressables asset pack"
        )
    return matches[0]


def read_catalog_from_xapk(xapk: Path) -> bytes:
    """Read ``catalog.bin`` out of an XAPK without unpacking it.

    The asset pack APKs are stored uncompressed inside the XAPK, so the nested
    archive can be read through a seekable member stream.
    """
    if not xapk.is_file():
        raise FileNotFoundError(f"xapk not found: {xapk}")

    with zipfile.ZipFile(xapk) as outer:
        inner_names = [n for n in outer.namelist() if n.endswith(".apk")]
        # Addressables pack first; the catalog lives there in every build seen so far.
        inner_names.sort(key=lambda n: "addressables" not in n.lower())
        for inner_name in inner_names:
            with outer.open(inner_name) as fh:
                try:
                    inner = zipfile.ZipFile(fh)
                except (zipfile.BadZipFile, OSError):
                    continue
                with inner:
                    member = next(
                        (n for n in inner.namelist() if n.endswith(CATALOG_MEMBER)),
                        None,
                    )
                    if member is not None:
                        return inner.read(member)

    raise FileNotFoundError(f"{CATALOG_MEMBER} not found in {xapk}")


def read_catalog(catalog: Path | None, xapk: Path | None) -> tuple[bytes, str]:
    """Load catalog bytes from an explicit file or from inside an XAPK."""
    if catalog is not None:
        if not catalog.is_file():
            raise FileNotFoundError(f"catalog not found: {catalog}")
        return catalog.read_bytes(), str(catalog.resolve())
    if xapk is not None:
        return read_catalog_from_xapk(xapk), f"{xapk.resolve()}!{CATALOG_MEMBER}"
    raise ValueError("either a catalog file or an xapk is required")


def select_bundles(
    names: list[str],
    *,
    only: tuple[str, ...] = (),
    skip: tuple[str, ...] = DEFAULT_SKIP,
) -> list[str]:
    """Filter bundle names. ``only`` takes precedence and disables ``skip``."""
    if only:
        return [n for n in names if any(term in _basename(n) for term in only)]
    return [n for n in names if not _basename(n).startswith(tuple(skip))]


def _basename(name: str) -> str:
    return name.rsplit("/", 1)[-1]


def bundle_url(name: str, cdn_root: str = CDN_ROOT) -> str:
    return cdn_root + name


def _download_one(url: str, dest: Path) -> int:
    """Fetch one bundle through a temp file so partials never look complete."""
    req = Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"},
    )
    tmp = dest.with_name(dest.name + ".part")
    try:
        with urlopen(req) as resp:
            expected = resp.headers.get("Content-Length")
            body = (
                gzip.GzipFile(fileobj=resp)
                if resp.headers.get("Content-Encoding") == "gzip"
                else resp
            )
            written = 0
            with tmp.open("wb") as fh:
                while chunk := body.read(_CHUNK):
                    fh.write(chunk)
                    written += len(chunk)
            if expected is not None and written != int(expected):
                raise DownloadError(
                    f"{url}: truncated: {written} of {expected} bytes",
                    retryable=True,
                )
        tmp.replace(dest)
        return written
    except DownloadError:
        tmp.unlink(missing_ok=True)
        raise
    except HTTPError as e:
        tmp.unlink(missing_ok=True)
        raise DownloadError(
            f"{url}: HTTP {e.code}", retryable=e.code in _RETRYABLE_STATUS
        ) from e
    except (URLError, OSError) as e:
        tmp.unlink(missing_ok=True)
        raise DownloadError(f"{url}: {e}", retryable=True) from e


def download_assets(
    *,
    catalog: Path | None = None,
    xapk: Path | None = None,
    out_dir: Path,
    cdn_root: str = CDN_ROOT,
    only: tuple[str, ...] = (),
    skip: tuple[str, ...] = DEFAULT_SKIP,
    jobs: int = DEFAULT_JOBS,
    retries: int = DEFAULT_RETRIES,
    clean: bool = False,
    dry_run: bool = False,
    verbose: bool = False,
) -> AssetsResult:
    """Parse the catalog and download every selected bundle into ``out_dir``."""
    data, source = read_catalog(catalog, xapk)
    names = parse_catalog(data)
    if not names:
        raise ValueError(f"no bundle names found in catalog: {source}")

    selected = select_bundles(names, only=only, skip=skip)
    warnings: list[str] = []
    if not selected:
        warnings.append("filters matched no bundles")

    result = AssetsResult(
        catalog_source=source,
        cdn_root=cdn_root,
        out_dir=out_dir,
        total_bundles=len(names),
        selected=len(selected),
        downloaded=0,
        skipped_existing=0,
        warnings=warnings,
    )

    if dry_run:
        for name in selected:
            print(bundle_url(name, cdn_root), flush=True)
        return result

    if clean and out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    pending: list[str] = []
    for name in selected:
        dest = out_dir / _basename(name)
        if dest.is_file() and dest.stat().st_size > 0:
            result.skipped_existing += 1
        else:
            pending.append(name)

    if result.skipped_existing:
        print(f"  {result.skipped_existing} already present", flush=True)

    errors = _run_downloads(
        pending,
        out_dir,
        cdn_root=cdn_root,
        jobs=jobs,
        retries=retries,
        verbose=verbose,
        result=result,
    )

    result.failed = sorted(errors)
    if errors:
        result.warnings.append(_summarize_errors(errors))
    return result


def _summarize_errors(errors: dict[str, str]) -> str:
    """Group failures by reason so a stale catalog does not print 10k lines."""
    reasons = Counter(message.rsplit(": ", 1)[-1] for message in errors.values())
    detail = ", ".join(f"{reason} ({count})" for reason, count in reasons.most_common())
    return f"{len(errors)} bundle(s) failed: {detail}"


def _run_downloads(
    names: list[str],
    out_dir: Path,
    *,
    cdn_root: str,
    jobs: int,
    retries: int,
    verbose: bool,
    result: AssetsResult,
) -> dict[str, str]:
    remaining = list(names)
    total = len(remaining)
    errors: dict[str, str] = {}

    for attempt in range(1, max(retries, 1) + 1):
        if not remaining:
            break
        if attempt > 1:
            print(
                f"  retry {attempt - 1}: {len(remaining)} bundle(s)",
                flush=True,
            )
        retryable: list[str] = []
        with ThreadPoolExecutor(max_workers=max(jobs, 1)) as pool:
            futures = {
                pool.submit(
                    _download_one,
                    bundle_url(name, cdn_root),
                    out_dir / _basename(name),
                ): name
                for name in remaining
            }
            for future in as_completed(futures):
                name = futures[future]
                try:
                    future.result()
                except DownloadError as e:
                    errors[name] = str(e)
                    if e.retryable:
                        retryable.append(name)
                    continue
                errors.pop(name, None)
                result.downloaded += 1
                done = result.downloaded
                if verbose:
                    print(f"  [{done}/{total}] {_basename(name)}", flush=True)
                elif done % 250 == 0:
                    print(f"  {done}/{total} bundles", flush=True)
        remaining = retryable

    return errors


def write_manifest(result: AssetsResult) -> Path:
    manifest = {
        "catalog_source": result.catalog_source,
        "cdn_root": result.cdn_root,
        "total_bundles": result.total_bundles,
        "selected": result.selected,
        "downloaded": result.downloaded,
        "skipped_existing": result.skipped_existing,
        "failed": result.failed,
        "warnings": result.warnings,
    }
    path = result.out_dir / "manifest.json"
    path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def run_assets_download(
    *,
    catalog: Path | None = None,
    xapk: Path | None = None,
    out_dir: Path,
    cdn_root: str = CDN_ROOT,
    only: tuple[str, ...] = (),
    skip: tuple[str, ...] = DEFAULT_SKIP,
    jobs: int = DEFAULT_JOBS,
    retries: int = DEFAULT_RETRIES,
    clean: bool = False,
    dry_run: bool = False,
    verbose: bool = False,
) -> AssetsResult:
    """Download bundles and write ``manifest.json`` unless this was a dry run."""
    result = download_assets(
        catalog=catalog,
        xapk=xapk,
        out_dir=out_dir,
        cdn_root=cdn_root,
        only=only,
        skip=skip,
        jobs=jobs,
        retries=retries,
        clean=clean,
        dry_run=dry_run,
        verbose=verbose,
    )
    if not dry_run:
        write_manifest(result)
    return result
