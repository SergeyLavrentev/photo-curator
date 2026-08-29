from __future__ import annotations

from datetime import datetime

from photo_curator.db import repository
from photo_curator.db.connection import database_connection
from photo_curator.paths import ApplicationPaths
from photo_curator.photos.native_publisher import NativePhotosImporter
from photo_curator.photos.photokit_provider import PhotoKitProvider


def prepare_photokit_acceptance(
    paths: ApplicationPaths,
    *,
    project_id: str,
    album_name: str | None = None,
) -> dict[str, object]:
    """Create one uniquely named album and return evidence for GUI-owned cleanup."""

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
    source_album_identifier = str(project.get("album_id") or "")
    if not source_album_identifier:
        raise RuntimeError("У проекта отсутствует исходный PhotoKit album identifier")
    provider.refresh_library()
    source_album_visible = any(
        album.id == source_album_identifier
        for album in [*provider.list_regular_albums(), *provider.list_shared_albums()]
    )
    if not source_album_visible:
        raise RuntimeError("Исходный PhotoKit-альбом больше недоступен")
    asset_uuid = next(
        (
            str(asset["asset_uuid"])
            for asset in assets
            if provider.asset_still_in_album(source_album_identifier, str(asset["asset_uuid"]))
        ),
        "",
    )
    if not asset_uuid:
        raise RuntimeError("PhotoKit не подтвердил ни одного исходного asset проекта")
    progress: list[dict[str, object]] = []
    name = album_name or (
        "PhotoCurator Acceptance Best — " + datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
    )
    reservation = importer.reserve_album(name)
    album_identifier = str(reservation.get("album_identifier") or "")
    if not album_identifier:
        raise RuntimeError("PhotoKit publisher не вернул album_identifier")
    result = importer.add_assets(
        name,
        [asset_uuid],
        album_identifier=album_identifier,
        progress=lambda phase, processed, total: progress.append(
            {"phase": phase, "processed": processed, "total": total}
        ),
    )
    if str(result.get("album_identifier") or "") != album_identifier:
        raise RuntimeError("PhotoKit publisher изменил identity acceptance-альбома")
    verification_error: str | None = None
    visible = False
    source_reused = False
    added = int(result.get("added") or 0)
    imported = int(result.get("imported") or 0)
    try:
        provider.refresh_library()
        visible = any(
            album.id == album_identifier and album.name == name
            for album in provider.list_regular_albums()
        )
        source_reused = provider.asset_still_in_album(album_identifier, asset_uuid)
    except Exception as error:
        verification_error = f"{type(error).__name__}: {str(error)[-500:]}"
    return {
        "prepared_schema_version": 1,
        "project_id": project_id,
        "album_name": name,
        "album_identifier": album_identifier,
        "source_album_identifier": source_album_identifier,
        "asset_identifier": asset_uuid,
        "source_album_visible_before_create": source_album_visible,
        "source_asset_membership_verified_before_create": True,
        "album_visible": visible,
        "source_asset_reused": source_reused,
        "no_duplicate_asset_created": imported == 0,
        "added": added,
        "imported": imported,
        "reused": int(result.get("reused") or 0),
        "progress_events": progress,
        "verification_error": verification_error,
    }


def finalize_photokit_acceptance(
    paths: ApplicationPaths,
    *,
    prepared: dict[str, object],
) -> dict[str, object]:
    """Verify GUI-owned cleanup and preservation without performing another write."""

    album_identifier = str(prepared.get("album_identifier") or "")
    source_album_identifier = str(prepared.get("source_album_identifier") or "")
    asset_uuid = str(prepared.get("asset_identifier") or "")
    if not album_identifier or not source_album_identifier or not asset_uuid:
        raise RuntimeError("PhotoKit acceptance preparation is incomplete")
    provider = PhotoKitProvider.from_environment(paths)
    if provider is None:
        raise RuntimeError("PHOTO_CURATOR_PHOTOKIT_HELPER не указывает на source helper")
    provider.refresh_library()
    album_removed = not any(
        album.id == album_identifier for album in provider.list_regular_albums()
    )
    source_album_preserved = any(
        album.id == source_album_identifier
        for album in [*provider.list_regular_albums(), *provider.list_shared_albums()]
    )
    source_preserved = source_album_preserved and provider.asset_still_in_album(
        source_album_identifier, asset_uuid
    )
    cleanup_confirmed = album_removed
    passed = (
        bool(prepared.get("album_visible"))
        and bool(prepared.get("source_asset_reused"))
        and bool(prepared.get("no_duplicate_asset_created"))
        and int(prepared.get("added") or 0) == 1
        and not prepared.get("verification_error")
        and cleanup_confirmed
        and source_preserved
    )
    return {
        "schema_version": 2,
        "passed": passed,
        "project_id": str(prepared.get("project_id") or ""),
        "album_name": str(prepared.get("album_name") or ""),
        "album_identifier": album_identifier,
        "source_album_identifier": source_album_identifier,
        "asset_identifier": asset_uuid,
        "source_album_visible_before_create": bool(
            prepared.get("source_album_visible_before_create")
        ),
        "source_asset_membership_verified_before_create": bool(
            prepared.get("source_asset_membership_verified_before_create")
        ),
        "album_visible": bool(prepared.get("album_visible")),
        "source_asset_reused": bool(prepared.get("source_asset_reused")),
        "no_duplicate_asset_created": bool(prepared.get("no_duplicate_asset_created")),
        "acceptance_album_removed": album_removed,
        "source_album_preserved_after_cleanup": source_album_preserved,
        "source_asset_preserved_after_cleanup": source_preserved,
        "added": int(prepared.get("added") or 0),
        "imported": int(prepared.get("imported") or 0),
        "reused": int(prepared.get("reused") or 0),
        "progress_events": list(prepared.get("progress_events") or []),
        "verification_error": prepared.get("verification_error"),
        "note": "Одноразовый acceptance-альбом удалён; исходный PHAsset сохранён",
    }


def run_photokit_acceptance(
    paths: ApplicationPaths,
    *,
    project_id: str,
    album_name: str | None = None,
) -> dict[str, object]:
    """Compatibility path for non-GUI callers; GUI uses prepare/finalize."""

    prepared = prepare_photokit_acceptance(
        paths,
        project_id=project_id,
        album_name=album_name,
    )
    importer = NativePhotosImporter(paths)
    importer.delete_album(str(prepared["album_identifier"]))
    report = finalize_photokit_acceptance(paths, prepared=prepared)
    if prepared.get("verification_error"):
        raise RuntimeError(str(prepared["verification_error"]).split(": ", 1)[-1])
    return report
