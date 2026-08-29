from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from photo_curator.analysis.ensemble import validated_ensemble_weights
from photo_curator.analysis.normalization import percentile_ranks

SWIPE_SCORE_SCHEMA_VERSION = 2
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


def engine_score_percentiles(
    assets: list[dict[str, object]],
    signals_by_asset: dict[str, dict[str, dict[str, object]]],
) -> dict[str, dict[str, float]]:
    """Normalize ensemble inputs within an album before applying learned weights."""
    result: dict[str, dict[str, float]] = {str(asset["asset_uuid"]): {} for asset in assets}
    specifications = {
        "apple": ("aesthetics", "overall_score", lambda value: (value + 1.0) * 50.0),
        "nima": ("nima_aesthetics", "aesthetic_score", lambda value: value),
        "mobileclip": ("mobileclip", "aesthetic_score", lambda value: value),
        "codex": ("codex_vision", "aesthetic_score", lambda value: value),
    }
    for output_key, (signal_kind, value_key, transform) in specifications.items():
        values: list[float | None] = []
        for asset in assets:
            signal = _signal_value(
                signals_by_asset.get(str(asset["asset_uuid"]), {}).get(signal_kind)
            )
            raw = signal.get(value_key) if signal else None
            values.append(transform(float(raw)) if isinstance(raw, (int, float)) else None)
        for asset, rank in zip(assets, percentile_ranks(values), strict=True):
            if rank is not None:
                result[str(asset["asset_uuid"])][output_key] = rank * 100.0
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
    ensemble_model: dict[str, object] | None = None,
    ensemble_percentiles: dict[str, float] | None = None,
    codex_ranking_validated: bool = False,
) -> SwipeScoreResult:
    apple_percentiles = apple_percentiles or {}
    aesthetics = _signal_value(signals.get("aesthetics"))
    codex = _signal_value(signals.get("codex_vision"))
    nima = _signal_value(signals.get("nima_aesthetics"))
    mobileclip = _signal_value(signals.get("mobileclip"))
    musiq = _signal_value(signals.get("musiq_quality"))
    generic_source = "neutral"
    validated_weights = validated_ensemble_weights(ensemble_model)
    weights = validated_weights or {"apple": 1.0, "nima": 0.0, "mobileclip": 0.0}
    ensemble_percentiles = ensemble_percentiles or {}
    local_scores: list[tuple[float, float]] = []
    if aesthetics is not None and isinstance(aesthetics.get("overall_score"), (int, float)):
        apple_aesthetics = _clamp((float(aesthetics["overall_score"]) + 1.0) * 50.0)
        local_scores.append(
            (
                ensemble_percentiles.get("apple", apple_aesthetics),
                weights["apple"],
            )
        )
        generic_source = "apple_vision_aesthetics"
    elif asset.get("apple_overall_percentile") is not None:
        apple_aesthetics = _clamp(float(asset["apple_overall_percentile"]) * 100.0)
        local_scores.append(
            (
                ensemble_percentiles.get("apple", apple_aesthetics),
                weights["apple"],
            )
        )
        generic_source = "apple_photos_overall"
    else:
        apple_aesthetics = None
    nima_score = _numeric_score(nima, "aesthetic_score")
    mobileclip_score = _numeric_score(mobileclip, "aesthetic_score")
    musiq_score = _numeric_score(musiq, "quality_score")
    codex_aesthetics = _numeric_score(codex, "aesthetic_score") if codex_ranking_validated else None
    model_disagreement, model_count = _model_disagreement(
        apple=apple_aesthetics,
        nima=nima_score,
        mobileclip=mobileclip_score,
        codex=codex_aesthetics,
        normalized=ensemble_percentiles,
    )
    for key, value, weight in (
        ("nima", nima_score, weights["nima"]),
        ("mobileclip", mobileclip_score, weights["mobileclip"]),
    ):
        if value is not None and validated_weights is not None and weight > 0:
            local_scores.append((ensemble_percentiles.get(key, value), weight))
    if local_scores:
        generic = sum(value * weight for value, weight in local_scores) / sum(
            weight for _, weight in local_scores
        )
        if len(local_scores) > 1 or apple_aesthetics is None:
            generic_source = "local_model_consensus"
    else:
        generic = 50.0
    if (
        codex_ranking_validated
        and codex is not None
        and isinstance(codex.get("aesthetic_score"), (int, float))
    ):
        generic = _clamp(float(codex["aesthetic_score"]))
        generic_source = "codex_vision"

    content = _positive_component(apple_percentiles, POSITIVE_APPLE_SCORE_KEYS["content_appeal"])
    composition = _positive_component(
        apple_percentiles,
        POSITIVE_APPLE_SCORE_KEYS["composition_and_attention"],
    )
    moment = _positive_component(apple_percentiles, POSITIVE_APPLE_SCORE_KEYS["moment_and_subject"])
    if codex_ranking_validated and codex is not None:
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
    if musiq_score is not None:
        # MUSIQ measures image quality, not attractiveness. It is a bounded technical
        # penalty until a held-out ablation proves an aesthetic ranking contribution.
        penalty += max(0.0, min(5.0, (40.0 - musiq_score) * 0.15))
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
    generic_rank_score = _clamp(weighted - penalty - utility_penalty)
    personal_delta = max(-20.0, min(20.0, float(personal_delta)))
    score = round(_clamp(generic_rank_score + personal_delta))

    available = {kind for kind, signal in signals.items() if _signal_value(signal) is not None}
    # Coverage and cross-model agreement are reliability evidence, not a probability.
    # DecisionResult exposes a probability only after an album-held-out calibrator validates.
    evidence_coverage = 0.30
    evidence_coverage += 0.20 if "aesthetics" in available else 0
    evidence_coverage += 0.08 if apple_percentiles else 0
    evidence_coverage += 0.03 if "attention_saliency" in available else 0
    evidence_coverage += 0.06 if "feature_print" in available else 0
    evidence_coverage += 0.04 if portrait_signal is not None and "faces" in available else 0
    evidence_coverage += 0.05 if duplicate is not None else 0
    evidence_coverage += (
        0.08 if validated_weights is not None and "nima_aesthetics" in available else 0
    )
    evidence_coverage += 0.10 if validated_weights is not None and "mobileclip" in available else 0
    evidence_coverage += 0.04 if "musiq_quality" in available else 0
    evidence_coverage += 0.10 if codex_ranking_validated and "codex_vision" in available else 0
    evidence_coverage += 0.04 * max(0.0, min(1.0, taste_reliability))
    evidence_coverage = min(0.90, evidence_coverage)
    if model_disagreement is None:
        # One aesthetic model cannot establish agreement with an independent model.
        confidence = min(0.70, evidence_coverage * 0.80)
    else:
        confidence = evidence_coverage * (1.0 - 0.65 * model_disagreement)
    confidence = max(0.20, min(0.90, confidence))

    components = {
        "generic_aesthetics": round(generic, 2),
        "apple_aesthetics": round(apple_aesthetics if apple_aesthetics is not None else 50.0, 2),
        "nima_aesthetics": round(nima_score if nima_score is not None else 50.0, 2),
        "mobileclip_aesthetics": round(
            mobileclip_score if mobileclip_score is not None else 50.0,
            2,
        ),
        "musiq_quality": round(musiq_score if musiq_score is not None else 50.0, 2),
        "content_appeal": round(content if content is not None else 50.0, 2),
        "composition_and_attention": round(composition if composition is not None else 50.0, 2),
        "moment_and_subject": round(moment if moment is not None else 50.0, 2),
        "portrait_signal": round(portrait_signal if portrait_signal is not None else 50.0, 2),
        "best_in_series": round(series, 2),
        "personal_taste": round(_clamp(50.0 + personal_delta * 2.5), 2),
        "personal_taste_reliability": round(max(0.0, min(1.0, taste_reliability)) * 100.0, 2),
        "diversity_value": 50.0,
        "technical_penalty": round(penalty + utility_penalty, 2),
        "evidence_coverage": round(evidence_coverage * 100.0, 2),
        "model_count": float(model_count),
        "model_disagreement": round((model_disagreement or 0.0) * 100.0, 2),
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
    if validated_weights:
        model_versions["generic_ensemble"] = str(ensemble_model["model_version"])
    if codex is not None and not codex_ranking_validated:
        model_versions["codex_ranking"] = "advisory-unvalidated"
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


def _numeric_score(value: dict[str, object] | None, key: str) -> float | None:
    if value is None or not isinstance(value.get(key), (int, float)):
        return None
    return _clamp(float(value[key]))


def _model_disagreement(
    *,
    apple: float | None,
    nima: float | None,
    mobileclip: float | None,
    codex: float | None,
    normalized: dict[str, float],
) -> tuple[float | None, int]:
    """Measure robust dispersion across independent aesthetic models.

    Album percentiles are preferred for local models so their different training scales do not
    masquerade as disagreement. Codex participates only after its ranking contract is validated.
    A single model yields unknown disagreement rather than a false claim of perfect agreement.
    """
    values = []
    for key, raw in (("apple", apple), ("nima", nima), ("mobileclip", mobileclip)):
        if raw is not None:
            values.append(_clamp(float(normalized.get(key, raw))))
    if codex is not None:
        values.append(_clamp(float(normalized.get("codex", codex))))
    if len(values) < 2:
        return None, len(values)
    center = median(values)
    mean_absolute_deviation = sum(abs(value - center) for value in values) / len(values)
    return min(1.0, mean_absolute_deviation / 50.0), len(values)


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
    laplacian = asset.get("subject_laplacian_variance")
    gradient = asset.get("subject_gradient_energy")
    if laplacian is None:
        laplacian = asset.get("laplacian_variance")
    if gradient is None:
        gradient = asset.get("gradient_energy")
    if (
        laplacian is not None
        and gradient is not None
        and float(laplacian) < 0.0004
        and float(gradient) < 0.00025
    ):
        penalty += 12.0
    black_clipped = asset.get("subject_black_clipped_ratio")
    white_clipped = asset.get("subject_white_clipped_ratio")
    luma_p05 = asset.get("subject_luma_p05")
    luma_p95 = asset.get("subject_luma_p95")
    if black_clipped is None:
        black_clipped = asset.get("black_clipped_ratio")
    if white_clipped is None:
        white_clipped = asset.get("white_clipped_ratio")
    if luma_p05 is None:
        luma_p05 = asset.get("luma_p05")
    if luma_p95 is None:
        luma_p95 = asset.get("luma_p95")
    clipped_exposure = (
        black_clipped is not None
        and luma_p95 is not None
        and float(black_clipped) >= 0.35
        and float(luma_p95) <= 0.35
    ) or (
        white_clipped is not None
        and luma_p05 is not None
        and float(white_clipped) >= 0.35
        and float(luma_p05) >= 0.65
    )
    if clipped_exposure:
        penalty += 14.0
    contrast_std = asset.get("contrast_std")
    dynamic_range = asset.get("dynamic_range")
    if (
        contrast_std is not None
        and dynamic_range is not None
        and float(contrast_std) < 0.06
        and float(dynamic_range) < 0.25
    ):
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
