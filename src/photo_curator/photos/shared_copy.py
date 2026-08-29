from __future__ import annotations

import json
import logging
import re
import shutil
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from threading import Lock

from photo_curator.db import repository
from photo_curator.db.connection import database_connection
from photo_curator.paths import ApplicationPaths
from photo_curator.photos.local_provider import LocalAlbumsProvider
from photo_curator.photos.provider import PhotoAlbum, PhotoAsset, PhotosProvider

LOGGER = logging.getLogger(__name__)
SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".heic", ".tif", ".tiff"}


class SharedCopyCoordinator:
    """Copy Shared Album renders into a persistent service-owned disk album."""

    def __init__(
        self,
        *,
        database_path: Path,
        paths: ApplicationPaths,
        provider: PhotosProvider,
        enabled: bool = True,
    ) -> None:
        self.database_path = database_path
        self.paths = paths
        self.provider = provider
        self.enabled = enabled
        self.local_provider = provider if isinstance(provider, LocalAlbumsProvider) else None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="shared-copy")
        self._futures: dict[str, Future[None]] = {}
        self._lock = Lock()
        self._asset_cache: dict[str, list[PhotoAsset]] = {}
        self._asset_index: dict[str, dict[str, PhotoAsset]] = {}

    @property
    def capability_available(self) -> bool:
        return self.enabled and self.local_provider is not None

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
            raise ValueError("Локальные альбомы Photo Curator недоступны")
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
            raise ValueError("Альбом с таким названием уже существует")
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
                    message="Создание дисковой копии поставлено в очередь",
                    clear_error=True,
                    reset_finished=True,
                )
            self._futures[job_id] = self._executor.submit(self.run, job_id)

    def run(self, job_id: str) -> None:
        if not self.local_provider:
            raise RuntimeError("LocalAlbumsProvider недоступен")
        album_id = f"local-{job_id}"
        album_root = self.local_provider.album_root(album_id)
        assets_root = album_root / "assets"
        try:
            with database_connection(self.database_path) as connection:
                job = repository.get_shared_copy_job(connection, job_id)
                repository.update_shared_copy_job(
                    connection,
                    job_id,
                    status="running",
                    processed=0,
                    message="Копируем фотографии в Photo Curator",
                    started=True,
                )
            wanted = json.loads(str(job["asset_uuids_json"]))
            assets = {asset.uuid: asset for asset in self.assets(str(job["shared_album_id"]))}
            assets_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            local_assets: list[PhotoAsset] = []
            copied = reused = warnings = 0
            for index, asset_uuid in enumerate(wanted, start=1):
                asset = assets.get(str(asset_uuid))
                source = _shared_render(asset) if asset else None
                if not asset or not source:
                    warnings += 1
                else:
                    target = assets_root / f"{asset_uuid}{source.suffix.lower()}"
                    if target.is_symlink():
                        raise RuntimeError("Небезопасная ссылка в локальном альбоме")
                    if target.is_file() and target.stat().st_size == source.stat().st_size:
                        reused += 1
                    else:
                        shutil.copy2(source, target)
                        copied += 1
                    local_assets.append(_local_asset(asset, target))
                with database_connection(self.database_path) as connection:
                    repository.update_shared_copy_job(
                        connection,
                        job_id,
                        processed=index,
                        imported=copied,
                        reused=reused,
                        warnings=warnings,
                        message=f"Скопировано {index} из {len(wanted)}",
                    )
            if len(local_assets) != len(wanted):
                raise RuntimeError("Не все Shared Album renders доступны локально")
            self.local_provider.write_album(
                PhotoAlbum(
                    id=album_id,
                    name=str(job["destination_album_name"]),
                    folder_path="Photo Curator Local",
                    photo_count=len(local_assets),
                ),
                local_assets,
            )
            with database_connection(self.database_path) as connection:
                repository.update_shared_copy_job(
                    connection,
                    job_id,
                    status="done",
                    processed=len(local_assets),
                    imported=copied,
                    reused=reused,
                    destination_album_id=album_id,
                    warnings=warnings,
                    message="Локальный альбом Photo Curator готов",
                    finished=True,
                )
        except Exception as error:
            LOGGER.exception("Shared Album disk copy failed job=%s", job_id)
            with database_connection(self.database_path) as connection:
                repository.update_shared_copy_job(
                    connection,
                    job_id,
                    status="error",
                    errors=1,
                    message="Не удалось создать дисковую копию",
                    error_text=str(error)[:500],
                    finished=True,
                )


def _shared_render(asset: PhotoAsset | None) -> Path | None:
    if not asset or not asset.is_photo:
        return None
    candidates = [
        path
        for path in (*asset.derivative_paths, asset.edited_path, asset.source_path)
        if path and path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES
    ]
    return max(candidates, key=lambda path: path.stat().st_size, default=None)


def _local_asset(asset: PhotoAsset, path: Path) -> PhotoAsset:
    return PhotoAsset(
        uuid=asset.uuid,
        original_filename=asset.original_filename,
        current_filename=asset.current_filename,
        taken_at=asset.taken_at,
        date_added=asset.date_added,
        width=asset.width,
        height=asset.height,
        original_width=asset.original_width,
        original_height=asset.original_height,
        orientation=asset.orientation,
        favorite=asset.favorite,
        hidden=asset.hidden,
        has_adjustments=asset.has_adjustments,
        is_live_photo=asset.is_live_photo,
        is_burst=asset.is_burst,
        burst_key=asset.burst_key,
        burst_default_pick=asset.burst_default_pick,
        is_photo=True,
        media_type="image",
        media_subtypes=asset.media_subtypes,
        creation_timestamp=asset.creation_timestamp,
        modification_timestamp=asset.modification_timestamp,
        edit_state=asset.edit_state,
        source_revision=None,
        source_path=path,
        apple_scores=asset.apple_scores,
    )


def _album_name(value: str | None, source_name: str, mode: str) -> str:
    requested = re.sub(r"[/\n\r\t:]+", " — ", (value or "").strip()).strip(" —")
    if requested:
        return requested[:120]
    label = "Test" if mode != "full" else "Local Copy"
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"PhotoCurator — {source_name} — {label} — {timestamp}"[:120]
