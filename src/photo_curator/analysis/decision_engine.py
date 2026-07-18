from __future__ import annotations

from dataclasses import dataclass

PROTECTED_FLAGS = {
    "favorite_protected",
    "edited_protected",
    "missing_preview",
    "analysis_error",
    "duplicate_leader",
    "ambiguous_duplicate",
    "leader_lower_resolution",
    "resolution_inversion",
}


@dataclass(frozen=True, slots=True)
class DecisionResult:
    disposition: str
    confidence: float
    flags: list[str]
    reasons: list[dict[str, object]]


def decide_asset(asset: dict[str, object], duplicate: dict[str, object] | None) -> DecisionResult:
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
            }
        )
    metric_flags = _metric_flags(asset)
    flags.update(metric_flags)
    reasons.extend({"code": flag} for flag in sorted(metric_flags))
    if flags & PROTECTED_FLAGS:
        return DecisionResult("review", 0.75, sorted(flags), reasons)
    if duplicate and not duplicate.get("is_leader"):
        confidence = float(duplicate.get("confidence") or 0.0)
        if duplicate.get("kind") == "exact" or confidence >= 0.92:
            return DecisionResult("reject", confidence, sorted(flags), reasons)
        return DecisionResult("review", confidence, sorted(flags), reasons)
    if metric_flags:
        return DecisionResult("review", 0.65, sorted(flags), reasons)
    return DecisionResult("keep", 0.7, sorted(flags), reasons)


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
    return flags
