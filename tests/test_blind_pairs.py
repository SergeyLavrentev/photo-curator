import base64
import struct
from datetime import datetime, timedelta

from PIL import Image, ImageDraw, ImageEnhance

from photo_curator.analysis.blind_pairs import scene_pairs
from photo_curator.analysis.hashes import color_histogram, dhash, phash, render_equivalence_hash
from photo_curator.native_worker import _next_quality_pair, _next_taste_pair


def _scene(tmp_path, name, variant=0, seconds=0, other_place=False):
    image = Image.new("RGB", (240, 180), (110, 170, 230))
    draw = ImageDraw.Draw(image)
    if other_place:
        draw.polygon([(0, 180), (40, 15), (80, 150), (200, 50), (240, 180)], fill=(55, 80, 30))
    else:
        draw.polygon([(0, 180), (115, 30), (240, 180)], fill=(55, 80, 30))
        draw.rectangle((160, 100, 180, 125), fill=(220, 180, 150))
    image = ImageEnhance.Brightness(image).enhance(1 + variant * 0.02)
    path = tmp_path / f"{name}.png"
    image.save(path)
    return {
        "asset_uuid": name,
        "taken_at": (datetime(2026, 1, 1) + timedelta(seconds=seconds)).isoformat(),
        "width": 240,
        "height": 180,
        "phash": phash(image),
        "dhash": dhash(image),
        "histogram": color_histogram(image),
        "normalized_pixel_hash": render_equivalence_hash(image),
        "review_path": str(path),
        "cache_state": "ready",
        "media_type": "image",
    }


def _signals(*assets):
    # Deliberately identical embeddings and genre: a semantic category alone must
    # never authorize comparison of different locations.
    return {
        asset["asset_uuid"]: {
            "feature_print": {
                "status": "ready",
                "value": {
                    "revision": 2,
                    "element_type": 1,
                    "element_count": 2,
                    "data_base64": base64.b64encode(struct.pack("<ff", 1, 0)).decode(),
                },
            }
        }
        for asset in assets
    }


def test_pairing_keeps_each_location_separate_even_with_same_category(tmp_path):
    first = _scene(tmp_path, "mountain-a1")
    second = _scene(tmp_path, "mountain-a2", variant=1, seconds=2)
    other = _scene(tmp_path, "mountain-b1", seconds=4, other_place=True)
    other_second = _scene(tmp_path, "mountain-b2", variant=1, seconds=6, other_place=True)
    assets = [first, second, other, other_second]
    pairs = scene_pairs(assets, _signals(*assets))
    assert {frozenset(pair) for pair, _ in pairs} == {
        frozenset(("mountain-a1", "mountain-a2")),
        frozenset(("mountain-b1", "mountain-b2")),
    }


def test_no_time_only_genre_only_exact_copy_or_distant_capture_fallback(tmp_path):
    first = _scene(tmp_path, "first")
    copy = _scene(tmp_path, "copy", seconds=1)
    different = _scene(tmp_path, "different", other_place=True, seconds=2)
    distant = _scene(tmp_path, "distant", variant=1, seconds=3600)
    undated = _scene(tmp_path, "undated", variant=1)
    undated["taken_at"] = None
    assets = [first, copy, different, distant, undated]
    assert not scene_pairs(assets, _signals(*assets))


def test_all_pair_pickers_accept_same_scene_without_score_or_group_bias(tmp_path):
    first = _scene(tmp_path, "first")
    second = _scene(tmp_path, "second", variant=1, seconds=1)
    assets, signals = [first, second], _signals(first, second)
    first["final_disposition"], second["final_disposition"] = "reject", "keep"
    first["swipe_score"], second["swipe_score"] = 0, 100
    context = {"first": {"group_id": "same"}, "second": {"group_id": "same"}}
    pair, remaining = _next_taste_pair(assets, signals, context, [])
    assert pair and remaining == 1
    assert _next_quality_pair("project", assets, [], signals)[0] == pair
    first["swipe_score"], second["swipe_score"] = 100, 0
    assert _next_taste_pair(assets[::-1], signals, {}, [])[0] == pair
    assert _next_taste_pair(
        assets, signals, {}, [{"left_uuid": "first", "right_uuid": "second"}]
    ) == (None, 0)


def test_matching_requires_compatible_features_and_available_images(tmp_path):
    first = _scene(tmp_path, "first")
    second = _scene(tmp_path, "second", variant=1, seconds=1)
    signals = _signals(first, second)
    signals["second"]["feature_print"]["engine_version"] = "different-model"
    assert not scene_pairs([first, second], signals)
    signals = _signals(first, second)
    second["review_path"] = str(tmp_path / "missing.png")
    assert not scene_pairs([first, second], signals)
