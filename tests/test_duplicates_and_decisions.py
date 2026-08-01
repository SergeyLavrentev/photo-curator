import random
from pathlib import Path

from PIL import Image

from photo_curator.analysis.decision_engine import decide_asset
from photo_curator.pipeline.coordinator import _diversity_demotions
from photo_curator.pipeline.duplicates import _candidate_pairs, find_duplicate_groups


def asset(uuid: str, *, pixels: int = 1000, favorite: bool = False) -> dict[str, object]:
    return {
        "asset_uuid": uuid,
        "width": pixels,
        "height": 1,
        "favorite": favorite,
        "has_adjustments": False,
        "cache_state": "ready",
        "taken_at": "2026-01-01T10:00:00+00:00",
        "phash": "0000000000000000",
        "dhash": "0000000000000000",
        "normalized_pixel_hash": "same",
        "histogram": [0.5, 0.5],
        "sharpness_percentile": 0.8,
        "contrast_percentile": 0.8,
        "technical_quality": 0.8,
    }


def test_duplicate_group_prefers_favorite_and_higher_resolution() -> None:
    groups = find_duplicate_groups(
        [asset("normal", pixels=2000), asset("favorite", pixels=1000, favorite=True)]
    )

    assert len(groups) == 1
    assert groups[0].leader_uuid == "favorite"
    assert "leader_lower_resolution" in groups[0].flags


def test_exact_duplicate_loser_is_excluded_and_leader_is_selected() -> None:
    normal = {"favorite": False, "has_adjustments": False, "cache_state": "ready"}
    loser = decide_asset(
        normal,
        {"kind": "exact", "confidence": 1.0, "is_leader": False, "flags": ["exact_duplicate"]},
    )
    leader = decide_asset(
        normal,
        {"kind": "exact", "confidence": 1.0, "is_leader": True, "flags": []},
    )

    assert loser.disposition == "reject"
    assert leader.disposition == "keep"
    assert "duplicate_leader" in leader.flags


def test_selection_score_ranks_photos_without_calling_a_low_score_bad() -> None:
    high = asset("high")
    low = asset("low")
    low.update(
        technical_quality=0.02,
        sharpness_percentile=0.02,
        contrast_percentile=0.02,
        apple_overall_percentile=0.02,
        luma_mean=0.03,
    )

    selected = decide_asset(high, None)
    low_ranked = decide_asset(low, None)

    assert selected.disposition == "keep"
    assert selected.score >= 70
    assert low_ranked.disposition == "keep"
    assert low_ranked.score < 38
    assert low_ranked.components["exposure"] < selected.components["exposure"]
    assert low_ranked.reasons[0]["code"] == "no_confirmed_defect"


def test_selection_density_does_not_turn_a_valid_photo_bad() -> None:
    borderline = asset("borderline")
    borderline.update(
        technical_quality=0.55,
        sharpness_percentile=0.55,
        contrast_percentile=0.55,
        apple_overall_percentile=0.55,
        luma_mean=0.5,
    )

    assert decide_asset(borderline, None, "compact").disposition == "keep"
    assert decide_asset(borderline, None, "broad").disposition == "keep"


def test_score_breakdown_exposes_series_rank_and_selection_confidence() -> None:
    leader = decide_asset(
        asset("leader"),
        {
            "kind": "near",
            "confidence": 0.93,
            "quality_margin": 0.0,
            "is_leader": True,
            "flags": [],
        },
    )
    loser = decide_asset(
        asset("loser"),
        {
            "kind": "near",
            "confidence": 0.93,
            "quality_margin": 0.2,
            "is_leader": False,
            "flags": [],
        },
    )

    assert leader.components["series_rank"] == 100
    assert loser.components["series_rank"] < leader.components["series_rank"]
    assert leader.components["selection_confidence"] == 93


def test_near_duplicate_requires_close_time_match_quality_gap_and_defect() -> None:
    normal = {
        "favorite": False,
        "has_adjustments": False,
        "cache_state": "ready",
        "sharpness_percentile": 0.05,
    }
    close_pair = {
        "kind": "near",
        "confidence": 0.98,
        "quality_margin": 0.20,
        "time_delta_seconds": 4.0,
        "is_leader": False,
        "flags": ["near_duplicate"],
        "pair_evidence": {
            "phash_distance": 2,
            "dhash_distance": 4,
            "histogram_similarity": 0.96,
            "normalized_pixel_mae": 0.06,
        },
    }
    without_margin = decide_asset(
        normal,
        {**close_pair, "quality_margin": 0.02},
    )
    too_late = decide_asset(normal, {**close_pair, "time_delta_seconds": 45.0})
    sharp_loser = decide_asset(
        {**normal, "sharpness_percentile": 0.50},
        close_pair,
    )
    confirmed_blurred_loser = decide_asset(normal, close_pair)

    assert without_margin.disposition == "keep"
    assert too_late.disposition == "keep"
    assert sharp_loser.disposition == "keep"
    assert confirmed_blurred_loser.disposition == "reject"
    assert confirmed_blurred_loser.reasons[0]["code"] == "weaker_duplicate"
    assert any(reason["code"] == "possible_blur" for reason in confirmed_blurred_loser.reasons)


def test_low_quality_portrait_flag_needs_a_confirmed_near_duplicate() -> None:
    portrait = {
        "favorite": False,
        "has_adjustments": False,
        "cache_state": "ready",
        "face_count": 1,
        "eyes_detected": 0,
        "face_capture_quality": 0.10,
    }

    decision = decide_asset(portrait, None, selected_threshold=100)

    assert decision.disposition == "keep"
    assert "poor_face_capture" in decision.flags


def test_favorite_edited_missing_and_ambiguous_assets_are_never_auto_rejected() -> None:
    cases = [
        ({"favorite": True, "has_adjustments": False, "cache_state": "ready"}, []),
        ({"favorite": False, "has_adjustments": True, "cache_state": "ready"}, []),
        ({"favorite": False, "has_adjustments": False, "cache_state": "missing"}, []),
        (
            {"favorite": False, "has_adjustments": False, "cache_state": "ready"},
            ["ambiguous_duplicate"],
        ),
    ]
    for row, flags in cases:
        decision = decide_asset(
            row,
            {"kind": "exact", "confidence": 1.0, "is_leader": False, "flags": flags}
            if flags
            else None,
        )
        assert decision.disposition != "reject"


def test_favorite_exact_duplicate_is_kept_for_explicit_user_control() -> None:
    decision = decide_asset(
        {"favorite": True, "has_adjustments": False, "cache_state": "ready"},
        {"kind": "exact", "confidence": 1.0, "is_leader": False, "flags": []},
    )

    assert decision.disposition == "keep"
    assert "favorite_protected" in decision.flags


def test_pair_confirmation_records_dhash_and_normalized_pixel_mae(tmp_path: Path) -> None:
    left_path, right_path = tmp_path / "left.jpg", tmp_path / "right.jpg"
    Image.new("RGB", (100, 100), (100, 120, 140)).save(left_path)
    Image.new("RGB", (100, 100), (102, 122, 142)).save(right_path)
    left, right = asset("left"), asset("right")
    left.update(normalized_pixel_hash="left", review_path=str(left_path))
    right.update(normalized_pixel_hash="right", review_path=str(right_path))

    groups = find_duplicate_groups([left, right])

    evidence = next(member.evidence for member in groups[0].members if not member.is_leader)
    assert evidence["dhash_distance"] == 0
    assert 0 < evidence["normalized_pixel_mae"] < 0.02


def test_candidate_reduction_avoids_pairwise_scan_for_normal_album_size() -> None:
    randomizer = random.Random(42)
    assets = [
        {"asset_uuid": f"asset-{index}", "phash": f"{randomizer.getrandbits(64):016x}"}
        for index in range(1500)
    ]

    candidates = list(_candidate_pairs(assets))

    assert len(candidates) < 10_000
    assert len(candidates) < len(assets) * (len(assets) - 1) // 2


def test_zero_burst_sentinel_does_not_force_unrelated_images_into_a_group() -> None:
    left = asset("left")
    right = asset("right")
    left.update(
        phash="0000000000000000",
        dhash="0000000000000000",
        normalized_pixel_hash="left",
        burst_key="0",
        taken_at=None,
    )
    right.update(
        phash="ffffffffffffffff",
        dhash="ffffffffffffffff",
        normalized_pixel_hash="right",
        burst_key="0",
        taken_at=None,
    )

    assert list(_candidate_pairs([left, right])) == []
    assert find_duplicate_groups([left, right]) == []


def test_large_same_phash_bucket_stays_bounded() -> None:
    assets = [
        {"asset_uuid": f"asset-{index:04d}", "phash": "0123456789abcdef"} for index in range(1671)
    ]

    assert len(list(_candidate_pairs(assets))) < 50_000


def test_temporal_diversity_keeps_only_three_unprotected_frames_per_scene() -> None:
    assets = []
    decisions = []
    for index in range(5):
        row = asset(f"frame-{index}")
        row["taken_at"] = f"2026-01-01T10:00:{index:02d}+00:00"
        row["technical_quality"] = 0.9 - index * 0.02
        row["sharpness_percentile"] = 0.9 - index * 0.02
        assets.append(row)
        decisions.append(decide_asset(row, None))

    demoted = _diversity_demotions(assets, decisions)

    assert demoted == {"frame-3", "frame-4"}
