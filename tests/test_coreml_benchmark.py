from __future__ import annotations

import json
import shutil
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from photo_curator.analysis.coreml_benchmark import CoreMLBenchmarkEngine
from photo_curator.analysis.model_registry import register_model
from photo_curator.cli import run_coreml_benchmark_command
from photo_curator.db.connection import database_connection
from photo_curator.paths import default_application_paths
from photo_curator.utils.subprocesses import CommandResult
from tests.test_pipeline import build_pipeline


def test_engine_validates_coreml_result_and_removes_requests(tmp_path: Path) -> None:
    paths = default_application_paths(tmp_path / "app")
    paths.ensure()
    model = tmp_path / "scorer.mlmodelc"
    model.mkdir()
    (model / "model.bin").write_bytes(b"test-model")
    image = tmp_path / "image.jpg"
    image.write_bytes(b"jpeg-placeholder")
    requests: list[dict[str, object]] = []

    def runner(args: list[str], *, timeout: int) -> CommandResult:
        if args[0] == "/usr/bin/swiftc":
            output = Path(args[args.index("-o") + 1])
            output.touch()
            return CommandResult(args, 0, "", "")
        request = json.loads(Path(args[1]).read_text(encoding="utf-8"))
        requests.append(request)
        Path(args[2]).write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "engine": {"name": "apple-coreml-image", "version": "1"},
                    "model": {"compute_units": "all"},
                    "assets": [
                        {
                            "asset_uuid": "asset-1",
                            "duration_ms": 4.2,
                            "values": [0.25, 0.75],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return CommandResult(args, 0, "", "")

    result = CoreMLBenchmarkEngine(paths, runner=runner, swiftc="/usr/bin/swiftc").benchmark(
        model, [("asset-1", image)], warmup_iterations=2, measured_iterations=4
    )

    assert result["model"]["compute_units"] == "all"
    assert requests[0]["warmup_iterations"] == 2
    assert requests[0]["measured_iterations"] == 4
    assert requests[0]["assets"] == [{"asset_uuid": "asset-1", "path": str(image.resolve())}]
    assert not list((paths.cache_dir / "_coreml_benchmark").glob("*.json"))


@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("xcrun") is None,
    reason="Core ML helper requires the macOS Swift toolchain",
)
def test_coreml_helper_compiles_and_reports_capability(tmp_path: Path) -> None:
    paths = default_application_paths(tmp_path / "app")
    paths.ensure()
    engine = CoreMLBenchmarkEngine(paths)

    engine._ensure_compiled()
    result = engine.runner([str(engine.executable), "--capability"], timeout=30)

    assert result.returncode == 0
    assert result.stdout.strip() == "coreml-image-benchmark-v1"


def test_cli_benchmarks_ready_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, _, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)
    model = tmp_path / "scorer.mlmodelc"
    model.mkdir()
    (model / "model.bin").write_bytes(b"test-model")
    calls: list[tuple[Path, list[tuple[str, Path]], int, int]] = []

    class FakeEngine:
        def __init__(self, received_paths):
            assert received_paths == paths

        def benchmark(self, model_path, assets, *, warmup_iterations, measured_iterations):
            calls.append((model_path, assets, warmup_iterations, measured_iterations))
            return {
                "schema_version": 1,
                "engine": {"name": "apple-coreml-image", "version": "1"},
                "model": {"compute_units": "all"},
                "assets": [
                    {"asset_uuid": asset_uuid, "duration_ms": 1.0, "values": [0.5]}
                    for asset_uuid, _ in assets
                ],
            }

    monkeypatch.setattr("photo_curator.cli.default_application_paths", lambda: paths)
    monkeypatch.setattr("photo_curator.cli.CoreMLBenchmarkEngine", FakeEngine)
    with database_connection(paths.database) as connection:
        registered = register_model(
            connection,
            name="Test scorer",
            version="1",
            model_path=model,
            license_id="Apache-2.0",
            source_url=None,
            commercial_use_allowed=True,
        )
    output = tmp_path / "coreml.json"

    result = run_coreml_benchmark_command(
        Namespace(
            project_id=project_id,
            model_id=registered["id"],
            warmup=1,
            iterations=3,
            output=output,
        )
    )

    assert result == 0
    assert len(calls[0][1]) == 12
    assert calls[0][0] == model
    assert calls[0][2:] == (1, 3)
    assert json.loads(output.read_text(encoding="utf-8"))["schema_version"] == 1
    assert json.loads(output.read_text(encoding="utf-8"))["registry"]["sha256"]
