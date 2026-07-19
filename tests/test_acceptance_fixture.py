from datetime import UTC, datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from photo_curator.analysis.hashes import color_histogram, dhash, phash, render_equivalence_hash
from photo_curator.analysis.image_loader import load_normalized
from photo_curator.analysis.normalization import percentile_ranks
from photo_curator.analysis.technical import technical_metrics
from photo_curator.pipeline.duplicates import _candidate_pairs, find_duplicate_groups


def _scene(index: int) -> Image.Image:
    image = Image.new("RGB", (320, 240), (25 + index * 17, 45 + index * 11, 70 + index * 9))
    draw = ImageDraw.Draw(image)
    draw.rectangle((15 + index * 3, 20, 170, 175), fill=(240, 220 - index * 8, 90))
    draw.ellipse((120, 50 + index * 2, 300, 225), fill=(60, 100 + index * 12, 210))
    draw.line((0, index * 19 + 20, 319, 220 - index * 9), fill="white", width=5)
    return image


def test_labelled_eighty_photo_fixture_keeps_series_separate(tmp_path: Path) -> None:
    rows = []
    labels = {}
    for scene in range(10):
        original = _scene(scene)
        variants = {
            "original": original,
            "exact": original.copy(),
            "recompressed": original.copy(),
            "resized": original.resize((160, 120), Image.Resampling.LANCZOS),
            "cropped": original.crop((40, 30, 300, 220)).resize(
                (320, 240), Image.Resampling.LANCZOS
            ),
            "blurred": original.filter(ImageFilter.GaussianBlur(8)),
            "dark": ImageEnhance.Brightness(original).enhance(0.16),
            "overexposed": ImageEnhance.Brightness(original).enhance(3.0),
        }
        for offset, (kind, image) in enumerate(variants.items()):
            uuid = f"scene-{scene:02d}-{kind}"
            path = tmp_path / f"{uuid}.jpg"
            image.save(path, quality=62 if kind == "recompressed" else 94)
            normalized = load_normalized(path, 1024)
            metrics = technical_metrics(normalized)
            rows.append(
                {
                    "asset_uuid": uuid,
                    "review_path": str(path),
                    "width": normalized.width,
                    "height": normalized.height,
                    "taken_at": (
                        datetime(2026, 1, 1, tzinfo=UTC)
                        + timedelta(minutes=scene * 10, seconds=offset)
                    ).isoformat(),
                    "cache_state": "ready",
                    "favorite": False,
                    "has_adjustments": False,
                    "dhash": dhash(normalized),
                    "phash": phash(normalized),
                    "normalized_pixel_hash": render_equivalence_hash(normalized),
                    "histogram": color_histogram(normalized),
                    **metrics,
                }
            )
            labels[uuid] = scene

    sharpness = percentile_ranks([float(row["laplacian_variance"]) for row in rows])
    contrast = percentile_ranks([float(row["contrast_std"]) for row in rows])
    for row, sharp, cont in zip(rows, sharpness, contrast, strict=True):
        row["sharpness_percentile"] = sharp
        row["contrast_percentile"] = cont
        row["technical_quality"] = (sharp + cont) / 2

    pairs = list(_candidate_pairs(rows))
    groups = find_duplicate_groups(rows, candidate_pairs=pairs)

    assert len(rows) == 80
    assert len(pairs) < len(rows) * (len(rows) - 1) // 2
    assert len(groups) >= 10
    assert all(
        len({labels[member.asset_uuid] for member in group.members}) == 1 for group in groups
    ), [
        [member.asset_uuid for member in group.members]
        for group in groups
        if len({labels[member.asset_uuid] for member in group.members}) != 1
    ]
    for scene in range(10):
        sharp = next(row for row in rows if row["asset_uuid"] == f"scene-{scene:02d}-original")
        blurred = next(row for row in rows if row["asset_uuid"] == f"scene-{scene:02d}-blurred")
        dark = next(row for row in rows if row["asset_uuid"] == f"scene-{scene:02d}-dark")
        overexposed = next(
            row for row in rows if row["asset_uuid"] == f"scene-{scene:02d}-overexposed"
        )
        assert float(sharp["laplacian_variance"]) > float(blurred["laplacian_variance"])
        assert float(dark["luma_mean"]) < float(sharp["luma_mean"])
        assert float(overexposed["white_clipped_ratio"]) > float(sharp["white_clipped_ratio"])
