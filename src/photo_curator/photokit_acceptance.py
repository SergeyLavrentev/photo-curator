from __future__ import annotations

from datetime import datetime

from photo_curator.db import repository
from photo_curator.db.connection import database_connection
from photo_curator.paths import ApplicationPaths
from photo_curator.photos.native_publisher import NativePhotosImporter
from photo_curator.photos.photokit_provider import PhotoKitProvider


def run_photokit_acceptance(
    paths: ApplicationPaths,
    *,
    project_id: str,
    album_name: str | None = None,
) -> dict[str, object]:
    """Create one uniquely named album and verify real PhotoKit read/write membership."""

    provider = PhotoKitProvider.from_environment(paths)
    if provider is None:
        raise RuntimeError("PHOTO_CURATOR_PHOTOKIT_HELPER не указывает на source helper")
    importer = NativePhotosImporter(paths)
    if not importer.capability_available:
        raise RuntimeError("PHOTO_CURATOR_PUBLISH_HELPER не указывает на publish helper")
    with database_connection(paths.database) as connection:
        project = repository.get_project(connection, project_id)
        assets = [
            asset
            for asset in repository.list_assets(connection, project_id)
            if not asset.get("no_longer_exists") and asset.get("asset_uuid")
        ]
    if not str(project.get("library_path") or "").startswith("photokit://"):
        raise RuntimeError("Acceptance требует проект из реальной PhotoKit Library")
    if not assets:
        raise RuntimeError("В проекте нет фото для acceptance")
    asset_uuid = str(assets[0]["asset_uuid"])
    progress: list[dict[str, object]] = []
    resolved = provider.refresh_assets([asset_uuid])
    if not resolved or resolved[0].is_missing or not resolved[0].source_path:
        raise RuntimeError("PhotoKit/iCloud не вернул review render тестового фото")
    name = album_name or (
        "PhotoCurator Acceptance — " + datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    )
    result = importer.duplicate_assets(
        name,
        [asset_uuid],
        progress=lambda phase, processed, total: progress.append(
            {"phase": phase, "processed": processed, "total": total}
        ),
    )
    album_identifier = str(result.get("album_identifier") or "")
    if not album_identifier:
        raise RuntimeError("PhotoKit publisher не вернул album_identifier")
    provider.refresh_library()
    visible = any(
        album.id == album_identifier and album.name == name
        for album in provider.list_regular_albums()
    )
    source_reused = provider.asset_still_in_album(album_identifier, asset_uuid)
    passed = visible and not source_reused and int(result.get("imported") or 0) == 1
    return {
        "schema_version": 1,
        "passed": passed,
        "project_id": project_id,
        "album_name": name,
        "album_identifier": album_identifier,
        "asset_identifier": asset_uuid,
        "source_render_verified": True,
        "album_visible": visible,
        "source_asset_not_reused": not source_reused,
        "imported": int(result.get("imported") or 0),
        "reused": int(result.get("reused") or 0),
        "progress_events": progress,
        "note": "Альбом намеренно оставлен в Photos как воспроизводимое acceptance evidence",
    }
