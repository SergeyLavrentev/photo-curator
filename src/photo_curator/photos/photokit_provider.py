from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from photo_curator.paths import ApplicationPaths
from photo_curator.photos.provider import PhotoAlbum, PhotoAsset, PhotoLibrary
from photo_curator.utils.safe_paths import ensure_within
from photo_curator.utils.subprocesses import CommandResult, run_command


class PhotoKitProvider:
    """Public PhotoKit source adapter backed by the bundled native helper."""

    def __init__(
        self,
        paths: ApplicationPaths,
        *,
        executable: str | Path,
        runner=run_command,
    ) -> None:
        self.paths = paths
        self.executable = Path(executable)
        self.runner = runner
        self._albums: dict[str, PhotoAlbum] | None = None
        self._assets_by_album: dict[str, list[PhotoAsset]] = {}
        self._membership_by_album: dict[str, set[str]] = {}

    @classmethod
    def from_environment(cls, paths: ApplicationPaths) -> PhotoKitProvider | None:
        executable = os.environ.get("PHOTO_CURATOR_PHOTOKIT_HELPER")
        if not executable or not Path(executable).is_file():
            return None
        return cls(paths, executable=executable)

    def refresh_library(self) -> None:
        self._albums = None
        self._assets_by_album.clear()
        self._membership_by_album.clear()

    def get_current_library(self) -> PhotoLibrary:
        self._ensure_capability()
        return PhotoLibrary(
            library_path="photokit://default",
            database_path=None,
            database_version="photokit-v1",
            photos_app_version=None,
            fingerprint="photokit-default-v1",
        )

    def list_regular_albums(self) -> list[PhotoAlbum]:
        self._load_albums()
        return sorted(
            (album for album in self._albums.values() if not album.is_shared),
            key=lambda album: album.name.casefold(),
        )

    def list_shared_albums(self) -> list[PhotoAlbum]:
        self._load_albums()
        return sorted(
            (album for album in self._albums.values() if album.is_shared),
            key=lambda album: album.name.casefold(),
        )

    def list_assets(self, album_id: str) -> list[PhotoAsset]:
        return self._load_assets(album_id)

    def list_shared_assets(self, album_id: str) -> list[PhotoAsset]:
        return self._load_assets(album_id)

    def refresh_assets(self, asset_uuids: list[str]) -> list[PhotoAsset]:
        if not asset_uuids:
            return []
        request_dir = self.paths.cache_dir / "photokit-requests"
        request_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        request = request_dir / f"{uuid.uuid4()}.json"
        request.write_text(json.dumps({"asset_identifiers": asset_uuids}), encoding="utf-8")
        output = self.paths.cache_dir / "photokit-renders" / "assets"
        try:
            result = self._run(["assets-by-id", str(request), str(output)], timeout=7200)
        finally:
            request.unlink(missing_ok=True)
        return self._assets_from_payload(_json_result(result), output.resolve())

    def asset_still_in_album(self, album_id: str, asset_uuid: str) -> bool:
        if album_id not in self._membership_by_album:
            result = self._run(["album-identifiers", album_id], timeout=300)
            self._membership_by_album[album_id] = set(_json_result(result))
        return asset_uuid in self._membership_by_album[album_id]

    def _ensure_capability(self) -> None:
        if not self.executable.is_file():
            raise RuntimeError("PhotoKit source helper is missing")
        result = self.runner([str(self.executable), "--capability"], timeout=30)
        if result.returncode != 0 or "photokit-source-v1" not in result.stdout:
            raise RuntimeError("PhotoKit source helper is unavailable")

    def _load_albums(self) -> None:
        if self._albums is not None:
            return
        result = self._run(["albums"], timeout=120)
        payload = _json_result(result)
        albums = [
            PhotoAlbum(
                id=str(item["id"]),
                name=str(item["name"]),
                is_shared=bool(item.get("is_shared")),
                photo_count=int(item.get("photo_count") or 0),
                video_count=int(item.get("video_count") or 0),
            )
            for group in ("regular", "shared")
            for item in payload.get(group, [])
        ]
        self._albums = {album.id: album for album in albums}

    def _load_assets(self, album_id: str) -> list[PhotoAsset]:
        if album_id in self._assets_by_album:
            return self._assets_by_album[album_id]
        self._load_albums()
        if album_id not in self._albums:
            raise KeyError(album_id)
        output = self.paths.cache_dir / "photokit-renders" / _safe_directory(album_id)
        result = self._run(["assets", album_id, str(output)], timeout=7200)
        payload = _json_result(result)
        output = output.resolve()
        assets = self._assets_from_payload(payload, output)
        self._assets_by_album[album_id] = assets
        self._membership_by_album[album_id] = {asset.uuid for asset in assets}
        return assets

    def _assets_from_payload(self, payload, output: Path) -> list[PhotoAsset]:
        return [
            PhotoAsset(
                uuid=str(item["uuid"]),
                original_filename=item.get("original_filename"),
                current_filename=item.get("current_filename"),
                taken_at=item.get("taken_at"),
                width=int(item.get("width") or 0),
                height=int(item.get("height") or 0),
                favorite=bool(item.get("favorite")),
                hidden=bool(item.get("hidden")),
                is_live_photo=bool(item.get("is_live_photo")),
                is_burst=bool(item.get("is_burst")),
                burst_key=item.get("burst_key"),
                burst_default_pick=bool(item.get("burst_default_pick")),
                is_missing=bool(item.get("is_missing")),
                is_photo=bool(item.get("is_photo")),
                source_path=(
                    ensure_within(Path(str(item["source_path"])), output)
                    if item.get("source_path")
                    else None
                ),
                provider_error=item.get("provider_error"),
            )
            for item in payload
        ]

    def _run(self, arguments: list[str], *, timeout: int) -> CommandResult:
        result = self.runner([str(self.executable), *arguments], timeout=timeout)
        if result.returncode != 0:
            message = next(
                (line.strip() for line in reversed(result.stderr.splitlines()) if line.strip()),
                "PhotoKit source helper failed",
            )
            raise RuntimeError(message[-1000:])
        return result


def _json_result(result: CommandResult):
    try:
        return json.loads(result.stdout.splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as error:
        raise RuntimeError("PhotoKit source helper returned invalid JSON") from error


def _safe_directory(identifier: str) -> str:
    import hashlib

    return hashlib.sha256(identifier.encode()).hexdigest()[:24]
