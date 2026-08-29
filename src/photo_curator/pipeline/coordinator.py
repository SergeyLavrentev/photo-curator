from __future__ import annotations

import hashlib
import json
import logging
import os
import statistics
import time
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from threading import Event, Lock, Semaphore

from photo_curator.analysis.codex_vision import (
    CODEX_ENGINE_VERSION,
    DEFAULT_BULK_MODEL,
    DEFAULT_COMPARE_MODEL,
    CodexVisionCancelled,
    CodexVisionError,
    CodexVisionRunner,
    build_batches,
    codex_status,
)
from photo_curator.analysis.curation import automatic_selection_state
from photo_curator.analysis.decision_engine import (
    DECISION_MODEL_VERSION,
    DecisionResult,
    album_selection_threshold,
    decide_asset,
)
from photo_curator.analysis.diversity import diversity_evidence
from photo_curator.analysis.hashes import color_histogram, dhash, phash, render_equivalence_hash
from photo_curator.analysis.image_loader import load_normalized
from photo_curator.analysis.local_models import (
    MODEL_ENGINE_NAMES,
    MODEL_ENGINE_VERSIONS,
    LocalModelCancelled,
    LocalModelEngine,
    LocalModelError,
)
from photo_curator.analysis.native_vision import NativeVisionEngine, NativeVisionError
from photo_curator.analysis.normalization import percentile_ranks
from photo_curator.analysis.swipe_score import (
    apple_score_percentiles,
    calculate_swipe_score,
    engine_score_percentiles,
)
from photo_curator.analysis.taste import TasteProfileError, compatible_taste_model
from photo_curator.analysis.technical import (
    TECHNICAL_ENGINE_VERSION,
    subject_quality_metrics,
    technical_metrics,
)
from photo_curator.db import repository
from photo_curator.db.connection import database_connection
from photo_curator.paths import ApplicationPaths
from photo_curator.photos.provider import PhotosProvider
from photo_curator.photos.render_resolver import resolve_source_render
from photo_curator.pipeline.duplicates import (
    DUPLICATE_ENGINE_VERSION,
    find_duplicate_groups,
    rerank_duplicate_groups,
)
from photo_curator.pipeline.previews import (
    analysis_preview_dimensions,
    analysis_preview_is_eligible,
    build_previews,
    shared_thumbnail_cache_path,
    source_fingerprint,
)

LOGGER = logging.getLogger(__name__)

STAGES = (
    "inventory",
    "previews",
    "metrics",
    "duplicates",
    "vision",
    "models",
    "codex",
    "decisions",
)
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
        model_engine: LocalModelEngine | None = None,
    ) -> None:
        self.database_path = database_path
        self.paths = paths
        self.provider = provider
        self.vision_engine = vision_engine
        self.model_engine = model_engine
        self._executor = ThreadPoolExecutor(
            max_workers=min(4, os.cpu_count() or 2), thread_name_prefix="photo-curator"
        )
        self._futures: dict[str, Future[None]] = {}
        self._cancel_events: dict[str, Event] = {}
        self._lock = Lock()
        self._project_operation_locks: dict[str, Lock] = {}

    @contextmanager
    def project_operation(self, project_id: str) -> Iterator[None]:
        """Serialize lifecycle mutations for one persisted analysis project."""
        with self._lock:
            operation_lock = self._project_operation_locks.setdefault(project_id, Lock())
        with operation_lock:
            yield

    def start(self, project_id: str, from_stage: str | None = None) -> str:
        with self.project_operation(project_id):
            with self._lock:
                current = self._futures.get(project_id)
                if current and not current.done():
                    raise RuntimeError("Pipeline is already running")
            with database_connection(self.database_path) as connection:
                previous_state = str(repository.get_project(connection, project_id)["state"])
                repository.set_project_state(connection, project_id, "running")
            try:
                with self._lock:
                    self._cancel_events[project_id] = Event()
                    future = self._executor.submit(self.run, project_id, from_stage=from_stage)
                    self._futures[project_id] = future
            except Exception:
                with database_connection(self.database_path) as connection:
                    repository.set_project_state(connection, project_id, previous_state)
                raise
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
                input_fingerprint = self._stage_input_fingerprint(project_id, stage)
                with database_connection(self.database_path) as connection:
                    previous_fingerprint = repository.stage_fingerprint(
                        connection, project_id, stage
                    )
                if (
                    stage not in {"inventory", "previews"}
                    and previous_fingerprint == input_fingerprint
                ):
                    LOGGER.info("Stage cache hit project=%s stage=%s", project_id, stage)
                    continue
                if previous_fingerprint and previous_fingerprint != input_fingerprint:
                    self._invalidate_stage(project_id, stage)
                LOGGER.info("Stage started project=%s stage=%s", project_id, stage)
                getattr(self, f"_stage_{stage}")(project_id)
                completed_fingerprint = self._stage_input_fingerprint(project_id, stage)
                with database_connection(self.database_path) as connection:
                    repository.record_stage_fingerprint(
                        connection, project_id, stage, completed_fingerprint
                    )
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

    def _stage_input_fingerprint(self, project_id: str, stage: str) -> str:
        with database_connection(self.database_path) as connection:
            project = repository.get_project(connection, project_id)
            settings = json.loads(str(project.get("settings_json") or "{}"))
            assets = [
                tuple(row)
                for row in connection.execute(
                    """
                    SELECT asset_uuid, current_filename, width, height, favorite,
                        has_adjustments, burst_key, burst_default_pick, no_longer_exists,
                        source_fingerprint, cache_state
                    FROM assets WHERE project_id=? ORDER BY asset_uuid
                    """,
                    (project_id,),
                ).fetchall()
            ]
            payload: object
            if stage == "inventory":
                payload = (project["library_fingerprint"], project["album_id"])
            elif stage == "previews":
                payload = ("preview-v3", assets)
            elif stage == "metrics":
                payload = (TECHNICAL_ENGINE_VERSION, assets)
            elif stage == "duplicates":
                metrics = [
                    tuple(row)
                    for row in connection.execute(
                        """
                        SELECT asset_uuid, phash, dhash, normalized_pixel_hash,
                            histogram_json, sharpness_percentile, contrast_percentile,
                            technical_quality
                        FROM metrics WHERE project_id=? ORDER BY asset_uuid
                        """,
                        (project_id,),
                    ).fetchall()
                ]
                payload = (DUPLICATE_ENGINE_VERSION, assets, metrics)
            elif stage == "vision":
                payload = (
                    "apple-vision-subject-v2",
                    _file_digest(
                        Path(__file__).parents[1]
                        / "analysis"
                        / "native"
                        / "photo_curator_vision.swift"
                    ),
                    bool((settings.get("local_engines") or {}).get("apple", True)),
                    [(row[0], row[9]) for row in assets],
                )
            elif stage == "models":
                manifest = Path(__file__).parents[3] / "packaging" / "models" / "models.json"
                manifest_digest = (
                    hashlib.sha256(manifest.read_bytes()).hexdigest()
                    if manifest.is_file()
                    else "missing"
                )
                payload = (
                    MODEL_ENGINE_VERSIONS,
                    settings.get("local_engines"),
                    manifest_digest,
                    _file_digest(
                        Path(__file__).parents[1]
                        / "analysis"
                        / "native"
                        / "photo_curator_local_models.swift"
                    ),
                    [(row[0], row[9]) for row in assets],
                )
            elif stage == "codex":
                payload = (
                    CODEX_ENGINE_VERSION,
                    DEFAULT_BULK_MODEL,
                    DEFAULT_COMPARE_MODEL,
                    _file_digest(Path(__file__).parents[1] / "analysis" / "codex_vision.py"),
                    settings.get("analysis_mode"),
                    [(row[0], row[9]) for row in assets],
                )
            else:
                signals = [
                    tuple(row)
                    for row in connection.execute(
                        """
                        SELECT asset_uuid, signal_kind, status, engine_version,
                            source_fingerprint, value_json, error_text
                        FROM analysis_signals
                        WHERE project_id=? ORDER BY asset_uuid, signal_kind
                        """,
                        (project_id,),
                    ).fetchall()
                ]
                groups = [
                    tuple(row)
                    for row in connection.execute(
                        """
                        SELECT group_id, kind, confidence, leader_uuid, flags_json
                        FROM duplicate_groups WHERE project_id=? ORDER BY group_id
                        """,
                        (project_id,),
                    ).fetchall()
                ]
                taste = connection.execute(
                    """
                    SELECT status, model_version, feature_schema, updated_at
                    FROM taste_profiles WHERE id='default'
                    """
                ).fetchone()
                payload = (
                    DECISION_MODEL_VERSION,
                    settings.get("selection_density"),
                    settings.get("validated_defect_auto_reject", False),
                    settings.get("validated_codex_ranking", False),
                    settings.get("ensemble_model"),
                    assets,
                    signals,
                    groups,
                    tuple(taste) if taste else None,
                )
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(encoded.encode()).hexdigest()

    def _invalidate_stage(self, project_id: str, stage: str) -> None:
        affected = {
            "inventory": STAGES,
            "previews": STAGES[1:],
            "metrics": ("metrics", "duplicates", "decisions"),
            "duplicates": ("duplicates", "codex", "decisions"),
            "vision": ("vision", "decisions"),
            "models": ("models", "decisions"),
            "codex": ("codex", "decisions"),
            "decisions": ("decisions",),
        }[stage]
        with database_connection(self.database_path) as connection:
            repository.clear_stage_fingerprints_from(connection, project_id, affected)
            if stage == "metrics":
                connection.execute("DELETE FROM metrics WHERE project_id=?", (project_id,))
            if stage in {"metrics", "duplicates"}:
                connection.execute("DELETE FROM duplicate_groups WHERE project_id=?", (project_id,))
            signal_kinds = {
                "vision": (
                    "aesthetics",
                    "feature_print",
                    "attention_saliency",
                    "faces",
                    "subject_quality",
                ),
                "codex": ("codex_vision",),
            }.get(stage)
            if signal_kinds:
                placeholders = ",".join("?" for _ in signal_kinds)
                connection.execute(
                    "DELETE FROM analysis_signals "
                    f"WHERE project_id=? AND signal_kind IN ({placeholders})",
                    (project_id, *signal_kinds),
                )
            if "decisions" in affected:
                connection.execute("DELETE FROM swipe_scores WHERE project_id=?", (project_id,))
                connection.execute(
                    "DELETE FROM decisions WHERE project_id=? AND manual_override=0",
                    (project_id,),
                )

    def _source_album_is_shared(self, project: dict[str, object]) -> bool:
        settings = json.loads(str(project.get("settings_json") or "{}"))
        stored = settings.get("source_album_shared")
        if isinstance(stored, bool):
            return stored
        album_id = str(project["album_id"])
        return any(album.id == album_id for album in self.provider.list_shared_albums())

    def _stage_inventory(self, project_id: str) -> None:
        with database_connection(self.database_path) as connection:
            project = repository.get_project(connection, project_id)
            job_id = repository.create_job(connection, project_id, "inventory", 0)

        last_progress_write = 0.0

        def report_progress(processed: int, total: int) -> None:
            nonlocal last_progress_write
            self._check_cancelled(project_id)
            now = time.monotonic()
            if processed < total and processed > 1 and now - last_progress_write < 0.25:
                return
            last_progress_write = now
            with database_connection(self.database_path) as connection:
                connection.execute("UPDATE jobs SET total_items=? WHERE id=?", (total, job_id))
                repository.update_job(
                    connection,
                    job_id,
                    processed=processed,
                    message=f"PhotoKit: подготовлено {processed} из {total}",
                )

        streaming = getattr(self.provider, "list_asset_metadata_with_progress", None)
        album_id = str(project["album_id"])
        source_album_shared = self._source_album_is_shared(project)
        if streaming:
            source_assets = streaming(album_id, report_progress)
        elif source_album_shared:
            source_assets = self.provider.list_shared_assets(album_id)
        else:
            source_assets = self.provider.list_assets(album_id)
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
        last_progress_write = 0.0

        def report_render_progress(processed: int, total: int) -> None:
            nonlocal last_progress_write
            self._check_cancelled(project_id)
            now = time.monotonic()
            if processed < total and processed > 1 and now - last_progress_write < 0.25:
                return
            last_progress_write = now
            with database_connection(self.database_path) as connection:
                connection.execute("UPDATE jobs SET total_items=? WHERE id=?", (total, job_id))
                repository.update_job(
                    connection,
                    job_id,
                    processed=processed,
                    message=f"PhotoKit render {processed} из {total}",
                )

        streaming = getattr(self.provider, "list_assets_with_progress", None)
        album_id = str(project["album_id"])
        if streaming:
            source_assets = streaming(album_id, report_render_progress)
        elif self._source_album_is_shared(project):
            source_assets = self.provider.list_shared_assets(album_id)
        else:
            source_assets = self.provider.list_assets(album_id)
        current_assets = {asset.uuid: asset for asset in source_assets if asset.is_photo}
        renders_reported = bool(current_assets) and all(
            asset.review_render for asset in current_assets.values()
        )
        # Native progress includes videos because list_assets preserves the
        # provider contract. The preview job itself tracks only inventoried
        # photos, so restore its final denominator before thumbnail work.
        with database_connection(self.database_path) as connection:
            connection.execute(
                "UPDATE jobs SET total_items=?, processed_items=MIN(processed_items, ?) WHERE id=?",
                (len(stored), len(stored), job_id),
            )
        errors = warnings = 0
        cache = self.paths.project_artifacts_dir / project_id
        for index, row in enumerate(stored, start=1):
            self._check_cancelled(project_id)
            asset_uuid = str(row["asset_uuid"])
            asset = current_assets.get(asset_uuid)
            render = resolve_source_render(asset) if asset else None
            try:
                if not render or not render.path:
                    warnings += 1
                    with database_connection(self.database_path) as connection:
                        repository.invalidate_asset_analysis(connection, project_id, asset_uuid)
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
                    asset_key = _asset_artifact_key(asset_uuid)
                    review_path = cache / "review" / f"{asset_key}.jpg"
                    thumbnail_path = cache / "thumbnails" / f"{asset_key}.jpg"
                    expected_fingerprint = source_fingerprint(render.path, render.kind)
                    if (
                        row.get("cache_state") == "ready"
                        and row.get("source_fingerprint") == expected_fingerprint
                        and review_path.is_file()
                        and thumbnail_path.is_file()
                        and analysis_preview_is_eligible(review_path)
                    ):
                        with database_connection(self.database_path) as connection:
                            repository.update_job(
                                connection,
                                job_id,
                                processed=len(stored) if renders_reported else index,
                                warnings=warnings,
                                errors=errors,
                                message=f"Thumbnail {index} из {len(stored)} · cache"
                                if renders_reported
                                else f"Preview {index} из {len(stored)} · cache",
                            )
                        continue
                    with PREVIEW_LIMIT:
                        result = build_previews(
                            render.path,
                            review_path,
                            thumbnail_path,
                            source_kind=render.kind,
                            source_is_review_render=asset.review_render,
                            shared_thumbnail_path=(
                                shared_thumbnail_cache_path(render.path)
                                if asset.review_render
                                else None
                            ),
                        )
                    stat = render.path.stat()
                    preview_width, preview_height = analysis_preview_dimensions(result.review_path)
                    analysis_eligible = analysis_preview_is_eligible(result.review_path)
                    render_warning = render.warning
                    if not analysis_eligible:
                        warnings += 1
                        size_warning = (
                            f"preview_too_small:{preview_width}x{preview_height};analysis_skipped"
                        )
                        render_warning = (
                            f"{render_warning} · {size_warning}" if render_warning else size_warning
                        )
                    with database_connection(self.database_path) as connection:
                        if (
                            row.get("source_fingerprint") != result.source_fingerprint
                            or not analysis_eligible
                        ):
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
                            cache_state="ready" if analysis_eligible else "degraded",
                            render_warning=render_warning,
                        )
            except Exception:
                errors += 1
                LOGGER.exception("Preview failed for %s", asset_uuid)
                with database_connection(self.database_path) as connection:
                    repository.invalidate_asset_analysis(connection, project_id, asset_uuid)
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
                    processed=len(stored) if renders_reported else index,
                    warnings=warnings,
                    errors=errors,
                    message=f"Thumbnail {index} из {len(stored)}"
                    if renders_reported
                    else f"Preview {index} из {len(stored)}",
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
            project = repository.get_project(connection, project_id)
            settings = json.loads(str(project.get("settings_json") or "{}"))
            engines = settings.get("local_engines")
            apple_enabled = not isinstance(engines, dict) or engines.get("apple", True) is True
            assets = [
                asset
                for asset in repository.list_assets(connection, project_id)
                if asset.get("cache_state") == "ready" and asset.get("phash")
            ]
            job_id = repository.create_job(connection, project_id, "vision", len(assets))
        if not apple_enabled:
            with database_connection(self.database_path) as connection:
                repository.update_job(
                    connection,
                    job_id,
                    status="done",
                    processed=len(assets),
                    message="Apple Vision отключён в настройках проекта",
                )
            return
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
                        "subject_quality",
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
            faces = row.get("faces")
            saliency = row.get("attention_saliency")
            face_rectangles = faces.get("face_rectangles") if isinstance(faces, dict) else None
            salient_objects = (
                saliency.get("salient_objects") if isinstance(saliency, dict) else None
            )
            raw_rectangles = (
                face_rectangles
                if isinstance(face_rectangles, list) and face_rectangles
                else salient_objects
                if isinstance(salient_objects, list)
                else []
            )
            roi_source = "faces" if raw_rectangles is face_rectangles else "attention_saliency"
            subject_value: dict[str, object] = {}
            subject_error: str | None = None
            if raw_rectangles:
                try:
                    image = load_normalized(Path(str(asset["review_path"])), max_dimension=1024)
                    subject_value = subject_quality_metrics(
                        image,
                        [value for value in raw_rectangles if isinstance(value, dict)],
                        source=roi_source,
                    )
                except (OSError, ValueError) as error:
                    subject_error = str(error)[-1000:]
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
                repository.upsert_analysis_signal(
                    connection,
                    project_id,
                    asset_uuid,
                    signal_kind="subject_quality",
                    schema_version=1,
                    engine_name="photo-curator-subject-roi",
                    engine_version="1",
                    request_revision=1,
                    source_fingerprint=_optional_string(asset.get("source_fingerprint")),
                    status="ready" if subject_value else "unavailable",
                    value=subject_value or None,
                    duration_ms=None,
                    error_text=subject_error or (None if subject_value else "No subject ROI"),
                )
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

    def _stage_models(self, project_id: str) -> None:
        with database_connection(self.database_path) as connection:
            project = repository.get_project(connection, project_id)
            settings = json.loads(str(project.get("settings_json") or "{}"))
            configured = settings.get("local_engines")
            configured = configured if isinstance(configured, dict) else {}
            enabled = {
                name
                for name in ("nima", "mobileclip", "musiq")
                if configured.get(name, True) is True
            }
            assets = [
                asset
                for asset in repository.list_assets(connection, project_id)
                if asset.get("cache_state") == "ready" and asset.get("review_path")
            ]
            existing = repository.analysis_signals_by_asset(connection, project_id)
            signal_specs = {
                "nima": ("nima_aesthetics", "nima"),
                "mobileclip": ("mobileclip", "mobileclip"),
                "musiq": ("musiq_quality", "musiq"),
            }
            disabled_kinds = [
                signal_kind
                for engine, (signal_kind, _) in signal_specs.items()
                if engine not in enabled
            ]
            if disabled_kinds:
                placeholders = ",".join("?" for _ in disabled_kinds)
                connection.execute(
                    "DELETE FROM analysis_signals "
                    f"WHERE project_id=? AND signal_kind IN ({placeholders})",
                    (project_id, *disabled_kinds),
                )
            job_id = repository.create_job(connection, project_id, "models", len(assets))
        if not enabled:
            with database_connection(self.database_path) as connection:
                repository.update_job(
                    connection,
                    job_id,
                    status="done",
                    processed=len(assets),
                    message="Дополнительные локальные модели отключены",
                )
            return
        if self.model_engine is None:
            self._record_model_stack_failure(
                project_id,
                job_id,
                assets,
                enabled,
                "Core ML model helper не настроен",
            )
            return
        try:
            digest_loader = getattr(self.model_engine, "verified_digests", None)
            verified_digests = digest_loader(enabled) if digest_loader else {}
        except (LocalModelError, OSError, ValueError) as error:
            self._record_model_stack_failure(
                project_id, job_id, assets, enabled, str(error)[-1000:]
            )
            return
        expected_versions = {
            engine: (
                f"{MODEL_ENGINE_VERSIONS[engine]}+sha256.{verified_digests[engine][:16]}"
                if verified_digests.get(engine)
                else MODEL_ENGINE_VERSIONS[engine]
            )
            for engine in enabled
        }
        pending = [
            asset
            for asset in assets
            if any(
                (
                    (
                        signal := existing.get(str(asset["asset_uuid"]), {}).get(
                            signal_specs[engine][0]
                        )
                    )
                    is None
                    or signal.get("status") != "ready"
                    or signal.get("source_fingerprint") != asset.get("source_fingerprint")
                    or signal.get("engine_version") != expected_versions[engine]
                )
                for engine in enabled
            )
        ]
        with database_connection(self.database_path) as connection:
            connection.execute("UPDATE jobs SET total_items=? WHERE id=?", (len(pending), job_id))
        if not pending:
            with database_connection(self.database_path) as connection:
                repository.update_job(
                    connection,
                    job_id,
                    status="done",
                    processed=0,
                    message="Core ML inference cache актуален",
                )
            return
        try:
            self._check_cancelled(project_id)
            report = self.model_engine.analyze(
                [(str(asset["asset_uuid"]), Path(str(asset["review_path"]))) for asset in pending],
                engines=enabled,
                cancelled=lambda: self._cancel_requested(project_id),
            )
            self._check_cancelled(project_id)
        except LocalModelCancelled as error:
            raise PipelineCancelled(str(error)) from error
        except (LocalModelError, OSError, ValueError) as error:
            LOGGER.exception("Local model stack failed for %s", project_id)
            self._record_model_stack_failure(
                project_id, job_id, pending, enabled, str(error)[-1000:]
            )
            return
        rows = report.get("assets")
        if not isinstance(rows, list):
            raise LocalModelError("Local model report has no assets")
        by_uuid = {
            str(row["asset_uuid"]): row
            for row in rows
            if isinstance(row, dict) and row.get("asset_uuid")
        }
        errors = 0
        report_digests = (
            report.get("model_digests") if isinstance(report.get("model_digests"), dict) else {}
        )
        for index, asset in enumerate(pending, start=1):
            self._check_cancelled(project_id)
            asset_uuid = str(asset["asset_uuid"])
            row = by_uuid.get(asset_uuid, {})
            row_errors = row.get("errors") if isinstance(row.get("errors"), dict) else {}
            durations = row.get("durations_ms") if isinstance(row.get("durations_ms"), dict) else {}
            with database_connection(self.database_path) as connection:
                for engine in sorted(enabled):
                    signal_kind, value_key = signal_specs[engine]
                    value = row.get(value_key)
                    error_text = row_errors.get(engine)
                    ready = isinstance(value, dict)
                    errors += int(not ready)
                    repository.upsert_analysis_signal(
                        connection,
                        project_id,
                        asset_uuid,
                        signal_kind=signal_kind,
                        schema_version=1,
                        engine_name=MODEL_ENGINE_NAMES[engine],
                        engine_version=(
                            f"{MODEL_ENGINE_VERSIONS[engine]}+sha256."
                            f"{str(report_digests[engine])[:16]}"
                            if report_digests.get(engine)
                            else MODEL_ENGINE_VERSIONS[engine]
                        ),
                        request_revision=1,
                        source_fingerprint=_optional_string(asset.get("source_fingerprint")),
                        status="ready" if ready else "error",
                        value=value if ready else None,
                        duration_ms=_optional_float(durations.get(engine)),
                        error_text=(
                            str(error_text) if error_text else "Local model returned no result"
                        )
                        if not ready
                        else None,
                    )
                repository.update_job(
                    connection,
                    job_id,
                    processed=index,
                    errors=errors,
                    message=f"Core ML модели {index} из {len(pending)}",
                )
        with database_connection(self.database_path) as connection:
            repository.update_job(
                connection,
                job_id,
                status="warning" if errors else "done",
                processed=len(pending),
                errors=errors,
                warnings=errors,
                message=(
                    f"Локальные модели завершены; ошибок сигналов: {errors}"
                    if errors
                    else "NIMA, MobileCLIP и MUSIQ завершены"
                ),
            )

    def _record_model_stack_failure(
        self,
        project_id: str,
        job_id: str,
        assets: list[dict[str, object]],
        enabled: set[str],
        detail: str,
    ) -> None:
        signal_kinds = {
            "nima": "nima_aesthetics",
            "mobileclip": "mobileclip",
            "musiq": "musiq_quality",
        }
        with database_connection(self.database_path) as connection:
            for asset in assets:
                for engine in sorted(enabled):
                    repository.upsert_analysis_signal(
                        connection,
                        project_id,
                        str(asset["asset_uuid"]),
                        signal_kind=signal_kinds[engine],
                        schema_version=1,
                        engine_name=MODEL_ENGINE_NAMES[engine],
                        engine_version=MODEL_ENGINE_VERSIONS[engine],
                        request_revision=1,
                        source_fingerprint=_optional_string(asset.get("source_fingerprint")),
                        status="unavailable",
                        value=None,
                        duration_ms=None,
                        error_text=detail,
                    )
            repository.update_job(
                connection,
                job_id,
                status="warning",
                processed=0,
                warnings=1,
                errors=len(assets) * len(enabled),
                message="Дополнительные Core ML модели недоступны; Apple-сигналы сохранены",
            )

    def _stage_codex(self, project_id: str) -> None:
        with database_connection(self.database_path) as connection:
            project = repository.get_project(connection, project_id)
            settings = json.loads(str(project.get("settings_json") or "{}"))
            if settings.get("analysis_mode") != "codex":
                return
            assets = [
                asset
                for asset in repository.list_assets(connection, project_id)
                if asset.get("cache_state") == "ready" and asset.get("review_path")
            ]
            groups = repository.list_duplicate_groups(connection, project_id)
            existing = repository.analysis_signals_by_asset(connection, project_id)
        if not assets:
            with database_connection(self.database_path) as connection:
                job_id = repository.create_job(connection, project_id, "codex", 0)
                repository.update_job(
                    connection,
                    job_id,
                    status="done",
                    processed=0,
                    message="Codex Vision пропущен: нет preview достаточного разрешения",
                )
            return
        status = codex_status()
        if not status.ready:
            raise CodexVisionError(status.detail or "Codex больше не авторизован через ChatGPT")
        pending = [
            asset
            for asset in assets
            if existing.get(str(asset["asset_uuid"]), {}).get("codex_vision", {}).get("status")
            != "ready"
        ]
        pending_ids = {str(asset["asset_uuid"]) for asset in pending}
        pending_groups = []
        for group in groups:
            members = [
                member
                for member in group.get("members", [])
                if str(member.get("asset_uuid")) in pending_ids
            ]
            if members:
                pending_groups.append({**group, "members": members})
        batches = build_batches(pending, pending_groups)
        with database_connection(self.database_path) as connection:
            job_id = repository.create_job(connection, project_id, "codex", len(batches))
        runner = CodexVisionRunner(Path(str(status.executable)))
        errors = 0
        cache = self.paths.cache_dir / project_id / "codex"
        for index, batch in enumerate(batches, start=1):
            self._check_cancelled(project_id)
            compare = any(asset.get("codex_group_id") for asset in batch)
            model = DEFAULT_COMPARE_MODEL if compare else DEFAULT_BULK_MODEL
            try:
                result = runner.analyze(
                    batch,
                    work_dir=cache / f"batch-{index:04d}",
                    model=model,
                    cancelled=lambda: self._cancel_requested(project_id),
                )
                with database_connection(self.database_path) as connection:
                    for asset in batch:
                        asset_uuid = str(asset["asset_uuid"])
                        repository.upsert_analysis_signal(
                            connection,
                            project_id,
                            asset_uuid,
                            signal_kind="codex_vision",
                            schema_version=1,
                            engine_name="codex-cli-chatgpt",
                            engine_version=CODEX_ENGINE_VERSION,
                            request_revision=1,
                            source_fingerprint=_optional_string(asset.get("source_fingerprint")),
                            status="ready",
                            value=result[asset_uuid],
                            duration_ms=None,
                            error_text=None,
                        )
                    _apply_codex_leaders(connection, project_id, batch, result)
            except CodexVisionCancelled as error:
                raise PipelineCancelled(str(error)) from error
            except (CodexVisionError, OSError, ValueError) as error:
                errors += 1
                LOGGER.exception("Codex vision batch failed project=%s batch=%s", project_id, index)
                with database_connection(self.database_path) as connection:
                    for asset in batch:
                        repository.upsert_analysis_signal(
                            connection,
                            project_id,
                            str(asset["asset_uuid"]),
                            signal_kind="codex_vision",
                            schema_version=1,
                            engine_name="codex-cli-chatgpt",
                            engine_version=CODEX_ENGINE_VERSION,
                            request_revision=1,
                            source_fingerprint=_optional_string(asset.get("source_fingerprint")),
                            status="error",
                            value=None,
                            duration_ms=None,
                            error_text=str(error)[-1000:],
                        )
            with database_connection(self.database_path) as connection:
                repository.update_job(
                    connection,
                    job_id,
                    processed=index,
                    errors=errors,
                    message=f"Codex Vision: пакет {index} из {len(batches)}",
                )
        with database_connection(self.database_path) as connection:
            repository.update_job(
                connection,
                job_id,
                status="warning" if errors else "done",
                processed=len(batches),
                errors=errors,
                warnings=errors,
                message=(
                    f"Codex Vision завершён; ошибок пакетов: {errors}"
                    if errors
                    else "Codex Vision завершён"
                ),
            )

    def _stage_decisions(self, project_id: str) -> None:
        with database_connection(self.database_path) as connection:
            assets = repository.list_assets(connection, project_id)
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
            ensemble_percentiles = engine_score_percentiles(assets, signal_by_asset)
            raw_taste_deltas = [
                (
                    taste_model.personal_delta(
                        signal_by_asset.get(str(asset["asset_uuid"]), {}).get("feature_print", {})
                    )
                    if taste_model
                    else 0.0
                )
                for asset in assets
            ]
            taste_ready = [
                bool(
                    signal_by_asset.get(str(asset["asset_uuid"]), {})
                    .get("feature_print", {})
                    .get("status")
                    == "ready"
                )
                for asset in assets
            ]
            active_deltas = [
                delta for delta, ready in zip(raw_taste_deltas, taste_ready, strict=True) if ready
            ]
            taste_center = statistics.median(active_deltas) if active_deltas else 0.0
            taste_deltas = [
                max(-12.0, min(12.0, delta - taste_center)) if ready else 0.0
                for delta, ready in zip(raw_taste_deltas, taste_ready, strict=True)
            ]
            groups = repository.list_duplicate_groups(connection, project_id)
            reranked_groups = rerank_duplicate_groups(
                groups,
                assets,
                signal_by_asset,
                personal_deltas={
                    str(asset["asset_uuid"]): delta
                    for asset, delta in zip(assets, taste_deltas, strict=True)
                },
                check_cancelled=lambda: self._check_cancelled(project_id),
            )
            repository.replace_duplicate_groups(connection, project_id, reranked_groups)
            duplicate_by_asset = repository.duplicate_context(connection, project_id)
            scored_assets = [
                _asset_with_analysis_flags(asset, signal_by_asset.get(str(asset["asset_uuid"]), {}))
                for asset in assets
            ]
            calibration = settings.get("decision_calibration")
            if isinstance(calibration, dict):
                for asset in scored_assets:
                    asset["decision_calibration"] = calibration
            swipe_scores = [
                calculate_swipe_score(
                    asset,
                    duplicate_by_asset.get(str(asset["asset_uuid"])),
                    signal_by_asset.get(str(asset["asset_uuid"]), {}),
                    apple_percentiles=apple_percentiles.get(str(asset["asset_uuid"]), {}),
                    personal_delta=personal_delta,
                    taste_model_version=taste_model.model_version if taste_model else None,
                    taste_reliability=taste_model.reliability if taste_model else 0.0,
                    ensemble_model=(
                        settings.get("ensemble_model")
                        if isinstance(settings.get("ensemble_model"), dict)
                        else None
                    ),
                    ensemble_percentiles=ensemble_percentiles.get(str(asset["asset_uuid"]), {}),
                    codex_ranking_validated=(settings.get("validated_codex_ranking") is True),
                )
                for asset, personal_delta in zip(scored_assets, taste_deltas, strict=True)
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
                    validated_defect_auto_reject=(
                        settings.get("validated_defect_auto_reject") is True
                    ),
                )
                for asset, swipe_score in zip(scored_assets, swipe_scores, strict=True)
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
                    validated_defect_auto_reject=(
                        settings.get("validated_defect_auto_reject") is True
                    ),
                )
                for asset, swipe_score in zip(scored_assets, swipe_scores, strict=True)
            ]
            selections = [
                automatic_selection_state(
                    asset,
                    decision,
                    duplicate_by_asset.get(str(asset["asset_uuid"])),
                    swipe_score,
                    selected_threshold=selected_threshold,
                )
                for asset, swipe_score, decision in zip(
                    scored_assets, swipe_scores, decisions, strict=True
                )
            ]
            for index, (asset, swipe_score, decision, selection) in enumerate(
                zip(assets, swipe_scores, decisions, selections, strict=True), start=1
            ):
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
                    selection=selection,
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
            best = {
                str(asset["asset_uuid"])
                for asset in repository.list_assets(connection, project_id)
                if asset.get("final_selection") == "pick"
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

    def _cancel_requested(self, project_id: str) -> bool:
        with self._lock:
            event = self._cancel_events.get(project_id)
        return bool(event and event.is_set())


def _apply_codex_leaders(
    connection,
    project_id: str,
    batch: list[dict[str, object]],
    result: dict[str, dict[str, object]],
) -> None:
    group_members: dict[str, list[str]] = {}
    for asset in batch:
        group_id = str(asset.get("codex_group_id") or "")
        if group_id:
            group_members.setdefault(group_id, []).append(str(asset["asset_uuid"]))
    for group_id, members in group_members.items():
        leaders = [
            asset_uuid
            for asset_uuid in members
            if int(result[asset_uuid].get("series_rank") or 0) == 1
            and float(result[asset_uuid].get("confidence") or 0.0) >= 0.75
        ]
        if len(leaders) == 1:
            repository.update_duplicate_group_leader(connection, project_id, group_id, leaders[0])


def _asset_with_analysis_flags(
    asset: dict[str, object], signals: dict[str, dict[str, object]]
) -> dict[str, object]:
    copy = dict(asset)
    subject_signal = signals.get("subject_quality")
    subject_value = (
        subject_signal.get("value")
        if subject_signal and subject_signal.get("status") == "ready"
        else None
    )
    if isinstance(subject_value, dict):
        copy.update(subject_value)
    signal = signals.get("codex_vision")
    value = signal.get("value") if signal and signal.get("status") == "ready" else None
    if not isinstance(value, dict):
        return copy
    mapping = {
        "blur": "possible_blur",
        "underexposed": "underexposed",
        "overexposed": "overexposed",
        "extreme_horizon": "extreme_horizon",
        "bad_angle": "bad_angle",
        "blocked_subject": "blocked_subject",
    }
    flags = {mapping[str(defect)] for defect in value.get("defects", []) if str(defect) in mapping}
    if value.get("face_visibility") == "unrecognizable":
        flags.add("poor_face_capture")
    copy["codex_flags"] = sorted(flags)
    copy["codex_reject_recommended"] = bool(value.get("reject_recommended"))
    copy["codex_confidence"] = float(value.get("confidence") or 0.0)
    copy["codex_reason"] = str(value.get("reason") or "")[:240]
    return copy


def _float(value: object) -> float | None:
    return float(value) if value is not None else None


def _optional_float(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _optional_string(value: object) -> str | None:
    return str(value) if value is not None else None


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "missing"


def _asset_artifact_key(asset_uuid: str) -> str:
    """Map opaque PhotoKit identifiers to a flat, path-safe artifact key."""
    return hashlib.sha256(asset_uuid.encode("utf-8")).hexdigest()


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
    adjusted_score = score.score
    adjusted_generic = score.generic_score
    if evidence.demoted:
        adjusted_score = max(0, adjusted_score - 25)
        adjusted_generic = max(0.0, adjusted_generic - 25.0)
    return replace(
        score,
        score=adjusted_score,
        generic_score=round(adjusted_generic, 2),
        components=components,
        reasons=reasons[:4],
        model_versions=models,
    )


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
