from __future__ import annotations

import hashlib
import json
import logging
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
        self._validate(payload, seen)
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


def _xcrun_swiftc(runner: Callable[..., CommandResult]) -> str | None:
    try:
        result = runner(["xcrun", "--find", "swiftc"], timeout=30)
        candidate = result.stdout.strip()
        return candidate if result.returncode == 0 and Path(candidate).is_file() else None
    except Exception:
        LOGGER.debug("xcrun swiftc lookup failed", exc_info=True)
        return None


def _bundled_executable() -> Path | None:
    if not getattr(sys, "frozen", False):
        return None
    candidate = Path(sys.executable).resolve().parents[2] / "native" / "photo-curator-vision"
    return candidate
