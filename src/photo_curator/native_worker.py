from __future__ import annotations

import json
import sys
from typing import TextIO

from photo_curator.acceptance import build_native_quality_evidence, evaluate_acceptance
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
from photo_curator.photos.photokit_provider import PhotoKitProvider
from photo_curator.photos.provider import PhotosProvider
from photo_curator.photos.publisher import PhotosPublisher
from photo_curator.pipeline.coordinator import STAGES, PipelineCoordinator

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
            repository.mark_running_jobs_interrupted(connection)

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
        from_stage = params.get("from_stage")
        if from_stage is not None and from_stage not in STAGES:
            raise NativeWorkerError("Unknown pipeline stage")
        with database_connection(self.paths.database) as connection:
            repository.get_project(connection, project_id)
        self.coordinator.start(project_id, from_stage=from_stage)
        return {"status": "started", "project_id": project_id}

    def _handle_resume_analysis(self, params: dict[str, object]) -> dict[str, object]:
        project_id = _required_string(params, "project_id")
        with database_connection(self.paths.database) as connection:
            repository.get_project(connection, project_id)
            jobs = repository.latest_jobs(connection, project_id)
        interrupted = next(
            (
                job
                for job in reversed(jobs)
                if job["status"] in {"interrupted", "cancelled", "error"}
            ),
            None,
        )
        from_stage = str(interrupted["stage"]) if interrupted else None
        self.coordinator.start(project_id, from_stage=from_stage)
        return {"status": "resumed", "project_id": project_id, "from_stage": from_stage}

    def _handle_cancel_analysis(self, params: dict[str, object]) -> dict[str, object]:
        project_id = _required_string(params, "project_id")
        if not self.coordinator.cancel(project_id):
            raise NativeWorkerError("Активный анализ не найден")
        return {"status": "cancelling", "project_id": project_id}

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
            duplicate_context = repository.duplicate_context(connection, project_id)
        for asset in assets:
            asset["duplicate_context"] = duplicate_context.get(str(asset["asset_uuid"]), {})
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
            asset = repository.get_asset(connection, project_id, asset_uuid)
            asset["duplicate_context"] = repository.duplicate_context(connection, project_id).get(
                asset_uuid, {}
            )
            return _asset_payload(asset)

    def _handle_taste_profile(self, params: dict[str, object]) -> dict[str, object]:
        del params
        with database_connection(self.paths.database) as connection:
            profile = repository.ensure_taste_profile(connection)
            examples = repository.list_preference_examples(connection)
        return _taste_payload(profile, examples)

    def _handle_taste_pair(self, params: dict[str, object]) -> dict[str, object]:
        project_id = _required_string(params, "project_id")
        with database_connection(self.paths.database) as connection:
            repository.get_project(connection, project_id)
            assets = repository.list_assets(connection, project_id)
            signals = repository.analysis_signals_by_asset(connection, project_id)
            duplicate_context = repository.duplicate_context(connection, project_id)
            examples = repository.list_preference_examples(connection)
        pair, remaining = _next_taste_pair(assets, signals, duplicate_context, examples)
        return {
            "pair": (
                {"left": _asset_payload(pair[0]), "right": _asset_payload(pair[1])}
                if pair
                else None
            ),
            "remaining": remaining,
        }

    def _handle_taste_preference(self, params: dict[str, object]) -> dict[str, object]:
        with database_connection(self.paths.database) as connection:
            examples = repository.list_preference_examples(connection)
            split = _next_preference_split(examples)
            example_id = capture_preference(
                connection,
                project_id=_required_string(params, "project_id"),
                left_uuid=_required_string(params, "left_uuid"),
                right_uuid=_required_string(params, "right_uuid"),
                preferred_uuid=_required_string(params, "preferred_uuid"),
                split=split,
            )
            profile = repository.get_taste_profile(connection)
            examples = repository.list_preference_examples(connection)
        return {
            "example_id": example_id,
            "split": split,
            "profile": _taste_payload(profile, examples),
        }

    def _handle_taste_train(self, params: dict[str, object]) -> dict[str, object]:
        del params
        with database_connection(self.paths.database) as connection:
            profile = train_taste_profile(connection)
            examples = repository.list_preference_examples(connection)
        return _taste_payload(profile, examples)

    def _handle_taste_status(self, params: dict[str, object]) -> dict[str, object]:
        paused = params.get("paused")
        if not isinstance(paused, bool):
            raise NativeWorkerError("paused must be boolean")
        with database_connection(self.paths.database) as connection:
            profile = repository.set_taste_profile_paused(connection, paused)
            examples = repository.list_preference_examples(connection)
        return _taste_payload(profile, examples)

    def _handle_taste_export(self, params: dict[str, object]) -> dict[str, object]:
        del params
        with database_connection(self.paths.database) as connection:
            profile = repository.ensure_taste_profile(connection)
            examples = repository.list_preference_examples(connection)
        return {
            "schema_version": 1,
            "profile": profile,
            "examples": examples,
        }

    def _handle_taste_reset(self, params: dict[str, object]) -> dict[str, str]:
        del params
        with database_connection(self.paths.database) as connection:
            repository.reset_taste_profile(connection)
        return {"status": "deleted"}

    def _handle_quality_export(self, params: dict[str, object]) -> dict[str, object]:
        project_id = _required_string(params, "project_id")
        with database_connection(self.paths.database) as connection:
            repository.get_project(connection, project_id)
            assets = repository.list_assets(connection, project_id)
            examples = repository.list_preference_examples(connection)
        return build_native_quality_evidence(project_id, assets, examples)

    def _handle_quality_status(self, params: dict[str, object]) -> dict[str, object]:
        evidence = self._handle_quality_export(params)
        return dict(evidence["summary"])

    def _handle_quality_top_k(self, params: dict[str, object]) -> dict[str, object]:
        project_id = _required_string(params, "project_id")
        asset_uuid = _required_string(params, "asset_uuid")
        selected = params.get("selected")
        if not isinstance(selected, bool):
            raise NativeWorkerError("selected must be boolean")
        with database_connection(self.paths.database) as connection:
            ordered = repository.set_quality_top_k(connection, project_id, asset_uuid, selected)
        return {"top_k": ordered, "count": len(ordered)}

    def _handle_quality_series(self, params: dict[str, object]) -> dict[str, object]:
        project_id = _required_string(params, "project_id")
        with database_connection(self.paths.database) as connection:
            return repository.label_quality_duplicate_group(
                connection,
                project_id,
                _required_string(params, "group_id"),
                _required_string(params, "leader_uuid"),
            )

    def _handle_quality_custom_series(self, params: dict[str, object]) -> dict[str, object]:
        project_id = _required_string(params, "project_id")
        raw_members = params.get("member_uuids")
        if not isinstance(raw_members, list) or not all(
            isinstance(value, str) and value for value in raw_members
        ):
            raise NativeWorkerError("member_uuids must be a non-empty string array")
        with database_connection(self.paths.database) as connection:
            return repository.label_quality_custom_group(
                connection,
                project_id,
                raw_members,
                _required_string(params, "leader_uuid"),
            )

    def _handle_quality_evaluate(self, params: dict[str, object]) -> dict[str, object]:
        project_id = _required_string(params, "project_id")
        manifest = params.get("manifest")
        snapshot = params.get("score_snapshot")
        if not isinstance(manifest, dict):
            raise NativeWorkerError("manifest must be an object")
        if not isinstance(snapshot, dict):
            raise NativeWorkerError("score_snapshot must be an object")
        with database_connection(self.paths.database) as connection:
            repository.get_project(connection, project_id)
            assets = repository.list_assets(connection, project_id)
            groups = repository.list_duplicate_groups(connection, project_id)
        return evaluate_acceptance(
            manifest,
            assets,
            groups,
            project_id=project_id,
            score_snapshot=snapshot,
        )

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
            base = PhotoKitProvider.from_environment(paths)
            if base is None:
                raise RuntimeError(
                    "Встроенный PhotoKit source helper отсутствует; переустановите Photo Curator"
                )
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
        "manual_disposition": asset.get("manual_disposition"),
        "swipe_score": asset.get("swipe_score"),
        "generic_score": asset.get("swipe_generic_score"),
        "personal_delta": asset.get("swipe_personal_delta"),
        "confidence": asset.get("swipe_confidence"),
        "components": asset.get("swipe_components") or {},
        "reasons": asset.get("swipe_reasons") or [],
        "duplicate_group": (asset.get("duplicate_context") or {}).get("group_id"),
        "duplicate_is_leader": bool((asset.get("duplicate_context") or {}).get("is_leader")),
        "quality_top_k_rank": asset.get("quality_top_k_rank"),
        "quality_duplicate_group": asset.get("quality_duplicate_group"),
        "quality_expected_leader": bool(asset.get("quality_expected_leader")),
    }


def _next_taste_pair(
    assets: list[dict[str, object]],
    signals: dict[str, dict[str, dict[str, object]]],
    duplicate_context: dict[str, dict[str, object]],
    examples: list[dict[str, object]],
) -> tuple[tuple[dict[str, object], dict[str, object]] | None, int]:
    seen = {
        tuple(sorted((str(example["left_uuid"]), str(example["right_uuid"]))))
        for example in examples
    }
    candidates = [
        asset
        for asset in assets
        if asset.get("cache_state") == "ready"
        and asset.get("review_path")
        and asset.get("final_disposition") in {"keep", "review"}
        and signals.get(str(asset["asset_uuid"]), {}).get("feature_print", {}).get("status")
        == "ready"
    ]
    candidates.sort(
        key=lambda asset: (
            -float(asset.get("swipe_score") or 0),
            str(asset["asset_uuid"]),
        )
    )

    ranked_pairs: list[tuple[float, float, str, str, dict[str, object], dict[str, object]]] = []
    for index, left in enumerate(candidates):
        left_uuid = str(left["asset_uuid"])
        left_group = duplicate_context.get(left_uuid, {}).get("group_id")
        for right in candidates[index + 1 : index + 9]:
            right_uuid = str(right["asset_uuid"])
            right_group = duplicate_context.get(right_uuid, {}).get("group_id")
            if left_group and left_group == right_group:
                continue
            key = tuple(sorted((left_uuid, right_uuid)))
            if key in seen:
                continue
            left_score = float(left.get("swipe_score") or 0)
            right_score = float(right.get("swipe_score") or 0)
            ranked_pairs.append(
                (
                    abs(left_score - right_score),
                    -max(left_score, right_score),
                    key[0],
                    key[1],
                    left,
                    right,
                )
            )
    if not ranked_pairs:
        return None, 0
    ranked_pairs.sort(key=lambda pair: pair[:4])
    best = ranked_pairs[0]
    return (best[4], best[5]), len(ranked_pairs)


def _taste_payload(
    profile: dict[str, object], examples: list[dict[str, object]]
) -> dict[str, object]:
    calibration_count = sum(example["split"] == "calibration" for example in examples)
    held_out_count = sum(example["split"] == "held_out" for example in examples)
    return {
        "id": profile["id"],
        "name": profile["name"],
        "status": profile["status"],
        "model_version": profile.get("model_version"),
        "training_examples": profile.get("training_examples") or 0,
        "preference_count": len(examples),
        "calibration_count": calibration_count,
        "held_out_count": held_out_count,
        "evidence": profile.get("evidence") or {},
    }


def _next_preference_split(examples: list[dict[str, object]]) -> str:
    calibration_count = sum(example["split"] == "calibration" for example in examples)
    if calibration_count < 3:
        return "calibration"
    post_warmup_count = max(0, len(examples) - 3)
    return "held_out" if post_warmup_count % 2 == 0 else "calibration"


def _publish_payload(publish: dict[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in publish.items()
        if key
        not in {"uuid_file", "dry_run_stdout", "dry_run_stderr", "apply_stdout", "apply_stderr"}
    }
