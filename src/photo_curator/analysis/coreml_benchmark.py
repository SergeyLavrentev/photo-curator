from __future__ import annotations

import hashlib
import json
import platform
import uuid
from collections.abc import Callable
from pathlib import Path

from photo_curator.paths import ApplicationPaths
from photo_curator.utils.subprocesses import CommandResult, find_executable, run_command

SOURCE = Path(__file__).parent / "native" / "photo_curator_coreml.swift"
ENGINE_NAME = "apple-coreml-image"


class CoreMLBenchmarkError(RuntimeError):
    """A Core ML model could not produce a trustworthy benchmark report."""


class CoreMLBenchmarkEngine:
    def __init__(
        self,
        paths: ApplicationPaths,
        *,
        runner: Callable[..., CommandResult] = run_command,
        swiftc: str | None = None,
    ) -> None:
        self.paths = paths
        self.runner = runner
        self.swiftc = swiftc or find_executable("swiftc")
        self.executable = paths.data_dir / "native" / "photo-curator-coreml"
        self.digest_file = self.executable.with_suffix(".sha256")

    def benchmark(
        self,
        model_path: Path,
        assets: list[tuple[str, Path]],
        *,
        warmup_iterations: int = 1,
        measured_iterations: int = 3,
    ) -> dict[str, object]:
        if model_path.suffix not in {".mlmodel", ".mlpackage", ".mlmodelc"}:
            raise ValueError("Core ML model must be .mlmodel, .mlpackage or .mlmodelc")
        if not model_path.exists():
            raise ValueError(f"Core ML model not found: {model_path}")
        if warmup_iterations < 0 or measured_iterations < 1:
            raise ValueError("Invalid Core ML benchmark iterations")
        seen: set[str] = set()
        rows = []
        for asset_uuid, path in assets:
            if not asset_uuid or asset_uuid in seen or not path.is_file():
                raise ValueError(f"Invalid Core ML benchmark asset: {asset_uuid} {path}")
            seen.add(asset_uuid)
            rows.append({"asset_uuid": asset_uuid, "path": str(path.resolve())})
        if not rows:
            raise ValueError("Core ML benchmark requires at least one asset")
        self._ensure_compiled()
        request_dir = self.paths.cache_dir / "_coreml_benchmark"
        request_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        request_id = uuid.uuid4().hex
        input_path = request_dir / f"{request_id}.input.json"
        output_path = request_dir / f"{request_id}.output.json"
        input_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "model_path": str(model_path.resolve()),
                    "warmup_iterations": warmup_iterations,
                    "measured_iterations": measured_iterations,
                    "assets": rows,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        try:
            result = self.runner(
                [str(self.executable), str(input_path), str(output_path)],
                timeout=max(180, len(rows) * (warmup_iterations + measured_iterations) * 15),
            )
            if result.returncode != 0:
                raise CoreMLBenchmarkError((result.stderr or "Core ML benchmark failed")[-2000:])
            try:
                payload = json.loads(output_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise CoreMLBenchmarkError("Core ML benchmark returned invalid JSON") from error
        finally:
            input_path.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)
        self._validate(payload, seen)
        return payload

    def _ensure_compiled(self) -> None:
        if not self.swiftc or not SOURCE.is_file():
            raise CoreMLBenchmarkError("Swift/Core ML toolchain is unavailable")
        target = f"{platform.machine()}-apple-macosx13.0"
        digest = hashlib.sha256(SOURCE.read_bytes() + target.encode()).hexdigest()
        if (
            self.executable.is_file()
            and self.digest_file.is_file()
            and self.digest_file.read_text(encoding="utf-8").strip() == digest
        ):
            return
        self.executable.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        result = self.runner(
            [
                self.swiftc,
                "-swift-version",
                "5",
                "-O",
                "-target",
                target,
                "-framework",
                "Vision",
                "-framework",
                "CoreML",
                "-framework",
                "AppKit",
                str(SOURCE),
                "-o",
                str(self.executable),
            ],
            timeout=180,
        )
        if result.returncode != 0:
            raise CoreMLBenchmarkError((result.stderr or "Core ML helper build failed")[-2000:])
        self.digest_file.write_text(digest, encoding="utf-8")

    @staticmethod
    def _validate(payload: object, expected_ids: set[str]) -> None:
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise CoreMLBenchmarkError("Unsupported Core ML benchmark schema")
        engine = payload.get("engine")
        model = payload.get("model")
        rows = payload.get("assets")
        summary = payload.get("summary")
        if not isinstance(engine, dict) or engine.get("name") != ENGINE_NAME:
            raise CoreMLBenchmarkError("Core ML engine identity mismatch")
        if not isinstance(model, dict) or model.get("compute_units") != "all":
            raise CoreMLBenchmarkError("Core ML benchmark did not request all compute units")
        if (
            not isinstance(summary, dict)
            or not isinstance(summary.get("peak_rss_bytes"), (int, float))
            or summary["peak_rss_bytes"] <= 0
        ):
            raise CoreMLBenchmarkError("Core ML peak memory evidence is missing")
        if not isinstance(rows, list):
            raise CoreMLBenchmarkError("Core ML benchmark assets are missing")
        returned = {str(row.get("asset_uuid")) for row in rows if isinstance(row, dict)}
        if returned != expected_ids or len(rows) != len(expected_ids):
            raise CoreMLBenchmarkError("Core ML benchmark returned incomplete assets")
