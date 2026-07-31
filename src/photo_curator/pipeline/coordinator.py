from __future__ import annotations

import json
import logging
import os
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Event, Lock, Semaphore

from photo_curator.analysis.decision_engine import (
    DECISION_MODEL_VERSION,
    DecisionResult,
    album_selection_threshold,
    decide_asset,
)
from photo_curator.analysis.diversity import diversity_evidence
from photo_curator.analysis.hashes import color_histogram, dhash, phash, render_equivalence_hash
from photo_curator.analysis.image_loader import load_normalized
from photo_curator.analysis.native_vision import NativeVisionEngine, NativeVisionError
from photo_curator.analysis.normalization import percentile_ranks
from photo_curator.analysis.swipe_score import apple_score_percentiles, calculate_swipe_score
from photo_curator.analysis.taste import TasteProfileError, compatible_taste_model
from photo_curator.analysis.technical import technical_metrics
from photo_curator.db import repository
from photo_curator.db.connection import database_connection
from photo_curator.paths import ApplicationPaths
from photo_curator.photos.provider import PhotosProvider
from photo_curator.photos.render_resolver import resolve_source_render
from photo_curator.pipeline.duplicates import find_duplicate_groups
from photo_curator.pipeline.previews import build_previews, source_fingerprint

LOGGER = logging.getLogger(__name__)

STAGES = ("inventory", "previews", "metrics", "duplicates", "vision", "decisions")
PREVIEW_LIMIT = Semaphore(2)


class PipelineCancelled(RuntimeError):
    pass


class PipelineCoordinator:
    def __init__(
        self,
        *,
        database_path: Path,
        paths: ApplicationPaths,
        provider: PhotosProvider,
        vision_engine: NativeVisionEngine | None = None,
    ) -> None:
        self.database_path = database_path
        self.paths = paths
        self.provider = provider
        self.vision_engine = vision_engine
        self._executor = ThreadPoolExecutor(
            max_workers=min(4, os.cpu_count() or 2), thread_name_prefix="photo-curator"
        )
        self._futures: dict[str, Future[None]] = {}
        self._cancel_events: dict[str, Event] = {}
        self._lock = Lock()

    def start(self, project_id: str, from_stage: str | None = None) -> str:
        with self._lock:
            current = self._futures.get(project_id)
            if current and not current.done():
                raise RuntimeError("Pipeline is already running")
            self._cancel_events[project_id] = Event()
            future = self._executor.submit(self.run, project_id, from_stage=from_stage)
            self._futures[project_id] = future
        return project_id

    def is_running(self, project_id: str) -> bool:
        """Return whether this coordinator still owns a live pipeline."""
        with self._lock:
            current = self._futures.get(project_id)
            return current is not None and not current.done()

    def cancel(self, project_id: str) -> bool:
        with self._lock:
            current = self._futures.get(project_id)
            event = self._cancel_events.get(project_id)
            if not current or current.done() or event is None:
                return False
            event.set()
            return True

    def run(self, project_id: str, *, from_stage: str | None = None) -> None:
        start_index = STAGES.index(from_stage) if from_stage else 0
        with self._lock:
            cancel_event = self._cancel_events.setdefault(project_id, Event())
        try:
            with database_connection(self.database_path) as connection:
                repository.set_project_state(connection, project_id, "running")
            for stage in STAGES[start_index:]:
                self._check_cancelled(project_id)
                LOGGER.info("Stage started project=%s stage=%s", project_id, stage)
                getattr(self, f"_stage_{stage}")(project_id)
                LOGGER.info("Stage completed project=%s stage=%s", project_id, stage)
            with database_connection(self.database_path) as connection:
                repository.set_project_state(connection, project_id, "ready")
        except PipelineCancelled:
            LOGGER.info("Pipeline cancelled project=%s", project_id)
            with database_connection(self.database_path) as connection:
                repository.cancel_running_jobs(connection, project_id)
                repository.set_project_state(connection, project_id, "interrupted")
        except Exception as error:
            LOGGER.exception("Pipeline failed for project %s", project_id)
            with database_connection(self.database_path) as connection:
                repository.fail_running_jobs(connection, project_id, str(error))
                repository.set_project_state(connection, project_id, "error")
            raise
        finally:
            with self._lock:
                if self._cancel_events.get(project_id) is cancel_event:
                    self._cancel_events.pop(project_id, None)

    def _check_cancelled(self, project_id: str) -> None:
        with self._lock:
            event = self._cancel_events.get(project_id)
        if event is not None and event.is_set():
            raise PipelineCancelled("Pipeline cancellation requested")

    def _stage_inventory(self, project_id: str) -> None:
        with database_connection(self.database_path) as connection:
            project = repository.get_project(connection, project_id)
            job_id = repository.create_job(connection, project_id, "inventory", 0)

        def report_progress(processed: int, total: int) -> None:
            self._check_cancelled(project_id)
            with database_connection(self.database_path) as connection:
                connection.execute("UPDATE jobs SET total_items=? WHERE id=?", (total, job_id))
                repository.update_job(
                    connection,
                    job_id,
                    processed=processed,
                    message=f"PhotoKit: подготовлено {processed} из {total}",
                )

        streaming = getattr(self.provider, "list_assets_with_progress", None)
        source_assets = (
            streaming(str(project["album_id"]), report_progress)
            if streaming
            else self.provider.list_assets(str(project["album_id"]))
        )
        assets = [asset for asset in source_assets if asset.is_photo]
        self._check_cancelled(project_id)
        with database_connection(self.database_path) as connection:
            connection.execute("UPDATE jobs SET total_items=? WHERE id=?", (len(assets), job_id))
            repository.upsert_assets(connection, project_id, assets)
            repository.update_job(
                connection,
                job_id,
                status="done",
                processed=len(assets),
                message=f"Инвентаризировано {len(assets)} фото",
            )
            repository.set_project_state(connection, project_id, "running")

    def _stage_previews(self, project_id: str) -> None:
        with database_connection(self.database_path) as connection:
            project = repository.get_project(connection, project_id)
            stored = repository.list_assets(connection, project_id)
            job_id = repository.create_job(connection, project_id, "previews", len(stored))
        current_assets = {
            asset.uuid: asset
            for asset in self.provider.list_assets(str(project["album_id"]))
            if asset.is_photo
        }
        errors = warnings = 0
        cache = self.paths.cache_dir / project_id
        for index, row in enumerate(stored, start=1):
            self._check_cancelled(project_id)
            asset_uuid = str(row["asset_uuid"])
            asset = current_assets.get(asset_uuid)
            render = resolve_source_render(asset) if asset else None
            try:
                if not render or not render.path:
                    warnings += 1
                    with database_connection(self.database_path) as connection:
                        repository.update_asset_preview(
                            connection,
                            project_id,
                            asset_uuid,
                            source_kind="missing",
                            source_size=None,
                            source_mtime=None,
                            source_fingerprint=None,
                            review_path=None,
                            thumbnail_path=None,
                            cache_state="missing",
                            render_warning=render.warning if render else "missing_preview",
                        )
                else:
                    review_path = cache / "review" / f"{asset_uuid}.jpg"
                    thumbnail_path = cache / "thumbnails" / f"{asset_uuid}.jpg"
                    expected_fingerprint = source_fingerprint(render.path, render.kind)
                    if (
                        row.get("cache_state") == "ready"
                        and row.get("source_fingerprint") == expected_fingerprint
                        and review_path.is_file()
                        and thumbnail_path.is_file()
                    ):
                        with database_connection(self.database_path) as connection:
                            repository.update_job(
                                connection,
                                job_id,
                                processed=index,
                                warnings=warnings,
                                errors=errors,
                                message=f"Preview {index} из {len(stored)} · cache",
                            )
                        continue
                    with PREVIEW_LIMIT:
                        result = build_previews(
                            render.path,
                            review_path,
                            thumbnail_path,
                            source_kind=render.kind,
                        )
                    stat = render.path.stat()
                    with database_connection(self.database_path) as connection:
                        if row.get("source_fingerprint") != result.source_fingerprint:
                            repository.invalidate_asset_analysis(connection, project_id, asset_uuid)
                        repository.update_asset_preview(
                            connection,
                            project_id,
                            asset_uuid,
                            source_kind=render.kind,
                            source_size=stat.st_size,
                            source_mtime=stat.st_mtime,
                            source_fingerprint=result.source_fingerprint,
                            review_path=str(result.review_path),
                            thumbnail_path=str(result.thumbnail_path),
                            cache_state="ready",
                            render_warning=render.warning,
                        )
            except Exception:
                errors += 1
                LOGGER.exception("Preview failed for %s", asset_uuid)
                with database_connection(self.database_path) as connection:
                    repository.update_asset_preview(
                        connection,
                        project_id,
                        asset_uuid,
                        source_kind=render.kind if render else "missing",
                        source_size=None,
                        source_mtime=None,
                        source_fingerprint=None,
                        review_path=None,
                        thumbnail_path=None,
                        cache_state="error",
                        render_warning=render.warning if render else "missing_preview",
                    )
            with database_connection(self.database_path) as connection:
                repository.update_job(
                    connection,
                    job_id,
                    processed=index,
                    warnings=warnings,
                    errors=errors,
                    message=f"Preview {index} из {len(stored)}",
                )
        with database_connection(self.database_path) as connection:
            repository.update_job(
                connection,
                job_id,
                status="warning" if errors or warnings else "done",
                processed=len(stored),
                warnings=warnings,
                errors=errors,
            )

    def _stage_metrics(self, project_id: str) -> None:
        with database_connection(self.database_path) as connection:
            assets = repository.list_assets(connection, project_id)
            ready = [asset for asset in assets if asset.get("cache_state") == "ready"]
            job_id = repository.create_job(connection, project_id, "metrics", len(ready))
        pending = [asset for asset in ready if not asset.get("phash")]
        errors = 0
        completed = len(ready) - len(pending)
        for index, asset in enumerate(pending, start=completed + 1):
            self._check_cancelled(project_id)
            try:
                image = load_normalized(Path(str(asset["review_path"])), max_dimension=1024)
                metrics = technical_metrics(image)
                metrics.update(
                    {
                        "dhash": dhash(image),
                        "phash": phash(image),
                        "normalized_pixel_hash": render_equivalence_hash(image),
                        "histogram_json": color_histogram(image),
                    }
                )
                with database_connection(self.database_path) as connection:
                    repository.upsert_metrics(
                        connection, project_id, str(asset["asset_uuid"]), metrics
                    )
            except Exception:
                errors += 1
                LOGGER.exception("Analysis failed for %s", asset["asset_uuid"])
                with database_connection(self.database_path) as connection:
                    repository.set_asset_analysis_error(
                        connection, project_id, str(asset["asset_uuid"])
                    )
            with database_connection(self.database_path) as connection:
                repository.update_job(
                    connection,
                    job_id,
                    processed=index,
                    errors=errors,
                    message=f"Анализ {index} из {len(ready)}",
                )
        self._normalize_metrics(project_id)
        with database_connection(self.database_path) as connection:
            repository.update_job(
                connection,
                job_id,
                status="warning" if errors else "done",
                processed=len(ready),
                errors=errors,
            )

    def _normalize_metrics(self, project_id: str) -> None:
        with database_connection(self.database_path) as connection:
            assets = [
                asset
                for asset in repository.list_assets(connection, project_id)
                if asset.get("phash")
            ]
        sharpness = percentile_ranks([_float(asset.get("laplacian_variance")) for asset in assets])
        gradient = percentile_ranks([_float(asset.get("gradient_energy")) for asset in assets])
        contrast = percentile_ranks([_float(asset.get("contrast_std")) for asset in assets])
        apple = percentile_ranks(
            [_float((asset.get("apple_scores") or {}).get("overall")) for asset in assets]
        )
        values = {}
        for asset, sharp, grad, cont, apple_rank in zip(
            assets, sharpness, gradient, contrast, apple, strict=True
        ):
            quality_values = [value for value in (sharp, grad, cont) if value is not None]
            values[str(asset["asset_uuid"])] = {
                "sharpness_percentile": sharp,
                "gradient_percentile": grad,
                "contrast_percentile": cont,
                "apple_overall_percentile": apple_rank,
                "technical_quality": sum(quality_values) / len(quality_values),
            }
        with database_connection(self.database_path) as connection:
            repository.update_metric_percentiles(connection, project_id, values)

    def _stage_duplicates(self, project_id: str) -> None:
        with database_connection(self.database_path) as connection:
            assets = repository.list_assets(connection, project_id)
            job_id = repository.create_job(connection, project_id, "duplicates", 0)

        def report_progress(processed: int, total: int) -> None:
            self._check_cancelled(project_id)
            with database_connection(self.database_path) as connection:
                connection.execute("UPDATE jobs SET total_items=? WHERE id=?", (total, job_id))
                repository.update_job(
                    connection,
                    job_id,
                    processed=processed,
                    message=f"Сравнение {processed} из {total} пар",
                )

        groups = find_duplicate_groups(assets, progress=report_progress)
        with database_connection(self.database_path) as connection:
            repository.replace_duplicate_groups(connection, project_id, groups)
            total = connection.execute(
                "SELECT total_items FROM jobs WHERE id=?", (job_id,)
            ).fetchone()["total_items"]
            repository.update_job(
                connection,
                job_id,
                status="done",
                processed=total,
                message=f"Найдено групп: {len(groups)}",
            )

    def _stage_vision(self, project_id: str) -> None:
        with database_connection(self.database_path) as connection:
            assets = [
                asset
                for asset in repository.list_assets(connection, project_id)
                if asset.get("cache_state") == "ready" and asset.get("phash")
            ]
            job_id = repository.create_job(connection, project_id, "vision", len(assets))
        if self.vision_engine is not None:
            self._stage_native_vision(project_id, job_id, assets)
            return
        with database_connection(self.database_path) as connection:
            repository.update_job(
                connection,
                job_id,
                status="warning",
                processed=0,
                warnings=1,
                message="Native Apple Vision helper не настроен; сигнал пропущен",
            )

    def _stage_native_vision(
        self, project_id: str, job_id: str, assets: list[dict[str, object]]
    ) -> None:
        try:
            self._check_cancelled(project_id)
            report = self.vision_engine.analyze(
                [(str(asset["asset_uuid"]), Path(str(asset["review_path"]))) for asset in assets]
            )
            self._check_cancelled(project_id)
        except (NativeVisionError, OSError, ValueError) as error:
            LOGGER.exception("Native Vision batch failed for %s", project_id)
            with database_connection(self.database_path) as connection:
                for asset in assets:
                    for signal_kind in (
                        "aesthetics",
                        "feature_print",
                        "attention_saliency",
                        "faces",
                    ):
                        repository.upsert_analysis_signal(
                            connection,
                            project_id,
                            str(asset["asset_uuid"]),
                            signal_kind=signal_kind,
                            schema_version=1,
                            engine_name="apple-vision-native",
                            engine_version="unknown",
                            request_revision=None,
                            source_fingerprint=_optional_string(asset.get("source_fingerprint")),
                            status="unavailable",
                            value=None,
                            duration_ms=None,
                            error_text=str(error)[-1000:],
                        )
                repository.update_job(
                    connection,
                    job_id,
                    status="warning",
                    processed=0,
                    warnings=1,
                    errors=len(assets),
                    message="Apple Vision недоступен; сохранена причина",
                )
            return
        engine = report.get("engine")
        rows = report.get("assets")
        raw_capabilities = report.get("capabilities")
        capabilities = raw_capabilities if isinstance(raw_capabilities, dict) else {}
        if not isinstance(engine, dict) or not isinstance(rows, list):
            raise NativeVisionError("Native Vision report contract нарушен")
        engine_name = str(engine.get("name") or "apple-vision-native")
        engine_version = str(engine.get("version") or "unknown")
        by_uuid = {
            str(row["asset_uuid"]): row
            for row in rows
            if isinstance(row, dict) and row.get("asset_uuid")
        }
        errors = 0
        for index, asset in enumerate(assets, start=1):
            self._check_cancelled(project_id)
            asset_uuid = str(asset["asset_uuid"])
            row = by_uuid[asset_uuid]
            raw_errors = row.get("errors")
            signal_errors = raw_errors if isinstance(raw_errors, dict) else {}
            durations = row.get("durations_ms")
            signal_durations = durations if isinstance(durations, dict) else {}
            asset_has_error = False
            with database_connection(self.database_path) as connection:
                for signal_kind in (
                    "aesthetics",
                    "feature_print",
                    "attention_saliency",
                    "faces",
                ):
                    value = row.get(signal_kind)
                    error_text = signal_errors.get(signal_kind)
                    status = (
                        "ready"
                        if isinstance(value, dict)
                        else "unavailable"
                        if capabilities.get(signal_kind) is False
                        else "error"
                    )
                    if status != "ready":
                        asset_has_error = True
                    repository.upsert_analysis_signal(
                        connection,
                        project_id,
                        asset_uuid,
                        signal_kind=signal_kind,
                        schema_version=1,
                        engine_name=engine_name,
                        engine_version=engine_version,
                        request_revision=_signal_revision(signal_kind, value),
                        source_fingerprint=_optional_string(asset.get("source_fingerprint")),
                        status=status,
                        value=value if isinstance(value, dict) else None,
                        duration_ms=_optional_float(signal_durations.get(signal_kind)),
                        error_text=(
                            str(error_text)
                            if error_text
                            else "Vision signal returned no result"
                            if status != "ready"
                            else None
                        ),
                    )
                faces = row.get("faces")
                if isinstance(faces, dict):
                    repository.update_vision_metrics(
                        connection,
                        project_id,
                        asset_uuid,
                        face_count=int(faces.get("face_count") or 0),
                        face_capture_quality=_optional_float(faces.get("best_capture_quality")),
                        eyes_detected=int(faces.get("eyes_detected") or 0),
                    )
                errors += int(asset_has_error)
                repository.update_job(
                    connection,
                    job_id,
                    processed=index,
                    errors=errors,
                    message=f"Apple Vision {index} из {len(assets)}",
                )
        with database_connection(self.database_path) as connection:
            repository.update_job(
                connection,
                job_id,
                status="warning" if errors else "done",
                processed=len(assets),
                errors=errors,
            )

    def _stage_decisions(self, project_id: str) -> None:
        with database_connection(self.database_path) as connection:
            assets = repository.list_assets(connection, project_id)
            duplicate_by_asset = repository.duplicate_context(connection, project_id)
            signal_by_asset = repository.analysis_signals_by_asset(connection, project_id)
            try:
                taste_model = compatible_taste_model(connection, signal_by_asset)
            except TasteProfileError:
                LOGGER.exception("Taste profile is invalid; generic ranking will be used")
                taste_model = None
            project = repository.get_project(connection, project_id)
            settings = json.loads(str(project.get("settings_json") or "{}"))
            density = str(settings.get("selection_density") or "balanced")
            job_id = repository.create_job(connection, project_id, "decisions", len(assets))
            apple_percentiles = apple_score_percentiles(assets)
            swipe_scores = [
                calculate_swipe_score(
                    asset,
                    duplicate_by_asset.get(str(asset["asset_uuid"])),
                    signal_by_asset.get(str(asset["asset_uuid"]), {}),
                    apple_percentiles=apple_percentiles.get(str(asset["asset_uuid"]), {}),
                    personal_delta=(
                        taste_model.personal_delta(
                            signal_by_asset.get(str(asset["asset_uuid"]), {}).get(
                                "feature_print", {}
                            )
                        )
                        if taste_model
                        else 0.0
                    ),
                    taste_model_version=taste_model.model_version if taste_model else None,
                )
                for asset in assets
            ]
            selected_threshold = album_selection_threshold(
                [score.score for score in swipe_scores], density
            )
            decisions = [
                decide_asset(
                    asset,
                    duplicate_by_asset.get(str(asset["asset_uuid"])),
                    density,
                    swipe_score,
                    selected_threshold=selected_threshold,
                )
                for asset, swipe_score in zip(assets, swipe_scores, strict=True)
            ]
            diversity = diversity_evidence(assets, decisions, signal_by_asset)
            swipe_scores = [
                _apply_diversity_to_score(score, diversity[str(asset["asset_uuid"])])
                for asset, score in zip(assets, swipe_scores, strict=True)
            ]
            selected_threshold = album_selection_threshold(
                [score.score for score in swipe_scores], density
            )
            decisions = [
                decide_asset(
                    asset,
                    duplicate_by_asset.get(str(asset["asset_uuid"])),
                    density,
                    swipe_score,
                    selected_threshold=selected_threshold,
                )
                for asset, swipe_score in zip(assets, swipe_scores, strict=True)
            ]
            for index, (asset, swipe_score, decision) in enumerate(
                zip(assets, swipe_scores, decisions, strict=True), start=1
            ):
                diversity_item = diversity[str(asset["asset_uuid"])]
                if diversity_item.demoted:
                    decision = replace(
                        decision,
                        disposition="reject",
                        flags=sorted({*decision.flags, "diversity_limit"}),
                        reasons=[_diversity_reason(diversity_item, demoted=True)],
                    )
                repository.upsert_swipe_score(
                    connection,
                    project_id,
                    str(asset["asset_uuid"]),
                    schema_version=swipe_score.schema_version,
                    score=swipe_score.score,
                    generic_score=swipe_score.generic_score,
                    personal_delta=swipe_score.personal_delta,
                    confidence=swipe_score.confidence,
                    components=swipe_score.components,
                    reasons=swipe_score.reasons,
                    model_versions=swipe_score.model_versions,
                    source_fingerprint=_optional_string(asset.get("source_fingerprint")),
                )
                repository.upsert_decision(
                    connection,
                    project_id,
                    str(asset["asset_uuid"]),
                    disposition=decision.disposition,
                    confidence=decision.confidence,
                    flags=decision.flags,
                    reasons=decision.reasons,
                )
                repository.update_job(
                    connection,
                    job_id,
                    processed=index,
                    message=f"Решения {index} из {len(assets)}",
                )
            eligible = [
                asset
                for asset in repository.list_assets(connection, project_id)
                if asset.get("final_disposition") == "keep"
                and asset.get("technical_quality") is not None
            ]
            best_count = max(1, round(len(eligible) * 0.1)) if eligible else 0
            best = {
                str(asset["asset_uuid"])
                for asset in sorted(
                    eligible,
                    key=lambda value: float(value.get("swipe_score") or 0),
                    reverse=True,
                )[:best_count]
            }
            repository.mark_best_candidates(connection, project_id, best)
            repository.update_project_settings(
                connection,
                project_id,
                {
                    "decision_model_version": DECISION_MODEL_VERSION,
                    "selection_threshold": selected_threshold,
                },
            )
            repository.update_job(connection, job_id, status="done", processed=len(assets))


def _float(value: object) -> float | None:
    return float(value) if value is not None else None


def _optional_float(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _optional_string(value: object) -> str | None:
    return str(value) if value is not None else None


def _signal_revision(signal_kind: str, value: object) -> int | None:
    if not isinstance(value, dict):
        return None
    if signal_kind == "faces":
        landmark = value.get("landmark_revision")
        quality = value.get("quality_revision")
        return int(landmark) if isinstance(landmark, int) and landmark == quality else None
    revision = value.get("revision")
    return int(revision) if isinstance(revision, int) else None


def _diversity_demotions(
    assets: list[dict[str, object]], decisions: list[DecisionResult]
) -> set[str]:
    evidence = diversity_evidence(assets, decisions, {})
    return {asset_uuid for asset_uuid, item in evidence.items() if item.demoted}


def _apply_diversity_to_score(score, evidence):
    components = {**score.components, "diversity_value": evidence.value}
    reasons = list(score.reasons)
    if evidence.demoted:
        reasons.append(_diversity_reason(evidence, demoted=True))
    elif evidence.reason in {"semantic_anchor", "semantic_distance"} and evidence.value >= 65:
        reasons.append(_diversity_reason(evidence, demoted=False))
    models = dict(score.model_versions)
    if evidence.model_version:
        models["diversity"] = evidence.model_version
    return replace(score, components=components, reasons=reasons[:4], model_versions=models)


def _diversity_reason(evidence, *, demoted: bool) -> dict[str, object]:
    reason: dict[str, object] = {
        "code": "too_similar_to_selected" if demoted else "adds_variety",
        "value": evidence.value,
    }
    if evidence.nearest_uuid:
        reason["nearest_uuid"] = evidence.nearest_uuid
    if evidence.similarity is not None:
        reason["similarity"] = evidence.similarity
    if evidence.reason:
        reason["method"] = evidence.reason
    return reason
