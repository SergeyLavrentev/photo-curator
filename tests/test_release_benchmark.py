from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

from photo_curator.cli import run_release_benchmark_command
from photo_curator.release_benchmark import run_release_benchmark


def test_release_benchmark_covers_rank_pair_and_payload_hot_paths() -> None:
    report = run_release_benchmark(counts=(100, 250), iterations=1, gallery_count=1_000)

    assert report["schema_version"] == 3
    assert report["passed"]
    assert report["gallery"]["asset_count"] == 1_000
    assert report["gallery"]["sql_page_p95_ms"] < 100
    assert report["environment"]["energy"]["status"] == "not_measured"
    for result in report["results"]:
        assert result["candidate_pairs"] > 0
        assert result["candidate_pairs"] < result["full_pair_count"]
        assert result["payload_bytes"] > result["asset_count"] * 100
        assert set(result["timings_seconds"]) == {
            "candidate_reduction",
            "swipe_scoring",
            "taste_pair_selection",
            "worker_payload_serialization",
        }


def test_release_benchmark_cli_writes_versioned_report(tmp_path: Path) -> None:
    output = tmp_path / "release.json"

    result = run_release_benchmark_command(
        Namespace(counts=(100,), iterations=1, gallery_count=1_000, output=output)
    )

    assert result == 0
    assert json.loads(output.read_text(encoding="utf-8"))["schema_version"] == 3
