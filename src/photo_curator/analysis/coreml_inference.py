from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from photo_curator.analysis.coreml_benchmark import CoreMLBenchmarkEngine
from photo_curator.analysis.model_registry import ModelRegistryError, model_sha256
from photo_curator.paths import ApplicationPaths

CACHE_SCHEMA_VERSION = 1


class CoreMLInferenceEngine:
    def __init__(
        self,
        paths: ApplicationPaths,
        *,
        benchmark_engine: CoreMLBenchmarkEngine | None = None,
    ) -> None:
        self.paths = paths
        self.benchmark_engine = benchmark_engine or CoreMLBenchmarkEngine(paths)

    def infer(
        self,
        model: dict[str, object],
        assets: list[tuple[str, Path, str]],
    ) -> dict[str, object]:
        model_path, checksum = self._validated_model(model)
        if not assets:
            raise ValueError("Core ML inference requires at least one asset")
        seen: set[str] = set()
        ordered: list[tuple[str, Path, str, Path]] = []
        cached: dict[str, dict[str, object]] = {}
        missing: list[tuple[str, Path]] = []
        for asset_uuid, path, source_fingerprint in assets:
            if not asset_uuid or asset_uuid in seen or not path.is_file() or not source_fingerprint:
                raise ValueError(f"Invalid Core ML inference asset: {asset_uuid} {path}")
            seen.add(asset_uuid)
            cache_path = self._cache_path(checksum, source_fingerprint)
            ordered.append((asset_uuid, path, source_fingerprint, cache_path))
            hit = self._read_cache(
                cache_path,
                asset_uuid=asset_uuid,
                model_sha256=checksum,
                source_fingerprint=source_fingerprint,
            )
            if hit is None:
                missing.append((asset_uuid, path))
            else:
                cached[asset_uuid] = hit

        produced: dict[str, dict[str, object]] = {}
        runtime: dict[str, object] | None = None
        if missing:
            runtime = self.benchmark_engine.benchmark(
                model_path,
                missing,
                warmup_iterations=1,
                measured_iterations=1,
            )
            for row in runtime["assets"]:
                if not isinstance(row, dict):
                    continue
                asset_uuid = str(row.get("asset_uuid", ""))
                if asset_uuid and not row.get("error"):
                    produced[asset_uuid] = row

        results: list[dict[str, object]] = []
        for asset_uuid, _, source_fingerprint, cache_path in ordered:
            if asset_uuid in cached:
                results.append({**cached[asset_uuid], "cache_hit": True})
                continue
            row = produced.get(asset_uuid)
            if row is None:
                results.append(
                    {
                        "asset_uuid": asset_uuid,
                        "source_fingerprint": source_fingerprint,
                        "cache_hit": False,
                        "error": "Core ML inference returned no successful output",
                    }
                )
                continue
            value = {
                "asset_uuid": asset_uuid,
                "source_fingerprint": source_fingerprint,
                "model_sha256": checksum,
                "output": row.get("output"),
                "median_duration_ms": row.get("median_duration_ms"),
                "error": None,
            }
            self._write_cache(cache_path, value)
            results.append({**value, "cache_hit": False})

        return {
            "schema_version": 1,
            "engine": {"name": "apple-coreml-image", "version": "1"},
            "model": {
                "id": model["id"],
                "name": model["name"],
                "version": model["version"],
                "sha256": checksum,
                "compute_policy": model["compute_policy"],
            },
            "summary": {
                "asset_count": len(results),
                "cache_hits": len(cached),
                "inferred_assets": len(missing),
                "failed_assets": sum(bool(row.get("error")) for row in results),
            },
            "runtime": runtime.get("summary") if runtime else None,
            "assets": results,
        }

    @staticmethod
    def _validated_model(model: dict[str, object]) -> tuple[Path, str]:
        if model.get("status") != "approved":
            raise ModelRegistryError("Only an approved Core ML model may run in production")
        if model.get("compute_policy") != "all":
            raise ModelRegistryError("Core ML model must use the all compute policy")
        path = Path(str(model.get("model_path", "")))
        expected = str(model.get("sha256", ""))
        if len(expected) != 64 or model_sha256(path) != expected:
            raise ModelRegistryError("Core ML model checksum is no longer valid")
        return path, expected

    def _cache_path(self, model_sha256: str, source_fingerprint: str) -> Path:
        key = hashlib.sha256(
            f"{CACHE_SCHEMA_VERSION}\0{model_sha256}\0{source_fingerprint}".encode()
        ).hexdigest()
        return self.paths.cache_dir / "coreml" / model_sha256 / f"{key}.json"

    @staticmethod
    def _read_cache(
        path: Path,
        *,
        asset_uuid: str,
        model_sha256: str,
        source_fingerprint: str,
    ) -> dict[str, object] | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if (
            not isinstance(value, dict)
            or value.get("cache_schema_version") != CACHE_SCHEMA_VERSION
            or value.get("asset_uuid") != asset_uuid
            or value.get("model_sha256") != model_sha256
            or value.get("source_fingerprint") != source_fingerprint
            or value.get("error")
        ):
            return None
        value.pop("cache_schema_version", None)
        return value

    @staticmethod
    def _write_cache(path: Path, value: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(
                {"cache_schema_version": CACHE_SCHEMA_VERSION, **value},
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        temporary.replace(path)
