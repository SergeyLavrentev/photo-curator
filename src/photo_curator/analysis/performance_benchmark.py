from __future__ import annotations

import os
import platform
from collections.abc import Callable, Sequence
from pathlib import Path
from statistics import median

from photo_curator.analysis.local_models import LocalModelEngine
from photo_curator.utils.subprocesses import CommandResult, find_executable, run_command

PERFORMANCE_BENCHMARK_SCHEMA_VERSION = 1
DEFAULT_MODES = ((1, 1), (16, 1), (16, 2))
UNSAFE_THERMAL_STATES = {"serious", "critical"}


def run_local_model_performance_benchmark(
    engine: LocalModelEngine,
    assets: list[tuple[str, Path]],
    *,
    engines: set[str],
    modes: Sequence[tuple[int, int]] = DEFAULT_MODES,
    repetitions: int = 2,
    max_peak_rss_bytes: int = 1_073_741_824,
    max_output_drift: float = 1e-4,
    energy_trace: Path | None = None,
    energy_joules: float | None = None,
    max_energy_joules: float | None = None,
    runner: Callable[..., CommandResult] = run_command,
) -> dict[str, object]:
    """Compare bounded helper schedules and keep energy acceptance fail-closed.

    A mode is ``(work_batch_size, max_concurrency)``. The benchmark never infers energy from
    runtime or CPU utilization: energy passes only with a retained trace, a positive measured
    value and an explicit budget.
    """

    if not assets:
        raise ValueError("Performance benchmark requires at least one asset")
    if not engines:
        raise ValueError("Performance benchmark requires at least one engine")
    if repetitions < 1:
        raise ValueError("Performance benchmark repetitions must be positive")
    if max_peak_rss_bytes < 1:
        raise ValueError("Peak RSS budget must be positive")
    if max_output_drift < 0:
        raise ValueError("Output drift tolerance must be non-negative")
    normalized_modes = _validate_modes(modes)
    runs: list[dict[str, object]] = []
    baseline_output: object | None = None
    for work_batch_size, max_concurrency in normalized_modes:
        samples = [
            engine.analyze(
                assets,
                engines=engines,
                work_batch_size=work_batch_size,
                max_concurrency=max_concurrency,
            )
            for _ in range(repetitions)
        ]
        outputs = [_stable_outputs(sample) for sample in samples]
        if baseline_output is None:
            baseline_output = outputs[0]
        summaries = [sample["summary"] for sample in samples]
        wall_samples = [float(summary["wall_duration_ms"]) for summary in summaries]
        peak_rss = max(int(summary["peak_rss_bytes"]) for summary in summaries)
        errors = max(int(summary["errors"]) for summary in summaries)
        thermal_states = [
            {
                "before": str(summary["thermal_state_before"]),
                "after": str(summary["thermal_state_after"]),
            }
            for summary in summaries
        ]
        comparisons = [_compare_outputs(baseline_output, output) for output in outputs]
        max_observed_drift = max(comparison[1] for comparison in comparisons)
        output_stable = all(comparison[0] for comparison in comparisons) and (
            max_observed_drift <= max_output_drift
        )
        thermal_safe = all(
            state["before"] not in UNSAFE_THERMAL_STATES
            and state["after"] not in UNSAFE_THERMAL_STATES
            for state in thermal_states
        )
        passed = errors == 0 and output_stable and thermal_safe and peak_rss <= max_peak_rss_bytes
        median_wall = median(wall_samples)
        runs.append(
            {
                "work_batch_size": work_batch_size,
                "max_concurrency": max_concurrency,
                "repetitions": repetitions,
                "wall_duration_ms": {
                    "samples": wall_samples,
                    "median": median_wall,
                },
                "throughput_assets_per_second": len(assets) / (median_wall / 1_000.0),
                "peak_rss_bytes": peak_rss,
                "errors": errors,
                "output_stable": output_stable,
                "max_observed_output_drift": max_observed_drift,
                "max_allowed_output_drift": max_output_drift,
                "thermal_states": thermal_states,
                "thermal_safe": thermal_safe,
                "passed": passed,
            }
        )
    passing = [run for run in runs if run["passed"]]
    concurrent_passing = [run for run in passing if int(run["max_concurrency"]) > 1]
    recommended = (
        min(passing, key=lambda row: float(row["wall_duration_ms"]["median"])) if passing else None
    )
    capacity_passed = bool(passing) and bool(concurrent_passing)
    energy = _energy_evidence(
        trace=energy_trace,
        joules=energy_joules,
        budget_joules=max_energy_joules,
    )
    return {
        "schema_version": PERFORMANCE_BENCHMARK_SCHEMA_VERSION,
        "environment": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "profiling_tools": profiling_tool_status(runner=runner),
        },
        "asset_count": len(assets),
        "engines": sorted(engines),
        "max_peak_rss_bytes": max_peak_rss_bytes,
        "runs": runs,
        "recommended_mode": (
            {
                "work_batch_size": recommended["work_batch_size"],
                "max_concurrency": recommended["max_concurrency"],
            }
            if recommended
            else None
        ),
        "capacity_passed": capacity_passed,
        "energy": energy,
        "passed": capacity_passed and bool(energy["passed"]),
    }


def profiling_tool_status(
    *, runner: Callable[..., CommandResult] = run_command
) -> dict[str, object]:
    xcrun = find_executable("xcrun")
    xctrace_path = None
    xctrace_detail = "xcrun is unavailable"
    if xcrun:
        result = runner([xcrun, "--find", "xctrace"], timeout=15)
        if result.returncode == 0 and result.stdout.strip():
            xctrace_path = result.stdout.strip()
            xctrace_detail = "available"
        else:
            xctrace_detail = (result.stderr or "xctrace is unavailable").strip()[-500:]
    powermetrics = find_executable("powermetrics")
    return {
        "xctrace": {
            "status": "available" if xctrace_path else "unavailable",
            "path": xctrace_path,
            "detail": xctrace_detail,
        },
        "powermetrics": {
            "status": "available" if powermetrics else "unavailable",
            "path": powermetrics,
            "requires_root": True,
            "current_process_is_root": os.geteuid() == 0,
        },
    }


def _validate_modes(modes: Sequence[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    normalized = tuple(dict.fromkeys(modes))
    if not normalized:
        raise ValueError("Performance benchmark requires at least one mode")
    for work_batch_size, max_concurrency in normalized:
        if not 1 <= work_batch_size <= 256:
            raise ValueError("work batch size must be between 1 and 256")
        if not 1 <= max_concurrency <= 4 or max_concurrency > work_batch_size:
            raise ValueError("max concurrency must be 1...4 and not exceed work batch size")
    if (1, 1) not in normalized:
        raise ValueError("Performance benchmark requires the 1x1 baseline mode")
    return normalized


def _stable_outputs(report: dict[str, object]) -> list[dict[str, object]]:
    rows = []
    for row in report["assets"]:
        stable = {
            key: value
            for key, value in row.items()
            if key not in {"durations_ms", "median_duration_ms"}
        }
        rows.append(stable)
    return rows


def _compare_outputs(expected: object, actual: object) -> tuple[bool, float]:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected == actual, 0.0
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return True, abs(float(expected) - float(actual))
    if isinstance(expected, dict) and isinstance(actual, dict):
        if set(expected) != set(actual):
            return False, float("inf")
        comparisons = [_compare_outputs(expected[key], actual[key]) for key in expected]
        return all(result[0] for result in comparisons), max(
            (result[1] for result in comparisons), default=0.0
        )
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return False, float("inf")
        comparisons = [
            _compare_outputs(left, right) for left, right in zip(expected, actual, strict=True)
        ]
        return all(result[0] for result in comparisons), max(
            (result[1] for result in comparisons), default=0.0
        )
    return expected == actual, 0.0 if expected == actual else float("inf")


def _energy_evidence(
    *, trace: Path | None, joules: float | None, budget_joules: float | None
) -> dict[str, object]:
    if trace is None and joules is None and budget_joules is None:
        return {
            "status": "not_measured",
            "passed": False,
            "reason": "A retained Instruments trace, joules and an explicit budget are required",
        }
    if trace is None or joules is None or budget_joules is None:
        raise ValueError("Energy evidence requires trace, joules and max_energy_joules together")
    if not trace.exists():
        raise ValueError(f"Energy trace does not exist: {trace}")
    if joules <= 0 or budget_joules <= 0:
        raise ValueError("Energy values and budget must be positive")
    return {
        "status": "measured",
        "trace": str(trace.resolve()),
        "joules": joules,
        "budget_joules": budget_joules,
        "passed": joules <= budget_joules,
    }
