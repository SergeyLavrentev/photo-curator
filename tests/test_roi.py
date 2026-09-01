from PIL import Image, ImageDraw, ImageFilter

from photo_curator.analysis.roi import (
    ROI_MAX_MEMBERS_PER_GROUP,
    measure_high_resolution_roi,
    normalize_series_roi,
    select_series_roi_candidates,
)


def _asset(asset_uuid: str, quality: float = 0.5) -> dict[str, object]:
    return {
        "asset_uuid": asset_uuid,
        "cache_state": "ready",
        "review_path": f"/{asset_uuid}.jpg",
        "technical_quality": quality,
        "sharpness_percentile": quality,
        "width": 2000,
        "height": 1500,
        "no_longer_exists": False,
    }


def test_roi_shortlist_is_bounded_keeps_leader_and_skips_exact_groups() -> None:
    assets = {f"asset-{index}": _asset(f"asset-{index}", index / 20) for index in range(12)}
    members = [{"asset_uuid": asset_uuid} for asset_uuid in assets]
    groups = [
        {
            "group_id": "near",
            "kind": "near",
            "leader_uuid": "asset-0",
            "flags": [],
            "members": members,
        },
        {
            "group_id": "exact",
            "kind": "exact",
            "leader_uuid": "asset-1",
            "flags": [],
            "members": members[:2],
        },
    ]

    selected = select_series_roi_candidates(groups, assets)

    assert len(selected) == ROI_MAX_MEMBERS_PER_GROUP
    assert "asset-0" in selected
    assert all(context["group_id"] == "near" for context in selected.values())


def test_high_resolution_roi_measures_face_and_eye_sharpness_without_blink_claim() -> None:
    image = Image.new("RGB", (1800, 1200), "#777777")
    draw = ImageDraw.Draw(image)
    for x in range(450, 1350, 20):
        draw.line((x, 250, x, 900), fill="white", width=4)
    measured = measure_high_resolution_roi(
        image,
        [{"x": 0.25, "y": 0.2, "width": 0.5, "height": 0.65}],
        source="faces",
    )

    assert measured["image_width"] == 1800
    assert measured["region_count"] == 1
    assert measured["eye_region_count"] == 2
    assert measured["eye_laplacian_variance"] is not None
    assert measured["eye_metric_contract"] == "sharpness_only_not_blink"


def test_roi_normalization_prefers_sharp_subject_and_keeps_blur_evidence_advisory() -> None:
    sharp = Image.new("RGB", (1200, 800), "#777777")
    draw = ImageDraw.Draw(sharp)
    for x in range(0, 1200, 12):
        draw.line((x, 0, x, 800), fill="white", width=3)
    blurred = sharp.filter(ImageFilter.GaussianBlur(12))
    rectangle = [{"x": 0.1, "y": 0.1, "width": 0.8, "height": 0.8}]

    normalized = normalize_series_roi(
        {
            "sharp": measure_high_resolution_roi(sharp, rectangle, source="attention_saliency"),
            "blurred": measure_high_resolution_roi(blurred, rectangle, source="attention_saliency"),
        }
    )

    assert (
        normalized["sharp"]["relative_quality_score"]
        > normalized["blurred"]["relative_quality_score"]
    )
    assert normalized["blurred"]["relative_motion_blur_evidence"] >= 0
    assert normalized["blurred"]["relative_defocus_blur_evidence"] >= 0
    assert normalized["blurred"]["evidence_contract"] == "within_series_advisory_v1"
