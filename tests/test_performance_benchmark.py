from __future__ import annotations

from pathlib import Path

import pytest

from photo_curator.analysis.performance_benchmark import (
    profiling_tool_status,
    run_local_model_performance_benchmark,
)
from photo_curator.utils.subprocesses import CommandResult


class FakeLocalModelEngine:
    def __init__(self, *, drift_concurrent: bool = False) -> None:
        self.drift_concurrent = drift_concurrent
        self.calls: list[tuple[int, int]] = []

    def analyze(
        self,
        assets,
        *,
        engines,
        work_batch_size,
        max_concurrency,
    ):
        self.calls.append((work_batch_size, max_concurrency))
        score = 62.0 + (0.1 if self.drift_concurrent and max_concurrency > 1 else 0.0)
        return {
            "assets": [
                {
                    "asset_uuid": asset_uuid,
                    "nima": {"aesthetic_score": score},
                    "durations_ms": {"nima": 10.0},
                    "errors": {},
                }
                for asset_uuid, _ in assets
            ],
            "summary": {
                "wall_duration_ms": 100.0 / max_concurrency,
                "peak_rss_bytes": 200_000_000 * max_concurrency,
                "errors": 0,
                "thermal_state_before": "nominal",
                "thermal_state_after": "fair" if max_concurrency > 1 else "nominal",
            },
        }


def unavailable_xctrace(args, *, timeout):
    return CommandResult(args, 72, "", "xctrace is unavailable")


def test_performance_benchmark_accepts_stable_bounded_concurrency_but_not_missing_energy(
    tmp_path: Path,
) -> None:
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"jpeg")
    engine = FakeLocalModelEngine()

    report = run_local_model_performance_benchmark(
        engine,  # type: ignore[arg-type]
        [("asset-1", photo)],
        engines={"nima"},
        modes=((1, 1), (16, 1), (16, 2)),
        repetitions=2,
        runner=unavailable_xctrace,
    )

    assert report["capacity_passed"] is True
    assert report["energy"]["status"] == "not_measured"
    assert report["passed"] is False
    assert report["recommended_mode"] == {"work_batch_size": 16, "max_concurrency": 2}
    assert engine.calls == [(1, 1), (1, 1), (16, 1), (16, 1), (16, 2), (16, 2)]


def test_performance_benchmark_rejects_output_drift_between_schedules(tmp_path: Path) -> None:
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"jpeg")

    report = run_local_model_performance_benchmark(
        FakeLocalModelEngine(drift_concurrent=True),  # type: ignore[arg-type]
        [("asset-1", photo)],
        engines={"nima"},
        modes=((1, 1), (16, 2)),
        repetitions=1,
        runner=unavailable_xctrace,
    )

    assert report["runs"][1]["output_stable"] is False
    assert report["runs"][1]["passed"] is False
    assert report["capacity_passed"] is False


def test_performance_benchmark_requires_trace_value_and_budget_for_energy(
    tmp_path: Path,
) -> None:
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"jpeg")
    trace = tmp_path / "energy.trace"
    trace.mkdir()

    report = run_local_model_performance_benchmark(
        FakeLocalModelEngine(),  # type: ignore[arg-type]
        [("asset-1", photo)],
        engines={"nima"},
        modes=((1, 1), (16, 2)),
        repetitions=1,
        energy_trace=trace,
        energy_joules=12.5,
        max_energy_joules=15.0,
        runner=unavailable_xctrace,
    )

    assert report["energy"]["passed"] is True
    assert report["passed"] is True
    with pytest.raises(ValueError, match="requires trace"):
        run_local_model_performance_benchmark(
            FakeLocalModelEngine(),  # type: ignore[arg-type]
            [("asset-1", photo)],
            engines={"nima"},
            modes=((1, 1), (16, 2)),
            repetitions=1,
            energy_joules=12.5,
            runner=unavailable_xctrace,
        )


def test_profiling_tool_status_is_fail_honest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "photo_curator.analysis.performance_benchmark.find_executable",
        lambda name: "/usr/bin/xcrun" if name == "xcrun" else "/usr/bin/powermetrics",
    )

    status = profiling_tool_status(runner=unavailable_xctrace)

    assert status["xctrace"]["status"] == "unavailable"
    assert status["powermetrics"]["requires_root"] is True
