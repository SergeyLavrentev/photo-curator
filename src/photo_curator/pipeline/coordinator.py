from __future__ import annotations

import json
import logging
import os
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from threading import Lock, Semaphore

from photo_curator.analysis.decision_engine import decide_asset
from photo_curator.analysis.hashes import color_histogram, dhash, phash, render_equivalence_hash
from photo_curator.analysis.image_loader import load_normalized
from photo_curator.analysis.normalization import percentile_ranks
from photo_curator.analysis.technical import technical_metrics
from photo_curator.analysis.vision import analyze_faces, vision_available
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


class PipelineCoordinator:
    def __init__(
        self,
        *,
        database_path: Path,
        paths: ApplicationPaths,
        provider: PhotosProvider,
    ) -> None:
        self.database_path = database_path
        self.paths = paths
        self.provider = provider
        self._executor = ThreadPoolExecutor(
            max_workers=min(4, os.cpu_count() or 2), thread_name_prefix="photo-curator"
        )
        self._futures: dict[str, Future[None]] = {}
        self._lock = Lock()

    def start(self, project_id: str, from_stage: str | None = None) -> str:
        with self._lock:
            current = self._futures.get(project_id)
            if current and not current.done():
                raise RuntimeError("Pipeline is already running")
            future = self._executor.submit(self.run, project_id, from_stage=from_stage)
            self._futures[project_id] = future
        return project_id

    def run(self, project_id: str, *, from_stage: str | None = None) -> None:
        start_index = STAGES.index(from_stage) if from_stage else 0
        try:
            with database_connection(self.database_path) as connection:
                repository.set_project_state(connection, project_id, "running")
            for stage in STAGES[start_index:]:
                LOGGER.info("Stage started project=%s stage=%s", project_id, stage)
                getattr(self, f"_stage_{stage}")(project_id)
                LOGGER.info("Stage completed project=%s stage=%s", project_id, stage)
            with database_connection(self.database_path) as connection:
                repository.set_project_state(connection, project_id, "ready")
        except Exception as error:
            LOGGER.exception("Pipeline failed for project %s", project_id)
            with database_connection(self.database_path) as connection:
                repository.fail_running_jobs(connection, project_id, str(error))
                repository.set_project_state(connection, project_id, "error")
            raise

    def _stage_inventory(self, project_id: str) -> None:
        with database_connection(self.database_path) as connection:
            project = repository.get_project(connection, project_id)
            job_id = repository.create_job(connection, project_id, "inventory", 0)
        assets = [
            asset for asset in self.provider.list_assets(str(project["album_id"])) if asset.is_photo
        ]
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
        if not vision_available():
            with database_connection(self.database_path) as connection:
                repository.update_job(
                    connection,
                    job_id,
                    status="warning",
                    processed=0,
                    warnings=1,
                    message="Apple Vision недоступен; используется технический рейтинг",
                )
            return
        errors = 0
        for index, asset in enumerate(assets, start=1):
            try:
                result = analyze_faces(Path(str(asset["review_path"])))
                with database_connection(self.database_path) as connection:
                    repository.update_vision_metrics(
                        connection,
                        project_id,
                        str(asset["asset_uuid"]),
                        face_count=result.face_count,
                        face_capture_quality=result.face_capture_quality,
                        eyes_detected=result.eyes_detected,
                    )
            except Exception:
                errors += 1
                LOGGER.exception("Vision analysis failed for %s", asset["asset_uuid"])
            with database_connection(self.database_path) as connection:
                repository.update_job(
                    connection,
                    job_id,
                    processed=index,
                    errors=errors,
                    message=f"Лица и глаза {index} из {len(assets)}",
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
            project = repository.get_project(connection, project_id)
            settings = json.loads(str(project.get("settings_json") or "{}"))
            density = str(settings.get("selection_density") or "balanced")
            job_id = repository.create_job(connection, project_id, "decisions", len(assets))
            for index, asset in enumerate(assets, start=1):
                decision = decide_asset(
                    asset,
                    duplicate_by_asset.get(str(asset["asset_uuid"])),
                    density,
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
                if asset.get("final_disposition") != "reject"
                and asset.get("technical_quality") is not None
            ]
            best_count = max(1, round(len(eligible) * 0.1)) if eligible else 0
            best = {
                str(asset["asset_uuid"])
                for asset in sorted(
                    eligible,
                    key=lambda value: float(value.get("technical_quality") or 0),
                    reverse=True,
                )[:best_count]
            }
            repository.mark_best_candidates(connection, project_id, best)
            repository.update_job(connection, job_id, status="done", processed=len(assets))


def _float(value: object) -> float | None:
    return float(value) if value is not None else None
