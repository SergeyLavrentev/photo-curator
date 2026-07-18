from photo_curator.analysis.decision_engine import decide_asset
from photo_curator.pipeline.duplicates import find_duplicate_groups


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
