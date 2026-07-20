from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from collections.abc import Callable
from pathlib import Path

from photo_curator.paths import ApplicationPaths
from photo_curator.utils.subprocesses import CommandResult, find_executable, run_command

LOGGER = logging.getLogger(__name__)
SOURCE = Path(__file__).parent / "native" / "photo_curator_publish.swift"
INFO_PLIST = Path(__file__).parent / "native" / "PhotoCuratorPublish-Info.plist"


class NativePhotosImporter:
    """Publish local image files through the public PhotoKit API without Photos UI."""

    def __init__(
        self,
        paths: ApplicationPaths,
        *,
        runner: Callable[..., CommandResult] = run_command,
        enabled: bool = True,
    ) -> None:
        self.paths = paths
        self.runner = runner
        self.enabled = enabled
        bundled = os.environ.get("PHOTO_CURATOR_PUBLISH_HELPER")
        self.bundled_executable = Path(bundled) if bundled else None
        self.swiftc = find_executable("swiftc") or _xcrun_swiftc(runner)
        self.executable = (
            self.bundled_executable
            if self.bundled_executable
            else paths.data_dir / "native" / "photo-curator-publish-helper"
        )
        self.digest_file = self.executable.with_suffix(".sha256")
        self._capability: bool | None = None

    @property
    def capability_available(self) -> bool:
        if not self.enabled:
            return False
        if self._capability is None:
            try:
                self._ensure_compiled()
                result = self.runner([str(self.executable), "--capability"], timeout=30)
                self._capability = result.returncode == 0 and "photokit-publish" in result.stdout
            except Exception:
                LOGGER.warning("Native PhotoKit publisher unavailable", exc_info=True)
                self._capability = False
        return self._capability

    def publish(self, album_name: str, files: list[Path]) -> dict[str, object]:
        return self._publish_request(
            {"album_name": album_name, "files": [str(path) for path in files]}
        )

    def publish_assets(self, album_name: str, asset_identifiers: list[str]) -> dict[str, object]:
        return self._publish_request(
            {"album_name": album_name, "asset_identifiers": asset_identifiers}
        )

    def _publish_request(self, payload: dict[str, object]) -> dict[str, object]:
        if not self.capability_available:
            raise ValueError("Нативная публикация в Photos недоступна")
        request_dir = self.paths.cache_dir / "_native_publish"
        request_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        request_path = request_dir / f"{uuid.uuid4()}.json"
        request_path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        try:
            result = self.runner([str(self.executable), str(request_path)], timeout=3600)
        finally:
            request_path.unlink(missing_ok=True)
        if result.returncode != 0:
            lines = [line.strip() for line in result.stderr.splitlines() if line.strip()]
            raise ValueError((lines[-1] if lines else "PhotoKit publish failed")[-500:])
        try:
            return json.loads(result.stdout.splitlines()[-1])
        except (IndexError, json.JSONDecodeError) as error:
            raise ValueError("PhotoKit helper вернул некорректный результат") from error

    def _ensure_compiled(self) -> None:
        if self.bundled_executable:
            if not self.bundled_executable.is_file():
                raise RuntimeError("Встроенный PhotoKit publisher отсутствует")
            return
        if not self.swiftc or not SOURCE.is_file() or not INFO_PLIST.is_file():
            raise RuntimeError("Swift/PhotoKit toolchain недоступен")
        digest = hashlib.sha256(SOURCE.read_bytes() + INFO_PLIST.read_bytes()).hexdigest()
        if (
            self.executable.is_file()
            and self.digest_file.is_file()
            and self.digest_file.read_text().strip() == digest
        ):
            return
        self.executable.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        result = self.runner(
            [
                self.swiftc,
                str(SOURCE),
                "-framework",
                "Photos",
                "-o",
                str(self.executable),
                "-Xlinker",
                "-sectcreate",
                "-Xlinker",
                "__TEXT",
                "-Xlinker",
                "__info_plist",
                "-Xlinker",
                str(INFO_PLIST),
            ],
            timeout=180,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or "Не удалось собрать PhotoKit helper")[-1000:])
        self.digest_file.write_text(digest, encoding="utf-8")


def _xcrun_swiftc(runner: Callable[..., CommandResult]) -> str | None:
    try:
        result = runner(["xcrun", "--find", "swiftc"], timeout=30)
        candidate = result.stdout.strip()
        return candidate if result.returncode == 0 and Path(candidate).is_file() else None
    except Exception:
        return None
