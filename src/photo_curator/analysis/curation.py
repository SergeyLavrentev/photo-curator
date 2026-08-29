from __future__ import annotations

from photo_curator.analysis.decision_engine import DecisionResult
from photo_curator.analysis.swipe_score import SwipeScoreResult

SELECTION_STATES = frozenset({"pick", "alternative", "review", "reject"})


def automatic_selection_state(
    asset: dict[str, object],
    decision: DecisionResult,
    duplicate: dict[str, object] | None,
    swipe_score: SwipeScoreResult,
    *,
    selected_threshold: int,
) -> str:
    """Separate safe deletion advice from membership in the proposed Best album."""
    if decision.disposition == "reject":
        return "reject"
    if decision.disposition == "review" or set(decision.flags) & {
        "missing_preview",
        "analysis_error",
        "ambiguous_duplicate",
    }:
        return "review"
    if any(reason.get("code") == "too_similar_to_selected" for reason in swipe_score.reasons):
        return "alternative"
    if duplicate is not None and not duplicate.get("is_leader"):
        if duplicate.get("recommended_pick") and swipe_score.score >= selected_threshold:
            return "pick"
        return "alternative"
    if bool(asset.get("favorite")) or bool(asset.get("has_adjustments")):
        return "pick"
    return "pick" if swipe_score.score >= selected_threshold else "alternative"
