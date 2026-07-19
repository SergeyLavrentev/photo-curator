from __future__ import annotations

import base64
import json
import shutil
import sys
from argparse import Namespace
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from photo_curator.analysis.native_vision import NativeVisionEngine, aesthetics_score_snapshot
from photo_curator.cli import run_vision_benchmark_command
from photo_curator.paths import default_application_paths
from tests.test_pipeline import build_pipeline


@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("xcrun") is None,
    reason="Apple Vision requires the macOS Swift toolchain",
)
def test_native_vision_helper_compiles_and_returns_versioned_signals(tmp_path: Path) -> None:
    image_path = tmp_path / "sample.jpg"
    image = Image.new("RGB", (640, 480), "#345d78")
    draw = ImageDraw.Draw(image)
    draw.ellipse((170, 90, 500, 420), fill="#e6a85c")
    image.save(image_path)
    paths = default_application_paths(tmp_path / "app")
    paths.ensure()

    result = NativeVisionEngine(paths).analyze(
        [("sample", image_path)], warmup_iterations=0, measured_iterations=1
    )

    assert result["schema_version"] == 1
    assert result["engine"]["name"] == "apple-vision-native"
    assert result["engine"]["architecture"] in {"arm64", "x86_64"}
    assert result["summary"]["asset_count"] == 1
    row = result["assets"][0]
    assert row["errors"] == {}
    assert -1 <= row["aesthetics"]["overall_score"] <= 1
    assert row["feature_print"]["element_count"] > 0
    assert base64.b64decode(row["feature_print"]["data_base64"])
    assert row["attention_saliency"]["heatmap_width"] > 0
    assert set(row["durations_ms"]) == {
        "aesthetics",
        "attention_saliency",
        "faces",
        "feature_print",
    }
    snapshot = aesthetics_score_snapshot("project", result)
    assert snapshot["project_id"] == "project"
    assert snapshot["engine"]["name"] == "apple-vision-aesthetics"
    assert 0 <= snapshot["scores"]["sample"] <= 100


def test_cli_benchmarks_ready_project_and_exports_acceptance_scores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)
    calls: list[tuple[list[tuple[str, Path]], int, int]] = []

    class FakeEngine:
        def __init__(self, received_paths):
            assert received_paths == paths

        def analyze(self, assets, *, warmup_iterations, measured_iterations):
            calls.append((assets, warmup_iterations, measured_iterations))
            return {
                "schema_version": 1,
                "engine": {"name": "apple-vision-native", "version": "1"},
                "assets": [
                    {
                        "asset_uuid": asset_uuid,
                        "aesthetics": {"overall_score": 0.5, "revision": 1},
                    }
                    for asset_uuid, _ in assets
                ],
            }

    monkeypatch.setattr("photo_curator.cli.default_application_paths", lambda: paths)
    monkeypatch.setattr("photo_curator.cli.NativeVisionEngine", FakeEngine)
    output = tmp_path / "vision.json"
    score_output = tmp_path / "vision-scores.json"

    result = run_vision_benchmark_command(
        Namespace(
            project_id=project_id,
            warmup=1,
            iterations=3,
            output=output,
            score_output=score_output,
        )
    )

    assert result == 0
    assert len(calls[0][0]) == 12
    assert calls[0][1:] == (1, 3)
    assert json.loads(output.read_text(encoding="utf-8"))["schema_version"] == 1
    snapshot = json.loads(score_output.read_text(encoding="utf-8"))
    assert snapshot["engine"]["version"] == "native-v1-revision-1"
    assert set(snapshot["scores"]) == {asset_uuid for asset_uuid, _ in calls[0][0]}
