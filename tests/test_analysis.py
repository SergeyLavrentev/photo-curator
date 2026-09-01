from dataclasses import dataclass
from pathlib import Path
from subprocess import TimeoutExpired
from time import monotonic

from PIL import Image, ImageDraw, ImageFilter

from photo_curator.analysis.hashes import dhash, hamming_distance, phash, render_equivalence_hash
from photo_curator.analysis.normalization import percentile_ranks, robust_stats
from photo_curator.analysis.technical import subject_quality_metrics, technical_metrics
from photo_curator.analysis.vision import analyze_faces, vision_available
from photo_curator.photos.osxphotos_provider import _score_dict


def patterned_image() -> Image.Image:
    image = Image.new("RGB", (320, 240), "#345d78")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 180, 170), fill="white")
    draw.ellipse((130, 80, 300, 230), fill="#d77b45")
    return image


def test_hashes_are_deterministic_and_distinguish_images() -> None:
    image = patterned_image()
    copy = image.copy()
    different = Image.new("RGB", image.size, "black")

    assert render_equivalence_hash(image) == render_equivalence_hash(copy)
    assert dhash(image) == dhash(copy)
    assert phash(image) == phash(copy)
    assert hamming_distance(phash(image), phash(different)) > 0


def test_blur_reduces_sharpness_metric() -> None:
    sharp = patterned_image()
    blurred = sharp.filter(ImageFilter.GaussianBlur(10))

    assert (
        technical_metrics(sharp)["laplacian_variance"]
        > technical_metrics(blurred)["laplacian_variance"]
    )


def test_exposure_and_entropy_metrics_are_bounded() -> None:
    dark = technical_metrics(Image.new("RGB", (100, 100), (5, 5, 5)))
    bright = technical_metrics(Image.new("RGB", (100, 100), (250, 250, 250)))

    assert dark["luma_mean"] < 0.05
    assert bright["luma_mean"] > 0.95
    assert dark["entropy"] >= 0


def test_subject_quality_uses_vision_roi_instead_of_distracting_background() -> None:
    image = Image.new("RGB", (200, 100), "#777777")
    subject = patterned_image().resize((80, 80))
    image.paste(subject, (110, 10))

    result = subject_quality_metrics(
        image,
        [{"x": 0.55, "y": 0.1, "width": 0.4, "height": 0.8}],
        source="attention_saliency",
    )

    assert result["roi_source"] == "attention_saliency"
    assert result["region_count"] == 1
    assert result["subject_laplacian_variance"] > 0
    assert 0 <= result["subject_directional_coherence"] <= 1


def test_horizon_and_face_boundary_evidence_are_explicit() -> None:
    image = Image.new("RGB", (400, 240), "#777777")
    draw = ImageDraw.Draw(image)
    draw.line((0, 160, 400, 70), fill="white", width=10)

    horizon = technical_metrics(image)
    boundary = subject_quality_metrics(
        image,
        [{"x": 0.0, "y": 0.15, "width": 0.35, "height": 0.7}],
        source="faces",
    )

    assert abs(horizon["dominant_horizon_degrees"]) >= 10
    assert horizon["horizon_support"] >= 0.25
    assert boundary["subject_boundary_contact_ratio"] >= 0.5
    assert boundary["subject_boundary_source"] == "faces"


def test_architectural_grid_is_not_misclassified_as_one_dominant_horizon() -> None:
    image = Image.new("RGB", (480, 320), "#333333")
    draw = ImageDraw.Draw(image)
    for x in range(30, 480, 45):
        draw.line((x, 0, x, 320), fill="white", width=5)
    for y in range(30, 320, 40):
        draw.line((0, y, 480, y), fill="white", width=5)

    result = technical_metrics(image)

    assert result["dominant_horizon_degrees"] is None
    assert result["horizon_support"] < 0.25


def test_single_high_contrast_vertical_edge_is_not_horizon_evidence() -> None:
    image = Image.new("RGB", (480, 320), "#222222")
    ImageDraw.Draw(image).rectangle((235, 0, 245, 320), fill="white")

    result = technical_metrics(image)

    assert result["dominant_horizon_degrees"] is None
    assert result["horizon_support"] == 0.0


def test_robust_normalization_handles_missing_values() -> None:
    assert percentile_ranks([1.0, None, 3.0]) == [0.0, None, 1.0]
    stats = robust_stats([1.0, 2.0, 100.0])
    assert stats["median"] == 2.0
    assert stats["mad"] == 1.0


def test_single_optional_score_is_neutral_and_dataclass_is_supported() -> None:
    @dataclass
    class Score:
        overall: float
        curation: float

    assert percentile_ranks([None, 0.9, None]) == [None, 0.5, None]
    assert _score_dict(Score(overall=0.9, curation=0.4)) == {
        "overall": 0.9,
        "curation": 0.4,
    }


def test_local_vision_face_analysis_is_capability_gated(tmp_path: Path) -> None:
    image_path = tmp_path / "plain.jpg"
    Image.new("RGB", (160, 120), "#345d78").save(image_path)

    started = monotonic()
    result = analyze_faces(image_path)

    assert result.face_count == 0
    assert result.eyes_detected == 0
    assert monotonic() - started < 5
    assert vision_available() in {True, False}


def test_legacy_vision_timeout_is_neutral(monkeypatch) -> None:
    def timeout(*args, **kwargs):
        raise TimeoutExpired(cmd=args[0], timeout=3)

    monkeypatch.setattr("photo_curator.analysis.vision.subprocess.run", timeout)

    result = analyze_faces(Path("never-opened.jpg"))

    assert result == type(result)(face_count=0, face_capture_quality=None, eyes_detected=0)
