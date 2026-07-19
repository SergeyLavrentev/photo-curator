from __future__ import annotations

import logging
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from photo_curator.photos.library_resolver import library_fingerprint
from photo_curator.photos.provider import PhotoAlbum, PhotoAsset, PhotoLibrary

LOGGER = logging.getLogger(__name__)


class OSXPhotosProvider:
    """Read-only adapter around the public osxphotos Python API."""

    def __init__(self, library_path: str | None = None) -> None:
        self._library_path = library_path
        self.__db = None

    def refresh_library(self) -> None:
        """Reopen the read-only Photos database after an external osxphotos write."""
        self.__db = None

    @property
    def _db(self):
        if self.__db is None:
            import osxphotos

            self.__db = osxphotos.PhotosDB(library_path=self._library_path)
        return self.__db

    def get_current_library(self) -> PhotoLibrary:
        library_path = str(self._db.library_path)
        database_path = str(self._db.db_path) if self._db.db_path else None
        version = str(self._db.db_version) if self._db.db_version is not None else None
        return PhotoLibrary(
            library_path=library_path,
            database_path=database_path,
            database_version=version,
            photos_app_version=None,
            fingerprint=library_fingerprint(library_path, database_path, version),
        )

    def list_regular_albums(self) -> list[PhotoAlbum]:
        albums = [self._album_from_info(info, shared=False) for info in self._db.album_info]
        return sorted(albums, key=lambda item: item.full_path.casefold())

    def list_shared_albums(self) -> list[PhotoAlbum]:
        albums = [self._album_from_info(info, shared=True) for info in self._db.album_info_shared]
        return sorted(albums, key=lambda item: item.full_path.casefold())

    def list_assets(self, album_id: str) -> list[PhotoAsset]:
        return self._assets_from_album(self._album_info(album_id))

    def list_shared_assets(self, album_id: str) -> list[PhotoAsset]:
        return self._assets_from_album(self._shared_album_info(album_id))

    def _assets_from_album(self, album: Any) -> list[PhotoAsset]:
        assets = []
        for photo in album.photos:
            try:
                assets.append(self._asset_from_info(photo))
            except Exception as error:
                uuid = str(getattr(photo, "uuid", "unknown"))
                LOGGER.warning(
                    "Asset metadata unavailable for UUID %s (%s)", uuid, type(error).__name__
                )
                assets.append(
                    PhotoAsset(
                        uuid=uuid,
                        current_filename=getattr(photo, "filename", None),
                        is_missing=True,
                        provider_error=type(error).__name__,
                    )
                )
        return assets

    def refresh_assets(self, asset_uuids: list[str]) -> list[PhotoAsset]:
        assets = []
        for photo in self._db.photos_by_uuid(asset_uuids):
            try:
                assets.append(self._asset_from_info(photo))
            except Exception:
                LOGGER.warning(
                    "Publish refresh failed for UUID %s", str(getattr(photo, "uuid", "unknown"))
                )
        return assets

    def asset_still_in_album(self, album_id: str, asset_uuid: str) -> bool:
        return any(photo.uuid == asset_uuid for photo in self._album_info(album_id).photos)

    def _album_info(self, album_id: str) -> Any:
        for album in self._db.album_info:
            if album.uuid == album_id:
                return album
        raise KeyError(f"Regular album not found: {album_id}")

    def _shared_album_info(self, album_id: str) -> Any:
        for album in self._db.album_info_shared:
            if album.uuid == album_id:
                return album
        raise KeyError(f"Shared album not found: {album_id}")

    @staticmethod
    def _album_from_info(info: Any, *, shared: bool) -> PhotoAlbum:
        photos = list(info.photos)
        return PhotoAlbum(
            id=str(info.uuid),
            name=str(info.title or "Без названия"),
            folder_path="/".join(getattr(info, "folder_names", None) or []),
            is_shared=shared,
            photo_count=sum(bool(photo.isphoto) for photo in photos),
            video_count=sum(bool(photo.ismovie) for photo in photos),
        )

    @staticmethod
    def _asset_from_info(photo: Any) -> PhotoAsset:
        derivatives = tuple(
            path
            for value in (getattr(photo, "path_derivatives", None) or [])
            if (path := _path_or_none(value))
        )
        score = getattr(photo, "score", None)
        scores = _score_dict(score)
        return PhotoAsset(
            uuid=str(photo.uuid),
            original_filename=photo.original_filename,
            current_filename=photo.filename,
            taken_at=_iso(getattr(photo, "date", None)),
            date_added=_iso(getattr(photo, "date_added", None)),
            width=getattr(photo, "width", None),
            height=getattr(photo, "height", None),
            original_width=getattr(photo, "original_width", None),
            original_height=getattr(photo, "original_height", None),
            orientation=getattr(photo, "orientation", None),
            favorite=bool(getattr(photo, "favorite", False)),
            hidden=bool(getattr(photo, "hidden", False)),
            has_adjustments=bool(getattr(photo, "hasadjustments", False)),
            is_live_photo=bool(getattr(photo, "live_photo", False)),
            is_burst=bool(getattr(photo, "burst", False)),
            burst_key=_optional_text(getattr(photo, "burst_key", None)),
            burst_default_pick=bool(getattr(photo, "burst_default_pick", False)),
            is_missing=bool(getattr(photo, "ismissing", False)),
            is_photo=bool(getattr(photo, "isphoto", True)),
            source_path=_path_or_none(getattr(photo, "path", None)),
            edited_path=_path_or_none(getattr(photo, "path_edited", None)),
            derivative_paths=derivatives,
            apple_scores=scores,
        )


def _path_or_none(value: Any) -> Path | None:
    return Path(value) if value else None


def _optional_text(value: Any) -> str | None:
    """Normalize osxphotos' numeric/empty sentinels to missing metadata."""
    if value in (None, False, 0, "", "0"):
        return None
    return str(value)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _score_dict(score: Any) -> dict[str, float] | None:
    if score is None:
        return None
    values = asdict(score) if is_dataclass(score) else score if isinstance(score, dict) else {}
    numeric = {
        str(key): float(value) for key, value in values.items() if isinstance(value, (int, float))
    }
    return numeric or None
