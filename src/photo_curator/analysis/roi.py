from __future__ import annotations

import math
from collections.abc import Mapping

from PIL import Image

from photo_curator.analysis.normalization import percentile_ranks
from photo_curator.analysis.technical import subject_quality_metrics

ROI_ENGINE_VERSION = "1-series-shortlist"
ROI_MAX_MEMBERS_PER_GROUP = 8


def select_series_roi_candidates(
    groups: list[dict[str, object]],
    assets_by_uuid: Mapping[str, dict[str, object]],
) -> dict[str, dict[str, object]]:
    """Choose a bounded, reproducible shortlist without decoding image pixels."""
    selected: dict[str, dict[str, object]] = {}
    for group in groups:
        kind = str(group.get("kind") or "")
        if kind == "exact":
            continue
        flags = {str(flag) for flag in group.get("flags") or []}
        members = [
            assets_by_uuid[str(member["asset_uuid"])]
            for member in group.get("members") or []
            if str(member.get("asset_uuid") or "") in assets_by_uuid
        ]
        eligible = [
            asset
            for asset in members
            if asset.get("cache_state") == "ready"
            and not asset.get("no_longer_exists")
            and asset.get("review_path")
        ]
        leader_uuid = str(group.get("leader_uuid") or "")
        ranked = sorted(
            eligible,
            key=lambda asset: (
                str(asset["asset_uuid"]) == leader_uuid,
                float(asset.get("technical_quality") or 0.0),
                float(asset.get("sharpness_percentile") or 0.0),
                int(asset.get("width") or 0) * int(asset.get("height") or 0),
                str(asset["asset_uuid"]),
            ),
            reverse=True,
        )[:ROI_MAX_MEMBERS_PER_GROUP]
        for asset in ranked:
            selected[str(asset["asset_uuid"])] = {
                "group_id": str(group["group_id"]),
                "group_kind": kind,
                "ambiguous": kind == "ambiguous" or "ambiguous_duplicate" in flags,
                "shortlist_size": len(ranked),
                "group_member_count": len(members),
            }
    return selected


def measure_high_resolution_roi(
    image: Image.Image,
    rectangles: list[dict[str, object]],
    *,
    source: str,
) -> dict[str, object]:
    subject = subject_quality_metrics(image, rectangles, source=source)
    if not subject:
        return {}
    eye_rectangles = _eye_region_rectangles(rectangles) if source == "faces" else []
    eyes = (
        subject_quality_metrics(image, eye_rectangles, source="face_eye_regions")
        if eye_rectangles
        else {}
    )
    return {
        "image_width": image.width,
        "image_height": image.height,
        "roi_source": source,
        "region_count": int(subject["region_count"]),
        "subject_laplacian_variance": float(subject["subject_laplacian_variance"]),
        "subject_gradient_energy": float(subject["subject_gradient_energy"]),
        "subject_directional_coherence": float(subject["subject_directional_coherence"]),
        "subject_black_clipped_ratio": float(subject["subject_black_clipped_ratio"]),
        "subject_white_clipped_ratio": float(subject["subject_white_clipped_ratio"]),
        "eye_region_count": int(eyes.get("region_count") or 0),
        "eye_laplacian_variance": (float(eyes["subject_laplacian_variance"]) if eyes else None),
        "eye_gradient_energy": float(eyes["subject_gradient_energy"]) if eyes else None,
        # These are continuous, uncalibrated measurements. They do not claim blink/open-eye.
        "eye_metric_contract": "sharpness_only_not_blink",
    }


def normalize_series_roi(
    measured_by_uuid: Mapping[str, dict[str, object]],
) -> dict[str, dict[str, object]]:
    uuids = sorted(measured_by_uuid)
    subject_values = [
        _log_metric(measured_by_uuid[asset_uuid].get("subject_laplacian_variance"))
        for asset_uuid in uuids
    ]
    eye_values = [
        _log_metric(measured_by_uuid[asset_uuid].get("eye_laplacian_variance"))
        for asset_uuid in uuids
    ]
    subject_percentiles = percentile_ranks(subject_values)
    eye_percentiles = percentile_ranks(eye_values)
    result: dict[str, dict[str, object]] = {}
    for asset_uuid, subject_rank, eye_rank in zip(
        uuids, subject_percentiles, eye_percentiles, strict=True
    ):
        raw = measured_by_uuid[asset_uuid]
        coherence = max(0.0, min(1.0, float(raw.get("subject_directional_coherence") or 0.0)))
        sharpness_rank = float(subject_rank) if subject_rank is not None else 0.5
        eye_rank_value = float(eye_rank) if eye_rank is not None else None
        low_detail = 1.0 - sharpness_rank
        quality = sharpness_rank * 75.0
        if eye_rank_value is not None:
            quality += eye_rank_value * 25.0
        else:
            quality += sharpness_rank * 25.0
        result[asset_uuid] = {
            **raw,
            "relative_subject_sharpness": round(sharpness_rank, 6),
            "relative_eye_sharpness": (
                round(eye_rank_value, 6) if eye_rank_value is not None else None
            ),
            "relative_quality_score": round(quality, 4),
            "relative_motion_blur_evidence": round(low_detail * coherence, 6),
            "relative_defocus_blur_evidence": round(low_detail * (1.0 - coherence), 6),
            "evidence_contract": "within_series_advisory_v1",
        }
    return result


def _log_metric(value: object) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        return None
    return math.log1p(numeric)


def _eye_region_rectangles(
    face_rectangles: list[dict[str, object]],
) -> list[dict[str, float]]:
    eyes: list[dict[str, float]] = []
    for rectangle in face_rectangles[:10]:
        try:
            x = float(rectangle["x"])
            y = float(rectangle["y"])
            width = float(rectangle["width"])
            height = float(rectangle["height"])
        except (KeyError, TypeError, ValueError):
            continue
        if width <= 0 or height <= 0:
            continue
        for offset in (0.10, 0.58):
            eyes.append(
                {
                    "x": max(0.0, min(1.0, x + width * offset)),
                    "y": max(0.0, min(1.0, y + height * 0.52)),
                    "width": max(0.0, min(1.0, width * 0.32)),
                    "height": max(0.0, min(1.0, height * 0.24)),
                }
            )
    return eyes
