from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DecisionResult:
    disposition: str
    confidence: float
    score: int
    components: dict[str, int]
    flags: list[str]
    reasons: list[dict[str, object]]


def decide_asset(
    asset: dict[str, object],
    duplicate: dict[str, object] | None,
    selection_density: str = "balanced",
) -> DecisionResult:
    flags: set[str] = set()
    reasons: list[dict[str, object]] = []
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
        reasons.append(
            {
                "code": "duplicate_group",
                "kind": duplicate.get("kind"),
                "confidence": duplicate.get("confidence"),
                "quality_margin": duplicate.get("quality_margin"),
            }
        )
    metric_flags = _metric_flags(asset)
    flags.update(metric_flags)
    reasons.extend({"code": flag} for flag in sorted(metric_flags))
    score, components = _selection_score(asset, duplicate, metric_flags)
    reasons.insert(0, {"code": "selection_score", "score": score, "components": components})
    if flags & {"missing_preview", "analysis_error", "ambiguous_duplicate"}:
        return DecisionResult("review", 0.75, score, components, sorted(flags), reasons)
    if bool(asset.get("favorite")) or bool(asset.get("has_adjustments")):
        return DecisionResult("keep", 0.9, score, components, sorted(flags), reasons)
    if duplicate and not duplicate.get("is_leader"):
        confidence = float(duplicate.get("confidence") or 0.0)
        quality_margin = float(duplicate.get("quality_margin") or 0.0)
        if duplicate.get("kind") == "exact" or (confidence >= 0.92 and quality_margin >= 0.08):
            return DecisionResult("reject", confidence, score, components, sorted(flags), reasons)
        return DecisionResult("review", confidence, score, components, sorted(flags), reasons)
    if duplicate and duplicate.get("is_leader"):
        return DecisionResult("keep", 0.9, score, components, sorted(flags), reasons)
    selected_threshold, excluded_threshold = {
        "compact": (82, 45),
        "balanced": (70, 38),
        "broad": (58, 30),
    }.get(selection_density, (70, 38))
    if score >= selected_threshold:
        return DecisionResult("keep", 0.8, score, components, sorted(flags), reasons)
    if score < excluded_threshold and metric_flags:
        return DecisionResult("reject", 0.72, score, components, sorted(flags), reasons)
    return DecisionResult("review", 0.65, score, components, sorted(flags), reasons)


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
