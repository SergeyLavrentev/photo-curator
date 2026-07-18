from __future__ import annotations

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
    source_path: Path | None = None
    edited_path: Path | None = None
    derivative_paths: tuple[Path, ...] = field(default_factory=tuple)
    apple_scores: dict[str, float] | None = None

    @property
    def pixel_count(self) -> int:
        return int(self.width or 0) * int(self.height or 0)


class PhotosProvider(Protocol):
    def get_current_library(self) -> PhotoLibrary: ...

    def list_regular_albums(self) -> list[PhotoAlbum]: ...

    def list_shared_albums(self) -> list[PhotoAlbum]: ...

    def list_assets(self, album_id: str) -> list[PhotoAsset]: ...

    def refresh_assets(self, asset_uuids: list[str]) -> list[PhotoAsset]: ...

    def asset_still_in_album(self, album_id: str, asset_uuid: str) -> bool: ...
