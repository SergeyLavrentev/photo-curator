import random
from pathlib import Path

from PIL import Image

from photo_curator.analysis.decision_engine import decide_asset
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


def test_exact_duplicate_loser_is_rejected_but_leader_is_protected() -> None:
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
    assert leader.disposition == "review"
    assert "duplicate_leader" in leader.flags


def test_near_duplicate_requires_both_confidence_and_quality_margin() -> None:
    normal = {"favorite": False, "has_adjustments": False, "cache_state": "ready"}
    without_margin = decide_asset(
        normal,
        {
            "kind": "near",
            "confidence": 0.98,
            "quality_margin": 0.02,
            "is_leader": False,
            "flags": ["near_duplicate"],
        },
    )
    with_margin = decide_asset(
        normal,
        {
            "kind": "near",
            "confidence": 0.98,
            "quality_margin": 0.20,
            "is_leader": False,
            "flags": ["near_duplicate"],
        },
    )

    assert without_margin.disposition == "review"
    assert with_margin.disposition == "reject"


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


def test_candidate_reduction_avoids_pairwise_scan_above_3000_assets() -> None:
    randomizer = random.Random(42)
    assets = [
        {"asset_uuid": f"asset-{index}", "phash": f"{randomizer.getrandbits(64):016x}"}
        for index in range(3001)
    ]

    candidates = list(_candidate_pairs(assets))

    assert len(candidates) < 10_000
    assert len(candidates) < len(assets) * (len(assets) - 1) // 2
