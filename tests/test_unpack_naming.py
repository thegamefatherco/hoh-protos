"""Tests for bundle naming, image selection, and indexing (no UnityPy needed)."""

from __future__ import annotations

import json
from pathlib import Path

from xapk_to_proto import unpack
from xapk_to_proto.unpack import (
    ImageRecord,
    UnpackResult,
    assign_filenames,
    build_index,
    bundle_address_prefix,
    is_empty_texture,
    safe_filename,
    select_bundles,
    select_image_objects,
)


def _record(
    name: str,
    *,
    bundle: str = "b_0123456789abcdef_assets_all_" + "0" * 32 + ".bundle",
    address: str | None = "b",
    path: str | None = None,
    object_type: str = "Sprite",
) -> ImageRecord:
    return ImageRecord(
        bundle=bundle,
        address_prefix=address,
        object_type=object_type,
        name=name,
        path=path or f"extracted/{address}/{name}.png",
        width=8,
        height=8,
    )


def _result(records: list[ImageRecord], **kwargs) -> UnpackResult:
    defaults = {
        "source": "test",
        "out_dir": Path("/tmp/out"),
        "unity_version": "6000.0.74f1",
        "bundles_total": 1,
        "bundles_selected": 1,
        "bundles_skipped": 0,
        "bundles_failed": 0,
        "images_written": len(records),
        "records": records,
    }
    defaults.update(kwargs)
    return UnpackResult(**defaults)


def test_bundle_address_prefix_recovers_the_addressables_address():
    name = "unit_queenboudicca_1725977d7e684513_assets_all_" + "a" * 32 + ".bundle"
    assert bundle_address_prefix(name) == "unit_queenboudicca"


def test_bundle_address_prefix_handles_scene_and_monoscript_bundles():
    scenes = (
        "icon_webgl_cursor_click_c9e6851333ec67a0_scenes_all_" + "b" * 32 + ".bundle"
    )
    assert bundle_address_prefix(scenes) == "icon_webgl_cursor_click"


def test_bundle_address_prefix_returns_none_for_hash_named_bundles():
    name = "0d68576bb4ef079a2084908b5463fc67_monoscripts_" + "c" * 32 + ".bundle"
    assert bundle_address_prefix(name) is None


def test_bundle_address_prefix_returns_none_for_unrecognised_names():
    assert bundle_address_prefix("not-a-bundle.txt") is None


def test_safe_filename_replaces_path_separators_and_reserved_characters():
    assert safe_filename("ui/icons:gold?") == "ui_icons_gold_"


def test_safe_filename_falls_back_when_nothing_usable_remains():
    assert safe_filename("") == "unnamed"
    assert safe_filename("...") == "unnamed"


def test_assign_filenames_suffixes_collisions_case_insensitively():
    assert assign_filenames(["icon", "Icon", "other", "icon"]) == [
        "icon",
        "Icon__2",
        "other",
        "icon__3",
    ]


def test_select_image_objects_skips_atlas_pages_and_sprite_shadowed_textures():
    candidates = [
        ("Sprite", "icon_gold"),
        ("Texture2D", "icon_gold"),
        ("Texture2D", "sactx-0-2048x2048-ASTC 8x8-Icons-13334a69"),
        ("Texture2D", "standalone_texture"),
        ("AssetBundle", "ignored"),
    ]
    assert select_image_objects(candidates) == [0, 3]


def test_select_image_objects_keeps_everything_when_atlas_textures_requested():
    candidates = [
        ("Sprite", "icon_gold"),
        ("Texture2D", "icon_gold"),
        ("Texture2D", "sactx-0-2048x2048-ASTC 8x8-Icons-13334a69"),
        ("AssetBundle", "ignored"),
    ]
    assert select_image_objects(candidates, include_atlas_textures=True) == [0, 1, 2]


def test_select_bundles_only_takes_precedence_over_skip():
    names = ["vfx_a.bundle", "hero_b.bundle", "hero_c.bundle"]
    assert select_bundles(names, only=("hero",), skip=("hero",)) == [
        "hero_b.bundle",
        "hero_c.bundle",
    ]


def test_select_bundles_without_filters_keeps_everything():
    names = ["a.bundle", "b.bundle"]
    assert select_bundles(names) == names


def test_build_index_maps_names_case_insensitively_to_paths():
    index = build_index(_result([_record("Icon_Gold")]))
    assert index["by_name"]["icon_gold"] == ["extracted/b/Icon_Gold.png"]


def test_build_index_reports_names_present_in_more_than_one_bundle():
    records = [
        _record("shared", address="one", path="extracted/one/shared.png"),
        _record("shared", address="two", path="extracted/two/shared.png"),
    ]
    index = build_index(_result(records))
    assert index["duplicate_names"] == 1
    assert len(index["by_name"]["shared"]) == 2


def test_build_index_records_imageless_bundles_so_addresses_still_resolve():
    bundle = "unit_boudicca_1725977d7e684513_assets_all_" + "d" * 32 + ".bundle"
    index = build_index(_result([], empty_bundles=[bundle]))
    assert index["by_bundle_prefix"]["unit_boudicca"] == {
        "bundle": bundle,
        "images": [],
    }


def test_load_previous_records_resumes_only_bundles_whose_files_survive(tmp_path):
    kept = _record("kept")
    lost = _record("lost", bundle="gone.bundle", path="extracted/b/lost.png")
    result = _result([kept, lost], out_dir=tmp_path)
    (tmp_path / "extracted" / "b").mkdir(parents=True)
    (tmp_path / kept.path).write_bytes(b"png")
    (tmp_path / unpack.INDEX_FILENAME).write_text(
        json.dumps(build_index(result)), encoding="utf-8"
    )

    previous = unpack._load_previous_records(tmp_path)

    assert kept.bundle in previous
    assert "gone.bundle" not in previous


def test_load_previous_records_returns_empty_without_an_index(tmp_path):
    assert unpack._load_previous_records(tmp_path) == {}


class _StreamData:
    def __init__(self, path: str = "", size: int = 0) -> None:
        self.path = path
        self.size = size


class _FakeTex:
    def __init__(
        self,
        *,
        width: int = 0,
        height: int = 0,
        image_data: bytes = b"",
        stream: _StreamData | None = None,
    ) -> None:
        self.m_Width = width
        self.m_Height = height
        self.image_data = image_data
        self.m_StreamData = stream


def test_is_empty_texture_detects_zero_dimensions():
    assert is_empty_texture(_FakeTex(width=0, height=0))
    assert is_empty_texture(_FakeTex(width=8, height=0))
    assert is_empty_texture(_FakeTex(width=0, height=8))


def test_is_empty_texture_detects_empty_stream_stub():
    # TMP Font Texture stubs: non-zero dims optional, but empty data + empty path.
    assert is_empty_texture(
        _FakeTex(width=0, height=0, stream=_StreamData(path="", size=0))
    )
    assert is_empty_texture(
        _FakeTex(width=64, height=64, image_data=b"", stream=_StreamData("", 0))
    )
    assert is_empty_texture(
        _FakeTex(width=64, height=64, image_data=b"", stream=_StreamData("a.resS", 0))
    )
    assert is_empty_texture(
        _FakeTex(width=64, height=64, image_data=b"", stream=None)
    )


def test_is_empty_texture_keeps_real_inline_or_streamed_data():
    assert not is_empty_texture(
        _FakeTex(width=8, height=8, image_data=b"\x00\x01")
    )
    assert not is_empty_texture(
        _FakeTex(
            width=8,
            height=8,
            image_data=b"",
            stream=_StreamData("tex.resS", 32),
        )
    )
