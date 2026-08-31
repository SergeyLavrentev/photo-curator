from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import shutil
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock, local
from typing import TextIO

from photo_curator.acceptance import build_database_quality_evidence, evaluate_acceptance
from photo_curator.analysis.codex_vision import codex_status
from photo_curator.analysis.local_models import LocalModelEngine
from photo_curator.analysis.native_vision import NativeVisionEngine
from photo_curator.analysis.taste import (
    MIN_CALIBRATION_PAIRS,
    capture_preference,
    capture_preference_vectors,
    feature_vector,
    train_taste_profile,
)
from photo_curator.db import repository
from photo_curator.db.connection import create_database_backup, database_connection
from photo_curator.db.migrations import SCHEMA_VERSION, migrate
from photo_curator.native_payloads import (
    album_payload as _album_payload,
)
from photo_curator.native_payloads import (
    asset_card_payload as _asset_card_payload,
)
from photo_curator.native_payloads import (
    asset_payload as _asset_payload,
)
from photo_curator.native_payloads import (
    job_payload as _job_payload,
)
from photo_curator.native_payloads import (
    project_payload as _project_payload,
)
from photo_curator.native_payloads import (
    publish_payload as _publish_payload,
)
from photo_curator.native_payloads import (
    related_asset_payload as _related_asset_payload,
)
from photo_curator.paths import ApplicationPaths
from photo_curator.photokit_acceptance import (
    finalize_photokit_acceptance,
    prepare_photokit_acceptance,
    run_photokit_acceptance,
)
from photo_curator.photos.local_provider import LocalAlbumsProvider
from photo_curator.photos.photokit_provider import PhotoKitProvider
from photo_curator.photos.provider import PhotosProvider, is_supported_photo
from photo_curator.photos.publisher import PhotosPublisher
from photo_curator.pipeline.coordinator import STAGES, PipelineCoordinator

WORKER_SCHEMA_VERSION = 3
TASTE_ONBOARDING_VERSION = 2
TASTE_ROUND_COUNT = 3
TASTE_ROUND_SIZE = 10
TASTE_ROUND_SELECTIONS = 3
TASTE_ROUND_REJECTIONS = 3


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
            model_engine=LocalModelEngine(paths),
        )
        self.publisher = publisher or PhotosPublisher(
            database_path=paths.database,
            paths=paths,
            provider=provider,
            legacy_cli_enabled=False,
        )
        self._dispatch_state = local()
        with database_connection(paths.database) as connection:
            migrate(connection)
            repository.mark_running_jobs_interrupted(connection)
        _cleanup_transient_cache(paths)

    def dispatch(
        self,
        method: str,
        params: dict[str, object],
        *,
        progress: Callable[[dict[str, object]], None] | None = None,
    ) -> object:
        handler = getattr(self, f"_handle_{method}", None)
        if not handler or method.startswith("_"):
            raise NativeWorkerError(f"Unknown worker method: {method}")
        previous = getattr(self._dispatch_state, "progress", None)
        self._dispatch_state.progress = progress
        try:
            return handler(params)
        finally:
            self._dispatch_state.progress = previous

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

    def _handle_codex_status(self, params: dict[str, object]) -> dict[str, object]:
        del params
        return codex_status().payload()

    def _handle_photokit_acceptance(self, params: dict[str, object]) -> dict[str, object]:
        project_id = _required_string(params, "project_id")
        if params.get("confirmed") is not True:
            raise NativeWorkerError("PhotoKit acceptance requires confirmed=true")
        result = run_photokit_acceptance(self.paths, project_id=project_id)
        _append_photokit_acceptance_audit(self.paths, result)
        return result

    def _handle_photokit_acceptance_prepare(self, params: dict[str, object]) -> dict[str, object]:
        project_id = _required_string(params, "project_id")
        if params.get("confirmed") is not True:
            raise NativeWorkerError("PhotoKit acceptance requires confirmed=true")
        return prepare_photokit_acceptance(self.paths, project_id=project_id)

    def _handle_photokit_acceptance_finalize(self, params: dict[str, object]) -> dict[str, object]:
        if params.get("confirmed") is not True:
            raise NativeWorkerError("PhotoKit acceptance requires confirmed=true")
        prepared = params.get("prepared")
        if not isinstance(prepared, dict):
            raise NativeWorkerError("prepared must be an object")
        result = finalize_photokit_acceptance(self.paths, prepared=prepared)
        _append_photokit_acceptance_audit(self.paths, result)
        return result

    def _handle_delete_project(self, params: dict[str, object]) -> dict[str, object]:
        project_id = _required_string(params, "project_id")
        if params.get("confirmed") is not True:
            raise NativeWorkerError("Project deletion requires confirmed=true")
        with self.coordinator.project_operation(project_id):
            if self.coordinator.is_running(project_id):
                raise NativeWorkerError("Сначала остановите выполняющийся анализ")
            with database_connection(self.paths.database) as connection:
                project = repository.get_project(connection, project_id)
                if project["state"] == "running":
                    raise NativeWorkerError("Сначала остановите выполняющийся анализ")
            _append_destructive_audit(
                self.paths,
                action="delete_project",
                status="requested",
                project_id=project_id,
            )
            backup = create_database_backup(
                self.paths.database,
                self.paths.data_dir / "backups",
                reason=f"before-delete-{project_id}",
            )
            with database_connection(self.paths.database) as connection:
                repository.delete_project(connection, project_id)
            _remove_project_cache(self.paths, project_id)
            _append_destructive_audit(
                self.paths,
                action="delete_project",
                status="completed",
                project_id=project_id,
                backup_path=str(backup),
            )
        return {"status": "deleted", "project_id": project_id, "backup_path": str(backup)}

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
        analysis_mode = str(params.get("analysis_mode") or "local")
        if analysis_mode not in {"local", "codex"}:
            raise NativeWorkerError("Unknown analysis mode")
        if analysis_mode == "codex":
            status = codex_status()
            if not status.ready:
                raise NativeWorkerError(status.detail or "Codex не готов к анализу")
        local_engines = {
            name: _boolean_param(params, f"engine_{name}", default=True)
            for name in ("apple", "nima", "mobileclip", "musiq")
        }
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
                analysis_mode=analysis_mode,
                local_engines=local_engines,
                source_provenance=(
                    "service_shared_copy"
                    if shared_copy
                    else "photokit"
                    if library.library_path.startswith("photokit://")
                    else "regular_album"
                ),
            )
            return _project_payload(repository.get_project(connection, project_id))

    def _handle_cleanup_abandoned_projects(self, params: dict[str, object]) -> dict[str, object]:
        del params
        # Kept as a backwards-compatible no-op. Analyses are persistent documents
        # and may only be removed by an explicit delete_project request.
        return {"removed_project_ids": [], "removed_count": 0}

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
        # Cancellation is cooperative: a blocking PhotoKit/Vision call may still
        # be unwinding after the UI has offered "Continue".  Retrying resume in
        # that interval must be idempotent instead of attempting a second run.
        if self.coordinator.is_running(project_id):
            return {"status": "already_running", "project_id": project_id}
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
            assets = repository.list_assets(connection, project_id)
        summary["unavailable_preview_files"] = _unavailable_preview_files(assets)
        return {
            "project": _project_payload(project),
            "summary": summary,
            "jobs": [_job_payload(job) for job in jobs],
        }

    def _handle_binary_decisions(self, params: dict[str, object]) -> dict[str, object]:
        project_id = _required_string(params, "project_id")
        with database_connection(self.paths.database) as connection:
            project = repository.get_project(connection, project_id)
            if project.get("state") != "ready":
                raise NativeWorkerError("Analysis is not ready")
            resolved = repository.resolve_legacy_review_decisions(connection, project_id)
            summary = repository.project_summary(connection, project_id)
        return {"resolved": resolved, "summary": summary}

    def _handle_assets(self, params: dict[str, object]) -> dict[str, object]:
        project_id = _required_string(params, "project_id")
        limit = max(1, min(5000, int(params.get("limit") or 5000)))
        offset = max(0, int(params.get("offset") or 0))
        raw_cursor = params.get("cursor")
        cursor_score: float | None = None
        cursor_asset_uuid: str | None = None
        if raw_cursor is not None:
            if not isinstance(raw_cursor, dict):
                raise NativeWorkerError("cursor must be an object")
            score = raw_cursor.get("score")
            asset_uuid = raw_cursor.get("asset_uuid")
            if (
                not isinstance(score, (int, float))
                or isinstance(score, bool)
                or not math.isfinite(float(score))
                or not isinstance(asset_uuid, str)
                or not asset_uuid
            ):
                raise NativeWorkerError("Invalid assets cursor")
            cursor_score = float(score)
            cursor_asset_uuid = asset_uuid
            offset = 0
        focus_asset_uuid = str(params.get("focus_asset_uuid") or "")
        disposition = params.get("disposition")
        if disposition is not None and disposition not in {"keep", "review", "reject"}:
            raise NativeWorkerError("Unknown disposition filter")
        selection = params.get("selection")
        if selection is not None and selection not in {"pick", "alternative", "review", "reject"}:
            raise NativeWorkerError("Unknown selection filter")
        with database_connection(self.paths.database) as connection:
            total = repository.count_assets(
                connection,
                project_id,
                disposition=str(disposition) if disposition is not None else None,
                selection=str(selection) if selection is not None else None,
            )
            raw_page = repository.list_assets_page(
                connection,
                project_id,
                limit=limit,
                offset=offset,
                disposition=str(disposition) if disposition is not None else None,
                selection=str(selection) if selection is not None else None,
                cursor_score=cursor_score,
                cursor_asset_uuid=cursor_asset_uuid,
            )
            page = list(raw_page)
            if focus_asset_uuid and offset == 0 and raw_cursor is None and limit > 1:
                try:
                    focused = repository.get_asset(connection, project_id, focus_asset_uuid)
                except KeyError:
                    focused = None
                if focused and (
                    (disposition is None or focused.get("final_disposition") == disposition)
                    and (selection is None or focused.get("final_selection") == selection)
                    and not any(str(asset["asset_uuid"]) == focus_asset_uuid for asset in page)
                ):
                    page = [focused, *page[: limit - 1]]
            page_ids = {str(asset["asset_uuid"]) for asset in page}
            duplicate_context = repository.duplicate_context(connection, project_id, page_ids)
            leader_ids = {
                str(context["leader_uuid"])
                for context in duplicate_context.values()
                if context.get("leader_uuid") and str(context["leader_uuid"]) not in page_ids
            }
            leaders_by_uuid = repository.assets_by_uuid(connection, project_id, leader_ids)
            album_references = [
                _related_asset_payload(asset)
                for asset in repository.list_assets_page(connection, project_id, limit=3)
            ]
        for asset in page:
            asset["duplicate_context"] = duplicate_context.get(str(asset["asset_uuid"]), {})
            leader_uuid = str(asset["duplicate_context"].get("leader_uuid") or "")
            if leader_uuid and leader_uuid != str(asset["asset_uuid"]):
                leader = leaders_by_uuid.get(leader_uuid)
                if leader:
                    asset["duplicate_leader"] = _related_asset_payload(leader)
            if any(
                isinstance(reason, dict) and reason.get("code") == "below_album_cutoff"
                for reason in asset.get("reasons") or []
            ):
                asset["album_references"] = album_references
        cursor_item = page[-1] if page else None
        next_cursor = None
        if cursor_item is not None and len(raw_page) == limit:
            raw_score = cursor_item.get("swipe_score")
            next_cursor = {
                "score": float(raw_score) if raw_score is not None else -1.0,
                "asset_uuid": str(cursor_item["asset_uuid"]),
            }
        return {
            "schema_version": 1,
            "items": [_asset_card_payload(asset) for asset in page],
            "total": total,
            "offset": offset,
            "next_cursor": next_cursor,
        }

    def _handle_series(self, params: dict[str, object]) -> dict[str, object]:
        project_id = _required_string(params, "project_id")
        group_id = _required_string(params, "group_id")
        with database_connection(self.paths.database) as connection:
            try:
                series = repository.get_duplicate_series(connection, project_id, group_id)
            except KeyError as error:
                raise NativeWorkerError("Series not found") from error
        items = list(series["items"])
        return {
            "payload_kind": "series",
            "schema_version": 1,
            "group_id": series["group_id"],
            "kind": series["kind"],
            "confidence": series["confidence"],
            "leader_uuid": series["leader_uuid"],
            "flags": series["flags"],
            "member_count": len(items),
            "items": [_asset_card_payload(asset) for asset in items],
        }

    def _handle_asset_details(self, params: dict[str, object]) -> dict[str, object]:
        project_id = _required_string(params, "project_id")
        asset_uuid = _required_string(params, "asset_uuid")
        with database_connection(self.paths.database) as connection:
            asset = repository.get_asset(connection, project_id, asset_uuid)
            context = repository.duplicate_context(connection, project_id, {asset_uuid}).get(
                asset_uuid, {}
            )
            asset["duplicate_context"] = context
            leader_uuid = str(context.get("leader_uuid") or "")
            if leader_uuid and leader_uuid != asset_uuid:
                leader = repository.assets_by_uuid(connection, project_id, {leader_uuid}).get(
                    leader_uuid
                )
                if leader:
                    asset["duplicate_leader"] = _related_asset_payload(leader)
            if any(
                isinstance(reason, dict) and reason.get("code") == "below_album_cutoff"
                for reason in asset.get("reasons") or []
            ):
                asset["album_references"] = [
                    _related_asset_payload(row)
                    for row in repository.list_assets_page(connection, project_id, limit=3)
                ]
        payload = _asset_payload(asset)
        payload["payload_kind"] = "asset_details"
        payload["payload_schema_version"] = 1
        return payload

    def _handle_decision(self, params: dict[str, object]) -> dict[str, object]:
        project_id = _required_string(params, "project_id")
        asset_uuid = _required_string(params, "asset_uuid")
        disposition = params.get("disposition")
        if disposition not in {None, "keep", "reject"}:
            raise NativeWorkerError("Unknown disposition")
        note = str(params["note"])[:1000] if params.get("note") is not None else None
        mutation_generation = params.get("mutation_generation")
        if mutation_generation is not None and (
            not isinstance(mutation_generation, int)
            or isinstance(mutation_generation, bool)
            or mutation_generation < 1
        ):
            raise NativeWorkerError("mutation_generation must be a positive integer")
        with database_connection(self.paths.database) as connection:
            repository.set_manual_decision(
                connection,
                project_id,
                asset_uuid,
                disposition,
                note,
                mutation_generation=mutation_generation,
            )
            asset = repository.get_asset(connection, project_id, asset_uuid)
            asset["duplicate_context"] = repository.duplicate_context(connection, project_id).get(
                asset_uuid, {}
            )
            return _asset_payload(asset)

    def _handle_rating(self, params: dict[str, object]) -> dict[str, object]:
        project_id = _required_string(params, "project_id")
        asset_uuid = _required_string(params, "asset_uuid")
        raw_rating = params.get("rating")
        if raw_rating is not None and (
            not isinstance(raw_rating, int)
            or isinstance(raw_rating, bool)
            or not 1 <= raw_rating <= 5
        ):
            raise NativeWorkerError("rating must be null or an integer between 1 and 5")
        with database_connection(self.paths.database) as connection:
            repository.set_manual_rating(connection, project_id, asset_uuid, raw_rating)
            asset = repository.get_asset(connection, project_id, asset_uuid)
            asset["duplicate_context"] = repository.duplicate_context(
                connection, project_id, {asset_uuid}
            ).get(asset_uuid, {})
            return _asset_payload(asset)

    def _handle_selection(self, params: dict[str, object]) -> dict[str, object]:
        project_id = _required_string(params, "project_id")
        asset_uuid = _required_string(params, "asset_uuid")
        selection = params.get("selection")
        if selection not in {None, "pick", "alternative", "review", "reject"}:
            raise NativeWorkerError("Unknown selection")
        mutation_generation = params.get("mutation_generation")
        if mutation_generation is not None and (
            not isinstance(mutation_generation, int)
            or isinstance(mutation_generation, bool)
            or mutation_generation < 1
        ):
            raise NativeWorkerError("mutation_generation must be a positive integer")
        with database_connection(self.paths.database) as connection:
            repository.set_manual_selection(
                connection,
                project_id,
                asset_uuid,
                selection,
                mutation_generation=mutation_generation,
            )
            asset = repository.get_asset(connection, project_id, asset_uuid)
            asset["duplicate_context"] = repository.duplicate_context(
                connection, project_id, {asset_uuid}
            ).get(asset_uuid, {})
            return _asset_payload(asset)

    def _handle_decisions_batch(self, params: dict[str, object]) -> dict[str, object]:
        project_id = _required_string(params, "project_id")
        raw_uuids = params.get("asset_uuids")
        if (
            not isinstance(raw_uuids, list)
            or not raw_uuids
            or not all(isinstance(value, str) and value for value in raw_uuids)
        ):
            raise NativeWorkerError("asset_uuids must be a non-empty string array")
        asset_uuids = list(dict.fromkeys(raw_uuids))
        disposition = params.get("disposition")
        if disposition not in {"keep", "reject"}:
            raise NativeWorkerError("Unknown disposition")
        with database_connection(self.paths.database) as connection:
            repository.get_project(connection, project_id)
            for asset_uuid in asset_uuids:
                repository.set_manual_decision(
                    connection,
                    project_id,
                    asset_uuid,
                    str(disposition),
                    None,
                )
            summary = repository.project_summary(connection, project_id)
        return {
            "status": "updated",
            "updated": len(asset_uuids),
            "summary": summary,
        }

    def _handle_taste_profile(self, params: dict[str, object]) -> dict[str, object]:
        del params
        with database_connection(self.paths.database) as connection:
            profile = repository.ensure_taste_profile(connection)
            examples = repository.list_preference_examples(connection)
            rounds = repository.list_taste_rounds(connection)
        return _taste_payload(profile, examples, rounds)

    def _handle_taste_round_prepare(self, params: dict[str, object]) -> dict[str, object]:
        album_id = _required_string(params, "album_id")
        mode = params.get("mode", "onboarding")
        if mode not in {"onboarding", "adjust"}:
            raise NativeWorkerError("Unknown taste round mode")
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
        with database_connection(self.paths.database) as connection:
            repository.ensure_taste_profile(connection)
            rounds = repository.list_taste_rounds(connection)
            completed = [item for item in rounds if item["status"] == "completed"]
            completed_onboarding = [
                item for item in completed if int(item["round_index"]) < TASTE_ROUND_COUNT
            ]
            if mode == "onboarding" and len(completed_onboarding) >= TASTE_ROUND_COUNT:
                profile = repository.get_taste_profile(connection)
                examples = repository.list_preference_examples(connection)
                return {
                    "round": None,
                    "profile": _taste_payload(profile, examples, rounds),
                }
            pending = next((item for item in rounds if item["status"] == "pending"), None)
            if pending and pending["album_id"] == album_id:
                return {
                    "round": _taste_round_payload(connection, pending),
                    "profile": _taste_payload(
                        repository.get_taste_profile(connection),
                        repository.list_preference_examples(connection),
                        rounds,
                    ),
                }
            if pending:
                discarded_paths = repository.discard_pending_taste_round(connection)
                for path in discarded_paths:
                    Path(path).unlink(missing_ok=True)
                rounds = repository.list_taste_rounds(connection)
            used = {str(asset_uuid) for item in rounds for asset_uuid in item["candidate_uuids"]}
            for example in repository.list_preference_examples(connection):
                used.add(str(example["left_uuid"]))
                used.add(str(example["right_uuid"]))
            round_index = max((int(item["round_index"]) for item in rounds), default=-1) + 1

        self._taste_progress("loading", 0, TASTE_ROUND_SIZE)
        sampler = getattr(self.provider, "sample_assets", None)
        if sampler:
            sampled = sampler(album_id, limit=16, excluded_uuids=used)
        else:
            sampled = [
                asset for asset in self.provider.list_assets(album_id) if asset.uuid not in used
            ]
            sampled.sort(key=lambda asset: (asset.taken_at or "", asset.uuid), reverse=True)
            sampled = sampled[:16]
        candidates = [
            asset
            for asset in sampled
            if is_supported_photo(asset)
            and not asset.hidden
            and not asset.is_missing
            and asset.source_path
            and asset.source_path.is_file()
        ]
        if len(candidates) < TASTE_ROUND_SIZE:
            raise NativeWorkerError(
                "В этом альбоме недостаточно новых доступных фотографий; выберите другой"
            )
        vision_engine = getattr(self.coordinator, "vision_engine", None)
        if vision_engine is None:
            raise NativeWorkerError("Apple Vision недоступен для настройки вкуса")
        report = vision_engine.analyze([(asset.uuid, asset.source_path) for asset in candidates])
        rows = report.get("assets")
        engine = report.get("engine")
        if not isinstance(rows, list) or not isinstance(engine, dict):
            raise NativeWorkerError("Apple Vision вернул некорректный результат")
        by_uuid = {
            str(row["asset_uuid"]): row
            for row in rows
            if isinstance(row, dict) and row.get("asset_uuid")
        }
        prepared = []
        with database_connection(self.paths.database) as connection:
            for asset in candidates:
                row = by_uuid.get(asset.uuid)
                value = row.get("feature_print") if row else None
                if not isinstance(value, dict):
                    continue
                signal = {
                    "status": "ready",
                    "value": value,
                    "engine_name": str(engine.get("name") or "apple-vision-native"),
                    "engine_version": str(engine.get("version") or "unknown"),
                    "request_revision": value.get("revision"),
                }
                try:
                    schema, _, encoded = feature_vector(signal)
                except ValueError:
                    continue
                repository.upsert_taste_asset(
                    connection,
                    asset_uuid=asset.uuid,
                    album_id=album_id,
                    filename=asset.current_filename or asset.original_filename,
                    taken_at=asset.taken_at,
                    review_path=str(self._persist_taste_render(asset.uuid, asset.source_path)),
                    feature_schema=schema,
                    feature_base64=encoded,
                )
                prepared.append(asset.uuid)
                self._taste_progress(
                    "vision",
                    min(len(prepared), TASTE_ROUND_SIZE),
                    TASTE_ROUND_SIZE,
                )
                if len(prepared) == TASTE_ROUND_SIZE:
                    break
            if len(prepared) != TASTE_ROUND_SIZE:
                raise NativeWorkerError(
                    "Не удалось подготовить 10 совместимых фотографий; выберите другой альбом"
                )
            round_value = repository.create_taste_round(
                connection,
                album_id=album_id,
                album_name=album.name,
                round_index=round_index,
                candidate_uuids=prepared,
            )
            profile = repository.get_taste_profile(connection)
            examples = repository.list_preference_examples(connection)
            rounds = repository.list_taste_rounds(connection)
            return {
                "round": _taste_round_payload(connection, round_value),
                "profile": _taste_payload(profile, examples, rounds),
            }

    def _handle_taste_round_submit(self, params: dict[str, object]) -> dict[str, object]:
        round_id = _required_string(params, "round_id")
        raw_selected = params.get("selected_uuids")
        raw_rejected = params.get("rejected_uuids")
        if not isinstance(raw_selected, list) or not all(
            isinstance(value, str) and value for value in raw_selected
        ):
            raise NativeWorkerError("selected_uuids must be a string array")
        selected = list(dict.fromkeys(raw_selected))
        if not isinstance(raw_rejected, list) or not all(
            isinstance(value, str) and value for value in raw_rejected
        ):
            raise NativeWorkerError("rejected_uuids must be a string array")
        rejected = list(dict.fromkeys(raw_rejected))
        if len(selected) != TASTE_ROUND_SELECTIONS:
            raise NativeWorkerError("Нужно выбрать ровно 3 фотографии")
        if len(rejected) != TASTE_ROUND_REJECTIONS:
            raise NativeWorkerError("Нужно отметить ровно 3 неподходящие фотографии")
        with database_connection(self.paths.database) as connection:
            rounds = repository.list_taste_rounds(connection)
            round_value = next((item for item in rounds if item["id"] == round_id), None)
            if not round_value:
                raise NativeWorkerError("Раунд настройки больше недоступен")
            candidates = [str(value) for value in round_value["candidate_uuids"]]
            if not set(selected).issubset(candidates):
                raise NativeWorkerError("Выбрана фотография вне текущего раунда")
            if not set(rejected).issubset(candidates):
                raise NativeWorkerError("Отмечена фотография вне текущего раунда")
            if set(selected).intersection(rejected):
                raise NativeWorkerError("Фотография не может одновременно нравиться и не нравиться")
            assets = repository.taste_assets(connection, candidates)
            if set(assets) != set(candidates):
                raise NativeWorkerError("Данные фотографий раунда неполны")
            schemas = {str(asset["feature_schema"]) for asset in assets.values()}
            if len(schemas) != 1:
                raise NativeWorkerError("Feature schema фотографий раунда не совпадает")
            if not any(item["status"] == "completed" for item in rounds):
                repository.delete_incompatible_preference_examples(
                    connection,
                    schemas.pop(),
                )
            round_index = int(round_value["round_index"])
            split = "held_out" if round_index == TASTE_ROUND_COUNT - 1 else "calibration"
            for preferred_uuid in selected:
                for rejected_uuid in rejected:
                    preferred = assets[preferred_uuid]
                    other = assets[rejected_uuid]
                    capture_preference_vectors(
                        connection,
                        left_uuid=preferred_uuid,
                        right_uuid=rejected_uuid,
                        preferred_uuid=preferred_uuid,
                        left_feature_schema=str(preferred["feature_schema"]),
                        left_feature_base64=str(preferred["feature_base64"]),
                        right_feature_schema=str(other["feature_schema"]),
                        right_feature_base64=str(other["feature_base64"]),
                        split=split,
                    )
            repository.complete_taste_round(connection, round_id, selected, rejected)
            rounds = repository.list_taste_rounds(connection)
            completed_onboarding = [
                item
                for item in rounds
                if item["status"] == "completed" and int(item["round_index"]) < TASTE_ROUND_COUNT
            ]
            calibration = [
                item
                for item in repository.list_preference_examples(connection)
                if item["split"] == "calibration"
            ]
            if len(calibration) >= MIN_CALIBRATION_PAIRS:
                profile = train_taste_profile(connection)
                if len(completed_onboarding) >= TASTE_ROUND_COUNT:
                    profile = repository.update_taste_evidence(
                        connection,
                        {
                            "onboarding_version": TASTE_ONBOARDING_VERSION,
                            "onboarding_rounds": len(completed_onboarding),
                        },
                    )
            else:
                profile = repository.get_taste_profile(connection)
            examples = repository.list_preference_examples(connection)
        return {
            "round": None,
            "profile": _taste_payload(profile, examples, rounds),
        }

    def _handle_taste_round_cancel(self, params: dict[str, object]) -> dict[str, object]:
        del params
        with database_connection(self.paths.database) as connection:
            discarded_paths = repository.discard_pending_taste_round(connection)
            profile = repository.get_taste_profile(connection)
            examples = repository.list_preference_examples(connection)
            rounds = repository.list_taste_rounds(connection)
        for path in discarded_paths:
            Path(path).unlink(missing_ok=True)
        return {
            "status": "cancelled",
            "profile": _taste_payload(profile, examples, rounds),
        }

    def _taste_progress(self, phase: str, processed: int, total: int) -> None:
        progress = getattr(self._dispatch_state, "progress", None)
        if progress:
            progress(
                {
                    "kind": "taste_progress",
                    "phase": phase,
                    "processed": processed,
                    "total": total,
                }
            )

    def _persist_taste_render(self, asset_uuid: str, source_path: Path) -> Path:
        destination_dir = self.paths.data_dir / "taste-onboarding"
        destination_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        name = hashlib.sha256(asset_uuid.encode()).hexdigest() + ".jpg"
        destination = destination_dir / name
        if not destination.is_file():
            shutil.copy2(source_path, destination)
        return destination

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
            rounds = repository.list_taste_rounds(connection)
        return {
            "example_id": example_id,
            "split": split,
            "profile": _taste_payload(profile, examples, rounds),
        }

    def _handle_taste_train(self, params: dict[str, object]) -> dict[str, object]:
        del params
        with database_connection(self.paths.database) as connection:
            profile = train_taste_profile(connection)
            examples = repository.list_preference_examples(connection)
            rounds = repository.list_taste_rounds(connection)
        return _taste_payload(profile, examples, rounds)

    def _handle_taste_status(self, params: dict[str, object]) -> dict[str, object]:
        paused = params.get("paused")
        if not isinstance(paused, bool):
            raise NativeWorkerError("paused must be boolean")
        with database_connection(self.paths.database) as connection:
            profile = repository.set_taste_profile_paused(connection, paused)
            examples = repository.list_preference_examples(connection)
            rounds = repository.list_taste_rounds(connection)
        return _taste_payload(profile, examples, rounds)

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
        shutil.rmtree(self.paths.data_dir / "taste-onboarding", ignore_errors=True)
        return {"status": "deleted"}

    def _handle_quality_export(self, params: dict[str, object]) -> dict[str, object]:
        project_id = _required_string(params, "project_id")
        with database_connection(self.paths.database) as connection:
            return build_database_quality_evidence(connection, project_id)

    def _handle_quality_status(self, params: dict[str, object]) -> dict[str, object]:
        evidence = self._handle_quality_export(params)
        return dict(evidence["summary"])

    def _handle_quality_candidates(self, params: dict[str, object]) -> dict[str, object]:
        project_id = _required_string(params, "project_id")
        raw_limit = params.get("limit", 75)
        if (
            not isinstance(raw_limit, int)
            or isinstance(raw_limit, bool)
            or not 1 <= raw_limit <= 100
        ):
            raise NativeWorkerError("limit must be between 1 and 100")
        with database_connection(self.paths.database) as connection:
            project = repository.get_project(connection, project_id)
            if project["state"] != "ready":
                raise NativeWorkerError("Мастер доступен после завершения анализа")
            assets = repository.list_assets(connection, project_id)
        candidates = _quality_candidates(project_id, assets, raw_limit)
        labelled = sum(
            candidate.get("quality_expected_disposition") in {"keep", "review", "reject"}
            for candidate in candidates
        )
        return {
            "items": [_quality_asset_payload(asset) for asset in candidates],
            "requested": raw_limit,
            "available": len(candidates),
            "labelled": labelled,
        }

    def _handle_quality_label(self, params: dict[str, object]) -> dict[str, object]:
        project_id = _required_string(params, "project_id")
        asset_uuid = _required_string(params, "asset_uuid")
        disposition = _required_string(params, "disposition")
        raw_codes = params.get("defect_codes", [])
        if not isinstance(raw_codes, list) or not all(
            isinstance(value, str) for value in raw_codes
        ):
            raise NativeWorkerError("defect_codes must be a string array")
        raw_severity = params.get("defect_severity")
        if raw_severity is not None and (
            not isinstance(raw_severity, int) or isinstance(raw_severity, bool)
        ):
            raise NativeWorkerError("defect_severity must be an integer")
        raw_confidence = params.get("defect_confidence")
        if raw_confidence is not None and (
            not isinstance(raw_confidence, (int, float)) or isinstance(raw_confidence, bool)
        ):
            raise NativeWorkerError("defect_confidence must be a number")
        note = params.get("note")
        if note is not None and not isinstance(note, str):
            raise NativeWorkerError("note must be a string")
        with database_connection(self.paths.database) as connection:
            repository.set_quality_label(
                connection,
                project_id,
                asset_uuid,
                disposition=disposition,
                defect_codes=raw_codes,
                defect_severity=raw_severity,
                defect_confidence=float(raw_confidence) if raw_confidence is not None else None,
                note=note,
            )
            asset = repository.get_asset(connection, project_id, asset_uuid)
        return _quality_asset_payload(asset)

    def _handle_quality_pair(self, params: dict[str, object]) -> dict[str, object]:
        project_id = _required_string(params, "project_id")
        with database_connection(self.paths.database) as connection:
            project = repository.get_project(connection, project_id)
            if project["state"] != "ready":
                raise NativeWorkerError("Мастер доступен после завершения анализа")
            assets = repository.list_assets(connection, project_id)
            examples = repository.list_quality_preference_examples(connection, project_id)
        candidates = [
            asset
            for asset in _quality_candidates(project_id, assets, 100)
            if bool(asset.get("quality_lab_sampled"))
            and asset.get("quality_expected_disposition") in {"keep", "review", "reject"}
        ]
        pair, remaining = _next_quality_pair(project_id, candidates, examples)
        return {
            "pair": (
                {
                    "left": _quality_asset_payload(pair[0]),
                    "right": _quality_asset_payload(pair[1]),
                }
                if pair
                else None
            ),
            "completed": len(examples),
            "eligible": len(candidates),
            "remaining": remaining,
        }

    def _handle_quality_preference(self, params: dict[str, object]) -> dict[str, object]:
        project_id = _required_string(params, "project_id")
        with database_connection(self.paths.database) as connection:
            example_id = repository.add_quality_preference_example(
                connection,
                project_id=project_id,
                left_uuid=_required_string(params, "left_uuid"),
                right_uuid=_required_string(params, "right_uuid"),
                preferred_uuid=_required_string(params, "preferred_uuid"),
            )
            completed = len(repository.list_quality_preference_examples(connection, project_id))
        return {"example_id": example_id, "split": "held_out", "completed": completed}

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
        project_id = _required_string(params, "project_id")
        with self.coordinator.project_operation(project_id):
            if self.coordinator.is_running(project_id):
                raise NativeWorkerError("Сначала остановите выполняющийся анализ")
            publish = self.publisher.dry_run(project_id, str(params.get("kind") or "best"))
            uuid_file = Path(str(publish["uuid_file"]))
            asset_uuids = [
                line for line in uuid_file.read_text(encoding="utf-8").splitlines() if line
            ]
            with database_connection(self.paths.database) as connection:
                assets = repository.assets_by_uuid(connection, project_id, set(asset_uuids))
            payload = _publish_payload(publish)
            payload["asset_uuids"] = asset_uuids
            payload["asset_set_sha256"] = hashlib.sha256(
                ("\n".join(asset_uuids) + "\n").encode()
            ).hexdigest()
            payload["items"] = [
                _related_asset_payload(assets[asset_uuid])
                for asset_uuid in asset_uuids
                if asset_uuid in assets
            ]
            return payload

    def _handle_publish_apply(self, params: dict[str, object]) -> dict[str, object]:
        if params.get("confirmed") is not True:
            raise NativeWorkerError("Publish apply requires confirmed=true")
        publish_id = _required_string(params, "publish_id")
        with database_connection(self.paths.database) as connection:
            publish = repository.get_publish(connection, publish_id)
        project_id = str(publish["project_id"])
        with self.coordinator.project_operation(project_id):
            if self.coordinator.is_running(project_id):
                raise NativeWorkerError("Сначала остановите выполняющийся анализ")
            return _publish_payload(
                self.publisher.apply(
                    publish_id,
                    progress=(
                        self._publish_progress
                        if getattr(self._dispatch_state, "progress", None)
                        else None
                    ),
                )
            )

    def _publish_progress(self, phase: str, processed: int, total: int) -> None:
        progress = getattr(self._dispatch_state, "progress", None)
        if progress:
            progress(
                {
                    "kind": "publish_progress",
                    "phase": phase,
                    "processed": processed,
                    "total": total,
                }
            )


def run_native_worker(
    paths: ApplicationPaths,
    *,
    input_stream: TextIO = sys.stdin,
    output_stream: TextIO = sys.stdout,
    worker: NativeWorker | None = None,
    demo: bool = False,
) -> int:
    with _exclusive_worker_lock(paths):
        return _run_native_worker_locked(
            paths,
            input_stream=input_stream,
            output_stream=output_stream,
            worker=worker,
            demo=demo,
        )


def _run_native_worker_locked(
    paths: ApplicationPaths,
    *,
    input_stream: TextIO,
    output_stream: TextIO,
    worker: NativeWorker | None,
    demo: bool,
) -> int:
    if worker is None:
        if demo:
            from photo_curator.photos.demo_publisher import DemoPhotosPublisher
            from photo_curator.photos.fake_provider import FakePhotosProvider

            base = FakePhotosProvider(paths.cache_dir / "demo-sources")
        else:
            base = PhotoKitProvider.from_environment(paths)
            if base is None:
                raise RuntimeError(
                    "Встроенный PhotoKit source helper отсутствует; переустановите Photo Curator"
                )
        provider = LocalAlbumsProvider(base, paths.data_dir / "local_albums")
        publisher = DemoPhotosPublisher(database_path=paths.database, paths=paths) if demo else None
        worker = NativeWorker(paths, provider=provider, publisher=publisher)
    output_lock = Lock()

    def emit(frame: dict[str, object]) -> None:
        with output_lock:
            output_stream.write(json.dumps(frame, ensure_ascii=False) + "\n")
            output_stream.flush()

    def execute(request: dict[str, object]) -> None:
        request_id: object = request.get("id")
        try:
            method = _required_string(request, "method")
            params = request.get("params") or {}
            if not isinstance(params, dict):
                raise NativeWorkerError("params must be an object")

            def send_progress(
                event: dict[str, object], correlation_id: object = request_id
            ) -> None:
                emit({"schema_version": 1, "id": correlation_id, "event": event})

            result = worker.dispatch(method, params, progress=send_progress)
            response = {"schema_version": 1, "id": request_id, "result": result}
        except Exception as error:
            response = {
                "schema_version": 1,
                "id": request_id,
                "error": {"type": type(error).__name__, "message": str(error)[-1000:]},
            }
        emit(response)

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="native-worker") as executor:
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
                if method == "shutdown":
                    emit(
                        {
                            "schema_version": 1,
                            "id": request_id,
                            "result": {"status": "bye"},
                        }
                    )
                    break
                executor.submit(execute, request)
            except Exception as error:
                emit(
                    {
                        "schema_version": 1,
                        "id": request_id,
                        "error": {
                            "type": type(error).__name__,
                            "message": str(error)[-1000:],
                        },
                    }
                )
    return 0


def _remove_project_cache(paths: ApplicationPaths, project_id: str) -> None:
    for root in (paths.cache_dir, paths.project_artifacts_dir):
        resolved_root = root.resolve()
        project_cache = (resolved_root / project_id).resolve()
        if project_cache.parent != resolved_root:
            raise NativeWorkerError("Некорректный путь кэша проекта")
        if project_cache.exists():
            shutil.rmtree(project_cache)


def _clear_cache(paths: ApplicationPaths) -> None:
    cache_root = paths.cache_dir.resolve()
    if not cache_root.exists():
        return
    for child in cache_root.iterdir():
        resolved = child.resolve()
        if resolved.parent != cache_root:
            raise NativeWorkerError("Некорректный путь внутри кэша")
        if resolved.is_dir():
            shutil.rmtree(resolved)
        else:
            resolved.unlink()


def _cleanup_transient_cache(paths: ApplicationPaths) -> None:
    cache_root = paths.cache_dir.resolve()
    if not cache_root.exists():
        return
    native_requests = (cache_root / "_native_vision").resolve()
    if native_requests.parent == cache_root and native_requests.is_dir():
        shutil.rmtree(native_requests)
    for pattern in (".preview-*", ".*.sips.jpg"):
        for candidate in cache_root.rglob(pattern):
            resolved = candidate.resolve()
            if resolved.is_relative_to(cache_root) and resolved.is_file():
                resolved.unlink()


def _required_string(values: dict[str, object], key: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value:
        raise NativeWorkerError(f"{key} is required")
    return value


def _boolean_param(values: dict[str, object], key: str, *, default: bool) -> bool:
    value = values.get(key, default)
    if not isinstance(value, bool):
        raise NativeWorkerError(f"{key} must be a boolean")
    return value


def _quality_candidates(
    project_id: str,
    assets: list[dict[str, object]],
    limit: int,
) -> list[dict[str, object]]:
    candidates = [
        asset
        for asset in assets
        if not asset.get("no_longer_exists")
        and asset.get("cache_state") == "ready"
        and _existing_file(asset.get("review_path"))
        and asset.get("final_disposition") in {"keep", "review", "reject"}
    ]
    candidates.sort(
        key=lambda asset: hashlib.sha256(
            f"quality-v1|{project_id}|{asset['asset_uuid']}".encode()
        ).hexdigest()
    )
    return candidates[:limit]


def _existing_file(path: object) -> bool:
    return isinstance(path, str) and bool(path) and Path(path).is_file()


def _unavailable_preview_files(assets: list[dict[str, object]]) -> int:
    return sum(
        not asset.get("no_longer_exists")
        and (
            not _existing_file(asset.get("review_path"))
            or not _existing_file(asset.get("thumbnail_path"))
        )
        for asset in assets
    )


def _quality_asset_payload(asset: dict[str, object]) -> dict[str, object]:
    """Blind quality-lab payload: never reveal engine scores, reasons or predictions."""
    raw_codes = asset.get("quality_defect_codes")
    if raw_codes is None:
        try:
            raw_codes = json.loads(str(asset.get("quality_defect_codes_json") or "[]"))
        except json.JSONDecodeError:
            raw_codes = []
    return {
        "payload_schema_version": 1,
        "asset_uuid": asset["asset_uuid"],
        "filename": asset.get("current_filename"),
        "thumbnail_path": asset.get("thumbnail_path"),
        "review_path": asset.get("review_path"),
        "cache_state": asset.get("cache_state"),
        "quality_disposition": asset.get("quality_expected_disposition"),
        "quality_top_k_rank": asset.get("quality_top_k_rank"),
        "quality_duplicate_group": asset.get("quality_duplicate_group"),
        "quality_expected_leader": bool(asset.get("quality_expected_leader")),
        "quality_defect_codes": list(raw_codes or []),
        "quality_defect_severity": asset.get("quality_defect_severity"),
        "quality_defect_confidence": asset.get("quality_defect_confidence"),
        "quality_note": asset.get("quality_note"),
        "quality_lab_sampled": bool(asset.get("quality_lab_sampled")),
    }


def _next_quality_pair(
    project_id: str,
    candidates: list[dict[str, object]],
    examples: list[dict[str, object]],
) -> tuple[tuple[dict[str, object], dict[str, object]] | None, int]:
    seen = {
        tuple(sorted((str(example["left_uuid"]), str(example["right_uuid"]))))
        for example in examples
    }
    pairs: list[tuple[str, dict[str, object], dict[str, object]]] = []
    for index, left in enumerate(candidates):
        for right in candidates[index + 1 : index + 7]:
            key = tuple(sorted((str(left["asset_uuid"]), str(right["asset_uuid"]))))
            if key in seen:
                continue
            digest = hashlib.sha256(
                f"quality-pair-v1|{project_id}|{key[0]}|{key[1]}".encode()
            ).hexdigest()
            pairs.append((digest, left, right))
    if not pairs:
        return None, 0
    pairs.sort(key=lambda value: value[0])
    _, left, right = pairs[0]
    return (left, right), len(pairs)


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
    profile: dict[str, object],
    examples: list[dict[str, object]],
    rounds: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    rounds = rounds or []
    calibration_count = sum(example["split"] == "calibration" for example in examples)
    held_out_count = sum(example["split"] == "held_out" for example in examples)
    completed_rounds = sum(
        item["status"] == "completed" and int(item["round_index"]) < TASTE_ROUND_COUNT
        for item in rounds
    )
    evidence = profile.get("evidence") or {}
    return {
        "id": profile["id"],
        "name": profile["name"],
        "status": profile["status"],
        "model_version": profile.get("model_version"),
        "training_examples": profile.get("training_examples") or 0,
        "preference_count": len(examples),
        "calibration_count": calibration_count,
        "held_out_count": held_out_count,
        "onboarding_version": int(evidence.get("onboarding_version") or 0),
        "onboarding_rounds_completed": completed_rounds,
        "onboarding_rounds_total": TASTE_ROUND_COUNT,
        "onboarding_complete": (
            int(evidence.get("onboarding_version") or 0) >= TASTE_ONBOARDING_VERSION
            and completed_rounds >= TASTE_ROUND_COUNT
            and profile["status"] in {"ready", "paused", "stale"}
        ),
        "evidence": evidence,
    }


def _taste_round_payload(
    connection,
    round_value: dict[str, object],
) -> dict[str, object]:
    candidate_uuids = [str(value) for value in round_value["candidate_uuids"]]
    assets = repository.taste_assets(connection, candidate_uuids)
    return {
        "id": round_value["id"],
        "round_index": round_value["round_index"],
        "round_number": int(round_value["round_index"]) + 1,
        "round_total": TASTE_ROUND_COUNT,
        "album_id": round_value["album_id"],
        "album_name": round_value["album_name"],
        "selection_limit": TASTE_ROUND_SELECTIONS,
        "rejection_limit": TASTE_ROUND_REJECTIONS,
        "is_adjustment": int(round_value["round_index"]) >= TASTE_ROUND_COUNT,
        "photos": [
            {
                "asset_uuid": asset_uuid,
                "filename": assets[asset_uuid].get("filename") or asset_uuid,
                "review_path": assets[asset_uuid]["review_path"],
            }
            for asset_uuid in candidate_uuids
            if asset_uuid in assets
        ],
    }


def _next_preference_split(examples: list[dict[str, object]]) -> str:
    calibration_count = sum(example["split"] == "calibration" for example in examples)
    if calibration_count < 3:
        return "calibration"
    post_warmup_count = max(0, len(examples) - 3)
    return "held_out" if post_warmup_count % 2 == 0 else "calibration"


@contextmanager
def _exclusive_worker_lock(paths: ApplicationPaths):
    data_dir = paths.data_dir if isinstance(paths, ApplicationPaths) else Path(paths)
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = data_dir / ".native-worker.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(
                "Photo Curator уже запущен; второй процесс не получил доступ к каталогу"
            ) from error
        os.ftruncate(descriptor, 0)
        os.write(descriptor, f"{os.getpid()}\n".encode())
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _append_destructive_audit(
    paths: ApplicationPaths,
    *,
    action: str,
    status: str,
    project_id: str,
    backup_path: str | None = None,
) -> None:
    paths.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    audit_path = paths.data_dir / "destructive-actions.jsonl"
    record = {
        "timestamp": datetime.now(UTC).isoformat(),
        "pid": os.getpid(),
        "action": action,
        "status": status,
        "project_id": project_id,
    }
    if backup_path:
        record["backup_path"] = backup_path
    descriptor = os.open(audit_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(descriptor, (json.dumps(record, ensure_ascii=False) + "\n").encode())
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _append_photokit_acceptance_audit(
    paths: ApplicationPaths,
    result: dict[str, object],
) -> None:
    paths.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    audit_path = paths.data_dir / "photokit-acceptance.jsonl"
    record = {
        "timestamp": datetime.now(UTC).isoformat(),
        "pid": os.getpid(),
        **result,
    }
    descriptor = os.open(audit_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(descriptor, (json.dumps(record, ensure_ascii=False) + "\n").encode())
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
