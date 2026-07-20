from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np

from photo_curator.analysis.decision_engine import DecisionResult
from photo_curator.analysis.taste import TasteProfileError, feature_vector

DIVERSITY_MODEL_VERSION = "vision-feature-diversity-v1"
SEMANTIC_SIMILARITY_THRESHOLD = 0.94
MAX_SIMILAR_FRAMES = 3
TEMPORAL_SCENE_SECONDS = 120


@dataclass(frozen=True, slots=True)
class DiversityEvidence:
    value: float
    nearest_uuid: str | None = None
    similarity: float | None = None
    demoted: bool = False
    reason: str | None = None
    model_version: str | None = None


def diversity_evidence(
    assets: list[dict[str, object]],
    decisions: list[DecisionResult],
    signals: dict[str, dict[str, dict[str, object]]],
) -> dict[str, DiversityEvidence]:
    """Explain and limit scene dominance without changing the aesthetic score.

    The function only demotes unprotected automatic Keep decisions. It uses the already
    persisted Vision feature print and falls back to the previous temporal scene guard
    when semantic features are unavailable.
    """
    result = {str(asset["asset_uuid"]): DiversityEvidence(value=50.0) for asset in assets}
    candidates: list[tuple[dict[str, object], DecisionResult]] = []
    protected: set[str] = set()
    for asset, decision in zip(assets, decisions, strict=True):
        asset_uuid = str(asset["asset_uuid"])
        if decision.disposition != "keep":
            continue
        if _is_protected(asset, decision):
            protected.add(asset_uuid)
            result[asset_uuid] = DiversityEvidence(value=100.0, reason="protected")
            continue
        candidates.append((asset, decision))

    temporal_demoted = _temporal_demotions(candidates)
    for asset_uuid in temporal_demoted:
        result[asset_uuid] = DiversityEvidence(
            value=10.0,
            demoted=True,
            reason="temporal_scene_limit",
            model_version=DIVERSITY_MODEL_VERSION,
        )

    vectors: dict[str, np.ndarray] = {}
    for asset, _ in candidates:
        asset_uuid = str(asset["asset_uuid"])
        if asset_uuid in temporal_demoted:
            continue
        signal = signals.get(asset_uuid, {}).get("feature_print")
        if not signal:
            continue
        try:
            _, vector, _ = feature_vector(signal)
        except TasteProfileError:
            continue
        norm = float(np.linalg.norm(vector))
        if norm > 1e-8:
            vectors[asset_uuid] = vector / norm

    ranked = sorted(
        (
            (asset, decision)
            for asset, decision in candidates
            if str(asset["asset_uuid"]) not in temporal_demoted
        ),
        key=lambda item: (
            int(item[1].score),
            int(item[0].get("width") or 0) * int(item[0].get("height") or 0),
            str(item[0]["asset_uuid"]),
        ),
        reverse=True,
    )
    selected_vectors: list[tuple[str, np.ndarray]] = []
    for asset, _ in ranked:
        asset_uuid = str(asset["asset_uuid"])
        vector = vectors.get(asset_uuid)
        if vector is None:
            if result[asset_uuid].value == 50.0:
                result[asset_uuid] = DiversityEvidence(value=50.0, reason="feature_unavailable")
            continue
        similarities = [
            (other_uuid, float(np.dot(vector, other_vector)))
            for other_uuid, other_vector in selected_vectors
        ]
        if not similarities:
            result[asset_uuid] = DiversityEvidence(
                value=100.0,
                reason="semantic_anchor",
                model_version=DIVERSITY_MODEL_VERSION,
            )
            selected_vectors.append((asset_uuid, vector))
            continue
        nearest_uuid, similarity = max(similarities, key=lambda item: item[1])
        similar_count = sum(value >= SEMANTIC_SIMILARITY_THRESHOLD for _, value in similarities)
        value = _diversity_value(similarity)
        demoted = (
            similarity >= SEMANTIC_SIMILARITY_THRESHOLD and similar_count >= MAX_SIMILAR_FRAMES
        )
        result[asset_uuid] = DiversityEvidence(
            value=value,
            nearest_uuid=nearest_uuid,
            similarity=round(similarity, 4),
            demoted=demoted,
            reason="semantic_scene_limit" if demoted else "semantic_distance",
            model_version=DIVERSITY_MODEL_VERSION,
        )
        if not demoted:
            selected_vectors.append((asset_uuid, vector))
    return result


def _temporal_demotions(
    candidates: list[tuple[dict[str, object], DecisionResult]],
) -> set[str]:
    timestamped = []
    for asset, decision in candidates:
        try:
            taken_at = datetime.fromisoformat(str(asset.get("taken_at")))
        except (TypeError, ValueError):
            continue
        timestamped.append((taken_at, asset, decision))
    timestamped.sort(key=lambda item: item[0])
    scenes: list[list[tuple[datetime, dict[str, object], DecisionResult]]] = []
    for candidate in timestamped:
        if not scenes or (candidate[0] - scenes[-1][0][0]).total_seconds() > TEMPORAL_SCENE_SECONDS:
            scenes.append([candidate])
        else:
            scenes[-1].append(candidate)
    demoted = set()
    for scene in scenes:
        ranked = sorted(
            scene,
            key=lambda item: (
                int(item[2].score),
                int(item[1].get("width") or 0) * int(item[1].get("height") or 0),
                str(item[1]["asset_uuid"]),
            ),
            reverse=True,
        )
        demoted.update(str(item[1]["asset_uuid"]) for item in ranked[MAX_SIMILAR_FRAMES:])
    return demoted


def _is_protected(asset: dict[str, object], decision: DecisionResult) -> bool:
    return bool(
        asset.get("favorite")
        or asset.get("has_adjustments")
        or "duplicate_leader" in decision.flags
    )


def _diversity_value(similarity: float) -> float:
    # A cosine similarity of 0.65 or lower is already visually distinct enough.
    value = (1.0 - max(-1.0, min(1.0, similarity))) / 0.35 * 100.0
    return round(max(0.0, min(100.0, value)), 2)
