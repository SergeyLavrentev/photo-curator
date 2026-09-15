from __future__ import annotations

import json
from typing import Protocol

from photo_curator.analysis.culling import culling_category


class AlbumPayloadSource(Protocol):
    id: str
    name: str
    folder_path: str | None
    full_path: str
    is_shared: bool
    photo_count: int
    video_count: int


def album_payload(album: AlbumPayloadSource) -> dict[str, object]:
    return {
        "id": album.id,
        "name": album.name,
        "folder_path": album.folder_path,
        "full_path": album.full_path,
        "is_shared": album.is_shared,
        "photo_count": album.photo_count,
        "video_count": album.video_count,
    }


def project_payload(project: dict[str, object]) -> dict[str, object]:
    return {
        "id": project["id"],
        "name": project["name"],
        "album_id": project["album_id"],
        "album_name": project["album_name"],
        "state": project["state"],
        "settings": json.loads(str(project.get("settings_json") or "{}")),
        "created_at": project["created_at"],
        "updated_at": project["updated_at"],
    }


def job_payload(job: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in job.items() if key not in {"error_text", "project_id"}}


def asset_payload(asset: dict[str, object]) -> dict[str, object]:
    technical_codes = {
        "possible_blur",
        "motion_blur",
        "defocus_blur",
        "underexposed",
        "overexposed",
        "low_contrast",
        "apple_low_overall",
        "poor_face_capture",
        "extreme_horizon",
        "bad_angle",
        "blocked_subject",
        "codex_confirmed_defect",
    }
    raw_reasons = [reason for reason in asset.get("reasons") or [] if isinstance(reason, dict)]
    detected_technical_codes = sorted(
        {
            *(str(code) for code in asset.get("flags", []) if str(code) in technical_codes),
            *(
                str(reason.get("code") or "")
                for reason in raw_reasons
                if str(reason.get("code") or "") in technical_codes
            ),
        }
    )
    reasons = []
    for raw_reason in raw_reasons:
        reason = dict(raw_reason)
        code = str(reason.get("code") or "")
        if code == "weaker_duplicate":
            reason["duplicate_kind"] = (asset.get("duplicate_context") or {}).get("kind")
        elif code == "technical_penalty":
            if not detected_technical_codes:
                continue
            reason["technical_defect_codes"] = detected_technical_codes
        elif code in technical_codes:
            reason["technical_defect_codes"] = [code]
        reasons.append(reason)
    swipe_components = asset.get("swipe_components") or {}
    evidence_coverage = swipe_components.get("evidence_coverage")
    model_count = int(swipe_components.get("model_count") or 0)
    model_disagreement = swipe_components.get("model_disagreement")
    raw_quality_defects = asset.get("quality_defect_codes")
    if raw_quality_defects is None:
        try:
            raw_quality_defects = json.loads(str(asset.get("quality_defect_codes_json") or "[]"))
        except json.JSONDecodeError:
            raw_quality_defects = []
    return {
        "asset_uuid": asset["asset_uuid"],
        "filename": asset.get("current_filename"),
        "thumbnail_path": asset.get("thumbnail_path"),
        "review_path": asset.get("review_path"),
        "cache_state": asset.get("cache_state"),
        "width": asset.get("width"),
        "height": asset.get("height"),
        "favorite": bool(asset.get("favorite")),
        "culling_category": culling_category(asset),
        "culling_reason": asset.get("culling_reason"),
        "auto_culling": asset.get("auto_culling"),
        "final_disposition": asset.get("final_disposition"),
        "auto_selection": asset.get("auto_selection"),
        "manual_selection": asset.get("manual_selection"),
        "final_selection": asset.get("final_selection"),
        "manual_disposition": asset.get("manual_disposition"),
        "manual_rating": asset.get("manual_rating"),
        "mutation_generation": asset.get("manual_mutation_generation"),
        "swipe_score": asset.get("swipe_score"),
        "generic_score": asset.get("swipe_generic_score"),
        "personal_delta": asset.get("swipe_personal_delta"),
        "confidence": asset.get("confidence"),
        "confidence_calibrated": "confidence_uncalibrated" not in set(asset.get("flags") or []),
        "evidence_coverage": (
            float(evidence_coverage) / 100.0
            if isinstance(evidence_coverage, (int, float))
            else asset.get("swipe_confidence")
        ),
        "model_count": model_count,
        "model_disagreement": (
            float(model_disagreement) / 100.0
            if model_count >= 2 and isinstance(model_disagreement, (int, float))
            else None
        ),
        "components": swipe_components,
        "reasons": reasons,
        "duplicate_group": (asset.get("duplicate_context") or {}).get("group_id"),
        "duplicate_member_count": int(
            (asset.get("duplicate_context") or {}).get("member_count") or 0
        ),
        "duplicate_is_leader": bool((asset.get("duplicate_context") or {}).get("is_leader")),
        "duplicate_leader_uuid": (asset.get("duplicate_context") or {}).get("leader_uuid"),
        "duplicate_leader": asset.get("duplicate_leader"),
        "album_references": asset.get("album_references") or [],
        "quality_top_k_rank": asset.get("quality_top_k_rank"),
        "quality_duplicate_group": asset.get("quality_duplicate_group"),
        "quality_expected_leader": bool(asset.get("quality_expected_leader")),
        "quality_disposition": asset.get("quality_expected_disposition"),
        "quality_defect_codes": list(raw_quality_defects or []),
        "quality_defect_severity": asset.get("quality_defect_severity"),
        "quality_defect_confidence": asset.get("quality_defect_confidence"),
        "quality_note": asset.get("quality_note"),
        "quality_lab_sampled": bool(asset.get("quality_lab_sampled")),
    }


def asset_card_payload(asset: dict[str, object]) -> dict[str, object]:
    payload = asset_payload(asset)
    payload["payload_kind"] = "asset_card"
    payload["payload_schema_version"] = 1
    payload["reasons"] = list(payload["reasons"][:2])
    payload["components"] = {}
    payload["duplicate_leader"] = None
    payload["album_references"] = []
    return payload


def related_asset_payload(asset: dict[str, object]) -> dict[str, object]:
    return {
        "asset_uuid": asset["asset_uuid"],
        "filename": asset.get("current_filename"),
        "thumbnail_path": asset.get("thumbnail_path"),
        "review_path": asset.get("review_path"),
    }


def publish_payload(publish: dict[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in publish.items()
        if key
        not in {"uuid_file", "dry_run_stdout", "dry_run_stderr", "apply_stdout", "apply_stderr"}
    }
