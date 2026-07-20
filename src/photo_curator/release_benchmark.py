from __future__ import annotations

import json
import platform
import random
import resource
import sys
from statistics import median
from time import perf_counter

from photo_curator.analysis.swipe_score import (
    apple_score_percentiles,
    calculate_swipe_score,
)
from photo_curator.native_worker import _asset_payload, _next_taste_pair
from photo_curator.pipeline.duplicates import _candidate_pairs

RELEASE_BENCHMARK_SCHEMA_VERSION = 1
DEFAULT_COUNTS = (100, 2_000, 5_000)
TOTAL_BUDGET_SECONDS = {100: 0.25, 2_000: 2.0, 5_000: 5.0}


def run_release_benchmark(
    *, counts: tuple[int, ...] = DEFAULT_COUNTS, iterations: int = 3
) -> dict[str, object]:
    if not counts or any(count < 2 for count in counts):
        raise ValueError("Benchmark counts must be at least 2")
    if iterations < 1:
        raise ValueError("Benchmark iterations must be positive")
    results = []
    for count in counts:
        samples = [_benchmark_count(count) for _ in range(iterations)]
        timings = {
            key: round(median(float(sample["timings_seconds"][key]) for sample in samples), 6)
            for key in samples[0]["timings_seconds"]
        }
        total = round(sum(timings.values()), 6)
        budget = TOTAL_BUDGET_SECONDS.get(count, max(0.25, count / 1_000.0))
        results.append(
            {
                "asset_count": count,
                "iterations": iterations,
                "candidate_pairs": samples[0]["candidate_pairs"],
                "full_pair_count": count * (count - 1) // 2,
                "payload_bytes": samples[0]["payload_bytes"],
                "timings_seconds": timings,
                "total_seconds": total,
                "budget_seconds": budget,
                "passed": total <= budget,
            }
        )
    return {
        "schema_version": RELEASE_BENCHMARK_SCHEMA_VERSION,
        "environment": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": sys.version.split()[0],
            "peak_rss_bytes": _peak_rss_bytes(),
            "energy": {
                "status": "not_measured",
                "reason": "Requires an Instruments/MetricKit release run outside unit tests",
            },
        },
        "results": results,
        "passed": all(bool(result["passed"]) for result in results),
    }


def _benchmark_count(count: int) -> dict[str, object]:
    assets, signals = _synthetic_assets(count)

    started = perf_counter()
    pairs = list(_candidate_pairs(assets))
    candidate_seconds = perf_counter() - started

    started = perf_counter()
    percentiles = apple_score_percentiles(assets)
    for asset in assets:
        calculate_swipe_score(
            asset,
            None,
            signals[str(asset["asset_uuid"])],
            apple_percentiles=percentiles[str(asset["asset_uuid"])],
        )
    scoring_seconds = perf_counter() - started

    started = perf_counter()
    pair, _ = _next_taste_pair(assets, signals, {}, [])
    taste_pair_seconds = perf_counter() - started
    if pair is None:
        raise RuntimeError("Synthetic benchmark produced no taste pair")

    started = perf_counter()
    payload = json.dumps([_asset_payload(asset) for asset in assets], separators=(",", ":"))
    serialization_seconds = perf_counter() - started
    return {
        "candidate_pairs": len(pairs),
        "payload_bytes": len(payload.encode("utf-8")),
        "timings_seconds": {
            "candidate_reduction": candidate_seconds,
            "swipe_scoring": scoring_seconds,
            "taste_pair_selection": taste_pair_seconds,
            "worker_payload_serialization": serialization_seconds,
        },
    }


def _synthetic_assets(
    count: int,
) -> tuple[list[dict[str, object]], dict[str, dict[str, dict[str, object]]]]:
    randomizer = random.Random(42 + count)
    assets = []
    signals = {}
    for index in range(count):
        asset_uuid = f"benchmark-{index:05d}"
        asset = {
            "asset_uuid": asset_uuid,
            "current_filename": f"IMG_{index:05d}.jpg",
            "review_path": f"/benchmark/{asset_uuid}.jpg",
            "thumbnail_path": f"/benchmark/{asset_uuid}-thumb.jpg",
            "cache_state": "ready",
            "final_disposition": "keep" if index % 4 == 0 else "review",
            "favorite": False,
            "width": 4_032,
            "height": 3_024,
            "phash": f"{randomizer.getrandbits(64):016x}",
            "sharpness_percentile": randomizer.random(),
            "contrast_percentile": randomizer.random(),
            "luma_mean": 0.2 + randomizer.random() * 0.6,
            "swipe_score": float(100 - index % 80),
            "swipe_generic_score": float(100 - index % 80),
            "swipe_personal_delta": 0.0,
            "swipe_confidence": 0.8,
            "swipe_components": {},
            "swipe_reasons": [],
            "apple_scores": {},
        }
        assets.append(asset)
        signals[asset_uuid] = {
            "aesthetics": {
                "status": "ready",
                "engine_name": "benchmark",
                "engine_version": "1",
                "request_revision": 1,
                "value": {"overall_score": (index % 100) / 50.0 - 1.0},
            },
            "feature_print": {
                "status": "ready",
                "engine_name": "benchmark",
                "engine_version": "1",
                "request_revision": 1,
                "value": {},
            },
        }
    return assets, signals


def _peak_rss_bytes() -> int:
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return value if sys.platform == "darwin" else value * 1_024
