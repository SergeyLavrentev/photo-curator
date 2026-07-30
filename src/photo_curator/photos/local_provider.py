from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from photo_curator.photos.provider import PhotoAlbum, PhotoAsset, PhotoLibrary, PhotosProvider
from photo_curator.utils.safe_paths import ensure_within


class LocalAlbumsProvider:
    """Expose service-owned disk albums alongside regular Photos albums."""

    PREFIX = "local-"

    def __init__(self, base: PhotosProvider, root: Path) -> None:
        self.base = base
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def album_root(self, album_id: str) -> Path:
        if not album_id.startswith(self.PREFIX):
            raise ValueError("Invalid local album id")
        return ensure_within(self.root / album_id, self.root)

    def refresh_library(self) -> None:
        self.base.refresh_library()

    def get_current_library(self) -> PhotoLibrary:
        return self.base.get_current_library()

    def list_regular_albums(self) -> list[PhotoAlbum]:
        return sorted(
            [*self.base.list_regular_albums(), *self._local_albums()],
            key=lambda album: album.full_path.casefold(),
        )

    def list_shared_albums(self) -> list[PhotoAlbum]:
        return self.base.list_shared_albums()

    def list_assets(self, album_id: str) -> list[PhotoAsset]:
        if album_id.startswith(self.PREFIX):
            return self._read_album(album_id)[1]
        return self.base.list_assets(album_id)

    def list_assets_with_progress(
        self, album_id: str, progress: Callable[[int, int], None]
    ) -> list[PhotoAsset]:
        if album_id.startswith(self.PREFIX):
            assets = self._read_album(album_id)[1]
            progress(len(assets), len(assets))
            return assets
        streaming = getattr(self.base, "list_assets_with_progress", None)
        if streaming:
            return streaming(album_id, progress)
        assets = self.base.list_assets(album_id)
        progress(len(assets), len(assets))
        return assets

    def list_shared_assets(self, album_id: str) -> list[PhotoAsset]:
        return self.base.list_shared_assets(album_id)

    def sample_assets(
        self,
        album_id: str,
        *,
        limit: int,
        excluded_uuids: set[str],
    ) -> list[PhotoAsset]:
        if not album_id.startswith(self.PREFIX):
            sampler = getattr(self.base, "sample_assets", None)
            if sampler:
                return sampler(
                    album_id,
                    limit=limit,
                    excluded_uuids=excluded_uuids,
                )
        assets = [asset for asset in self.list_assets(album_id) if asset.uuid not in excluded_uuids]
        assets.sort(key=lambda asset: (asset.taken_at or "", asset.uuid), reverse=True)
        return assets[:limit]

    def refresh_assets(self, asset_uuids: list[str]) -> list[PhotoAsset]:
        wanted = set(asset_uuids)
        local_assets: dict[str, PhotoAsset] = {}
        for album in self._local_albums():
            for asset in self.list_assets(album.id):
                if asset.uuid in wanted:
                    local_assets.setdefault(asset.uuid, asset)
        remaining = sorted(wanted - local_assets.keys())
        return [*local_assets.values(), *self.base.refresh_assets(remaining)]

    def asset_still_in_album(self, album_id: str, asset_uuid: str) -> bool:
        if album_id.startswith(self.PREFIX):
            return any(asset.uuid == asset_uuid for asset in self.list_assets(album_id))
        return self.base.asset_still_in_album(album_id, asset_uuid)

    def write_album(self, album: PhotoAlbum, assets: list[PhotoAsset]) -> None:
        root = self.album_root(album.id)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = {"album": asdict(album), "assets": [_asset_json(asset) for asset in assets]}
        temporary = root / "manifest.json.tmp"
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(root / "manifest.json")

    def _local_albums(self) -> list[PhotoAlbum]:
        albums = []
        for manifest in sorted(self.root.glob(f"{self.PREFIX}*/manifest.json")):
            try:
                album, _ = self._read_manifest(manifest)
                albums.append(album)
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                continue
        return albums

    def _read_album(self, album_id: str) -> tuple[PhotoAlbum, list[PhotoAsset]]:
        manifest = ensure_within(self.album_root(album_id) / "manifest.json", self.root)
        if not manifest.is_file():
            raise KeyError(album_id)
        return self._read_manifest(manifest)

    def _read_manifest(self, manifest: Path) -> tuple[PhotoAlbum, list[PhotoAsset]]:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        album = PhotoAlbum(**payload["album"])
        assets = [_asset_from_json(item, self.root) for item in payload["assets"]]
        return album, assets


def _asset_json(asset: PhotoAsset) -> dict[str, object]:
    payload = asdict(asset)
    for key in ("source_path", "edited_path"):
        payload[key] = str(payload[key]) if payload[key] else None
    payload["derivative_paths"] = [str(path) for path in asset.derivative_paths]
    return payload


def _asset_from_json(payload: dict[str, object], root: Path) -> PhotoAsset:
    values = dict(payload)
    for key in ("source_path", "edited_path"):
        if values.get(key):
            values[key] = ensure_within(Path(str(values[key])), root)
    values["derivative_paths"] = tuple(
        ensure_within(Path(str(path)), root) for path in values.get("derivative_paths", [])
    )
    return PhotoAsset(**values)
