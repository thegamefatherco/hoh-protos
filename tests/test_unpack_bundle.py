"""Real-bundle unpacking checks. Skipped unless a local XAPK fixture exists."""

from __future__ import annotations

import pickle
from pathlib import Path

import pytest

from xapk_to_proto import unpack

pytest.importorskip("UnityPy", reason="requires the assets extra")

from tests.fixture_paths import FIXTURE_XAPK as XAPK

pytestmark = pytest.mark.skipif(
    not XAPK.is_file(), reason=f"requires the {XAPK} fixture"
)


@pytest.fixture(scope="module")
def source() -> unpack.XapkSource:
    return unpack.xapk_bundle_source(XAPK)


def _first_matching(source: unpack.XapkSource, term: str) -> str:
    match = next((n for n in source.names() if term in n), None)
    if match is None:
        pytest.skip(f"no bundle matching {term!r} in {XAPK}")
    return match


def test_xapk_source_finds_the_addressables_bundles(source):
    assert len(source.names()) > 1000
    assert source.describe().endswith("AddressablesAssetPack.apk")


def test_xapk_source_survives_pickling_for_the_process_pool(source):
    revived = pickle.loads(pickle.dumps(source))
    name = source.names()[0]
    assert revived.read(name) == source.read(name)


def test_unpacking_an_atlas_bundle_yields_sprites_named_like_game_addresses(
    source, tmp_path
):
    name = _first_matching(source, "spriteatlas_iconspantheonnodes")

    outcome = unpack.unpack_bundle(source.read(name), name, tmp_path)

    assert not outcome.failed
    assert outcome.unity_version.startswith("6000.")
    names = {r.name for r in outcome.records}
    assert any(n.startswith("icon_pantheon_") for n in names)
    assert all(r.object_type == "Sprite" for r in outcome.records)
    for record in outcome.records:
        assert (tmp_path / record.path).is_file()


def test_atlas_page_textures_are_excluded_by_default(source, tmp_path):
    name = _first_matching(source, "spriteatlas_iconspantheonnodes")

    default = unpack.unpack_bundle(source.read(name), name, tmp_path / "default")
    everything = unpack.unpack_bundle(
        source.read(name), name, tmp_path / "all", include_atlas_textures=True
    )

    assert len(everything.records) > len(default.records)
    assert not any(r.name.startswith("sactx-") for r in default.records)
    assert any(r.name.startswith("sactx-") for r in everything.records)


def test_unpacking_a_prefab_bundle_reports_no_images_without_failing(source, tmp_path):
    name = _first_matching(source, "unit_5710")

    outcome = unpack.unpack_bundle(source.read(name), name, tmp_path)

    assert not outcome.failed
    assert outcome.records == []


def test_unpack_all_writes_an_index_that_maps_names_to_files(source, tmp_path):
    name = _first_matching(source, "spriteatlas_iconspantheonnodes")

    result = unpack.unpack_all(source, tmp_path, only=(name,), jobs=1)
    index_path = unpack.write_index(result)

    assert result.images_written > 0
    assert index_path.is_file()
    index = unpack.build_index(result)
    sample = result.records[0]
    assert sample.path in index["by_name"][sample.name.lower()]
