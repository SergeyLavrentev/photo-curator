from __future__ import annotations

import json
import platform
import random
import resource
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median
from time import perf_counter

from PIL import Image

from photo_curator.analysis.swipe_score import (
    apple_score_percentiles,
    calculate_swipe_score,
)
from photo_curator.db import repository
from photo_curator.db.connection import database_connection
from photo_curator.db.migrations import migrate
from photo_curator.native_worker import _asset_payload, _next_taste_pair
from photo_curator.pipeline.duplicates import _candidate_pairs

RELEASE_BENCHMARK_SCHEMA_VERSION = 2
DEFAULT_COUNTS = (100, 2_000, 5_000)
TOTAL_BUDGET_SECONDS = {100: 0.25, 2_000: 2.0, 5_000: 5.0}


def run_release_benchmark(
    *,
    counts: tuple[int, ...] = DEFAULT_COUNTS,
    iterations: int = 3,
    gallery_count: int = 50_000,
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
    gallery = run_gallery_data_benchmark(gallery_count)
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
        "gallery": gallery,
        "passed": all(bool(result["passed"]) for result in results) and bool(gallery["passed"]),
    }


def run_gallery_data_benchmark(count: int = 50_000) -> dict[str, object]:
    """Exercise the real SQLite card query, cursor, mutation and JPEG downsample path."""
    if count < 100:
        raise ValueError("Gallery benchmark requires at least 100 assets")
    with tempfile.TemporaryDirectory(prefix="photo-curator-gallery-") as temporary:
        root = Path(temporary)
        database = root / "gallery.sqlite3"
        thumbnail = root / "thumbnail.jpg"
        Image.new("RGB", (1_600, 1_067), (71, 104, 138)).save(thumbnail, "JPEG", quality=88)
        _seed_gallery_database(database, thumbnail, count)
        cold_started = perf_counter()
        with database_connection(database) as connection:
            total = repository.count_assets(connection, "benchmark", selection="pick")
            first_page = repository.list_assets_page(
                connection, "benchmark", selection="pick", limit=48
            )
        cold_ms = (perf_counter() - cold_started) * 1_000
        samples = []
        cursor_score = float(first_page[-1]["swipe_score"])
        cursor_uuid = str(first_page[-1]["asset_uuid"])
        with database_connection(database) as connection:
            for _ in range(30):
                started = perf_counter()
                page = repository.list_assets_page(
                    connection,
                    "benchmark",
                    selection="pick",
                    limit=48,
                    cursor_score=cursor_score,
                    cursor_asset_uuid=cursor_uuid,
                )
                samples.append((perf_counter() - started) * 1_000)
                if not page:
                    break
                cursor_score = float(page[-1]["swipe_score"])
                cursor_uuid = str(page[-1]["asset_uuid"])
            mutation_started = perf_counter()
            repository.set_manual_decision(
                connection, "benchmark", str(first_page[0]["asset_uuid"]), "reject"
            )
            repository.list_assets_page(connection, "benchmark", selection="pick", limit=48)
            mutation_ms = (perf_counter() - mutation_started) * 1_000
        decode_samples = []
        for _ in range(30):
            started = perf_counter()
            with Image.open(thumbnail) as image:
                image.thumbnail((640, 640), Image.Resampling.LANCZOS)
                image.load()
            decode_samples.append((perf_counter() - started) * 1_000)
        query_p95 = _percentile(samples, 0.95)
        decode_p95 = _percentile(decode_samples, 0.95)
        return {
            "schema_version": 1,
            "asset_count": count,
            "pick_count": total,
            "page_size": 48,
            "cold_first_page_ms": round(cold_ms, 3),
            "sql_page_p95_ms": round(query_p95, 3),
            "mutation_refresh_ms": round(mutation_ms, 3),
            "jpeg_downsample_p95_ms": round(decode_p95, 3),
            "database_bytes": database.stat().st_size,
            "thresholds_ms": {"cold_first_page": 700, "sql_page_p95": 100},
            "passed": cold_ms < 700 and query_p95 < 100,
        }


def _seed_gallery_database(database: Path, thumbnail: Path, count: int) -> None:
    now = "2026-01-01T00:00:00+00:00"
    with database_connection(database) as connection:
        migrate(connection)
        connection.execute(
            """
            INSERT INTO projects (
                id, name, library_path, library_fingerprint, album_id, album_name,
                album_full_path, state, settings_json, created_at, updated_at
            ) VALUES ('benchmark', 'Benchmark', 'photokit://benchmark', 'fixture',
                'album', 'Album', 'Album', 'ready', '{}', ?, ?)
            """,
            (now, now),
        )
        assets = []
        decisions = []
        scores = []
        for index in range(count):
            uuid = f"asset-{index:06d}"
            selection = ("pick", "alternative", "review")[index % 3]
            disposition = "keep" if selection != "review" else "review"
            assets.append(
                (
                    "benchmark",
                    uuid,
                    f"IMG_{index:06d}.HEIC",
                    4_032,
                    3_024,
                    str(thumbnail),
                    str(thumbnail),
                    now,
                    now,
                )
            )
            decisions.append(
                (
                    "benchmark",
                    uuid,
                    disposition,
                    disposition,
                    selection,
                    selection,
                    0.5,
                    now,
                )
            )
            score = float(100 - index % 101)
            scores.append(("benchmark", uuid, score, score, now))
        connection.executemany(
            """
            INSERT INTO assets (
                project_id, asset_uuid, current_filename, width, height,
                review_path, thumbnail_path, cache_state, metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ready', '{}', ?, ?)
            """,
            assets,
        )
        connection.executemany(
            """
            INSERT INTO decisions (
                project_id, asset_uuid, auto_disposition, final_disposition,
                auto_selection, final_selection, confidence, flags_json, reasons_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '[]', '[]', ?)
            """,
            decisions,
        )
        connection.executemany(
            """
            INSERT INTO swipe_scores (
                project_id, asset_uuid, schema_version, score, generic_score,
                confidence, components_json, reasons_json, model_versions_json, calculated_at
            ) VALUES (?, ?, 1, ?, ?, 0.8, '{}', '[]', '{}', ?)
            """,
            scores,
        )
        connection.execute("PRAGMA optimize")


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return float("inf")
    ranked = sorted(values)
    return ranked[min(len(ranked) - 1, int(len(ranked) * percentile))]


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
    series_hash = 0
    for index in range(count):
        asset_uuid = f"benchmark-{index:05d}"
        if index % 20 == 0:
            series_hash = randomizer.getrandbits(64)
        phash_value = (
            series_hash ^ (1 << (index % 8)) if index % 20 < 3 else randomizer.getrandbits(64)
        )
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
            "phash": f"{phash_value:016x}",
            "taken_at": (
                datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=index * 60)
            ).isoformat(),
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
