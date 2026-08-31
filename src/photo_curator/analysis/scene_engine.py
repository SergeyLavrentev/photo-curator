from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import datetime
from itertools import pairwise
from statistics import median

import numpy as np

from photo_curator.analysis.hashes import hamming_distance
from photo_curator.analysis.similarity import histogram_similarity
from photo_curator.analysis.taste import TasteProfileError, feature_vector

SCENE_ENGINE_NAME = "scene-aware-shadow"
SCENE_ENGINE_VERSION = "3.1.0-shadow"

_EPISODE_MIN_GAP_SECONDS = 300.0
_EPISODE_MAX_GAP_SECONDS = 1_800.0
_SEMANTIC_MIN_THRESHOLD = 0.72
_SEMANTIC_MAX_THRESHOLD = 0.93
_EPISODE_LOCATION_CHANGE_KM = 2.0


@dataclass(frozen=True, slots=True)
class ShadowMember:
    asset_uuid: str
    position: int
    rank_score: float | None = None
    novelty_score: float | None = None
    recommended: bool = False
    evidence: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ShadowNode:
    node_id: str
    parent_node_id: str | None
    kind: str
    position: int
    budget: int | None
    metadata: dict[str, object]
    members: tuple[ShadowMember, ...]


@dataclass(frozen=True, slots=True)
class SceneShadowResult:
    engine_name: str
    engine_version: str
    config: dict[str, object]
    summary: dict[str, object]
    nodes: tuple[ShadowNode, ...]


def build_scene_shadow(
    assets: list[dict[str, object]],
    signals: dict[str, dict[str, dict[str, object]]],
    *,
    selection_density: str = "balanced",
) -> SceneShadowResult:
    """Build an immutable scene-first proposal without changing product decisions."""
    ordered = sorted(
        (
            asset
            for asset in assets
            if not asset.get("no_longer_exists") and asset.get("media_type") == "image"
        ),
        key=lambda asset: (
            int(asset.get("album_position") or 0),
            str(asset.get("asset_uuid")),
        ),
    )
    vectors = {
        str(asset["asset_uuid"]): vector
        for asset in ordered
        if (vector := _signal_vector(signals.get(str(asset["asset_uuid"]), {}))) is not None
    }
    rank_scores = {
        str(asset["asset_uuid"]): _leader_quality(asset, signals.get(str(asset["asset_uuid"]), {}))
        for asset in ordered
    }
    episodes, episode_threshold = _capture_episodes(ordered)
    nodes: list[ShadowNode] = []
    exact_nodes = _exact_duplicate_nodes(ordered, rank_scores)
    nodes.extend(exact_nodes)
    scene_count = 0
    stack_count = 0
    recommended: set[str] = set()
    semantic_thresholds: list[float] = []

    for episode_position, episode_assets in enumerate(episodes):
        episode_id = _node_id("episode", episode_assets)
        episode_members = tuple(
            ShadowMember(
                asset_uuid=str(asset["asset_uuid"]),
                position=int(asset.get("album_position") or 0),
            )
            for asset in episode_assets
        )
        nodes.append(
            ShadowNode(
                node_id=episode_id,
                parent_node_id=None,
                kind="episode",
                position=episode_position,
                budget=None,
                metadata={
                    "start_timestamp": _asset_timestamp(episode_assets[0]),
                    "end_timestamp": _asset_timestamp(episode_assets[-1]),
                    "timestamped_members": sum(
                        _asset_timestamp(asset) is not None for asset in episode_assets
                    ),
                    "located_members": sum(
                        _asset_location(asset) is not None for asset in episode_assets
                    ),
                },
                members=episode_members,
            )
        )
        scenes, semantic_threshold = _semantic_scenes(episode_assets, vectors)
        semantic_thresholds.append(semantic_threshold)
        for scene_position, scene_assets in enumerate(scenes):
            scene_count += 1
            scene_id = _node_id("scene", scene_assets)
            stacks = _moment_stacks(scene_assets, vectors, semantic_threshold)
            stack_by_asset = {
                str(asset["asset_uuid"]): stack_index
                for stack_index, stack in enumerate(stacks)
                for asset in stack
            }
            budget = _scene_budget(len(scene_assets), selection_density)
            selected, novelty = _ranked_subset(
                scene_assets,
                vectors,
                rank_scores,
                stack_by_asset,
                budget,
            )
            recommended.update(selected)
            scene_members = tuple(
                ShadowMember(
                    asset_uuid=str(asset["asset_uuid"]),
                    position=int(asset.get("album_position") or 0),
                    rank_score=rank_scores[str(asset["asset_uuid"])],
                    novelty_score=novelty[str(asset["asset_uuid"])],
                    recommended=str(asset["asset_uuid"]) in selected,
                    evidence={
                        "technical_quality": _technical_quality(asset),
                        "aesthetic_appeal": _aesthetic_appeal(
                            signals.get(str(asset["asset_uuid"]), {})
                        ),
                        "protected": _is_protected(asset),
                        "stack_position": stack_by_asset[str(asset["asset_uuid"])],
                    },
                )
                for asset in scene_assets
            )
            nodes.append(
                ShadowNode(
                    node_id=scene_id,
                    parent_node_id=episode_id,
                    kind="scene",
                    position=scene_position,
                    budget=budget,
                    metadata={
                        "semantic_threshold": semantic_threshold,
                        "feature_coverage": round(
                            sum(str(asset["asset_uuid"]) in vectors for asset in scene_assets)
                            / len(scene_assets),
                            6,
                        ),
                        "selection_policy": "protected-plus-quality-novelty",
                    },
                    members=scene_members,
                )
            )
            for stack_position, stack_assets in enumerate(stacks):
                stack_count += 1
                stack_id = _node_id("stack", stack_assets)
                nodes.append(
                    ShadowNode(
                        node_id=stack_id,
                        parent_node_id=scene_id,
                        kind="moment_stack",
                        position=stack_position,
                        budget=sum(str(asset["asset_uuid"]) in selected for asset in stack_assets),
                        metadata={
                            "complete_link": True,
                            "member_limit": None,
                        },
                        members=tuple(
                            ShadowMember(
                                asset_uuid=str(asset["asset_uuid"]),
                                position=int(asset.get("album_position") or 0),
                                rank_score=rank_scores[str(asset["asset_uuid"])],
                                novelty_score=novelty[str(asset["asset_uuid"])],
                                recommended=str(asset["asset_uuid"]) in selected,
                            )
                            for asset in stack_assets
                        ),
                    )
                )

    config = {
        "mode": "shadow_advisory",
        "selection_density": selection_density,
        "episode_gap_policy": "album-order-adaptive",
        "episode_gap_seconds": episode_threshold,
        "episode_location_change_km": _EPISODE_LOCATION_CHANGE_KM,
        "semantic_change_policy": "adjacent-adaptive-mad",
        "semantic_threshold_bounds": [_SEMANTIC_MIN_THRESHOLD, _SEMANTIC_MAX_THRESHOLD],
        "stack_policy": "complete-link-visual-semantic",
        "exact_duplicate_policy": "independent-render-equivalence",
        "uses_codex_ranking": False,
        "uses_personal_taste": False,
        "mutates_product_decisions": False,
    }
    summary = {
        "asset_count": len(ordered),
        "episode_count": len(episodes),
        "scene_count": scene_count,
        "moment_stack_count": stack_count,
        "exact_group_count": len(exact_nodes),
        "recommended_count": len(recommended),
        "feature_coverage": round(len(vectors) / len(ordered), 6) if ordered else 0.0,
        "semantic_threshold_median": (
            round(float(median(semantic_thresholds)), 6) if semantic_thresholds else None
        ),
    }
    return SceneShadowResult(
        engine_name=SCENE_ENGINE_NAME,
        engine_version=SCENE_ENGINE_VERSION,
        config=config,
        summary=summary,
        nodes=tuple(nodes),
    )


def _capture_episodes(
    assets: list[dict[str, object]],
) -> tuple[list[list[dict[str, object]]], float]:
    if not assets:
        return [], _EPISODE_MIN_GAP_SECONDS
    gaps = []
    for left, right in pairwise(assets):
        left_timestamp = _asset_timestamp(left)
        right_timestamp = _asset_timestamp(right)
        if left_timestamp is not None and right_timestamp is not None:
            gap = max(0.0, right_timestamp - left_timestamp)
            if gap > 0:
                gaps.append(gap)
    if gaps:
        center = float(median(gaps))
        deviation = float(median(abs(gap - center) for gap in gaps))
        threshold = max(_EPISODE_MIN_GAP_SECONDS, center + max(90.0, deviation * 6.0))
        threshold = min(_EPISODE_MAX_GAP_SECONDS, threshold)
    else:
        threshold = _EPISODE_MIN_GAP_SECONDS
    episodes: list[list[dict[str, object]]] = [[assets[0]]]
    for asset in assets[1:]:
        previous = episodes[-1][-1]
        previous_timestamp = _asset_timestamp(previous)
        timestamp = _asset_timestamp(asset)
        time_change = (
            previous_timestamp is not None
            and timestamp is not None
            and timestamp - previous_timestamp > threshold
        )
        location_change = _is_location_change_point(
            previous,
            asset,
            previous_timestamp=previous_timestamp,
            timestamp=timestamp,
        )
        if time_change or location_change:
            episodes.append([])
        episodes[-1].append(asset)
    return episodes, round(threshold, 6)


def _semantic_scenes(
    assets: list[dict[str, object]], vectors: dict[str, np.ndarray]
) -> tuple[list[list[dict[str, object]]], float]:
    if not assets:
        return [], _SEMANTIC_MIN_THRESHOLD
    similarities = [
        similarity
        for left, right in pairwise(assets)
        if (similarity := _visual_semantic_similarity(left, right, vectors)) is not None
    ]
    if similarities:
        center = float(median(similarities))
        deviation = float(median(abs(value - center) for value in similarities))
        threshold = max(
            _SEMANTIC_MIN_THRESHOLD,
            min(_SEMANTIC_MAX_THRESHOLD, center - max(0.025, deviation * 2.5)),
        )
    else:
        threshold = _SEMANTIC_MIN_THRESHOLD
    scenes: list[list[dict[str, object]]] = [[assets[0]]]
    for asset in assets[1:]:
        similarity = _visual_semantic_similarity(scenes[-1][-1], asset, vectors)
        if similarity is not None and similarity < threshold:
            scenes.append([])
        scenes[-1].append(asset)
    return scenes, round(threshold, 6)


def _moment_stacks(
    assets: list[dict[str, object]],
    vectors: dict[str, np.ndarray],
    semantic_threshold: float,
) -> list[list[dict[str, object]]]:
    stacks: list[list[dict[str, object]]] = []
    for asset in assets:
        target = next(
            (
                stack
                for stack in stacks
                if all(
                    _comparable_moment(asset, member, vectors, semantic_threshold)
                    for member in stack
                )
            ),
            None,
        )
        if target is None:
            stacks.append([asset])
        else:
            target.append(asset)
    return stacks


def _comparable_moment(
    left: dict[str, object],
    right: dict[str, object],
    vectors: dict[str, np.ndarray],
    semantic_threshold: float,
) -> bool:
    if left.get("normalized_pixel_hash") and left.get("normalized_pixel_hash") == right.get(
        "normalized_pixel_hash"
    ):
        return True
    left_ratio = _aspect_ratio(left)
    right_ratio = _aspect_ratio(right)
    aspect_delta = abs(left_ratio - right_ratio) / max(left_ratio, right_ratio, 0.001)
    if aspect_delta > 0.12:
        return False
    similarity = _visual_semantic_similarity(left, right, vectors)
    if similarity is None or similarity < max(0.90, semantic_threshold + 0.04):
        return False
    same_burst = bool(
        left.get("burst_key")
        and left.get("burst_key") not in {"0", 0}
        and left.get("burst_key") == right.get("burst_key")
    )
    try:
        phash_distance = hamming_distance(str(left["phash"]), str(right["phash"]))
        dhash_distance = hamming_distance(str(left["dhash"]), str(right["dhash"]))
    except (KeyError, TypeError, ValueError):
        return same_burst and similarity >= 0.94
    histogram = histogram_similarity(
        list(left.get("histogram") or []), list(right.get("histogram") or [])
    )
    return bool(
        (phash_distance <= 18 and dhash_distance <= 28 and histogram >= 0.72)
        or (same_burst and phash_distance <= 24 and histogram >= 0.60)
    )


def _ranked_subset(
    assets: list[dict[str, object]],
    vectors: dict[str, np.ndarray],
    rank_scores: dict[str, float],
    stack_by_asset: dict[str, int],
    budget: int,
) -> tuple[set[str], dict[str, float]]:
    if not assets:
        return set(), {}
    asset_by_uuid = {str(asset["asset_uuid"]): asset for asset in assets}
    scene_uuids = set(asset_by_uuid)
    selected: list[str] = []
    protected = sorted(
        (str(asset["asset_uuid"]) for asset in assets if _is_protected(asset)),
        key=lambda asset_uuid: rank_scores[asset_uuid],
        reverse=True,
    )
    selected.extend(protected)
    if not selected:
        selected.append(
            max(scene_uuids, key=lambda asset_uuid: (rank_scores[asset_uuid], asset_uuid))
        )
    target = max(budget, len(selected))
    novelty = {
        str(asset["asset_uuid"]): _marginal_novelty(
            str(asset["asset_uuid"]), selected, vectors, stack_by_asset
        )
        for asset in assets
    }
    while len(selected) < min(target, len(assets)):
        remaining = [
            str(asset["asset_uuid"])
            for asset in assets
            if str(asset["asset_uuid"]) not in selected
            and not _duplicates_selected_render(asset, selected, asset_by_uuid)
        ]
        if not remaining:
            break
        for asset_uuid in remaining:
            novelty[asset_uuid] = _marginal_novelty(asset_uuid, selected, vectors, stack_by_asset)
        winner = max(
            remaining,
            key=lambda asset_uuid: (
                rank_scores[asset_uuid] * 0.70 + novelty[asset_uuid] * 0.30,
                rank_scores[asset_uuid],
                asset_uuid,
            ),
        )
        selected.append(winner)
    for asset in assets:
        asset_uuid = str(asset["asset_uuid"])
        novelty[asset_uuid] = _marginal_novelty(
            asset_uuid,
            [selected_uuid for selected_uuid in selected if selected_uuid != asset_uuid],
            vectors,
            stack_by_asset,
        )
    return set(selected), novelty


def _marginal_novelty(
    asset_uuid: str,
    selected: list[str],
    vectors: dict[str, np.ndarray],
    stack_by_asset: dict[str, int],
) -> float:
    if not selected:
        return 1.0
    vector = vectors.get(asset_uuid)
    similarities = []
    if vector is not None:
        for other_uuid in selected:
            other_vector = vectors.get(other_uuid)
            similarity = (
                _vector_similarity(vector, other_vector) if other_vector is not None else None
            )
            if similarity is not None:
                similarities.append(similarity)
    if similarities:
        return max(0.0, 1.0 - max(similarities))
    same_stack = any(
        stack_by_asset.get(other_uuid) == stack_by_asset.get(asset_uuid) for other_uuid in selected
    )
    return 0.15 if same_stack else 0.65


def _duplicates_selected_render(
    asset: dict[str, object],
    selected: list[str],
    asset_by_uuid: dict[str, dict[str, object]],
) -> bool:
    render_hash = asset.get("normalized_pixel_hash")
    return bool(
        render_hash
        and not _is_protected(asset)
        and any(
            asset_by_uuid[asset_uuid].get("normalized_pixel_hash") == render_hash
            for asset_uuid in selected
        )
    )


def _exact_duplicate_nodes(
    assets: list[dict[str, object]], rank_scores: dict[str, float]
) -> list[ShadowNode]:
    buckets: dict[str, list[dict[str, object]]] = {}
    for asset in assets:
        render_hash = asset.get("normalized_pixel_hash")
        if render_hash:
            buckets.setdefault(str(render_hash), []).append(asset)
    nodes = []
    for position, bucket in enumerate(
        sorted(
            (values for values in buckets.values() if len(values) >= 2),
            key=lambda values: min(int(asset.get("album_position") or 0) for asset in values),
        )
    ):
        leader = max(
            bucket,
            key=lambda asset: (
                _is_protected(asset),
                rank_scores[str(asset["asset_uuid"])],
                _pixels(asset),
                str(asset["asset_uuid"]),
            ),
        )
        leader_uuid = str(leader["asset_uuid"])
        nodes.append(
            ShadowNode(
                node_id=_node_id("exact", bucket),
                parent_node_id=None,
                kind="exact_duplicate",
                position=position,
                budget=1,
                metadata={
                    "render_hash": str(bucket[0]["normalized_pixel_hash"]),
                    "leader_uuid": leader_uuid,
                    "safety_layer": True,
                },
                members=tuple(
                    ShadowMember(
                        asset_uuid=str(asset["asset_uuid"]),
                        position=int(asset.get("album_position") or 0),
                        rank_score=rank_scores[str(asset["asset_uuid"])],
                        novelty_score=0.0,
                        recommended=str(asset["asset_uuid"]) == leader_uuid,
                        evidence={"render_equivalent": True},
                    )
                    for asset in bucket
                ),
            )
        )
    return nodes


def _scene_budget(member_count: int, density: str) -> int:
    multiplier = {"compact": 0.8, "balanced": 1.0, "broad": 1.35}.get(density, 1.0)
    return min(member_count, max(1, math.ceil(math.sqrt(member_count) * multiplier)))


def _leader_quality(asset: dict[str, object], signals: dict[str, dict[str, object]]) -> float:
    technical = _technical_quality(asset)
    aesthetic = _aesthetic_appeal(signals)
    faces = _signal_value(signals.get("faces"))
    face_quality = None
    if faces and int(faces.get("face_count") or 0) > 0:
        raw = faces.get("best_capture_quality")
        face_quality = float(raw) if isinstance(raw, (int, float)) else 0.5
    values = [(technical, 0.62), (aesthetic, 0.30)]
    if face_quality is not None:
        values.append((face_quality, 0.08))
    return sum(value * weight for value, weight in values) / sum(weight for _, weight in values)


def _technical_quality(asset: dict[str, object]) -> float:
    value = asset.get("technical_quality")
    return float(value) if isinstance(value, (int, float)) else 0.5


def _aesthetic_appeal(signals: dict[str, dict[str, object]]) -> float:
    value = _signal_value(signals.get("aesthetics"))
    score = value.get("overall_score") if value else None
    return (float(score) + 1.0) / 2.0 if isinstance(score, (int, float)) else 0.5


def _visual_semantic_similarity(
    left: dict[str, object],
    right: dict[str, object],
    vectors: dict[str, np.ndarray],
) -> float | None:
    left_vector = vectors.get(str(left["asset_uuid"]))
    right_vector = vectors.get(str(right["asset_uuid"]))
    if left_vector is not None and right_vector is not None:
        similarity = _vector_similarity(left_vector, right_vector)
        if similarity is not None:
            return similarity
    try:
        phash_similarity = 1.0 - hamming_distance(str(left["phash"]), str(right["phash"])) / 64.0
    except (KeyError, TypeError, ValueError):
        return None
    histogram = histogram_similarity(
        list(left.get("histogram") or []), list(right.get("histogram") or [])
    )
    return phash_similarity * 0.65 + histogram * 0.35


def _signal_vector(signals: dict[str, dict[str, object]]) -> np.ndarray | None:
    signal = signals.get("feature_print")
    if not signal or signal.get("status") != "ready":
        return None
    try:
        _, vector, _ = feature_vector(signal)
    except TasteProfileError:
        return None
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 1e-8 else None


def _vector_similarity(left: np.ndarray, right: np.ndarray) -> float | None:
    if left.size != right.size:
        return None
    similarity = float(np.dot(left, right))
    if not math.isfinite(similarity):
        return None
    return max(-1.0, min(1.0, similarity))


def _signal_value(signal: dict[str, object] | None) -> dict[str, object] | None:
    value = signal.get("value") if signal and signal.get("status") == "ready" else None
    return value if isinstance(value, dict) else None


def _asset_timestamp(asset: dict[str, object]) -> float | None:
    value = asset.get("creation_timestamp")
    if isinstance(value, (int, float)):
        return float(value)
    taken_at = asset.get("taken_at")
    if not taken_at:
        return None
    try:
        return datetime.fromisoformat(str(taken_at)).timestamp()
    except ValueError:
        return None


def _asset_location(asset: dict[str, object]) -> tuple[float, float] | None:
    latitude = asset.get("latitude")
    longitude = asset.get("longitude")
    if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
        return None
    if not (-90.0 <= float(latitude) <= 90.0 and -180.0 <= float(longitude) <= 180.0):
        return None
    return float(latitude), float(longitude)


def _is_location_change_point(
    left: dict[str, object],
    right: dict[str, object],
    *,
    previous_timestamp: float | None,
    timestamp: float | None,
) -> bool:
    left_location = _asset_location(left)
    right_location = _asset_location(right)
    if left_location is None or right_location is None:
        return False
    distance = _haversine_km(left_location, right_location)
    if distance < _EPISODE_LOCATION_CHANGE_KM:
        return False
    if previous_timestamp is None or timestamp is None:
        return True
    elapsed = max(0.0, timestamp - previous_timestamp)
    return distance >= 25.0 or elapsed >= 120.0


def _haversine_km(left: tuple[float, float], right: tuple[float, float]) -> float:
    left_latitude, left_longitude = map(math.radians, left)
    right_latitude, right_longitude = map(math.radians, right)
    latitude_delta = right_latitude - left_latitude
    longitude_delta = right_longitude - left_longitude
    value = (
        math.sin(latitude_delta / 2.0) ** 2
        + math.cos(left_latitude) * math.cos(right_latitude) * math.sin(longitude_delta / 2.0) ** 2
    )
    return 6_371.0088 * 2.0 * math.asin(min(1.0, math.sqrt(value)))


def _is_protected(asset: dict[str, object]) -> bool:
    return bool(asset.get("favorite") or asset.get("has_adjustments"))


def _node_id(kind: str, assets: list[dict[str, object]]) -> str:
    member_ids = "\0".join(str(asset["asset_uuid"]) for asset in assets)
    return f"{kind}-{hashlib.sha256(member_ids.encode()).hexdigest()[:16]}"


def _pixels(asset: dict[str, object]) -> int:
    return int(asset.get("width") or 0) * int(asset.get("height") or 0)


def _aspect_ratio(asset: dict[str, object]) -> float:
    return float(asset.get("width") or 1) / float(asset.get("height") or 1)
