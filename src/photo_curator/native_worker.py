from __future__ import annotations

import json
import os
import sys
from typing import TextIO

from photo_curator.analysis.native_vision import NativeVisionEngine
from photo_curator.analysis.taste import (
    capture_preference,
    train_taste_profile,
)
from photo_curator.db import repository
from photo_curator.db.connection import database_connection
from photo_curator.db.migrations import SCHEMA_VERSION, migrate
from photo_curator.paths import ApplicationPaths
from photo_curator.photos.local_provider import LocalAlbumsProvider
from photo_curator.photos.osxphotos_provider import OSXPhotosProvider
from photo_curator.photos.photokit_provider import PhotoKitProvider
from photo_curator.photos.provider import PhotosProvider
from photo_curator.photos.publisher import PhotosPublisher
from photo_curator.pipeline.coordinator import PipelineCoordinator

WORKER_SCHEMA_VERSION = 1


class NativeWorkerError(ValueError):
    pass


class NativeWorker:
    """Versioned local IPC surface for the native macOS client."""

    def __init__(
        self,
        paths: ApplicationPaths,
        *,
        provider: PhotosProvider,
        coordinator: PipelineCoordinator | None = None,
        publisher: PhotosPublisher | None = None,
    ) -> None:
        self.paths = paths
        self.provider = provider
        self.coordinator = coordinator or PipelineCoordinator(
            database_path=paths.database,
            paths=paths,
            provider=provider,
            vision_engine=NativeVisionEngine(paths),
        )
        self.publisher = publisher or PhotosPublisher(
            database_path=paths.database,
            paths=paths,
            provider=provider,
        )
        with database_connection(paths.database) as connection:
            migrate(connection)

    def dispatch(self, method: str, params: dict[str, object]) -> object:
        handler = getattr(self, f"_handle_{method}", None)
        if not handler or method.startswith("_"):
            raise NativeWorkerError(f"Unknown worker method: {method}")
        return handler(params)

    def _handle_status(self, params: dict[str, object]) -> dict[str, object]:
        del params
        return {
            "status": "ready",
            "worker_schema_version": WORKER_SCHEMA_VERSION,
            "database_schema_version": SCHEMA_VERSION,
        }

    def _handle_albums(self, params: dict[str, object]) -> dict[str, object]:
        del params
        return {
            "regular": [_album_payload(album) for album in self.provider.list_regular_albums()],
            "shared": [_album_payload(album) for album in self.provider.list_shared_albums()],
        }

    def _handle_projects(self, params: dict[str, object]) -> list[dict[str, object]]:
        del params
        with database_connection(self.paths.database) as connection:
            return [_project_payload(project) for project in repository.list_projects(connection)]

    def _handle_create_project(self, params: dict[str, object]) -> dict[str, object]:
        album_id = _required_string(params, "album_id")
        albums = {
            album.id: album
            for album in [
                *self.provider.list_regular_albums(),
                *self.provider.list_shared_albums(),
            ]
        }
        album = albums.get(album_id)
        if not album:
            raise NativeWorkerError("Выбранный альбом больше недоступен")
        density = str(params.get("selection_density") or "balanced")
        if density not in {"compact", "balanced", "broad"}:
            raise NativeWorkerError("Unknown selection density")
        name = str(params.get("name") or "").strip() or album.name
        with database_connection(self.paths.database) as connection:
            shared_copy = repository.completed_shared_copy_for_album(connection, album.id)
            library = self.provider.get_current_library()
            project_id = repository.create_project(
                connection,
                name=name,
                library=library,
                album=album,
                selection_density=density,
                source_provenance=(
                    "service_shared_copy"
                    if shared_copy
                    else "photokit"
                    if library.library_path.startswith("photokit://")
                    else "regular_album"
                ),
            )
            return _project_payload(repository.get_project(connection, project_id))

    def _handle_start_analysis(self, params: dict[str, object]) -> dict[str, object]:
        project_id = _required_string(params, "project_id")
        with database_connection(self.paths.database) as connection:
            repository.get_project(connection, project_id)
        self.coordinator.start(project_id)
        return {"status": "started", "project_id": project_id}

    def _handle_project(self, params: dict[str, object]) -> dict[str, object]:
        project_id = _required_string(params, "project_id")
        with database_connection(self.paths.database) as connection:
            project = repository.get_project(connection, project_id)
            summary = repository.project_summary(connection, project_id)
            jobs = repository.latest_jobs(connection, project_id)
        return {
            "project": _project_payload(project),
            "summary": summary,
            "jobs": [_job_payload(job) for job in jobs],
        }

    def _handle_assets(self, params: dict[str, object]) -> dict[str, object]:
        project_id = _required_string(params, "project_id")
        limit = max(1, min(5000, int(params.get("limit") or 5000)))
        with database_connection(self.paths.database) as connection:
            assets = repository.list_assets(connection, project_id)
        assets.sort(
            key=lambda asset: (
                -float(asset.get("swipe_score") or -1),
                str(asset["asset_uuid"]),
            )
        )
        return {
            "items": [_asset_payload(asset) for asset in assets[:limit]],
            "total": len(assets),
        }

    def _handle_decision(self, params: dict[str, object]) -> dict[str, object]:
        project_id = _required_string(params, "project_id")
        asset_uuid = _required_string(params, "asset_uuid")
        disposition = params.get("disposition")
        if disposition not in {None, "keep", "review", "reject"}:
            raise NativeWorkerError("Unknown disposition")
        note = str(params["note"])[:1000] if params.get("note") is not None else None
        with database_connection(self.paths.database) as connection:
            repository.set_manual_decision(connection, project_id, asset_uuid, disposition, note)
            return _asset_payload(repository.get_asset(connection, project_id, asset_uuid))

    def _handle_taste_profile(self, params: dict[str, object]) -> dict[str, object]:
        del params
        with database_connection(self.paths.database) as connection:
            profile = repository.ensure_taste_profile(connection)
            examples = repository.list_preference_examples(connection)
        return _taste_payload(profile, len(examples))

    def _handle_taste_preference(self, params: dict[str, object]) -> dict[str, object]:
        with database_connection(self.paths.database) as connection:
            example_id = capture_preference(
                connection,
                project_id=_required_string(params, "project_id"),
                left_uuid=_required_string(params, "left_uuid"),
                right_uuid=_required_string(params, "right_uuid"),
                preferred_uuid=_required_string(params, "preferred_uuid"),
                split=str(params.get("split") or "calibration"),
            )
            profile = repository.get_taste_profile(connection)
            count = len(repository.list_preference_examples(connection))
        return {"example_id": example_id, "profile": _taste_payload(profile, count)}

    def _handle_taste_train(self, params: dict[str, object]) -> dict[str, object]:
        del params
        with database_connection(self.paths.database) as connection:
            profile = train_taste_profile(connection)
            count = len(repository.list_preference_examples(connection))
        return _taste_payload(profile, count)

    def _handle_taste_status(self, params: dict[str, object]) -> dict[str, object]:
        paused = params.get("paused")
        if not isinstance(paused, bool):
            raise NativeWorkerError("paused must be boolean")
        with database_connection(self.paths.database) as connection:
            profile = repository.set_taste_profile_paused(connection, paused)
            count = len(repository.list_preference_examples(connection))
        return _taste_payload(profile, count)

    def _handle_taste_reset(self, params: dict[str, object]) -> dict[str, str]:
        del params
        with database_connection(self.paths.database) as connection:
            repository.reset_taste_profile(connection)
        return {"status": "deleted"}

    def _handle_publish_dry_run(self, params: dict[str, object]) -> dict[str, object]:
        return _publish_payload(
            self.publisher.dry_run(
                _required_string(params, "project_id"), str(params.get("kind") or "best")
            )
        )

    def _handle_publish_apply(self, params: dict[str, object]) -> dict[str, object]:
        if params.get("confirmed") is not True:
            raise NativeWorkerError("Publish apply requires confirmed=true")
        return _publish_payload(self.publisher.apply(_required_string(params, "publish_id")))


def run_native_worker(
    paths: ApplicationPaths,
    *,
    input_stream: TextIO = sys.stdin,
    output_stream: TextIO = sys.stdout,
    worker: NativeWorker | None = None,
    demo: bool = False,
) -> int:
    if worker is None:
        if demo:
            from photo_curator.photos.fake_provider import FakePhotosProvider

            base = FakePhotosProvider(paths.cache_dir / "demo-sources")
        else:
            configured_photokit = os.environ.get("PHOTO_CURATOR_PHOTOKIT_HELPER")
            if configured_photokit:
                base = PhotoKitProvider.from_environment(paths)
                if base is None:
                    raise RuntimeError("Встроенный PhotoKit source helper отсутствует")
            else:
                base = OSXPhotosProvider()
        provider = LocalAlbumsProvider(base, paths.data_dir / "local_albums")
        worker = NativeWorker(paths, provider=provider)
    for raw_line in input_stream:
        line = raw_line.strip()
        if not line:
            continue
        request_id: object = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict) or request.get("schema_version") != 1:
                raise NativeWorkerError("Unsupported worker request schema")
            request_id = request.get("id")
            method = _required_string(request, "method")
            params = request.get("params") or {}
            if not isinstance(params, dict):
                raise NativeWorkerError("params must be an object")
            if method == "shutdown":
                response = {"schema_version": 1, "id": request_id, "result": {"status": "bye"}}
                output_stream.write(json.dumps(response, ensure_ascii=False) + "\n")
                output_stream.flush()
                return 0
            result = worker.dispatch(method, params)
            response = {"schema_version": 1, "id": request_id, "result": result}
        except Exception as error:
            response = {
                "schema_version": 1,
                "id": request_id,
                "error": {"type": type(error).__name__, "message": str(error)[-1000:]},
            }
        output_stream.write(json.dumps(response, ensure_ascii=False) + "\n")
        output_stream.flush()
    return 0


def _required_string(values: dict[str, object], key: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value:
        raise NativeWorkerError(f"{key} is required")
    return value


def _album_payload(album) -> dict[str, object]:
    return {
        "id": album.id,
        "name": album.name,
        "folder_path": album.folder_path,
        "full_path": album.full_path,
        "is_shared": album.is_shared,
        "photo_count": album.photo_count,
        "video_count": album.video_count,
    }


def _project_payload(project: dict[str, object]) -> dict[str, object]:
    return {
        "id": project["id"],
        "name": project["name"],
        "album_id": project["album_id"],
        "album_name": project["album_name"],
        "state": project["state"],
        "settings": json.loads(str(project.get("settings_json") or "{}")),
        "created_at": project["created_at"],
        "updated_at": project["updated_at"],
    }


def _job_payload(job: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in job.items() if key not in {"error_text", "project_id"}}


def _asset_payload(asset: dict[str, object]) -> dict[str, object]:
    return {
        "asset_uuid": asset["asset_uuid"],
        "filename": asset.get("current_filename"),
        "thumbnail_path": asset.get("thumbnail_path"),
        "review_path": asset.get("review_path"),
        "width": asset.get("width"),
        "height": asset.get("height"),
        "favorite": bool(asset.get("favorite")),
        "final_disposition": asset.get("final_disposition"),
        "swipe_score": asset.get("swipe_score"),
        "generic_score": asset.get("swipe_generic_score"),
        "personal_delta": asset.get("swipe_personal_delta"),
        "confidence": asset.get("swipe_confidence"),
        "components": asset.get("swipe_components") or {},
        "reasons": asset.get("swipe_reasons") or [],
    }


def _taste_payload(profile: dict[str, object], count: int) -> dict[str, object]:
    return {
        "id": profile["id"],
        "name": profile["name"],
        "status": profile["status"],
        "model_version": profile.get("model_version"),
        "training_examples": profile.get("training_examples") or 0,
        "preference_count": count,
        "evidence": profile.get("evidence") or {},
    }


def _publish_payload(publish: dict[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in publish.items()
        if key
        not in {"uuid_file", "dry_run_stdout", "dry_run_stderr", "apply_stdout", "apply_stderr"}
    }
