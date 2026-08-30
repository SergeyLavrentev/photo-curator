import base64
import struct

from photo_curator.analysis.scene_engine import build_scene_shadow


def _feature(*values: float) -> dict[str, object]:
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


def _asset(index: int, *, timestamp: float | None = None) -> dict[str, object]:
    return {
        "asset_uuid": f"asset-{index:03d}",
        "album_position": index,
        "media_type": "image",
        "no_longer_exists": 0,
        "creation_timestamp": timestamp,
        "taken_at": None,
        "width": 4032,
        "height": 3024,
        "favorite": False,
        "has_adjustments": False,
        "technical_quality": 0.55 + index / 500.0,
        "phash": "0123456789abcdef",
        "dhash": "0011223344556677",
        "normalized_pixel_hash": f"render-{index:03d}",
        "histogram": [0.25, 0.25, 0.25, 0.25],
    }


def _signals(assets: list[dict[str, object]], vectors: list[tuple[float, float]]):
    return {
        str(asset["asset_uuid"]): {
            "feature_print": _feature(*vector),
            "aesthetics": {
                "status": "ready",
                "value": {"overall_score": -0.5 + index / max(1, len(assets))},
            },
        }
        for index, (asset, vector) in enumerate(zip(assets, vectors, strict=True))
    }


def test_scene_shadow_reduces_one_hundred_good_frames_with_variable_budget() -> None:
    assets = [_asset(index, timestamp=1_780_000_000.125 + index * 6) for index in range(100)]
    signals = _signals(assets, [(1.0, 0.01) for _ in assets])

    result = build_scene_shadow(assets, signals, selection_density="balanced")

    scenes = [node for node in result.nodes if node.kind == "scene"]
    stacks = [node for node in result.nodes if node.kind == "moment_stack"]
    assert result.config["mutates_product_decisions"] is False
    assert result.config["uses_codex_ranking"] is False
    assert result.summary["episode_count"] == 1
    assert result.summary["scene_count"] == 1
    assert len(scenes) == 1 and scenes[0].budget == 10
    assert sum(member.recommended for member in scenes[0].members) == 10
    assert len({member.rank_score for member in scenes[0].members}) > 20
    assert len(stacks) == 1 and len(stacks[0].members) == 100
    assert stacks[0].metadata["member_limit"] is None


def test_semantic_change_point_splits_scenes_without_a_fixed_time_window() -> None:
    assets = [_asset(index, timestamp=1_780_000_000.5 + index * 90) for index in range(6)]
    vectors = [(1.0, 0.0)] * 3 + [(0.0, 1.0)] * 3

    result = build_scene_shadow(assets, _signals(assets, vectors))

    episodes = [node for node in result.nodes if node.kind == "episode"]
    scenes = [node for node in result.nodes if node.kind == "scene"]
    assert len(episodes) == 1
    assert [[member.asset_uuid for member in scene.members] for scene in scenes] == [
        ["asset-000", "asset-001", "asset-002"],
        ["asset-003", "asset-004", "asset-005"],
    ]


def test_missing_timestamps_do_not_disable_visual_scene_and_stack_matching() -> None:
    assets = [_asset(index) for index in range(8)]

    result = build_scene_shadow(assets, _signals(assets, [(1.0, 0.0)] * len(assets)))

    assert result.summary["episode_count"] == 1
    assert result.summary["scene_count"] == 1
    stack = next(node for node in result.nodes if node.kind == "moment_stack")
    assert len(stack.members) == 8


def test_incompatible_feature_dimensions_fall_back_to_visual_metrics() -> None:
    assets = [_asset(0), _asset(1)]
    signals = _signals(assets, [(1.0, 0.0), (1.0, 0.0)])
    signals["asset-001"]["feature_print"] = _feature(1.0, 0.0, 0.0)

    result = build_scene_shadow(assets, signals)

    assert result.summary["episode_count"] == 1
    assert result.summary["scene_count"] == 1
    stack = next(node for node in result.nodes if node.kind == "moment_stack")
    assert len(stack.members) == 2


def test_exact_duplicates_are_an_independent_album_wide_safety_layer() -> None:
    assets = [_asset(index, timestamp=1_780_000_000.0 + index * 3_600) for index in range(3)]
    assets[0]["normalized_pixel_hash"] = "same-render"
    assets[2]["normalized_pixel_hash"] = "same-render"
    assets[2]["favorite"] = True
    vectors = [(1.0, 0.0), (0.0, 1.0), (1.0, 0.0)]

    result = build_scene_shadow(assets, _signals(assets, vectors))

    exact = next(node for node in result.nodes if node.kind == "exact_duplicate")
    assert {member.asset_uuid for member in exact.members} == {"asset-000", "asset-002"}
    assert exact.metadata["leader_uuid"] == "asset-002"
    assert exact.metadata["safety_layer"] is True
    assert sum(member.recommended for member in exact.members) == 1
