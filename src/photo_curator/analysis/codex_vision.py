from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

CODEX_ENGINE_VERSION = "codex-vision-v1"
DEFAULT_BULK_MODEL = "gpt-5.6-luna"
DEFAULT_COMPARE_MODEL = "gpt-5.6-terra"
MAX_IMAGES_PER_REQUEST = 12


class CodexVisionError(RuntimeError):
    pass


class CodexVisionCancelled(CodexVisionError):
    pass


@dataclass(frozen=True, slots=True)
class CodexStatus:
    state: str
    executable: str | None
    version: str | None = None
    auth_kind: str | None = None
    detail: str | None = None

    @property
    def ready(self) -> bool:
        return self.state == "ready" and self.auth_kind == "chatgpt"

    def payload(self) -> dict[str, object]:
        return {
            "state": self.state,
            "ready": self.ready,
            "executable": self.executable,
            "version": self.version,
            "auth_kind": self.auth_kind,
            "detail": self.detail,
        }


def discover_codex() -> Path | None:
    configured = os.environ.get("PHOTO_CURATOR_CODEX_BINARY")
    candidates = [
        Path(configured).expanduser() if configured else None,
        Path(shutil.which("codex")) if shutil.which("codex") else None,
        Path("/Applications/ChatGPT.app/Contents/Resources/codex"),
        Path("/Applications/Codex.app/Contents/Resources/codex"),
        Path.home() / "Applications/ChatGPT.app/Contents/Resources/codex",
        Path.home() / "Applications/Codex.app/Contents/Resources/codex",
        Path("/opt/homebrew/bin/codex"),
        Path("/usr/local/bin/codex"),
    ]
    for candidate in candidates:
        if candidate and candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    return None


def codex_status(*, timeout: float = 10.0) -> CodexStatus:
    executable = discover_codex()
    if executable is None:
        return CodexStatus(
            "missing",
            None,
            detail="Установите приложение ChatGPT или Codex CLI.",
        )
    try:
        version_result = subprocess.run(
            [str(executable), "--version"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        auth_result = subprocess.run(
            [str(executable), "login", "status"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return CodexStatus("error", str(executable), detail=str(error))
    version = (version_result.stdout or version_result.stderr).strip() or None
    auth_text = f"{auth_result.stdout}\n{auth_result.stderr}".strip()
    normalized = auth_text.lower()
    if auth_result.returncode == 0 and "chatgpt" in normalized:
        return CodexStatus("ready", str(executable), version, "chatgpt", auth_text)
    if auth_result.returncode == 0 and "api" in normalized:
        return CodexStatus(
            "api_key",
            str(executable),
            version,
            "api_key",
            "Codex вошёл через API key. Этот режим требует входа через ChatGPT.",
        )
    return CodexStatus(
        "signed_out",
        str(executable),
        version,
        None,
        auth_text or "Войдите в ChatGPT/Codex и повторите проверку.",
    )


def build_batches(
    assets: list[dict[str, object]],
    groups: list[dict[str, object]],
    *,
    limit: int = MAX_IMAGES_PER_REQUEST,
) -> list[list[dict[str, object]]]:
    if limit < 2:
        raise ValueError("Codex batch limit must be at least 2")
    by_uuid = {str(asset["asset_uuid"]): asset for asset in assets}
    grouped: set[str] = set()
    units: list[list[dict[str, object]]] = []
    for group in groups:
        members = []
        for member in group.get("members", []):
            asset_uuid = str(member.get("asset_uuid") or "")
            asset = by_uuid.get(asset_uuid)
            if asset is None or asset_uuid in grouped:
                continue
            copy = dict(asset)
            copy["codex_group_id"] = str(group.get("group_id") or "")
            copy["codex_group_kind"] = str(group.get("kind") or "near")
            members.append(copy)
            grouped.add(asset_uuid)
        if members:
            for offset in range(0, len(members), limit):
                units.append(members[offset : offset + limit])
    singles = [dict(asset) for asset in assets if str(asset["asset_uuid"]) not in grouped]
    units.extend([singles[offset : offset + limit] for offset in range(0, len(singles), limit)])

    batches: list[list[dict[str, object]]] = []
    current: list[dict[str, object]] = []
    for unit in units:
        if current and len(current) + len(unit) > limit:
            batches.append(current)
            current = []
        current.extend(unit)
        if len(current) == limit:
            batches.append(current)
            current = []
    if current:
        batches.append(current)
    return batches


class CodexVisionRunner:
    def __init__(self, executable: Path | None = None) -> None:
        self.executable = executable or discover_codex()

    def analyze(
        self,
        batch: list[dict[str, object]],
        *,
        work_dir: Path,
        model: str,
        cancelled: Callable[[], bool] | None = None,
        timeout: float = 20 * 60,
    ) -> dict[str, dict[str, object]]:
        if self.executable is None:
            raise CodexVisionError("Codex не найден")
        images = [Path(str(asset["review_path"])) for asset in batch]
        if any(not image.is_file() for image in images):
            raise CodexVisionError("Одна из review-копий для Codex недоступна")
        work_dir.mkdir(parents=True, exist_ok=True)
        schema_path = work_dir / "result-schema.json"
        output_path = work_dir / "result.json"
        stderr_path = work_dir / "codex.stderr.log"
        schema_path.write_text(json.dumps(_OUTPUT_SCHEMA, ensure_ascii=False), encoding="utf-8")
        prompt = _prompt(batch)
        command = [
            str(self.executable),
            "exec",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--model",
            model,
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_path),
            "--color",
            "never",
            prompt,
            "--image",
            *[str(image) for image in images],
        ]
        with stderr_path.open("w", encoding="utf-8") as stderr_stream:
            process = subprocess.Popen(
                command,
                cwd=work_dir,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=stderr_stream,
                text=True,
            )
            started = time.monotonic()
            while process.poll() is None:
                if cancelled and cancelled():
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                    raise CodexVisionCancelled("Анализ Codex остановлен")
                if time.monotonic() - started > timeout:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                    raise CodexVisionError("Codex не завершил пакет за 20 минут")
                time.sleep(0.25)
        stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
        if process.returncode != 0:
            raise CodexVisionError((stderr.strip() or "Codex завершился с ошибкой")[-2000:])
        try:
            value = json.loads(output_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise CodexVisionError("Codex вернул некорректный JSON") from error
        expected = {str(asset["asset_uuid"]) for asset in batch}
        rows = value.get("photos") if isinstance(value, dict) else None
        if not isinstance(rows, list):
            raise CodexVisionError("В ответе Codex отсутствует массив photos")
        result = {
            str(row.get("asset_id")): row
            for row in rows
            if isinstance(row, dict) and str(row.get("asset_id")) in expected
        }
        if set(result) != expected:
            raise CodexVisionError("Codex вернул результаты не для всех фотографий пакета")
        return result


def _prompt(batch: list[dict[str, object]]) -> str:
    mapping = "\n".join(
        f"{index}. asset_id={asset['asset_uuid']}; "
        f"series={asset.get('codex_group_id') or 'none'}; "
        f"series_kind={asset.get('codex_group_kind') or 'none'}"
        for index, asset in enumerate(batch, start=1)
    )
    return f"""
Ты — строгий, но консервативный фоторедактор. Проанализируй приложенные изображения
в указанном порядке. Оцени композицию, интересность сюжета, момент, ясность лица,
смаз, экспозицию, сильно заваленный горизонт и действительно плохой ракурс.

Фотографии с одинаковым series сравнивай непосредственно друг с другом. Для каждой
серии назначь уникальные series_rank от 1 (лучший кадр) до N. Для одиночного кадра
series_rank должен быть 0. Не называй фотографию плохой только потому, что она обычная
или слабее лучших кадров альбома. reject_recommended=true разрешён только для явного
технического брака либо уверенного проигравшего почти одинаковой серии. Плохой личный
вкус или скучный сюжет понизит оценки, но сам по себе не является достаточным reject.

Верни только данные по заданной JSON Schema. asset_id скопируй без изменений.

Соответствие изображений:
{mapping}
""".strip()


_SCORE = {"type": "integer", "minimum": 0, "maximum": 100}
_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "photos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "asset_id": {"type": "string"},
                    "aesthetic_score": _SCORE,
                    "composition_score": _SCORE,
                    "interestingness_score": _SCORE,
                    "moment_score": _SCORE,
                    "face_visibility": {
                        "type": "string",
                        "enum": ["clear", "partial", "unrecognizable", "no_face"],
                    },
                    "defects": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [
                                "blur",
                                "underexposed",
                                "overexposed",
                                "extreme_horizon",
                                "bad_angle",
                                "blocked_subject",
                            ],
                        },
                    },
                    "series_rank": {"type": "integer", "minimum": 0, "maximum": 20},
                    "reject_recommended": {"type": "boolean"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "reason": {"type": "string", "maxLength": 240},
                },
                "required": [
                    "asset_id",
                    "aesthetic_score",
                    "composition_score",
                    "interestingness_score",
                    "moment_score",
                    "face_visibility",
                    "defects",
                    "series_rank",
                    "reject_recommended",
                    "confidence",
                    "reason",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["photos"],
    "additionalProperties": False,
}
