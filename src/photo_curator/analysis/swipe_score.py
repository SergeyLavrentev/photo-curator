from __future__ import annotations

from dataclasses import dataclass

from photo_curator.analysis.normalization import percentile_ranks

SWIPE_SCORE_SCHEMA_VERSION = 1
POSITIVE_APPLE_SCORE_KEYS = {
    "content_appeal": (
        "interesting_subject",
        "well_chosen_subject",
        "immersiveness",
        "interaction",
        "harmonious_color",
        "lively_color",
    ),
    "composition_and_attention": (
        "pleasant_composition",
        "well_framed_subject",
        "pleasant_perspective",
        "pleasant_symmetry",
        "pleasant_camera_tilt",
    ),
    "moment_and_subject": (
        "well_timed_shot",
        "well_chosen_subject",
        "interesting_subject",
        "pleasant_lighting",
    ),
}


@dataclass(frozen=True, slots=True)
class SwipeScoreResult:
    schema_version: int
    score: int
    generic_score: float
    personal_delta: float
    confidence: float
    components: dict[str, float]
    reasons: list[dict[str, object]]
    model_versions: dict[str, str]


def apple_score_percentiles(
    assets: list[dict[str, object]],
) -> dict[str, dict[str, float]]:
    """Normalize positive internal Photos scores within one project.

    A zero positive score is treated as unavailable because Photos commonly stores zero
    sentinels for attributes it did not emit. Defect scores are intentionally not included.
    """
    keys = sorted({key for values in POSITIVE_APPLE_SCORE_KEYS.values() for key in values})
    result: dict[str, dict[str, float]] = {str(asset["asset_uuid"]): {} for asset in assets}
    for key in keys:
        values = []
        for asset in assets:
            raw = (asset.get("apple_scores") or {}).get(key)
            value = float(raw) if isinstance(raw, (int, float)) and float(raw) != 0 else None
            values.append(value)
        for asset, rank in zip(assets, percentile_ranks(values), strict=True):
            if rank is not None:
                result[str(asset["asset_uuid"])][key] = rank
    return result


def calculate_swipe_score(
    asset: dict[str, object],
    duplicate: dict[str, object] | None,
    signals: dict[str, dict[str, object]],
    *,
    apple_percentiles: dict[str, float] | None = None,
    personal_delta: float = 0.0,
    taste_model_version: str | None = None,
    taste_reliability: float = 0.0,
) -> SwipeScoreResult:
    apple_percentiles = apple_percentiles or {}
    aesthetics = _signal_value(signals.get("aesthetics"))
    codex = _signal_value(signals.get("codex_vision"))
    generic_source = "neutral"
    if aesthetics is not None and isinstance(aesthetics.get("overall_score"), (int, float)):
        generic = _clamp((float(aesthetics["overall_score"]) + 1.0) * 50.0)
        generic_source = "apple_vision_aesthetics"
    elif asset.get("apple_overall_percentile") is not None:
        generic = _clamp(float(asset["apple_overall_percentile"]) * 100.0)
        generic_source = "apple_photos_overall"
    else:
        generic = 50.0
    if codex is not None and isinstance(codex.get("aesthetic_score"), (int, float)):
        generic = _clamp(float(codex["aesthetic_score"]))
        generic_source = "codex_vision"

    content = _positive_component(apple_percentiles, POSITIVE_APPLE_SCORE_KEYS["content_appeal"])
    composition = _positive_component(
        apple_percentiles,
        POSITIVE_APPLE_SCORE_KEYS["composition_and_attention"],
    )
    saliency = _signal_value(signals.get("attention_saliency"))
    attention: float | None = None
    if saliency is not None:
        salient_objects = saliency.get("salient_objects")
        if isinstance(salient_objects, list):
            attention = {0: 50.0, 1: 88.0, 2: 78.0, 3: 66.0}.get(len(salient_objects), 55.0)
            composition = (
                attention if composition is None else (composition * 0.7) + (attention * 0.3)
            )
    moment = _positive_component(apple_percentiles, POSITIVE_APPLE_SCORE_KEYS["moment_and_subject"])
    if codex is not None:
        if isinstance(codex.get("interestingness_score"), (int, float)):
            content = _clamp(float(codex["interestingness_score"]))
        if isinstance(codex.get("composition_score"), (int, float)):
            composition = _clamp(float(codex["composition_score"]))
        if isinstance(codex.get("moment_score"), (int, float)):
            moment = _clamp(float(codex["moment_score"]))
    faces = _signal_value(signals.get("faces"))
    portrait_signal: float | None = None
    if faces is not None and int(faces.get("face_count") or 0) > 0:
        quality = faces.get("best_capture_quality")
        portrait_signal = _clamp(float(quality) * 100.0) if quality is not None else 50.0

    series = _series_score(duplicate)
    penalty = _technical_penalty(asset, duplicate)
    values_and_weights = [
        (generic, 0.50),
        (series, 0.08),
    ]
    for value, weight in ((content, 0.18), (composition, 0.14), (moment, 0.10)):
        if value is not None:
            values_and_weights.append((value, weight))
    if portrait_signal is not None:
        values_and_weights.append((portrait_signal, 0.08))
    weighted = sum(value * weight for value, weight in values_and_weights) / sum(
        weight for _, weight in values_and_weights
    )
    utility_penalty = 8.0 if aesthetics is not None and aesthetics.get("is_utility") else 0.0
    codex_defects = codex.get("defects", []) if codex is not None else []
    codex_penalty = min(20.0, 7.0 * len(codex_defects)) if isinstance(codex_defects, list) else 0.0
    generic_rank_score = _clamp(weighted - penalty - utility_penalty - codex_penalty)
    personal_delta = max(-20.0, min(20.0, float(personal_delta)))
    score = round(_clamp(generic_rank_score + personal_delta))

    available = {kind for kind, signal in signals.items() if signal.get("status") == "ready"}
    confidence = 0.42
    confidence += 0.25 if "aesthetics" in available else 0
    confidence += 0.08 if apple_percentiles else 0
    confidence += 0.08 if "attention_saliency" in available else 0
    confidence += 0.05 if "feature_print" in available else 0
    confidence += 0.05 if portrait_signal is not None and "faces" in available else 0
    confidence += 0.07 if duplicate is not None else 0
    confidence += 0.18 if "codex_vision" in available else 0
    confidence += 0.05 * max(0.0, min(1.0, taste_reliability))
    confidence = min(0.98, confidence)

    components = {
        "generic_aesthetics": round(generic, 2),
        "content_appeal": round(content if content is not None else 50.0, 2),
        "composition_and_attention": round(composition if composition is not None else 50.0, 2),
        "moment_and_subject": round(moment if moment is not None else 50.0, 2),
        "portrait_signal": round(portrait_signal if portrait_signal is not None else 50.0, 2),
        "best_in_series": round(series, 2),
        "personal_taste": round(_clamp(50.0 + personal_delta * 2.5), 2),
        "personal_taste_reliability": round(max(0.0, min(1.0, taste_reliability)) * 100.0, 2),
        "diversity_value": 50.0,
        "technical_penalty": round(penalty + utility_penalty + codex_penalty, 2),
    }
    reasons = _reasons(
        components,
        generic_source=generic_source,
        utility=bool(aesthetics and aesthetics.get("is_utility")),
        duplicate=duplicate,
        personal_delta=personal_delta,
    )
    model_versions = _model_versions(signals)
    if taste_model_version:
        model_versions["personal_taste"] = taste_model_version
    return SwipeScoreResult(
        schema_version=SWIPE_SCORE_SCHEMA_VERSION,
        score=score,
        generic_score=round(generic_rank_score, 2),
        personal_delta=round(personal_delta, 2),
        confidence=round(confidence, 3),
        components=components,
        reasons=reasons,
        model_versions=model_versions,
    )


def _signal_value(signal: dict[str, object] | None) -> dict[str, object] | None:
    if not signal or signal.get("status") != "ready":
        return None
    value = signal.get("value")
    return value if isinstance(value, dict) else None


def _positive_component(values: dict[str, float], keys: tuple[str, ...]) -> float | None:
    available = [float(values[key]) * 100.0 for key in keys if key in values]
    return sum(available) / len(available) if available else None


def _series_score(duplicate: dict[str, object] | None) -> float:
    if duplicate is None:
        return 70.0
    if duplicate.get("is_leader"):
        return 100.0
    confidence = float(duplicate.get("confidence") or 0.0)
    margin = max(0.0, float(duplicate.get("quality_margin") or 0.0))
    return _clamp(65.0 - confidence * 25.0 - margin * 100.0)


def _technical_penalty(asset: dict[str, object], duplicate: dict[str, object] | None) -> float:
    penalty = 0.0
    sharpness = asset.get("sharpness_percentile")
    if sharpness is not None and float(sharpness) <= 0.10:
        penalty += 12.0
    luma = asset.get("luma_mean")
    if luma is not None and (float(luma) < 0.12 or float(luma) > 0.90):
        penalty += 14.0
    contrast = asset.get("contrast_percentile")
    if contrast is not None and float(contrast) <= 0.08:
        penalty += 8.0
    if duplicate and not duplicate.get("is_leader"):
        penalty += 45.0 if duplicate.get("kind") == "exact" else 18.0
    return min(65.0, penalty)


def _reasons(
    components: dict[str, float],
    *,
    generic_source: str,
    utility: bool,
    duplicate: dict[str, object] | None,
    personal_delta: float,
) -> list[dict[str, object]]:
    candidates = [
        (components["generic_aesthetics"], "strong_aesthetics"),
        (components["composition_and_attention"], "strong_composition"),
        (components["content_appeal"], "interesting_subject"),
        (components["moment_and_subject"], "strong_moment"),
    ]
    reasons: list[dict[str, object]] = []
    if personal_delta >= 2:
        reasons.append({"code": "personal_taste_match", "value": round(personal_delta, 1)})
    if duplicate and duplicate.get("is_leader"):
        reasons.append({"code": "best_in_series", "value": components["best_in_series"]})
    for value, code in sorted(candidates, reverse=True):
        if value >= 60 and len(reasons) < 3:
            reasons.append({"code": code, "value": round(value, 1), "source": generic_source})
    if utility and len(reasons) < 3:
        reasons.append({"code": "utility_image"})
    if components["technical_penalty"] > 0 and len(reasons) < 3:
        reasons.append({"code": "technical_penalty", "value": components["technical_penalty"]})
    return reasons[:3] or [{"code": "neutral_baseline", "source": generic_source}]


def _model_versions(signals: dict[str, dict[str, object]]) -> dict[str, str]:
    result = {}
    for kind, signal in sorted(signals.items()):
        engine = signal.get("engine_name")
        version = signal.get("engine_version")
        revision = signal.get("request_revision")
        if engine and version:
            suffix = f"/revision-{revision}" if revision is not None else ""
            result[kind] = f"{engine}@{version}{suffix}"
    return result


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))
