import json
from pathlib import Path

import pytest

from photo_curator.analysis.local_models import (
    LocalModelCancelled,
    LocalModelEngine,
    LocalModelError,
)
from photo_curator.paths import default_application_paths
from photo_curator.utils.subprocesses import CommandCancelled, CommandResult


def test_local_model_engine_fails_closed_when_requested_resource_is_missing(
    tmp_path: Path,
) -> None:
    paths = default_application_paths(tmp_path)
    paths.ensure()
    engine = LocalModelEngine(paths, model_root=tmp_path / "models")

    with pytest.raises(LocalModelError, match=r"nima-inception-v2-ava\.mlpackage"):
        engine._ensure_ready({"nima"})


def test_local_model_report_requires_exact_asset_and_engine_sets() -> None:
    report = {
        "schema_version": 1,
        "engine": {"name": "photo-curator-local-models", "version": "2"},
        "enabled_engines": ["nima", "musiq"],
        "assets": [
            {
                "asset_uuid": "asset-1",
                "nima": {"aesthetic_score": 62.0},
                "musiq": {"quality_score": 58.0},
                "errors": {},
            }
        ],
        "summary": {
            "peak_rss_bytes": 123_000_000,
            "wall_duration_ms": 42.0,
            "thermal_state_before": "nominal",
            "thermal_state_after": "nominal",
            "energy_status": "not_measured",
            "work_batch_size": 16,
            "max_concurrency": 2,
        },
    }

    LocalModelEngine._validate(report, {"asset-1"}, {"nima", "musiq"})
    with pytest.raises(LocalModelError, match="incomplete asset set"):
        LocalModelEngine._validate(report, {"asset-1", "asset-2"}, {"nima", "musiq"})
    with pytest.raises(LocalModelError, match="enabled-engine set mismatch"):
        LocalModelEngine._validate(report, {"asset-1"}, {"mobileclip"})


def test_local_model_report_rejects_non_finite_or_out_of_range_scores() -> None:
    report = {
        "schema_version": 1,
        "engine": {"name": "photo-curator-local-models", "version": "2"},
        "enabled_engines": ["nima"],
        "assets": [
            {"asset_uuid": "asset-1", "nima": {"aesthetic_score": float("nan")}, "errors": {}}
        ],
        "summary": {
            "peak_rss_bytes": 123_000_000,
            "wall_duration_ms": 42.0,
            "thermal_state_before": "nominal",
            "thermal_state_after": "nominal",
            "energy_status": "not_measured",
            "work_batch_size": 16,
            "max_concurrency": 2,
        },
    }

    with pytest.raises(LocalModelError, match="invalid nima score"):
        LocalModelEngine._validate(report, {"asset-1"}, {"nima"})


def test_local_model_engine_rejects_unknown_engine_before_launch(tmp_path: Path) -> None:
    paths = default_application_paths(tmp_path)
    paths.ensure()
    engine = LocalModelEngine(paths, model_root=tmp_path / "models")

    with pytest.raises(ValueError, match="Unknown local model engines"):
        engine.analyze([], engines={"unknown"})


def test_local_model_report_requires_runtime_and_thermal_evidence() -> None:
    report = {
        "schema_version": 1,
        "engine": {"name": "photo-curator-local-models", "version": "2"},
        "enabled_engines": ["nima"],
        "assets": [{"asset_uuid": "asset-1", "nima": {"aesthetic_score": 62.0}}],
        "summary": {"peak_rss_bytes": 0},
    }

    with pytest.raises(LocalModelError, match="runtime evidence"):
        LocalModelEngine._validate(report, {"asset-1"}, {"nima"})


def test_local_model_engine_maps_subprocess_cancellation(tmp_path: Path) -> None:
    model = tmp_path / "models" / "nima-inception-v2-ava.mlpackage"
    model.mkdir(parents=True)
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"jpeg")

    def cancelled_runner(*_args, **_kwargs):
        raise CommandCancelled("cancelled")

    engine = LocalModelEngine(
        default_application_paths(tmp_path),
        model_root=model.parent,
        runner=cancelled_runner,
    )
    engine._ensure_ready = lambda _engines: {"nima": "0" * 64}  # type: ignore[method-assign]

    with pytest.raises(LocalModelCancelled, match="cancelled"):
        engine.analyze([("asset-1", photo)], engines={"nima"}, cancelled=lambda: True)


def test_local_model_engine_sends_and_validates_bounded_schedule(tmp_path: Path) -> None:
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"jpeg")
    requests = []

    def runner(args, *, timeout, cancelled=None):
        request = json.loads(Path(args[1]).read_text(encoding="utf-8"))
        requests.append(request)
        Path(args[2]).write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "engine": {"name": "photo-curator-local-models", "version": "2"},
                    "enabled_engines": ["nima"],
                    "assets": [
                        {
                            "asset_uuid": "asset-1",
                            "nima": {"aesthetic_score": 62.0},
                            "errors": {},
                        }
                    ],
                    "summary": {
                        "peak_rss_bytes": 123_000_000,
                        "wall_duration_ms": 42.0,
                        "thermal_state_before": "nominal",
                        "thermal_state_after": "nominal",
                        "energy_status": "not_measured",
                        "work_batch_size": 8,
                        "max_concurrency": 2,
                    },
                }
            ),
            encoding="utf-8",
        )
        return CommandResult(args, 0, "", "")

    engine = LocalModelEngine(default_application_paths(tmp_path), runner=runner)
    engine._ensure_ready = lambda _engines: {"nima": "0" * 64}  # type: ignore[method-assign]

    engine.analyze([("asset-1", photo)], engines={"nima"}, work_batch_size=8, max_concurrency=2)

    assert requests[0]["work_batch_size"] == 8
    assert requests[0]["max_concurrency"] == 2
