from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import sys
import uuid
from collections.abc import Callable
from pathlib import Path

from photo_curator.paths import ApplicationPaths
from photo_curator.utils.subprocesses import (
    CommandCancelled,
    CommandResult,
    find_executable,
    run_command,
)

SOURCE = Path(__file__).parent / "native" / "photo_curator_local_models.swift"
ENGINE_NAME = "photo-curator-local-models"
ENGINE_VERSION = "2"
DEFAULT_WORK_BATCH_SIZE = 16
DEFAULT_MAX_CONCURRENCY = 2
MAX_WORK_BATCH_SIZE = 256
MAX_CONCURRENCY = 4
MODEL_ENGINE_NAMES = {
    "nima": "nima-inception-v2-ava-coreml",
    "mobileclip": "mobileclip-s0-coreml",
    "musiq": "musiq-koniq10k-coreml",
}
MODEL_ENGINE_VERSIONS = {
    "nima": "inception-resnet-v2-ava-fp16",
    "mobileclip": "s0-apple-coreml",
    "musiq": "koniq10k-fp16-fixed337",
}
VALID_ENGINES = frozenset(MODEL_ENGINE_NAMES)


class LocalModelError(RuntimeError):
    """The optional local model stack could not return a trustworthy report."""


class LocalModelCancelled(LocalModelError):
    """The local model helper stopped after a cooperative cancellation request."""


class LocalModelEngine:
    def __init__(
        self,
        paths: ApplicationPaths,
        *,
        runner: Callable[..., CommandResult] = run_command,
        swiftc: str | None = None,
        model_root: Path | None = None,
    ) -> None:
        self.paths = paths
        self.runner = runner
        self.swiftc = swiftc or find_executable("swiftc")
        bundled = _bundled_resources()
        self._is_bundled = bundled is not None
        if bundled is not None:
            self.executable, self.model_root = bundled
        else:
            self.executable = paths.data_dir / "native" / "photo-curator-local-models"
            configured = os.environ.get("PHOTO_CURATOR_MODEL_ROOT")
            self.model_root = model_root or (
                Path(configured).expanduser().resolve()
                if configured
                else Path.cwd() / ".model-cache"
            )
        self.digest_file = self.executable.with_suffix(".sha256")

    def analyze(
        self,
        assets: list[tuple[str, Path]],
        *,
        engines: set[str],
        work_batch_size: int = DEFAULT_WORK_BATCH_SIZE,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, object]:
        unknown = engines - VALID_ENGINES
        if unknown:
            raise ValueError(f"Unknown local model engines: {sorted(unknown)}")
        if not engines:
            return _empty_report()
        if not 1 <= work_batch_size <= MAX_WORK_BATCH_SIZE:
            raise ValueError(f"work_batch_size must be between 1 and {MAX_WORK_BATCH_SIZE}")
        if not 1 <= max_concurrency <= MAX_CONCURRENCY:
            raise ValueError(f"max_concurrency must be between 1 and {MAX_CONCURRENCY}")
        if max_concurrency > work_batch_size:
            raise ValueError("max_concurrency cannot exceed work_batch_size")
        rows: list[dict[str, str]] = []
        seen: set[str] = set()
        for asset_uuid, path in assets:
            if not asset_uuid or asset_uuid in seen or not path.is_file():
                raise ValueError(f"Invalid local-model asset: {asset_uuid} {path}")
            seen.add(asset_uuid)
            rows.append({"asset_uuid": asset_uuid, "path": str(path.resolve())})
        if not rows:
            return _empty_report()
        model_digests = self._ensure_ready(engines)
        request_dir = self.paths.cache_dir / "_local_models"
        request_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        request_id = uuid.uuid4().hex
        input_path = request_dir / f"{request_id}.input.json"
        output_path = request_dir / f"{request_id}.output.json"
        input_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "model_root": str(self.model_root.resolve()),
                    "compiled_cache_root": str(
                        (self.paths.cache_dir / "_compiled_models").resolve()
                    ),
                    "model_digests": model_digests,
                    "engines": sorted(engines),
                    "assets": rows,
                    "work_batch_size": work_batch_size,
                    "max_concurrency": max_concurrency,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        try:
            try:
                result = self.runner(
                    [str(self.executable), str(input_path), str(output_path)],
                    timeout=max(180, len(rows) * len(engines) * 20),
                    cancelled=cancelled,
                )
            except CommandCancelled as error:
                raise LocalModelCancelled(str(error)) from error
            if result.returncode != 0:
                raise LocalModelError((result.stderr or "Local model helper failed")[-2000:])
            try:
                payload = json.loads(output_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise LocalModelError("Local model helper returned invalid JSON") from error
        finally:
            input_path.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)
        self._validate(
            payload,
            seen,
            engines,
            expected_work_batch_size=work_batch_size,
            expected_max_concurrency=max_concurrency,
        )
        payload["model_digests"] = model_digests
        return payload

    def verified_digests(self, engines: set[str]) -> dict[str, str]:
        """Verify resources and expose immutable versions for incremental inference."""
        return self._ensure_ready(engines)

    def _ensure_ready(self, engines: set[str]) -> dict[str, str]:
        required = {
            "nima": "nima-inception-v2-ava.mlpackage",
            "mobileclip": "mobileclip_s0_image.mlpackage",
            "musiq": "musiq-koniq10k.mlpackage",
        }
        missing = [
            required[name]
            for name in sorted(engines)
            if not (self.model_root / required[name]).exists()
        ]
        if "mobileclip" in engines and not (self.model_root / "mobileclip-prompts.json").is_file():
            missing.append("mobileclip-prompts.json")
        if missing:
            raise LocalModelError("Local model resources are missing: " + ", ".join(missing))
        model_digests = self._verify_model_manifest(engines, required)
        if self._is_bundled:
            if not self.executable.is_file():
                raise LocalModelError("Bundled local model helper is missing")
            return model_digests
        if not self.swiftc or not SOURCE.is_file():
            raise LocalModelError("Swift/Core ML toolchain is unavailable")
        target = f"{platform.machine()}-apple-macosx13.0"
        digest = hashlib.sha256(SOURCE.read_bytes() + target.encode()).hexdigest()
        if (
            self.executable.is_file()
            and self.digest_file.is_file()
            and self.digest_file.read_text(encoding="utf-8").strip() == digest
        ):
            return model_digests
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
            raise LocalModelError((result.stderr or "Local model helper build failed")[-2000:])
        self.digest_file.write_text(digest, encoding="utf-8")
        return model_digests

    def _verify_model_manifest(self, engines: set[str], required: dict[str, str]) -> dict[str, str]:
        manifest_path = self.model_root / "models.json"
        if not manifest_path.is_file():
            manifest_path = Path.cwd() / "packaging" / "models" / "models.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            rows = manifest["models"]
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise LocalModelError("Local model manifest is missing or invalid") from error
        by_name = {
            str(row.get("name")): row for row in rows if isinstance(row, dict) and row.get("name")
        }
        names = {required[engine] for engine in engines}
        if "mobileclip" in engines:
            names.add("mobileclip-prompts.json")
        verified: dict[str, str] = {}
        for name in sorted(names):
            row = by_name.get(name)
            expected = str(row.get("content_sha256") or "") if row else ""
            if len(expected) != 64:
                raise LocalModelError(f"Local model manifest has no digest for {name}")
            actual = _content_digest(self.model_root / name, name)
            if actual != expected:
                raise LocalModelError(f"Local model digest mismatch: {name}")
            verified[name] = actual
        return {engine: verified[required[engine]] for engine in engines}

    @staticmethod
    def _validate(
        payload: object,
        expected_ids: set[str],
        engines: set[str],
        *,
        expected_work_batch_size: int = DEFAULT_WORK_BATCH_SIZE,
        expected_max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    ) -> None:
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise LocalModelError("Unsupported local model report schema")
        engine = payload.get("engine")
        rows = payload.get("assets")
        if (
            not isinstance(engine, dict)
            or engine.get("name") != ENGINE_NAME
            or str(engine.get("version")) != ENGINE_VERSION
        ):
            raise LocalModelError("Local model helper identity mismatch")
        if not isinstance(rows, list):
            raise LocalModelError("Local model report has no assets")
        returned = {str(row.get("asset_uuid")) for row in rows if isinstance(row, dict)}
        if returned != expected_ids or len(rows) != len(expected_ids):
            raise LocalModelError("Local model helper returned an incomplete asset set")
        reported = payload.get("enabled_engines")
        if not isinstance(reported, list) or set(map(str, reported)) != engines:
            raise LocalModelError("Local model helper enabled-engine set mismatch")
        summary = payload.get("summary")
        if not isinstance(summary, dict):
            raise LocalModelError("Local model helper summary is missing")
        if (
            not isinstance(summary.get("peak_rss_bytes"), (int, float))
            or float(summary["peak_rss_bytes"]) <= 0
            or not isinstance(summary.get("wall_duration_ms"), (int, float))
            or float(summary["wall_duration_ms"]) <= 0
            or summary.get("thermal_state_before")
            not in {"nominal", "fair", "serious", "critical", "unknown"}
            or summary.get("thermal_state_after")
            not in {"nominal", "fair", "serious", "critical", "unknown"}
            or summary.get("energy_status") != "not_measured"
            or summary.get("work_batch_size") != expected_work_batch_size
            or summary.get("max_concurrency") != expected_max_concurrency
        ):
            raise LocalModelError("Local model runtime evidence is invalid")
        value_keys = {"nima": "nima", "mobileclip": "mobileclip", "musiq": "musiq"}
        score_keys = {
            "nima": "aesthetic_score",
            "mobileclip": "aesthetic_score",
            "musiq": "quality_score",
        }
        for row in rows:
            if not isinstance(row, dict):
                raise LocalModelError("Local model helper returned an invalid asset row")
            errors = row.get("errors") if isinstance(row.get("errors"), dict) else {}
            for name in engines:
                value = row.get(value_keys[name])
                if value is None and isinstance(errors.get(name), str):
                    continue
                if not isinstance(value, dict):
                    raise LocalModelError(f"Local model helper returned no {name} result")
                score = value.get(score_keys[name])
                if (
                    not isinstance(score, (int, float))
                    or not math.isfinite(float(score))
                    or not 0.0 <= float(score) <= 100.0
                ):
                    raise LocalModelError(f"Local model helper returned invalid {name} score")


def _bundled_resources() -> tuple[Path, Path] | None:
    configured_helper = os.environ.get("PHOTO_CURATOR_LOCAL_MODELS_HELPER")
    configured_models = os.environ.get("PHOTO_CURATOR_MODEL_ROOT")
    if configured_helper and configured_models:
        return Path(configured_helper), Path(configured_models)
    if not getattr(sys, "frozen", False):
        return None
    resources = Path(sys.executable).resolve().parents[1]
    return resources / "native" / "photo-curator-local-models", resources / "models"


def _empty_report() -> dict[str, object]:
    return {
        "schema_version": 1,
        "engine": {"name": ENGINE_NAME, "version": ENGINE_VERSION},
        "enabled_engines": [],
        "assets": [],
        "summary": {"asset_count": 0, "successful_signals": 0, "errors": 0},
    }


def _content_digest(path: Path, manifest_name: str) -> str:
    if not path.exists():
        raise LocalModelError(f"Local model resource is missing: {manifest_name}")
    files = (
        [path] if path.is_file() else sorted(value for value in path.rglob("*") if value.is_file())
    )
    lines = []
    for value in files:
        relative = manifest_name if value == path else f"{manifest_name}/{value.relative_to(path)}"
        lines.append(f"{hashlib.sha256(value.read_bytes()).hexdigest()}  {relative}\n".encode())
    return hashlib.sha256(b"".join(lines)).hexdigest()
