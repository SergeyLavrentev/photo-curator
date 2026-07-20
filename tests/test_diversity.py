import base64
import struct

from photo_curator.analysis.decision_engine import decide_asset
from photo_curator.analysis.diversity import (
    DIVERSITY_MODEL_VERSION,
    diversity_evidence,
)


def _asset(asset_uuid: str, *, favorite: bool = False, minute: int = 0):
    return {
        "asset_uuid": asset_uuid,
        "width": 1200,
        "height": 800,
        "favorite": favorite,
        "has_adjustments": False,
        "cache_state": "ready",
        "taken_at": f"2026-01-01T10:{minute:02d}:00+00:00",
        "technical_quality": 0.9,
        "sharpness_percentile": 0.9,
        "contrast_percentile": 0.9,
        "luma_mean": 0.5,
    }


def _feature(*values: float):
    return {
        "status": "ready",
        "engine_name": "apple-vision-native",
        "engine_version": "test-v1",
        "request_revision": 2,
        "value": {
            "revision": 2,
            "element_count": len(values),
            "element_type": 1,
            "data_base64": base64.b64encode(struct.pack(f"<{len(values)}f", *values)).decode(),
        },
    }


def test_semantic_diversity_demotes_only_the_fourth_similar_unprotected_keep() -> None:
    assets = [_asset(f"same-{index}", minute=index * 3) for index in range(4)]
    assets.append(_asset("different", minute=20))
    decisions = [decide_asset(asset, None) for asset in assets]
    signals = {
        str(asset["asset_uuid"]): {
            "feature_print": _feature(
                *(1.0, 0.0) if asset["asset_uuid"] != "different" else (0.0, 1.0)
            )
        }
        for asset in assets
    }

    evidence = diversity_evidence(assets, decisions, signals)
    demoted = {asset_uuid for asset_uuid, item in evidence.items() if item.demoted}

    assert len(demoted) == 1
    assert demoted <= {f"same-{index}" for index in range(4)}
    assert not evidence["different"].demoted
    assert evidence["different"].value == 100
    assert all(
        item.model_version == DIVERSITY_MODEL_VERSION
        for item in evidence.values()
        if item.reason in {"semantic_anchor", "semantic_distance", "semantic_scene_limit"}
    )


def test_favorite_is_never_demoted_by_semantic_or_temporal_diversity() -> None:
    assets = [_asset(f"normal-{index}", minute=index * 3) for index in range(4)]
    assets.append(_asset("favorite", favorite=True, minute=12))
    decisions = [decide_asset(asset, None) for asset in assets]
    signals = {str(asset["asset_uuid"]): {"feature_print": _feature(1.0, 0.0)} for asset in assets}

    evidence = diversity_evidence(assets, decisions, signals)

    assert not evidence["favorite"].demoted
    assert evidence["favorite"].reason == "protected"
    assert evidence["favorite"].value == 100
