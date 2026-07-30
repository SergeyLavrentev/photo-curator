from __future__ import annotations

from dataclasses import dataclass
from math import ceil

from photo_curator.analysis.swipe_score import SwipeScoreResult


@dataclass(frozen=True, slots=True)
class DecisionResult:
    disposition: str
    confidence: float
    score: int
    components: dict[str, int]
    flags: list[str]
    reasons: list[dict[str, object]]


DECISION_MODEL_VERSION = 2
SELECTED_THRESHOLDS = {
    "compact": 82,
    "balanced": 74,
    "broad": 58,
}
SELECTION_RATIOS = {
    "compact": 0.25,
    "balanced": 0.45,
    "broad": 0.65,
}


def album_selection_threshold(scores: list[int], selection_density: str) -> int:
    """Return a strict, album-relative cutoff for the requested result size."""
    if not scores:
        return SELECTED_THRESHOLDS.get(selection_density, 74)
    ranked = sorted((max(0, min(100, int(score))) for score in scores), reverse=True)
    target = max(1, ceil(len(ranked) * SELECTION_RATIOS.get(selection_density, 0.45)))
    relative_cutoff = ranked[min(target - 1, len(ranked) - 1)]
    return max(SELECTED_THRESHOLDS.get(selection_density, 74), relative_cutoff)


def binary_disposition(
    score: int,
    selection_density: str,
    flags: set[str] | list[str] | tuple[str, ...] = (),
    *,
    selected_threshold: int | None = None,
) -> str:
    """Resolve every automatic result into the two user-facing buckets."""
    flag_set = set(flags)
    if flag_set & {"missing_preview", "analysis_error", "ambiguous_duplicate"}:
        return "keep"
    threshold = selected_threshold or SELECTED_THRESHOLDS.get(selection_density, 74)
    return "keep" if score >= threshold else "reject"


def decide_asset(
    asset: dict[str, object],
    duplicate: dict[str, object] | None,
    selection_density: str = "balanced",
    swipe_score: SwipeScoreResult | None = None,
    *,
    selected_threshold: int | None = None,
) -> DecisionResult:
    flags: set[str] = set()
    if bool(asset.get("favorite")):
        flags.add("favorite_protected")
    if bool(asset.get("has_adjustments")):
        flags.add("edited_protected")
    if asset.get("cache_state") != "ready":
        flags.add("missing_preview")
    if asset.get("cache_state") in {"error", "analysis_error"}:
        flags.add("analysis_error")
    metadata = asset.get("metadata") or {}
    if metadata.get("provider_error"):
        flags.add("analysis_error")
    if metadata.get("render_warning"):
        flags.add(str(metadata["render_warning"]))
    if duplicate:
        flags.update(duplicate.get("flags", []))
        if duplicate.get("is_leader"):
            flags.add("duplicate_leader")
        else:
            flags.add("duplicate_loser")
    metric_flags = _metric_flags(asset)
    flags.update(metric_flags)
    if swipe_score is None:
        score, components = _selection_score(asset, duplicate, metric_flags)
        decision_confidence = 0.65
    else:
        score = swipe_score.score
        components = {key: round(value) for key, value in swipe_score.components.items()}
        decision_confidence = swipe_score.confidence
    threshold = selected_threshold or SELECTED_THRESHOLDS.get(selection_density, 74)
    if flags & {"missing_preview", "analysis_error", "ambiguous_duplicate"}:
        return _result(
            "keep",
            0.5,
            score,
            components,
            flags,
            duplicate,
            swipe_score,
            threshold,
            leading_reason="analysis_unavailable_kept",
        )
    if bool(asset.get("favorite")) or bool(asset.get("has_adjustments")):
        leading = "favorite_protected" if asset.get("favorite") else "edited_protected"
        return _result(
            "keep",
            0.9,
            score,
            components,
            flags,
            duplicate,
            swipe_score,
            threshold,
            leading_reason=leading,
        )
    if duplicate and not duplicate.get("is_leader"):
        confidence = float(duplicate.get("confidence") or 0.0)
        quality_margin = float(duplicate.get("quality_margin") or 0.0)
        if duplicate.get("kind") == "exact" or (confidence >= 0.92 and quality_margin >= 0.08):
            return _result(
                "reject",
                confidence,
                score,
                components,
                flags,
                duplicate,
                swipe_score,
                threshold,
                leading_reason="weaker_duplicate",
            )
        # A weak near-duplicate signal is not enough on its own. The regular
        # selection threshold below decides whether the frame stays.
    if duplicate and duplicate.get("is_leader"):
        return _result(
            "keep",
            0.9,
            score,
            components,
            flags,
            duplicate,
            swipe_score,
            threshold,
            leading_reason="best_in_series",
        )
    disposition = "keep" if score >= threshold else "reject"
    confidence = _threshold_confidence(score, threshold, decision_confidence)
    return _result(
        disposition,
        confidence,
        score,
        components,
        flags,
        duplicate,
        swipe_score,
        threshold,
    )


def _threshold_confidence(score: int, threshold: int, signal_confidence: float) -> float:
    distance_confidence = 0.52 + min(0.38, abs(score - threshold) / 35.0)
    return round(min(0.95, distance_confidence * 0.75 + signal_confidence * 0.25), 3)


def _result(
    disposition: str,
    confidence: float,
    score: int,
    components: dict[str, int],
    flags: set[str],
    duplicate: dict[str, object] | None,
    swipe_score: SwipeScoreResult | None,
    threshold: int,
    *,
    leading_reason: str | None = None,
) -> DecisionResult:
    reasons = _decision_reasons(
        disposition,
        score,
        components,
        flags,
        duplicate,
        swipe_score,
        threshold,
        leading_reason=leading_reason,
    )
    reasons.append({"code": "selection_score", "score": score, "components": components})
    if swipe_score:
        reasons.append(
            {
                "code": "swipe_score",
                "schema_version": swipe_score.schema_version,
                "score": swipe_score.score,
                "generic_score": swipe_score.generic_score,
                "personal_delta": swipe_score.personal_delta,
                "confidence": swipe_score.confidence,
                "components": swipe_score.components,
                "model_versions": swipe_score.model_versions,
            }
        )
    return DecisionResult(
        disposition,
        round(confidence, 3),
        score,
        components,
        sorted(flags),
        reasons,
    )


def _decision_reasons(
    disposition: str,
    score: int,
    components: dict[str, int],
    flags: set[str],
    duplicate: dict[str, object] | None,
    swipe_score: SwipeScoreResult | None,
    threshold: int,
    *,
    leading_reason: str | None,
) -> list[dict[str, object]]:
    reasons: list[dict[str, object]] = []

    def add(code: str, **values: object) -> None:
        if code not in {str(item["code"]) for item in reasons}:
            reasons.append({"code": code, **values})

    if leading_reason:
        add(leading_reason)
    if disposition == "keep":
        if duplicate and duplicate.get("is_leader"):
            add("best_in_series")
        positive_codes = {
            "strong_aesthetics",
            "strong_composition",
            "interesting_subject",
            "strong_moment",
            "personal_taste_match",
            "adds_variety",
        }
        for reason in swipe_score.reasons if swipe_score else []:
            code = str(reason.get("code") or "")
            if code in positive_codes:
                add(code, value=reason.get("value"))
        if not reasons:
            add("above_album_cutoff", score=score, threshold=threshold)
        return reasons[:3]

    negative_flag_order = (
        "possible_blur",
        "underexposed",
        "overexposed",
        "low_contrast",
        "apple_low_overall",
    )
    for code in negative_flag_order:
        if code in flags:
            add(code)
    if duplicate and not duplicate.get("is_leader"):
        add("weaker_duplicate")
    if swipe_score:
        values = swipe_score.components
        if float(values.get("generic_aesthetics") or 50) < 50:
            add("weak_aesthetics")
        if float(values.get("composition_and_attention") or 50) < 50:
            add("weak_composition")
        if float(values.get("content_appeal") or 50) < 50:
            add("weak_subject")
        if float(values.get("moment_and_subject") or 50) < 50:
            add("weak_moment")
        if swipe_score.personal_delta <= -2:
            add("personal_taste_mismatch")
        if float(values.get("technical_penalty") or 0) > 0:
            add("technical_penalty", value=values["technical_penalty"])
    add("below_album_cutoff", score=score, threshold=threshold)
    return reasons[:3]


def _selection_score(
    asset: dict[str, object],
    duplicate: dict[str, object] | None,
    metric_flags: set[str],
) -> tuple[int, dict[str, int]]:
    quality = _percent(asset.get("technical_quality"), fallback=50)
    sharpness = _percent(asset.get("sharpness_percentile"), fallback=quality)
    contrast = _percent(asset.get("contrast_percentile"), fallback=quality)
    apple = _percent(asset.get("apple_overall_percentile"), fallback=quality)
    exposure = 100
    luma = asset.get("luma_mean")
    if luma is not None:
        exposure = max(0, round(100 - abs(float(luma) - 0.5) * 180))
    penalty = 0
    if "possible_blur" in metric_flags:
        penalty += 18
    if metric_flags & {"underexposed", "overexposed"}:
        penalty += 18
    if "low_contrast" in metric_flags:
        penalty += 12
    if duplicate and not duplicate.get("is_leader"):
        penalty += 55 if duplicate.get("kind") == "exact" else 28
    bonus = 10 if asset.get("favorite") else 0
    bonus += 6 if asset.get("has_adjustments") else 0
    bonus += 8 if duplicate and duplicate.get("is_leader") else 0
    face_quality = asset.get("face_capture_quality")
    if face_quality is not None:
        bonus += round(float(face_quality) * 8)
    if int(asset.get("face_count") or 0) and not int(asset.get("eyes_detected") or 0):
        penalty += 4
    series_rank = 0
    selection_confidence = 70
    if duplicate:
        selection_confidence = round(float(duplicate.get("confidence") or 0) * 100)
        if duplicate.get("is_leader"):
            series_rank = 100
        else:
            margin = max(0.0, float(duplicate.get("quality_margin") or 0))
            series_rank = max(0, round(75 - margin * 100))
    score = round(
        quality * 0.35
        + sharpness * 0.25
        + contrast * 0.12
        + exposure * 0.18
        + apple * 0.10
        + bonus
        - penalty
    )
    components = {
        "quality": quality,
        "sharpness": sharpness,
        "contrast": contrast,
        "exposure": exposure,
        "apple": apple,
        "bonus": bonus,
        "penalty": penalty,
        "face_quality": _percent(face_quality, fallback=0),
        "series_rank": series_rank,
        "selection_confidence": selection_confidence,
    }
    return max(0, min(100, score)), components


def _percent(value: object, *, fallback: int) -> int:
    return round(float(value) * 100) if value is not None else fallback


def _metric_flags(asset: dict[str, object]) -> set[str]:
    flags = set()
    sharpness = asset.get("sharpness_percentile")
    if sharpness is not None and float(sharpness) <= 0.10:
        flags.add("possible_blur")
    luma = asset.get("luma_mean")
    if luma is not None and float(luma) < 0.12:
        flags.add("underexposed")
    if luma is not None and float(luma) > 0.90:
        flags.add("overexposed")
    contrast = asset.get("contrast_percentile")
    if contrast is not None and float(contrast) <= 0.08:
        flags.add("low_contrast")
    apple = asset.get("apple_overall_percentile")
    if apple is not None and float(apple) <= 0.05:
        flags.add("apple_low_overall")
    return flags
