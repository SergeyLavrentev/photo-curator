import base64
import random
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from photo_curator.analysis.decision_engine import decide_asset
from photo_curator.pipeline.coordinator import _diversity_demotions
from photo_curator.pipeline.duplicates import (
    _candidate_pairs,
    find_duplicate_groups,
    rerank_duplicate_groups,
)


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


@pytest.mark.parametrize(
    "unavailable",
    [
        {"no_longer_exists": 1},
        {"is_missing": 1},
        {"cache_state": "missing"},
        {"cache_state": "degraded"},
    ],
)
def test_duplicate_group_excludes_unavailable_assets(
    unavailable: dict[str, object],
) -> None:
    active = asset("active")
    inactive = {**asset("inactive"), **unavailable}

    assert find_duplicate_groups([active, inactive]) == []


def test_signal_rerank_drops_group_when_previous_leader_is_inactive() -> None:
    initial_assets = [asset("leader", pixels=2000), asset("copy", pixels=2000)]
    initial = find_duplicate_groups(initial_assets)[0]
    persisted = {
        "group_id": initial.group_id,
        "leader_uuid": initial.leader_uuid,
        "flags": initial.flags,
        "members": [{"asset_uuid": member.asset_uuid} for member in initial.members],
    }
    next(item for item in initial_assets if item["asset_uuid"] == initial.leader_uuid)[
        "no_longer_exists"
    ] = 1

    assert rerank_duplicate_groups([persisted], initial_assets, {}) == []


def test_duplicate_leader_is_rebuilt_after_full_quality_signals() -> None:
    initial_assets = [asset("large", pixels=3000), asset("clear-face", pixels=2000)]
    initial = find_duplicate_groups(initial_assets)[0]
    assert initial.leader_uuid == "large"
    group = {
        "group_id": initial.group_id,
        "leader_uuid": initial.leader_uuid,
        "flags": initial.flags,
        "members": [{"asset_uuid": member.asset_uuid} for member in initial.members],
    }
    signals = {
        "large": {
            "aesthetics": {"status": "ready", "value": {"overall_score": -0.8}},
            "faces": {
                "status": "ready",
                "value": {"face_count": 1, "eyes_detected": 0, "best_capture_quality": 0.1},
            },
        },
        "clear-face": {
            "aesthetics": {"status": "ready", "value": {"overall_score": 0.9}},
            "nima_aesthetics": {
                "status": "ready",
                "value": {"aesthetic_score": 95.0},
            },
            "faces": {
                "status": "ready",
                "value": {"face_count": 1, "eyes_detected": 1, "best_capture_quality": 0.95},
            },
        },
    }

    rebuilt = rerank_duplicate_groups([group], initial_assets, signals)[0]

    assert rebuilt.leader_uuid == "clear-face"
    assert all(
        member.is_leader == (member.asset_uuid == "clear-face") for member in rebuilt.members
    )
    loser = next(member for member in rebuilt.members if member.asset_uuid == "large")
    assert loser.evidence["exact"] is True
    assert loser.quality_score < next(
        member.quality_score for member in rebuilt.members if member.is_leader
    )


def test_duplicate_rerank_checks_cancellation_before_expensive_work() -> None:
    calls = 0

    def cancelled() -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        rerank_duplicate_groups(
            [],
            [asset("left"), asset("right")],
            {},
            check_cancelled=cancelled,
        )

    assert calls == 1


def test_temporal_candidate_window_does_not_drop_the_41st_neighbour() -> None:
    random.seed(42)
    photos = []
    for index in range(50):
        item = asset(f"dense-{index:02d}")
        item["phash"] = f"{random.getrandbits(64):016x}"
        item["dhash"] = f"{random.getrandbits(64):016x}"
        item["normalized_pixel_hash"] = f"render-{index}"
        photos.append(item)

    pairs = {
        frozenset((str(left["asset_uuid"]), str(right["asset_uuid"])))
        for left, right in _candidate_pairs(photos)
    }

    assert frozenset(("dense-00", "dense-45")) in pairs


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
    assert "confidence_uncalibrated" in leader.flags


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
        "laplacian_variance": 0.0001,
        "gradient_energy": 0.0001,
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
        {
            **normal,
            "sharpness_percentile": 0.50,
            "laplacian_variance": 0.02,
            "gradient_energy": 0.01,
        },
        close_pair,
    )
    confirmed_blurred_loser = decide_asset(normal, close_pair)
    validated_blurred_loser = decide_asset(normal, close_pair, validated_defect_auto_reject=True)

    assert without_margin.disposition == "keep"
    assert too_late.disposition == "keep"
    assert sharp_loser.disposition == "keep"
    assert confirmed_blurred_loser.disposition == "keep"
    assert confirmed_blurred_loser.reasons[0]["code"] == "defect_review_required"
    assert any(reason["code"] == "possible_blur" for reason in confirmed_blurred_loser.reasons)
    assert validated_blurred_loser.disposition == "reject"
    assert validated_blurred_loser.reasons[0]["code"] == "weaker_duplicate"


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


def test_eye_landmark_availability_is_not_treated_as_open_eye_quality() -> None:
    base = {
        "favorite": False,
        "has_adjustments": False,
        "cache_state": "ready",
        "face_count": 1,
        "face_capture_quality": 0.75,
    }

    without_landmarks = decide_asset({**base, "eyes_detected": 0}, None)
    with_landmarks = decide_asset({**base, "eyes_detected": 1}, None)

    assert without_landmarks.score == with_landmarks.score
    assert without_landmarks.flags == with_landmarks.flags


def test_local_horizon_and_cropped_face_evidence_remain_review_only() -> None:
    decision = decide_asset(
        {
            "favorite": False,
            "has_adjustments": False,
            "cache_state": "ready",
            "dominant_horizon_degrees": 17.0,
            "horizon_support": 0.5,
            "subject_boundary_source": "faces",
            "subject_boundary_contact_ratio": 0.8,
        },
        None,
    )

    assert decision.disposition == "keep"
    assert {"extreme_horizon", "blocked_subject"} <= set(decision.flags)


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


def test_post_signal_series_discovers_exposure_variant_and_keeps_distinct_moment(
    tmp_path: Path,
) -> None:
    left_path, right_path = tmp_path / "left.jpg", tmp_path / "right.jpg"
    source = Image.new("RGB", (180, 120), (50, 80, 110))
    for x in range(30, 150):
        for y in range(25, 95):
            source.putpixel((x, y), (180, 120, 70))
    source.save(left_path)
    source.point(lambda value: min(255, value + 30)).save(right_path)
    left, right = asset("left"), asset("right")
    left.update(
        phash="0000000000000000",
        dhash="0000000000000000",
        normalized_pixel_hash="left",
        review_path=str(left_path),
    )
    right.update(
        phash="ffffffffffffffff",
        dhash="ffffffffffffffff",
        normalized_pixel_hash="right",
        review_path=str(right_path),
    )

    def signal(values: list[float]) -> dict[str, object]:
        encoded = base64.b64encode(np.asarray(values, dtype="<f4").tobytes()).decode()
        return {
            "feature_print": {
                "status": "ready",
                "engine_name": "vision",
                "engine_version": "1",
                "request_revision": 1,
                "value": {
                    "element_type": 1,
                    "element_count": len(values),
                    "data_base64": encoded,
                    "revision": 1,
                },
            }
        }

    groups = rerank_duplicate_groups(
        [],
        [left, right],
        {"left": signal([1.0, 0.0]), "right": signal([0.99, 0.141])},
    )

    assert len(groups) == 1
    assert groups[0].kind == "scene"
    loser = next(member for member in groups[0].members if not member.is_leader)
    assert loser.evidence["feature_print_similarity"] >= 0.989
    assert loser.evidence["exposure_invariant_crop_mae"] < 0.05
    assert loser.evidence["recommended_pick"] is True


def test_candidate_reduction_avoids_pairwise_scan_for_normal_album_size() -> None:
    randomizer = random.Random(42)
    assets = [
        {"asset_uuid": f"asset-{index}", "phash": f"{randomizer.getrandbits(64):016x}"}
        for index in range(1500)
    ]

    candidates = list(_candidate_pairs(assets))

    assert len(candidates) < 10_000
    assert len(candidates) < len(assets) * (len(assets) - 1) // 2


def test_candidate_search_multi_probes_every_phash_pair_with_distance_ten() -> None:
    flipped_positions = (63, 62, 50, 49, 37, 36, 24, 23, 11, 10)
    right_hash = sum(1 << position for position in flipped_positions)
    left = {"asset_uuid": "left", "phash": "0000000000000000"}
    right = {"asset_uuid": "right", "phash": f"{right_hash:016x}"}

    candidates = {
        tuple(sorted((str(first["asset_uuid"]), str(second["asset_uuid"]))))
        for first, second in _candidate_pairs([left, right])
    }

    assert candidates == {("left", "right")}


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


def test_capture_time_alone_never_demotes_technically_good_frames() -> None:
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

    assert demoted == set()
