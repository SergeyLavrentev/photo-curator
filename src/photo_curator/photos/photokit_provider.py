from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable
from pathlib import Path

from photo_curator.paths import ApplicationPaths
from photo_curator.photos.provider import PhotoAlbum, PhotoAsset, PhotoLibrary
from photo_curator.utils.safe_paths import ensure_within
from photo_curator.utils.subprocesses import CommandResult, run_command, run_streaming_command

ProgressCallback = Callable[[int, int], None]


class PhotoKitProvider:
    """Public PhotoKit source adapter backed by the bundled native helper."""

    def __init__(
        self,
        paths: ApplicationPaths,
        *,
        executable: str | Path,
        runner=run_command,
        streaming_runner=None,
    ) -> None:
        self.paths = paths
        self.executable = Path(executable)
        self.runner = runner
        self.streaming_runner = streaming_runner
        self._albums: dict[str, PhotoAlbum] | None = None
        self._assets_by_album: dict[str, list[PhotoAsset]] = {}
        self._membership_by_album: dict[str, set[str]] = {}

    @classmethod
    def from_environment(cls, paths: ApplicationPaths) -> PhotoKitProvider | None:
        executable = os.environ.get("PHOTO_CURATOR_PHOTOKIT_HELPER")
        if not executable or not Path(executable).is_file():
            return None
        return cls(paths, executable=executable, streaming_runner=run_streaming_command)

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

    def list_assets_with_progress(
        self, album_id: str, progress: ProgressCallback
    ) -> list[PhotoAsset]:
        return self._load_assets(album_id, progress=progress)

    def list_asset_metadata_with_progress(
        self, album_id: str, progress: ProgressCallback
    ) -> list[PhotoAsset]:
        self._load_albums()
        if album_id not in self._albums:
            raise KeyError(album_id)
        # Inventory is the project snapshot boundary. Always ask PhotoKit for
        # current metadata and force the following preview stage to revalidate
        # its render cache keys against current PHAsset modification dates.
        self._assets_by_album.pop(album_id, None)
        payload = (
            self._run_streaming_assets(
                ["asset-metadata-jsonl", album_id],
                progress=progress,
                timeout=300,
            )
            if self.streaming_runner
            else _json_result(self._run(["asset-metadata", album_id], timeout=300))
        )
        assets = self._assets_from_payload(payload, None)
        self._membership_by_album[album_id] = {asset.uuid for asset in assets}
        return assets

    def list_shared_assets(self, album_id: str) -> list[PhotoAsset]:
        return self._load_assets(album_id)

    def sample_assets(
        self,
        album_id: str,
        *,
        limit: int,
        excluded_uuids: set[str],
    ) -> list[PhotoAsset]:
        """Render only a recent bounded sample for taste onboarding."""
        self._load_albums()
        if album_id not in self._albums:
            raise KeyError(album_id)
        identifiers = _json_result(self._run(["album-photo-identifiers", album_id], timeout=300))
        selected = [identifier for identifier in identifiers if identifier not in excluded_uuids][
            :limit
        ]
        return self.refresh_assets(selected)

    def refresh_assets(self, asset_uuids: list[str]) -> list[PhotoAsset]:
        return self._render_assets_by_id(asset_uuids, command="assets-by-id")

    def repair_assets(self, asset_uuids: list[str]) -> list[PhotoAsset]:
        """Retry degraded/missing renders with PhotoKit network access enabled."""
        repaired = self._render_assets_by_id(asset_uuids, command="repair-assets-by-id")
        replacements = {asset.uuid: asset for asset in repaired}
        for album_id, cached in self._assets_by_album.items():
            self._assets_by_album[album_id] = [
                replacements.get(asset.uuid, asset) for asset in cached
            ]
        return repaired

    def _render_assets_by_id(self, asset_uuids: list[str], *, command: str) -> list[PhotoAsset]:
        if not asset_uuids:
            return []
        request_dir = self.paths.cache_dir / "photokit-requests"
        request_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        request = request_dir / f"{uuid.uuid4()}.json"
        request.write_text(json.dumps({"asset_identifiers": asset_uuids}), encoding="utf-8")
        output = self.paths.cache_dir / "photokit-renders" / "assets-v2"
        try:
            result = self._run([command, str(request), str(output)], timeout=7200)
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
        if result.returncode != 0 or "photokit-source-media-v3" not in result.stdout:
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

    def _load_assets(
        self, album_id: str, *, progress: ProgressCallback | None = None
    ) -> list[PhotoAsset]:
        if album_id in self._assets_by_album:
            cached = self._assets_by_album[album_id]
            # ~/Library/Caches is purgeable. Never return an in-memory PhotoKit
            # payload whose advertised review render has disappeared on disk:
            # preview repair must ask PhotoKit to materialize it again.
            if all(
                not asset.review_render or bool(asset.source_path and asset.source_path.is_file())
                for asset in cached
            ):
                if progress:
                    progress(len(cached), len(cached))
                return cached
            self._assets_by_album.pop(album_id, None)
        self._load_albums()
        if album_id not in self._albums:
            raise KeyError(album_id)
        # One versioned, asset-scoped cache is shared by album analysis, taste
        # onboarding and repeated projects. The native helper includes the
        # PHAsset modification date in each filename, so edited assets do not
        # accidentally reuse an older render.
        output = self.paths.cache_dir / "photokit-renders" / "assets-v2"
        payload = (
            self._run_streaming_assets(
                ["assets-jsonl", album_id, str(output)],
                progress=progress,
                timeout=7200,
            )
            if self.streaming_runner
            else _json_result(self._run(["assets", album_id, str(output)], timeout=7200))
        )
        output = output.resolve()
        assets = self._assets_from_payload(payload, output)
        self._assets_by_album[album_id] = assets
        self._membership_by_album[album_id] = {asset.uuid for asset in assets}
        return assets

    def _run_streaming_assets(
        self,
        arguments: list[str],
        *,
        progress: ProgressCallback | None,
        timeout: int,
    ) -> list[dict[str, object]]:
        final_assets: list[dict[str, object]] | None = None

        def handle_line(line: str) -> None:
            nonlocal final_assets
            try:
                frame = json.loads(line)
            except json.JSONDecodeError as error:
                raise RuntimeError("PhotoKit source helper returned invalid JSONL") from error
            if not isinstance(frame, dict):
                raise RuntimeError("PhotoKit source helper returned invalid JSONL frame")
            if frame.get("type") == "progress":
                if progress:
                    progress(int(frame.get("processed") or 0), int(frame.get("total") or 0))
                return
            if frame.get("type") == "result" and isinstance(frame.get("assets"), list):
                final_assets = frame["assets"]
                return
            raise RuntimeError("PhotoKit source helper returned unknown JSONL frame")

        result = self.streaming_runner(
            [str(self.executable), *arguments],
            on_stdout_line=handle_line,
            timeout=timeout,
        )
        if result.returncode != 0:
            message = next(
                (line.strip() for line in reversed(result.stderr.splitlines()) if line.strip()),
                "PhotoKit source helper failed",
            )
            raise RuntimeError(message[-1000:])
        if final_assets is None:
            raise RuntimeError("PhotoKit source helper returned no final result")
        return final_assets

    def _assets_from_payload(self, payload, output: Path | None) -> list[PhotoAsset]:
        return [
            PhotoAsset(
                uuid=str(item["uuid"]),
                original_filename=item.get("original_filename"),
                current_filename=item.get("current_filename"),
                taken_at=item.get("taken_at"),
                creation_timestamp=(
                    float(item["creation_timestamp"])
                    if item.get("creation_timestamp") is not None
                    else None
                ),
                modification_timestamp=(
                    float(item["modification_timestamp"])
                    if item.get("modification_timestamp") is not None
                    else None
                ),
                width=int(item.get("width") or 0),
                height=int(item.get("height") or 0),
                orientation=(
                    int(item["orientation"]) if item.get("orientation") is not None else None
                ),
                favorite=bool(item.get("favorite")),
                hidden=bool(item.get("hidden")),
                has_adjustments=bool(item.get("has_adjustments")),
                is_live_photo=bool(item.get("is_live_photo")),
                is_burst=bool(item.get("is_burst")),
                burst_key=item.get("burst_key"),
                burst_default_pick=bool(item.get("burst_default_pick")),
                is_missing=bool(item.get("is_missing")),
                is_photo=bool(item.get("is_photo")),
                media_type=str(
                    item.get("media_type") or ("image" if item.get("is_photo") else "video")
                ),
                media_subtypes=int(item.get("media_subtypes") or 0),
                edit_state=str(
                    item.get("edit_state")
                    or ("adjusted" if item.get("has_adjustments") else "original")
                ),
                source_revision=(
                    str(item["source_revision"]) if item.get("source_revision") else None
                ),
                source_path=(
                    ensure_within(Path(str(item["source_path"])), output)
                    if item.get("source_path") and output is not None
                    else None
                ),
                provider_error=item.get("provider_error"),
                review_render=bool(item.get("review_render")),
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
