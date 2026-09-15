"""Two review buckets. A removal suggestion is never permission to delete a Photos asset."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from photo_curator.analysis.decision_engine import DecisionResult


def automatic_culling(
    asset: dict, decision: DecisionResult, duplicate: dict | None
) -> tuple[str, str]:
    flags = set(decision.flags)
    if flags & {
        "duplicate_leader",
        "favorite_protected",
        "edited_protected",
        "missing_preview",
        "analysis_error",
        "ambiguous_duplicate",
        "resolution_inversion",
    }:
        return "keep", "protected_or_uncertain"
    if duplicate and duplicate.get("is_leader"):
        return "keep", "series_representative"
    if decision.disposition == "reject":
        return "reject", "confirmed_duplicate_or_defect"
    if (
        duplicate
        and not duplicate.get("is_leader")
        and not duplicate.get("recommended_pick")
        and float(duplicate.get("confidence") or 0) >= 0.92
    ):
        return "reject", "redundant_frame"
    # Absolute, corroborated technical signals; relative rank and aesthetics are excluded.
    if flags & {"motion_blur", "defocus_blur"} and "possible_blur" in flags:
        return "reject", "visible_blur_candidate"
    if flags & {"underexposed", "overexposed"} and "low_contrast" in flags:
        return "reject", "exposure_detail_loss"
    return "keep", "no_clear_removal_reason"


def effective_culling_sql(alias: str = "d") -> str:
    return f"""CASE
        WHEN {alias}.manual_disposition IS NOT NULL THEN
            CASE WHEN {alias}.manual_disposition='reject' THEN 'reject' ELSE 'keep' END
        WHEN {alias}.manual_selection IS NOT NULL THEN
            CASE WHEN {alias}.manual_selection='reject' THEN 'reject' ELSE 'keep' END
        ELSE COALESCE({alias}.auto_culling, 'keep') END"""


def culling_category(asset: dict) -> str:
    manual = asset.get("manual_disposition") or asset.get("manual_selection")
    if manual is not None:
        return "reject" if manual == "reject" else "keep"
    return str(
        asset.get("auto_culling")
        or ("reject" if asset.get("final_disposition") == "reject" else "keep")
    )


def selection_filter_sql(alias: str, selection: str) -> str:
    if selection in {"cull_keep", "cull_reject"}:
        return f"({effective_culling_sql(alias)}) = ?"
    return f"{alias}.final_selection = ?"


def selection_filter_value(selection: str) -> str:
    return selection.removeprefix("cull_")
