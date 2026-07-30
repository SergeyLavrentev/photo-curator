from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import sys
import uuid
from collections.abc import Callable
from pathlib import Path

from photo_curator.paths import ApplicationPaths
from photo_curator.utils.subprocesses import CommandResult, find_executable, run_command

LOGGER = logging.getLogger(__name__)
SOURCE = Path(__file__).parent / "native" / "photo_curator_vision.swift"
ENGINE_NAME = "apple-vision-native"
ENGINE_VERSION = "1"
DEFAULT_BATCH_SIZE = 250


class NativeVisionError(RuntimeError):
    """Native Vision benchmark could not produce a trustworthy result."""


class NativeVisionEngine:
    def __init__(
        self,
        paths: ApplicationPaths,
        *,
        runner: Callable[..., CommandResult] = run_command,
        swiftc: str | None = None,
    ) -> None:
        self.paths = paths
        self.runner = runner
        self.swiftc = swiftc or find_executable("swiftc") or _xcrun_swiftc(runner)
        bundled = _bundled_executable()
        self._is_bundled = bundled is not None
        self.executable = bundled or paths.data_dir / "native" / "photo-curator-vision"
        self.digest_file = self.executable.with_suffix(".sha256")

    def analyze(
        self,
        assets: list[tuple[str, Path]],
        *,
        warmup_iterations: int = 0,
        measured_iterations: int = 1,
    ) -> dict[str, object]:
        if warmup_iterations < 0 or measured_iterations < 1:
            raise ValueError("Некорректное количество benchmark iterations")
        seen: set[str] = set()
        rows = []
        for asset_uuid, path in assets:
            if not asset_uuid or asset_uuid in seen:
                raise ValueError(f"Повторный или пустой asset UUID: {asset_uuid}")
            if not path.is_file():
                raise ValueError(f"Файл для Vision не найден: {path}")
            seen.add(asset_uuid)
            rows.append({"asset_uuid": asset_uuid, "path": str(path.resolve())})
        self._ensure_compiled()
        reports = [
            self._analyze_batch(
                rows[index : index + DEFAULT_BATCH_SIZE],
                warmup_iterations=warmup_iterations,
                measured_iterations=measured_iterations,
            )
            for index in range(0, len(rows), DEFAULT_BATCH_SIZE)
        ]
        payload = _merge_reports(
            reports,
            warmup_iterations=warmup_iterations,
            measured_iterations=measured_iterations,
        )
        self._validate(payload, seen)
        return payload

    def _analyze_batch(
        self,
        rows: list[dict[str, str]],
        *,
        warmup_iterations: int,
        measured_iterations: int,
    ) -> dict[str, object]:
        request_dir = self.paths.cache_dir / "_native_vision"
        request_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        request_id = uuid.uuid4().hex
        request_path = request_dir / f"{request_id}.input.json"
        result_path = request_dir / f"{request_id}.output.json"
        request_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "warmup_iterations": warmup_iterations,
                    "measured_iterations": measured_iterations,
                    "assets": rows,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        try:
            result = self.runner(
                [str(self.executable), str(request_path), str(result_path)],
                timeout=max(120, len(rows) * (warmup_iterations + measured_iterations) * 10),
            )
            if result.returncode != 0:
                raise NativeVisionError((result.stderr or "Native Vision failed")[-1000:])
            try:
                payload = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise NativeVisionError("Native Vision вернул некорректный JSON") from error
        finally:
            request_path.unlink(missing_ok=True)
            result_path.unlink(missing_ok=True)
        self._validate(payload, {row["asset_uuid"] for row in rows})
        return payload

    def _ensure_compiled(self) -> None:
        if self._is_bundled:
            if self.executable.is_file():
                return
            raise NativeVisionError("В bundle отсутствует Vision helper")
        if not self.swiftc or not SOURCE.is_file():
            raise NativeVisionError("Swift/Vision toolchain недоступен")
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
                "CoreVideo",
                str(SOURCE),
                "-o",
                str(self.executable),
            ],
            timeout=180,
        )
        if result.returncode != 0:
            raise NativeVisionError((result.stderr or "Не удалось собрать Vision helper")[-2000:])
        self.digest_file.write_text(digest, encoding="utf-8")

    @staticmethod
    def _validate(payload: object, expected_ids: set[str]) -> None:
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise NativeVisionError("Native Vision schema_version не поддерживается")
        engine = payload.get("engine")
        if not isinstance(engine, dict) or engine.get("name") != ENGINE_NAME:
            raise NativeVisionError("Native Vision engine identity не совпадает")
        rows = payload.get("assets")
        summary = payload.get("summary")
        if (
            not isinstance(summary, dict)
            or not isinstance(summary.get("peak_rss_bytes"), (int, float))
            or summary["peak_rss_bytes"] <= 0
        ):
            raise NativeVisionError("Native Vision peak memory evidence отсутствует")
        if not isinstance(rows, list):
            raise NativeVisionError("Native Vision assets отсутствуют")
        returned_ids = {str(row.get("asset_uuid")) for row in rows if isinstance(row, dict)}
        if returned_ids != expected_ids or len(rows) != len(expected_ids):
            raise NativeVisionError("Native Vision вернул неполный набор assets")


def aesthetics_score_snapshot(project_id: str, payload: dict[str, object]) -> dict[str, object]:
    rows = payload.get("assets")
    if not isinstance(rows, list):
        raise NativeVisionError("Native Vision assets отсутствуют")
    scores: dict[str, float] = {}
    revision: int | None = None
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("asset_uuid"), str):
            raise NativeVisionError("Native Vision asset имеет некорректный UUID")
        aesthetics = row.get("aesthetics")
        if not isinstance(aesthetics, dict):
            raise NativeVisionError(f"Aesthetics недоступен для {row['asset_uuid']}")
        score = aesthetics.get("overall_score")
        current_revision = aesthetics.get("revision")
        if not isinstance(score, (int, float)) or not -1 <= float(score) <= 1:
            raise NativeVisionError(f"Aesthetics score некорректен для {row['asset_uuid']}")
        if not isinstance(current_revision, int):
            raise NativeVisionError("Aesthetics revision отсутствует")
        if revision is not None and revision != current_revision:
            raise NativeVisionError("Aesthetics revision изменился внутри одного benchmark")
        revision = current_revision
        scores[str(row["asset_uuid"])] = (float(score) + 1.0) * 50.0
    if revision is None:
        raise NativeVisionError("Aesthetics benchmark не содержит assets")
    return {
        "schema_version": 1,
        "project_id": project_id,
        "engine": {
            "name": "apple-vision-aesthetics",
            "version": f"native-v{ENGINE_VERSION}-revision-{revision}",
        },
        "scores": scores,
    }


def _merge_reports(
    reports: list[dict[str, object]],
    *,
    warmup_iterations: int,
    measured_iterations: int,
) -> dict[str, object]:
    if not reports:
        return {
            "schema_version": 1,
            "engine": {
                "name": ENGINE_NAME,
                "version": ENGINE_VERSION,
            },
            "capabilities": {},
            "warmup_iterations": warmup_iterations,
            "measured_iterations": measured_iterations,
            "assets": [],
            "summary": {
                "asset_count": 0,
                "successful_assets": 0,
                "wall_time_ms": 0,
                "peak_rss_bytes": 1,
                "stage_durations": {},
            },
        }
    first = reports[0]
    assets: list[object] = []
    successful_assets = 0
    wall_time_ms = 0.0
    peak_rss_bytes = 0
    for report in reports:
        if report.get("engine") != first.get("engine"):
            raise NativeVisionError("Native Vision engine изменился между пакетами")
        if report.get("capabilities") != first.get("capabilities"):
            raise NativeVisionError("Native Vision capabilities изменились между пакетами")
        rows = report.get("assets")
        summary = report.get("summary")
        if not isinstance(rows, list) or not isinstance(summary, dict):
            raise NativeVisionError("Native Vision batch contract нарушен")
        assets.extend(rows)
        successful_assets += int(summary.get("successful_assets") or 0)
        wall_time_ms += float(summary.get("wall_time_ms") or 0)
        peak_rss_bytes = max(peak_rss_bytes, int(summary.get("peak_rss_bytes") or 0))
    return {
        "schema_version": 1,
        "engine": first["engine"],
        "capabilities": first.get("capabilities", {}),
        "warmup_iterations": warmup_iterations,
        "measured_iterations": measured_iterations,
        "assets": assets,
        "summary": {
            "asset_count": len(assets),
            "successful_assets": successful_assets,
            "wall_time_ms": wall_time_ms,
            "peak_rss_bytes": peak_rss_bytes,
            "stage_durations": {},
        },
    }


def _xcrun_swiftc(runner: Callable[..., CommandResult]) -> str | None:
    try:
        result = runner(["xcrun", "--find", "swiftc"], timeout=30)
        candidate = result.stdout.strip()
        return candidate if result.returncode == 0 and Path(candidate).is_file() else None
    except Exception:
        LOGGER.debug("xcrun swiftc lookup failed", exc_info=True)
        return None


def _bundled_executable() -> Path | None:
    configured = os.environ.get("PHOTO_CURATOR_VISION_HELPER")
    if configured:
        return Path(configured)
    if not getattr(sys, "frozen", False):
        return None
    candidate = Path(sys.executable).resolve().parents[1] / "native" / "photo-curator-vision"
    return candidate
