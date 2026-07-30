from __future__ import annotations

import hashlib
import json
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from photo_curator.acceptance import build_native_quality_evidence, evaluate_acceptance
from photo_curator.analysis.native_vision import NativeVisionEngine
from photo_curator.analysis.taste import (
    capture_preference,
    capture_preference_vectors,
    feature_vector,
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

WORKER_SCHEMA_VERSION = 2
TASTE_ONBOARDING_VERSION = 2
TASTE_ROUND_COUNT = 3
TASTE_ROUND_SIZE = 10
TASTE_ROUND_SELECTIONS = 3


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
            legacy_cli_enabled=False,
        )
        self._progress_callback: Callable[[dict[str, object]], None] | None = None
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
        previous = self._progress_callback
        self._progress_callback = progress
        try:
            return handler(params)
        finally:
            self._progress_callback = previous

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

    def _handle_delete_project(self, params: dict[str, object]) -> dict[str, object]:
        project_id = _required_string(params, "project_id")
        with database_connection(self.paths.database) as connection:
            project = repository.get_project(connection, project_id)
            if project["state"] == "running":
                raise NativeWorkerError("Сначала остановите выполняющийся анализ")
            repository.delete_project(connection, project_id)
        _remove_project_cache(self.paths, project_id)
        return {"status": "deleted", "project_id": project_id}

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
        disposition = params.get("disposition")
        if disposition is not None and disposition not in {"keep", "review", "reject"}:
            raise NativeWorkerError("Unknown disposition filter")
        with database_connection(self.paths.database) as connection:
            assets = repository.list_assets(connection, project_id)
            duplicate_context = repository.duplicate_context(connection, project_id)
        if disposition is not None:
            assets = [asset for asset in assets if asset.get("final_disposition") == disposition]
        for asset in assets:
            asset["duplicate_context"] = duplicate_context.get(str(asset["asset_uuid"]), {})
        assets.sort(
            key=lambda asset: (
                -float(asset.get("swipe_score") or -1),
                str(asset["asset_uuid"]),
            )
        )
        return {
            "items": [_asset_payload(asset) for asset in assets[offset : offset + limit]],
            "total": len(assets),
            "offset": offset,
        }

    def _handle_decision(self, params: dict[str, object]) -> dict[str, object]:
        project_id = _required_string(params, "project_id")
        asset_uuid = _required_string(params, "asset_uuid")
        disposition = params.get("disposition")
        if disposition not in {None, "keep", "reject"}:
            raise NativeWorkerError("Unknown disposition")
        note = str(params["note"])[:1000] if params.get("note") is not None else None
        with database_connection(self.paths.database) as connection:
            repository.set_manual_decision(connection, project_id, asset_uuid, disposition, note)
            asset = repository.get_asset(connection, project_id, asset_uuid)
            asset["duplicate_context"] = repository.duplicate_context(connection, project_id).get(
                asset_uuid, {}
            )
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
            if len(completed) >= TASTE_ROUND_COUNT:
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
            round_index = len(completed)

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
            if asset.is_photo
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
        if not isinstance(raw_selected, list) or not all(
            isinstance(value, str) and value for value in raw_selected
        ):
            raise NativeWorkerError("selected_uuids must be a string array")
        selected = list(dict.fromkeys(raw_selected))
        if len(selected) != TASTE_ROUND_SELECTIONS:
            raise NativeWorkerError("Нужно выбрать ровно 3 фотографии")
        with database_connection(self.paths.database) as connection:
            rounds = repository.list_taste_rounds(connection)
            round_value = next((item for item in rounds if item["id"] == round_id), None)
            if not round_value:
                raise NativeWorkerError("Раунд настройки больше недоступен")
            candidates = [str(value) for value in round_value["candidate_uuids"]]
            if not set(selected).issubset(candidates):
                raise NativeWorkerError("Выбрана фотография вне текущего раунда")
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
            rejected = [value for value in candidates if value not in selected]
            for selected_index, preferred_uuid in enumerate(selected):
                for rejected_index, rejected_uuid in enumerate(rejected):
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
                        split=("held_out" if rejected_index == selected_index else "calibration"),
                    )
            repository.complete_taste_round(connection, round_id, selected)
            rounds = repository.list_taste_rounds(connection)
            completed = [item for item in rounds if item["status"] == "completed"]
            if len(completed) >= TASTE_ROUND_COUNT:
                profile = train_taste_profile(connection)
                profile = repository.update_taste_evidence(
                    connection,
                    {
                        "onboarding_version": TASTE_ONBOARDING_VERSION,
                        "onboarding_rounds": len(completed),
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
        if self._progress_callback:
            self._progress_callback(
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
        return _publish_payload(
            self.publisher.apply(
                _required_string(params, "publish_id"),
                progress=self._publish_progress if self._progress_callback else None,
            )
        )

    def _publish_progress(self, phase: str, processed: int, total: int) -> None:
        if self._progress_callback:
            self._progress_callback(
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

            def send_progress(
                event: dict[str, object], correlation_id: object = request_id
            ) -> None:
                frame = {
                    "schema_version": 1,
                    "id": correlation_id,
                    "event": event,
                }
                output_stream.write(json.dumps(frame, ensure_ascii=False) + "\n")
                output_stream.flush()

            result = worker.dispatch(method, params, progress=send_progress)
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


def _remove_project_cache(paths: ApplicationPaths, project_id: str) -> None:
    cache_root = paths.cache_dir.resolve()
    project_cache = (cache_root / project_id).resolve()
    if project_cache.parent != cache_root:
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
    profile: dict[str, object],
    examples: list[dict[str, object]],
    rounds: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    rounds = rounds or []
    calibration_count = sum(example["split"] == "calibration" for example in examples)
    held_out_count = sum(example["split"] == "held_out" for example in examples)
    completed_rounds = sum(item["status"] == "completed" for item in rounds)
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


def _publish_payload(publish: dict[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in publish.items()
        if key
        not in {"uuid_file", "dry_run_stdout", "dry_run_stderr", "apply_stdout", "apply_stderr"}
    }
