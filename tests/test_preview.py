"""Static HTML preview generation for unpacked images."""

from __future__ import annotations

import json
from pathlib import Path

from xapk_to_proto import preview, unpack
from xapk_to_proto.cli import (
    _resolve_preview_assets_args,
    build_parser,
)
from xapk_to_proto.game_api import ENV_WORLD


def _record(
    *,
    name: str = "icon_tome",
    folder: str = "spriteatlas_icons",
    width: int = 64,
    height: int = 64,
    address_prefix: str | None | object = ...,
) -> unpack.ImageRecord:
    prefix: str | None
    if address_prefix is ...:
        prefix = folder
    else:
        prefix = address_prefix  # type: ignore[assignment]
    return unpack.ImageRecord(
        bundle=f"{folder}_deadbeefdeadbeef_assets_all_0123456789abcdef0123456789abcdef.bundle",
        address_prefix=prefix,
        object_type="Sprite",
        name=name,
        path=f"extracted/{folder}/{name}.png",
        width=width,
        height=height,
    )


def test_build_manifest_slims_records_and_sorts():
    records = [
        _record(name="zebra", folder="b"),
        _record(name="alpha", folder="a"),
        _record(name="beta", folder="a"),
    ]
    manifest = preview.build_manifest(records)
    assert [m["name"] for m in manifest] == ["alpha", "beta", "zebra"]
    assert manifest[0] == {
        "name": "alpha",
        "path": "extracted/a/alpha.png",
        "folder": "a",
        "width": 64,
        "height": 64,
        "type": "Sprite",
        "address_prefix": "a",
    }


def test_write_preview_embeds_manifest_and_relative_image_refs(tmp_path: Path):
    records = [
        _record(name="icon_tome"),
        _record(name="wool", folder="spriteatlas_icons"),
    ]
    out = tmp_path / "unpacked"
    out.mkdir()
    preview_dir = preview.write_preview(out, records)

    assert preview_dir == out / preview.PREVIEW_DIRNAME
    html = (preview_dir / "index.html").read_text(encoding="utf-8")
    css = (preview_dir / "style.css").read_text(encoding="utf-8")
    js = (preview_dir / "app.js").read_text(encoding="utf-8")

    assert '<script src="app.js"></script>' in html
    assert 'href="style.css"' in html
    assert "Asset preview" in html
    assert "#grid" in css
    assert "window" not in js or "ASSETS" in js
    assert "icon_tome" in js
    assert "extracted/spriteatlas_icons/icon_tome.png" in js
    assert "../" in js  # relative src from preview/ to extracted/
    assert "wool" in js

    # Embedded JSON must be parseable from the JS assignment.
    start = js.index("const ASSETS = ") + len("const ASSETS = ")
    end = js.index(";", start)
    embedded = json.loads(js[start:end])
    assert len(embedded) == 2
    assert {e["name"] for e in embedded} == {"icon_tome", "wool"}


def test_write_preview_replaces_previous_directory(tmp_path: Path):
    out = tmp_path / "unpacked"
    first = preview.write_preview(out, [_record(name="old")])
    stale = first / "stale.txt"
    stale.write_text("gone", encoding="utf-8")
    preview.write_preview(out, [_record(name="new")])
    assert not stale.exists()
    js = (out / preview.PREVIEW_DIRNAME / "app.js").read_text(encoding="utf-8")
    assert '"name":"new"' in js
    assert '"name":"old"' not in js


def test_write_preview_from_index(tmp_path: Path):
    unpacked = tmp_path / "unpacked"
    unpacked.mkdir()
    records = [_record(name="mozart", folder="mozart")]
    index = {
        "schema_version": 2,
        "records": [
            {
                "bundle": records[0].bundle,
                "address_prefix": records[0].address_prefix,
                "object_type": records[0].object_type,
                "name": records[0].name,
                "path": records[0].path,
                "width": records[0].width,
                "height": records[0].height,
            }
        ],
        "mesh_records": [
            {
                "bundle": records[0].bundle,
                "address_prefix": "mozart",
                "object_type": "Mesh",
                "name": "Skeleton",
                "path": "extracted/mozart/Skeleton.obj",
                "vertex_count": 10,
            }
        ],
    }
    (unpacked / unpack.INDEX_FILENAME).write_text(
        json.dumps(index) + "\n", encoding="utf-8"
    )

    preview_dir, count = preview.write_preview_from_index(unpacked)
    assert count == 1
    assert preview_dir.is_dir()
    js = (preview_dir / "app.js").read_text(encoding="utf-8")
    assert "mozart" in js
    assert "Skeleton.obj" not in js


def test_preview_assets_cli_defaults(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(ENV_WORLD, raising=False)
    args = build_parser().parse_args(["preview-assets", "--version", "1.50.3"])
    assert _resolve_preview_assets_args(args) is None
    assert args.index == (
        tmp_path / "output" / "un0" / "1.50.3" / "unpacked"
    ).resolve()
