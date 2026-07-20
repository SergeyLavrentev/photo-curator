from __future__ import annotations

from pathlib import Path

import pytest

from photo_curator.analysis.coreml_inference import CoreMLInferenceEngine
from photo_curator.analysis.model_registry import ModelRegistryError, model_sha256
from photo_curator.paths import default_application_paths


class FakeBenchmarkEngine:
    def __init__(self) -> None:
        self.calls: list[list[tuple[str, Path]]] = []

    def benchmark(
        self,
        model_path: Path,
        assets: list[tuple[str, Path]],
        *,
        warmup_iterations: int,
        measured_iterations: int,
    ) -> dict[str, object]:
        assert model_path.suffix == ".mlmodelc"
        assert (warmup_iterations, measured_iterations) == (1, 1)
        self.calls.append(assets)
        return {
            "schema_version": 1,
            "summary": {"median_inference_ms": 2.5},
            "assets": [
                {
                    "asset_uuid": asset_uuid,
                    "median_duration_ms": 2.5,
                    "output": {"kind": "multi_array", "values": [0.75]},
                    "error": None,
                }
                for asset_uuid, _ in assets
            ],
        }


def approved_model(path: Path) -> dict[str, object]:
    return {
        "id": "model-1",
        "name": "Aesthetic scorer",
        "version": "1",
        "model_path": str(path),
        "sha256": model_sha256(path),
        "compute_policy": "all",
        "status": "approved",
    }


def test_inference_batches_misses_and_reuses_fingerprint_cache(tmp_path: Path) -> None:
    paths = default_application_paths(tmp_path / "home")
    model_path = tmp_path / "scorer.mlmodelc"
    model_path.mkdir()
    (model_path / "weights.bin").write_bytes(b"model")
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    benchmark = FakeBenchmarkEngine()
    engine = CoreMLInferenceEngine(paths, benchmark_engine=benchmark)
    model = approved_model(model_path)

    initial = engine.infer(
        model,
        [("first", first, "render-a"), ("second", second, "render-b")],
    )
    repeated = engine.infer(
        model,
        [("second", second, "render-b"), ("first", first, "render-a")],
    )

    assert benchmark.calls == [[("first", first), ("second", second)]]
    assert initial["summary"] == {
        "asset_count": 2,
        "cache_hits": 0,
        "inferred_assets": 2,
        "failed_assets": 0,
    }
    assert repeated["summary"]["cache_hits"] == 2
    assert [row["asset_uuid"] for row in repeated["assets"]] == ["second", "first"]
    assert all(row["cache_hit"] for row in repeated["assets"])


def test_changed_render_fingerprint_is_a_cache_miss(tmp_path: Path) -> None:
    paths = default_application_paths(tmp_path / "home")
    model_path = tmp_path / "scorer.mlmodelc"
    model_path.mkdir()
    (model_path / "weights.bin").write_bytes(b"model")
    image = tmp_path / "image.jpg"
    image.write_bytes(b"image")
    benchmark = FakeBenchmarkEngine()
    engine = CoreMLInferenceEngine(paths, benchmark_engine=benchmark)
    model = approved_model(model_path)

    engine.infer(model, [("asset", image, "render-v1")])
    result = engine.infer(model, [("asset", image, "render-v2")])

    assert len(benchmark.calls) == 2
    assert result["summary"]["cache_hits"] == 0


def test_corrupt_cache_is_recomputed(tmp_path: Path) -> None:
    paths = default_application_paths(tmp_path / "home")
    model_path = tmp_path / "scorer.mlmodelc"
    model_path.mkdir()
    (model_path / "weights.bin").write_bytes(b"model")
    image = tmp_path / "image.jpg"
    image.write_bytes(b"image")
    benchmark = FakeBenchmarkEngine()
    engine = CoreMLInferenceEngine(paths, benchmark_engine=benchmark)
    model = approved_model(model_path)

    engine.infer(model, [("asset", image, "render")])
    cache_files = list((paths.cache_dir / "coreml").rglob("*.json"))
    assert len(cache_files) == 1
    cache_files[0].write_text("not-json", encoding="utf-8")

    result = engine.infer(model, [("asset", image, "render")])

    assert len(benchmark.calls) == 2
    assert result["summary"]["cache_hits"] == 0


def test_inference_rejects_unapproved_or_changed_model(tmp_path: Path) -> None:
    paths = default_application_paths(tmp_path / "home")
    model_path = tmp_path / "scorer.mlmodelc"
    model_path.mkdir()
    weights = model_path / "weights.bin"
    weights.write_bytes(b"model")
    image = tmp_path / "image.jpg"
    image.write_bytes(b"image")
    engine = CoreMLInferenceEngine(paths, benchmark_engine=FakeBenchmarkEngine())
    model = approved_model(model_path)

    candidate = {**model, "status": "candidate"}
    with pytest.raises(ModelRegistryError, match="approved"):
        engine.infer(candidate, [("asset", image, "render")])

    weights.write_bytes(b"changed")
    with pytest.raises(ModelRegistryError, match="checksum"):
        engine.infer(model, [("asset", image, "render")])
