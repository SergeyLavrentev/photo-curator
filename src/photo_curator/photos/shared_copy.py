from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from threading import Lock

from photo_curator.db import repository
from photo_curator.db.connection import database_connection
from photo_curator.paths import ApplicationPaths
from photo_curator.photos.provider import PhotoAlbum, PhotoAsset, PhotosProvider
from photo_curator.utils.safe_paths import safe_rmtree
from photo_curator.utils.subprocesses import CommandResult, find_executable, run_command

LOGGER = logging.getLogger(__name__)
SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".heic", ".tif", ".tiff"}
IMPORT_BATCH_SIZE = 25
NATIVE_SOURCE = Path(__file__).parent / "native" / "photo_curator_photos.swift"
NATIVE_INFO_PLIST = Path(__file__).parent / "native" / "PhotoCuratorPhotos-Info.plist"


class PhotoKitBridge:
    """Compile and invoke a small supported PhotoKit helper without Photos.app UI."""

    def __init__(
        self,
        paths: ApplicationPaths,
        *,
        runner: Callable[..., CommandResult] = run_command,
    ) -> None:
        self.paths = paths
        self.runner = runner
        self.swiftc = find_executable("swiftc") or _xcrun_swiftc(runner)
        self.executable = paths.data_dir / "native" / "photo-curator-photos-helper"
        self.digest_file = self.executable.with_suffix(".sha256")
        self._capability: bool | None = None

    @property
    def capability_available(self) -> bool:
        if self._capability is None:
            try:
                self._ensure_compiled()
                probe = self.runner([str(self.executable), "--capability"], timeout=30)
                self._capability = probe.returncode == 0 and "photokit" in probe.stdout
            except Exception:
                LOGGER.warning("Native PhotoKit helper unavailable", exc_info=True)
                self._capability = False
        return self._capability

    def add_files(
        self,
        *,
        job_id: str,
        album_name: str,
        files: list[Path],
        album_identifier: str | None,
    ) -> dict[str, object]:
        self._ensure_compiled()
        request_path = self.paths.cache_dir / "_shared_copies" / job_id / "request.json"
        request_path.write_text(
            json.dumps(
                {
                    "album_name": album_name,
                    "album_identifier": album_identifier,
                    "files": [str(path) for path in files],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        result = self.runner([str(self.executable), str(request_path)], timeout=900)
        if result.returncode != 0:
            detail = next(
                (line.strip() for line in reversed(result.stderr.splitlines()) if line.strip()),
                "PhotoKit helper завершился с ошибкой",
            )
            raise RuntimeError(detail[-500:])
        try:
            return json.loads(result.stdout.splitlines()[-1])
        except (IndexError, json.JSONDecodeError) as error:
            raise RuntimeError("PhotoKit helper вернул некорректный результат") from error

    def _ensure_compiled(self) -> None:
        if not self.swiftc or not NATIVE_SOURCE.is_file() or not NATIVE_INFO_PLIST.is_file():
            raise RuntimeError("Swift/PhotoKit toolchain недоступен")
        digest = hashlib.sha256(
            NATIVE_SOURCE.read_bytes() + NATIVE_INFO_PLIST.read_bytes()
        ).hexdigest()
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
                str(NATIVE_SOURCE),
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
                str(NATIVE_INFO_PLIST),
            ],
            timeout=180,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or "Не удалось собрать PhotoKit helper")[-1000:])
        self.digest_file.write_text(digest, encoding="utf-8")


class SharedCopyCoordinator:
    """Create a regular Photos album through the public native PhotoKit API."""

    def __init__(
        self,
        *,
        database_path: Path,
        paths: ApplicationPaths,
        provider: PhotosProvider,
        bridge: PhotoKitBridge | None = None,
        enabled: bool = True,
    ) -> None:
        self.database_path = database_path
        self.paths = paths
        self.provider = provider
        self.bridge = bridge or PhotoKitBridge(paths)
        self.enabled = enabled
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="shared-copy")
        self._futures: dict[str, Future[None]] = {}
        self._lock = Lock()
        self._asset_cache: dict[str, list[PhotoAsset]] = {}
        self._asset_index: dict[str, dict[str, PhotoAsset]] = {}

    @property
    def capability_available(self) -> bool:
        return self.enabled and self.bridge.capability_available

    def shared_album(self, album_id: str) -> PhotoAlbum:
        albums = {album.id: album for album in self.provider.list_shared_albums()}
        try:
            return albums[album_id]
        except KeyError as error:
            raise ValueError("Shared Album не найден") from error

    def assets(self, album_id: str) -> list[PhotoAsset]:
        self.shared_album(album_id)
        with self._lock:
            cached = self._asset_cache.get(album_id)
        if cached is not None:
            return list(cached)
        assets = self.provider.list_shared_assets(album_id)
        with self._lock:
            self._asset_cache[album_id] = list(assets)
            self._asset_index[album_id] = {asset.uuid: asset for asset in assets}
        return list(assets)

    def asset(self, album_id: str, asset_uuid: str) -> PhotoAsset:
        self.assets(album_id)
        with self._lock:
            asset = self._asset_index.get(album_id, {}).get(asset_uuid)
        if not asset:
            raise KeyError(asset_uuid)
        return asset

    def prepare(
        self,
        album_id: str,
        *,
        mode: str,
        destination_album_name: str | None = None,
        sample_size: int | None = None,
        asset_uuids: list[str] | None = None,
    ) -> dict[str, object]:
        if not self.capability_available:
            raise ValueError("Нативный PhotoKit helper недоступен")
        if mode not in {"full", "sample", "custom"}:
            raise ValueError("Неизвестный режим локальной копии")
        album = self.shared_album(album_id)
        all_assets = self.assets(album_id)
        photos = [asset for asset in all_assets if asset.is_photo]
        by_uuid = {asset.uuid: asset for asset in photos}
        if mode == "full":
            selected = photos
        elif mode == "sample":
            if sample_size is None or not 1 <= sample_size <= min(500, len(photos)):
                raise ValueError("Размер выборки должен быть от 1 до 500 фотографий")
            selected = photos[:sample_size]
        else:
            requested = list(dict.fromkeys(asset_uuids or []))
            if not requested or len(requested) > 500:
                raise ValueError("Выберите от 1 до 500 фотографий")
            missing = [uuid for uuid in requested if uuid not in by_uuid]
            if missing:
                raise ValueError("Часть выбранных фотографий отсутствует в Shared Album")
            selected = [by_uuid[uuid] for uuid in requested]
        unavailable = [asset.uuid for asset in selected if _shared_render(asset) is None]
        if unavailable:
            raise ValueError(
                f"Для {len(unavailable)} фотографий нет локального render; "
                "откройте Shared Album в Photos один раз для синхронизации"
            )
        destination = _album_name(destination_album_name, album.name, mode)
        if any(item.name == destination for item in self.provider.list_regular_albums()):
            raise ValueError("Обычный альбом с таким названием уже существует")
        with database_connection(self.database_path) as connection:
            job_id = repository.create_shared_copy_job(
                connection,
                shared_album_id=album.id,
                shared_album_name=album.name,
                destination_album_name=destination,
                mode=mode,
                asset_uuids=[asset.uuid for asset in selected],
                skipped_videos=sum(not asset.is_photo for asset in all_assets),
            )
            return repository.get_shared_copy_job(connection, job_id)

    def start(self, job_id: str) -> None:
        with self._lock:
            current = self._futures.get(job_id)
            if current and not current.done():
                raise RuntimeError("Локальная копия уже создаётся")
            with database_connection(self.database_path) as connection:
                job = repository.get_shared_copy_job(connection, job_id)
            if job["status"] not in {"planned", "interrupted", "error"}:
                raise ValueError("Эту локальную копию нельзя запустить повторно")
            with database_connection(self.database_path) as connection:
                repository.update_shared_copy_job(
                    connection,
                    job_id,
                    status="queued",
                    processed=0,
                    imported=0,
                    reused=0,
                    warnings=0,
                    errors=0,
                    message="Создание локальной копии поставлено в очередь",
                    clear_error=True,
                    reset_finished=True,
                )
            self._futures[job_id] = self._executor.submit(self.run, job_id)

    def run(self, job_id: str) -> None:
        staging = self.paths.cache_dir / "_shared_copies" / job_id
        try:
            with database_connection(self.database_path) as connection:
                job = repository.get_shared_copy_job(connection, job_id)
                repository.update_shared_copy_job(
                    connection,
                    job_id,
                    status="running",
                    processed=0,
                    message="Подготавливаем локальные файлы",
                    started=True,
                )
            wanted = json.loads(str(job["asset_uuids_json"]))
            assets = {asset.uuid: asset for asset in self.assets(str(job["shared_album_id"]))}
            staging.mkdir(parents=True, exist_ok=True, mode=0o700)
            prepared: list[Path] = []
            warnings = 0
            for index, asset_uuid in enumerate(wanted, start=1):
                asset = assets.get(str(asset_uuid))
                source = _shared_render(asset) if asset else None
                if not source:
                    warnings += 1
                else:
                    target = staging / f"{asset_uuid}{source.suffix.lower()}"
                    if not target.is_file() or target.stat().st_size != source.stat().st_size:
                        shutil.copy2(source, target)
                    prepared.append(target)
                with database_connection(self.database_path) as connection:
                    repository.update_shared_copy_job(
                        connection,
                        job_id,
                        processed=index,
                        warnings=warnings,
                        message=f"Подготовлено {index} из {len(wanted)}",
                    )
            if len(prepared) != len(wanted):
                raise RuntimeError("Не все Shared Album renders доступны локально")
            imported = reused = 0
            album_identifier = str(job.get("destination_album_id") or "") or None
            for offset in range(0, len(prepared), IMPORT_BATCH_SIZE):
                batch = prepared[offset : offset + IMPORT_BATCH_SIZE]
                result = self.bridge.add_files(
                    job_id=job_id,
                    album_name=str(job["destination_album_name"]),
                    files=batch,
                    album_identifier=album_identifier,
                )
                album_identifier = str(result["album_identifier"])
                imported += int(result.get("imported", 0))
                reused += int(result.get("reused", 0))
                with database_connection(self.database_path) as connection:
                    repository.update_shared_copy_job(
                        connection,
                        job_id,
                        destination_album_id=album_identifier,
                        imported=imported,
                        reused=reused,
                        message=f"Добавлено в Photos {min(offset + len(batch), len(prepared))} "
                        f"из {len(prepared)}",
                    )
            try:
                self.provider.refresh_library()
                matches = [
                    album
                    for album in self.provider.list_regular_albums()
                    if album.name == str(job["destination_album_name"])
                ]
                if len(matches) == 1:
                    album_identifier = matches[0].id
                else:
                    warnings += 1
            except Exception:
                warnings += 1
            with database_connection(self.database_path) as connection:
                repository.update_shared_copy_job(
                    connection,
                    job_id,
                    status="done",
                    processed=len(prepared),
                    imported=imported,
                    reused=reused,
                    destination_album_id=album_identifier,
                    warnings=warnings,
                    message="Локальная копия готова",
                    finished=True,
                )
            safe_rmtree(staging, self.paths.cache_dir)
        except Exception as error:
            LOGGER.exception("Shared Album copy failed job=%s", job_id)
            with database_connection(self.database_path) as connection:
                repository.update_shared_copy_job(
                    connection,
                    job_id,
                    status="error",
                    errors=1,
                    message="Не удалось создать локальную копию",
                    error_text=str(error)[:500],
                    finished=True,
                )


def _xcrun_swiftc(runner: Callable[..., CommandResult]) -> str | None:
    try:
        result = runner(["xcrun", "--find", "swiftc"], timeout=30)
        candidate = result.stdout.strip()
        return candidate if result.returncode == 0 and Path(candidate).is_file() else None
    except Exception:
        return None


def _shared_render(asset: PhotoAsset | None) -> Path | None:
    if not asset or not asset.is_photo:
        return None
    candidates = [
        path
        for path in (*asset.derivative_paths, asset.edited_path, asset.source_path)
        if path and path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES
    ]
    return max(candidates, key=lambda path: path.stat().st_size, default=None)


def _album_name(value: str | None, source_name: str, mode: str) -> str:
    requested = re.sub(r"[/\n\r\t:]+", " — ", (value or "").strip()).strip(" —")
    if requested:
        return requested[:120]
    label = "R7 Test" if mode != "full" else "Local Copy"
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"PhotoCurator — {source_name} — {label} — {timestamp}"[:120]
