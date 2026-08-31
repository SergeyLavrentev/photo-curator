from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class PhotoLibrary:
    library_path: str
    database_path: str | None
    database_version: str | None
    photos_app_version: str | None
    fingerprint: str


@dataclass(frozen=True, slots=True)
class PhotoAlbum:
    id: str
    name: str
    folder_path: str = ""
    is_shared: bool = False
    photo_count: int = 0
    video_count: int = 0

    @property
    def full_path(self) -> str:
        return f"{self.folder_path}/{self.name}" if self.folder_path else self.name


@dataclass(frozen=True, slots=True)
class PhotoAsset:
    uuid: str
    original_filename: str | None = None
    current_filename: str | None = None
    taken_at: str | None = None
    date_added: str | None = None
    width: int | None = None
    height: int | None = None
    original_width: int | None = None
    original_height: int | None = None
    orientation: int | None = None
    favorite: bool = False
    hidden: bool = False
    has_adjustments: bool = False
    is_live_photo: bool = False
    is_burst: bool = False
    burst_key: str | None = None
    burst_default_pick: bool = False
    is_missing: bool = False
    is_photo: bool = True
    media_type: str = "image"
    media_subtypes: int = 0
    creation_timestamp: float | None = None
    modification_timestamp: float | None = None
    latitude: float | None = None
    longitude: float | None = None
    edit_state: str = "original"
    source_revision: str | None = None
    source_path: Path | None = None
    edited_path: Path | None = None
    derivative_paths: tuple[Path, ...] = field(default_factory=tuple)
    apple_scores: dict[str, float] | None = None
    provider_error: str | None = None
    review_render: bool = False

    @property
    def pixel_count(self) -> int:
        return int(self.width or 0) * int(self.height or 0)


def normalized_media_type(asset: PhotoAsset) -> str:
    value = str(asset.media_type or "").casefold()
    if not asset.is_photo and value == "image":
        return "video"
    if value in {"image", "video", "audio"}:
        return value
    return "image" if asset.is_photo else "video"


def is_supported_photo(asset: PhotoAsset) -> bool:
    return asset.is_photo and normalized_media_type(asset) == "image"


def photo_asset_revision(asset: PhotoAsset) -> str:
    if asset.source_revision:
        return str(asset.source_revision)
    source_stat: tuple[int, int] | None = None
    if asset.source_path:
        try:
            stat = asset.source_path.stat()
            source_stat = (stat.st_size, stat.st_mtime_ns)
        except OSError:
            pass
    payload = {
        "uuid": asset.uuid,
        "creation_timestamp": asset.creation_timestamp,
        "modification_timestamp": asset.modification_timestamp,
        "latitude": asset.latitude,
        "longitude": asset.longitude,
        "taken_at": asset.taken_at,
        "media_type": normalized_media_type(asset),
        "media_subtypes": int(asset.media_subtypes),
        "edit_state": asset.edit_state,
        "has_adjustments": asset.has_adjustments,
        "dimensions": [asset.width, asset.height],
        "orientation": asset.orientation,
        "source_stat": source_stat,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


class PhotosProvider(Protocol):
    def refresh_library(self) -> None: ...

    def get_current_library(self) -> PhotoLibrary: ...

    def list_regular_albums(self) -> list[PhotoAlbum]: ...

    def list_shared_albums(self) -> list[PhotoAlbum]: ...

    def list_assets(self, album_id: str) -> list[PhotoAsset]: ...

    def list_shared_assets(self, album_id: str) -> list[PhotoAsset]: ...

    def refresh_assets(self, asset_uuids: list[str]) -> list[PhotoAsset]: ...

    def asset_still_in_album(self, album_id: str, asset_uuid: str) -> bool: ...
