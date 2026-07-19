from __future__ import annotations

import importlib.metadata
import json
import secrets
import stat
import zipfile
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from photo_curator import __version__
from photo_curator.db import repository
from photo_curator.db.connection import database_connection
from photo_curator.db.migrations import SCHEMA_VERSION, migrate
from photo_curator.paths import ApplicationPaths, default_application_paths
from photo_curator.photos.doctor import run_doctor
from photo_curator.photos.fake_provider import FakePhotosProvider
from photo_curator.photos.osxphotos_provider import OSXPhotosProvider
from photo_curator.photos.provider import PhotosProvider
from photo_curator.photos.publisher import PhotosPublisher
from photo_curator.pipeline.coordinator import PipelineCoordinator
from photo_curator.utils.safe_paths import ensure_within, safe_rmtree
from photo_curator.web.security import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    SessionSecrets,
    has_valid_session,
)

PACKAGE_ROOT = Path(__file__).parent
templates = Jinja2Templates(directory=PACKAGE_ROOT / "web" / "templates")


class ProjectCreate(BaseModel):
    album_id: str
    name: str = Field(min_length=1, max_length=120)
    selection_density: Literal["compact", "balanced", "broad"] = "balanced"
    source_provenance: Literal["regular_album", "manual_shared_copy"] = "regular_album"


class DecisionPatch(BaseModel):
    disposition: Literal["keep", "review", "reject"] | None
    note: str | None = Field(default=None, max_length=1000)


class BatchDecisionPatch(BaseModel):
    asset_uuids: list[str] = Field(min_length=1, max_length=200)
    disposition: Literal["keep", "review", "reject"] | None


class LeaderPatch(BaseModel):
    asset_uuid: str


class PublishApplyRequest(BaseModel):
    publish_id: str
    confirmed: bool


def create_app(
    *,
    demo: bool = False,
    paths: ApplicationPaths | None = None,
    session_secrets: SessionSecrets | None = None,
    provider: PhotosProvider | None = None,
) -> FastAPI:
    app_paths = paths or default_application_paths()
    secrets_ = session_secrets or SessionSecrets.generate()
    selected_provider = provider or (
        FakePhotosProvider(app_paths.cache_dir / "_demo_sources") if demo else _try_real_provider()
    )
    coordinator = (
        PipelineCoordinator(
            database_path=app_paths.database,
            paths=app_paths,
            provider=selected_provider,
        )
        if selected_provider
        else None
    )
    publisher = (
        PhotosPublisher(
            database_path=app_paths.database,
            paths=app_paths,
            provider=selected_provider,
            enabled=not demo,
        )
        if selected_provider
        else None
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        app_paths.ensure()
        with database_connection(app_paths.database) as connection:
            migrate(connection)
            repository.mark_running_jobs_interrupted(connection)
        if demo and selected_provider and coordinator:
            _ensure_demo_project(app_paths, selected_provider, coordinator)
        yield

    app = FastAPI(
        title="Photo Curator",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.demo = demo
    app.state.paths = app_paths
    app.state.provider = selected_provider
    app.state.coordinator = coordinator
    app.state.publisher = publisher
    app.state.session_secrets = secrets_
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "testserver"],
    )
    app.mount("/static", StaticFiles(directory=PACKAGE_ROOT / "web" / "static"), name="static")

    @app.exception_handler(KeyError)
    async def not_found(_: Request, __: KeyError):
        return JSONResponse({"detail": "Resource not found"}, status_code=404)

    @app.middleware("http")
    async def session_gate(request: Request, call_next: Any):
        if request.url.path == "/health":
            return await call_next(request)
        startup_token = request.query_params.get("token")
        if startup_token and secrets.compare_digest(startup_token, secrets_.startup_token):
            response = RedirectResponse(request.url.path or "/", status_code=303)
            response.set_cookie(
                SESSION_COOKIE,
                secrets_.session_token,
                httponly=True,
                samesite="strict",
                secure=False,
            )
            response.set_cookie(
                CSRF_COOKIE,
                secrets_.csrf_token,
                httponly=False,
                samesite="strict",
                secure=False,
            )
            return response
        if not has_valid_session(request, secrets_):
            return HTMLResponse(
                "<h1>Photo Curator</h1><p>Откройте защищённую ссылку из терминала.</p>",
                status_code=401,
            )
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            csrf = request.headers.get("x-csrf-token", "")
            cookie = request.cookies.get(CSRF_COOKIE, "")
            if not (
                secrets.compare_digest(csrf, secrets_.csrf_token)
                and secrets.compare_digest(cookie, secrets_.csrf_token)
            ):
                return JSONResponse({"detail": "CSRF validation failed"}, status_code=403)
        return await call_next(request)

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request):
        with database_connection(app_paths.database) as connection:
            projects = repository.list_projects(connection)
        albums, shared, provider_error = _safe_album_lists(selected_provider)
        environment = _home_environment(selected_provider, albums, provider_error)
        return templates.TemplateResponse(
            request,
            "home.html",
            _context(
                request,
                demo=demo,
                projects=projects,
                albums=albums,
                shared_albums=shared,
                provider_error=provider_error,
                environment=environment,
                csrf_token=secrets_.csrf_token,
            ),
        )

    @app.get("/doctor", response_class=HTMLResponse)
    async def doctor(request: Request):
        checks = run_doctor(selected_provider, app_paths)
        return templates.TemplateResponse(
            request,
            "doctor.html",
            _context(request, demo=demo, checks=checks),
        )

    @app.get("/projects/{project_id}", response_class=HTMLResponse)
    async def project_dashboard(request: Request, project_id: str):
        project, summary, jobs = _project_data(app_paths.database, project_id)
        with database_connection(app_paths.database) as connection:
            all_assets = repository.list_assets(connection, project_id)
            distribution = _quality_distribution(all_assets)
            latest_publish = repository.latest_publish(connection, project_id)
        selected_preview = sorted(
            (asset for asset in all_assets if asset.get("final_disposition") == "keep"),
            key=lambda asset: int(asset.get("selection_score") or 0),
            reverse=True,
        )[:10]
        project_cache = app_paths.cache_dir / project_id
        summary["cache_size"] = _human_size(_directory_size(project_cache))
        summary["cache_last_access"] = _last_access(project_cache)
        project_settings = json.loads(str(project.get("settings_json") or "{}"))
        return templates.TemplateResponse(
            request,
            "project.html",
            _context(
                request,
                demo=demo,
                project=project,
                summary=summary,
                distribution=distribution,
                selected_preview=selected_preview,
                project_settings=project_settings,
                stages=_stage_views(jobs, summary, latest_publish),
                csrf_token=secrets_.csrf_token,
            ),
        )

    @app.get("/projects/{project_id}/review", response_class=HTMLResponse)
    async def review_page(
        request: Request,
        project_id: str,
        page: Annotated[int, Query(ge=1)] = 1,
        category: str = "all",
        sort: Literal["taken_at", "technical_quality", "filename", "resolution"] = "taken_at",
    ):
        with database_connection(app_paths.database) as connection:
            project = repository.get_project(connection, project_id)
            all_assets = repository.list_assets(connection, project_id)
        filtered = _filter_assets(all_assets, category)
        filtered.sort(
            key=lambda asset: _asset_sort_key(asset, sort),
            reverse=sort in {"technical_quality", "resolution"},
        )
        page_size = 60
        total_pages = max(1, (len(filtered) + page_size - 1) // page_size)
        page = min(page, total_pages)
        start = (page - 1) * page_size
        return templates.TemplateResponse(
            request,
            "review.html",
            _context(
                request,
                demo=demo,
                project=project,
                assets=filtered[start : start + page_size],
                category=category,
                sort=sort,
                page=page,
                total_pages=total_pages,
                total_assets=len(filtered),
                csrf_token=secrets_.csrf_token,
            ),
        )

    @app.get("/projects/{project_id}/duplicates", response_class=HTMLResponse)
    async def duplicates_page(request: Request, project_id: str):
        with database_connection(app_paths.database) as connection:
            project = repository.get_project(connection, project_id)
            groups = repository.list_duplicate_groups(connection, project_id)
        return templates.TemplateResponse(
            request,
            "duplicates.html",
            _context(
                request, demo=demo, project=project, groups=groups, csrf_token=secrets_.csrf_token
            ),
        )

    @app.get("/projects/{project_id}/publish", response_class=HTMLResponse)
    async def publish_page(
        request: Request,
        project_id: str,
        kind: Literal["best", "reject"] = "best",
    ):
        project, summary, _ = _project_data(app_paths.database, project_id)
        validation = publisher.validate(project_id, kind) if publisher else None
        with database_connection(app_paths.database) as connection:
            latest = repository.latest_publish(connection, project_id, kind)
            publish_counts = repository.publish_summary(connection, project_id)
        return templates.TemplateResponse(
            request,
            "publish.html",
            _context(
                request,
                demo=demo,
                project=project,
                summary=summary,
                validation=validation,
                publish=latest,
                publish_counts=publish_counts,
                publish_kind=kind,
                csrf_token=secrets_.csrf_token,
            ),
        )

    @app.get("/media/{project_id}/{kind}/{asset_uuid}")
    async def media(project_id: str, kind: Literal["thumbnail", "review"], asset_uuid: str):
        with database_connection(app_paths.database) as connection:
            asset = repository.get_asset(connection, project_id, asset_uuid)
        key = "thumbnail_path" if kind == "thumbnail" else "review_path"
        path_value = asset.get(key)
        if not path_value:
            raise HTTPException(404, "Preview unavailable")
        try:
            path = ensure_within(Path(str(path_value)), app_paths.cache_dir)
        except ValueError as error:
            raise HTTPException(404, "Invalid media path") from error
        if not path.is_file():
            raise HTTPException(404, "Preview unavailable")
        return FileResponse(
            path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"}
        )

    @app.get("/api/status")
    async def status() -> dict[str, Any]:
        return {
            "status": "ready",
            "mode": "demo" if demo else "local",
            "version": __version__,
            "schema_version": SCHEMA_VERSION,
            "provider_available": selected_provider is not None,
        }

    @app.get("/api/albums")
    async def albums_api() -> dict[str, list[dict[str, object]]]:
        _require_provider(selected_provider)
        try:
            regular = selected_provider.list_regular_albums()
            shared = selected_provider.list_shared_albums()
        except Exception as error:
            raise HTTPException(503, "Photos library unavailable; run doctor") from error
        return {
            "regular": [_album_json(album, disabled=False) for album in regular],
            "shared": [_album_json(album, disabled=True) for album in shared],
        }

    @app.post("/api/projects", status_code=201)
    async def create_project_api(payload: ProjectCreate) -> dict[str, str]:
        _require_provider(selected_provider)
        name = payload.name.strip()
        if not name:
            raise HTTPException(422, "Project name cannot be blank")
        try:
            albums = {album.id: album for album in selected_provider.list_regular_albums()}
        except Exception as error:
            raise HTTPException(503, "Photos library unavailable; run doctor") from error
        album = albums.get(payload.album_id)
        if not album:
            raise HTTPException(400, "Можно выбрать только обычный альбом")
        with database_connection(app_paths.database) as connection:
            project_id = repository.create_project(
                connection,
                name=name,
                library=selected_provider.get_current_library(),
                album=album,
                selection_density=payload.selection_density,
                source_provenance=payload.source_provenance,
            )
        return {"id": project_id, "url": f"/projects/{project_id}"}

    @app.get("/api/projects/{project_id}")
    async def project_api(project_id: str) -> dict[str, object]:
        project, summary, jobs = _project_data(app_paths.database, project_id)
        with database_connection(app_paths.database) as connection:
            latest_publish = repository.latest_publish(connection, project_id)
        project.pop("library_path", None)
        project.pop("database_path", None)
        project["settings"] = json.loads(str(project.pop("settings_json", "{}")))
        return {
            "project": project,
            "summary": summary,
            "jobs": [_public_job(job) for job in jobs],
            "stages": _stage_views(jobs, summary, latest_publish),
        }

    @app.delete("/api/projects/{project_id}", status_code=204)
    async def delete_project_api(project_id: str):
        with database_connection(app_paths.database) as connection:
            repository.delete_project(connection, project_id)
        project_cache = app_paths.cache_dir / project_id
        if project_cache.exists():
            safe_rmtree(project_cache, app_paths.cache_dir)

    @app.post("/api/projects/{project_id}/pipeline/start", status_code=202)
    async def start_pipeline(project_id: str) -> dict[str, str]:
        if not coordinator:
            raise HTTPException(503, "Photos provider unavailable")
        _ensure_project_exists(app_paths.database, project_id)
        try:
            coordinator.start(project_id)
        except RuntimeError as error:
            raise HTTPException(409, str(error)) from error
        return {"status": "started", "project_id": project_id}

    @app.post("/api/projects/{project_id}/pipeline/resume", status_code=202)
    async def resume_pipeline(project_id: str) -> dict[str, str]:
        if not coordinator:
            raise HTTPException(503, "Photos provider unavailable")
        _ensure_project_exists(app_paths.database, project_id)
        with database_connection(app_paths.database) as connection:
            jobs = repository.latest_jobs(connection, project_id)
        interrupted = next(
            (job for job in reversed(jobs) if job["status"] in {"interrupted", "error"}), None
        )
        coordinator.start(project_id, from_stage=str(interrupted["stage"]) if interrupted else None)
        return {"status": "resumed"}

    @app.post("/api/projects/{project_id}/stages/{stage}/retry", status_code=202)
    async def retry_stage(project_id: str, stage: str) -> dict[str, str]:
        if not coordinator:
            raise HTTPException(503, "Photos provider unavailable")
        _ensure_project_exists(app_paths.database, project_id)
        try:
            coordinator.start(project_id, from_stage=stage)
        except ValueError as error:
            raise HTTPException(400, "Unknown stage") from error
        return {"status": "started", "stage": stage}

    @app.post("/api/projects/{project_id}/retry-missing", status_code=202)
    async def retry_missing(project_id: str) -> dict[str, str]:
        if not coordinator:
            raise HTTPException(503, "Photos provider unavailable")
        _ensure_project_exists(app_paths.database, project_id)
        coordinator.start(project_id, from_stage="previews")
        return {"status": "started", "stage": "previews"}

    @app.get("/api/jobs/{job_id}")
    async def job_api(job_id: str) -> dict[str, object]:
        with database_connection(app_paths.database) as connection:
            return _public_job(repository.get_job(connection, job_id))

    @app.get("/api/projects/{project_id}/assets")
    async def assets_api(
        project_id: str,
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=200)] = 60,
        disposition: Literal["keep", "review", "reject"] | None = None,
        flag: str | None = None,
        sort: Literal["taken_at", "technical_quality", "filename", "resolution"] = "taken_at",
        order: Literal["asc", "desc"] = "asc",
    ) -> dict[str, object]:
        with database_connection(app_paths.database) as connection:
            assets = repository.list_assets(connection, project_id)
        if disposition:
            assets = [asset for asset in assets if asset.get("final_disposition") == disposition]
        if flag:
            assets = [asset for asset in assets if flag in asset.get("flags", [])]
        assets.sort(key=lambda asset: _asset_sort_key(asset, sort), reverse=order == "desc")
        start = (page - 1) * page_size
        return {
            "items": [_public_asset(asset) for asset in assets[start : start + page_size]],
            "total": len(assets),
            "page": page,
            "page_size": page_size,
        }

    @app.get("/api/projects/{project_id}/assets/{asset_uuid}")
    async def asset_api(project_id: str, asset_uuid: str) -> dict[str, object]:
        with database_connection(app_paths.database) as connection:
            return _public_asset(repository.get_asset(connection, project_id, asset_uuid))

    @app.patch("/api/projects/{project_id}/assets/{asset_uuid}/decision")
    async def decision_api(
        project_id: str, asset_uuid: str, payload: DecisionPatch
    ) -> dict[str, object]:
        with database_connection(app_paths.database) as connection:
            repository.set_manual_decision(
                connection, project_id, asset_uuid, payload.disposition, payload.note
            )
            return _public_asset(repository.get_asset(connection, project_id, asset_uuid))

    @app.patch("/api/projects/{project_id}/assets/batch-decision")
    async def batch_decision_api(project_id: str, payload: BatchDecisionPatch) -> dict[str, int]:
        with database_connection(app_paths.database) as connection:
            for uuid in payload.asset_uuids:
                repository.set_manual_decision(connection, project_id, uuid, payload.disposition)
        return {"updated": len(payload.asset_uuids)}

    @app.patch("/api/projects/{project_id}/duplicate-groups/{group_id}/leader")
    async def leader_api(project_id: str, group_id: str, payload: LeaderPatch) -> dict[str, str]:
        with database_connection(app_paths.database) as connection:
            repository.set_group_leader(connection, project_id, group_id, payload.asset_uuid)
        if coordinator:
            coordinator.start(project_id, from_stage="decisions")
        return {"leader_uuid": payload.asset_uuid}

    @app.post("/api/projects/{project_id}/decisions/recalculate", status_code=202)
    async def recalculate(project_id: str) -> dict[str, str]:
        if not coordinator:
            raise HTTPException(503, "Photos provider unavailable")
        _ensure_project_exists(app_paths.database, project_id)
        coordinator.start(project_id, from_stage="decisions")
        return {"status": "started"}

    @app.post("/api/projects/{project_id}/publish/dry-run")
    async def publish_dry_run(project_id: str) -> dict[str, object]:
        if not publisher:
            raise HTTPException(503, "Publisher unavailable")
        try:
            return _public_publish(publisher.dry_run(project_id))
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @app.post("/api/projects/{project_id}/publish/best/dry-run")
    async def publish_best_dry_run(project_id: str) -> dict[str, object]:
        if not publisher:
            raise HTTPException(503, "Publisher unavailable")
        try:
            return _public_publish(publisher.dry_run(project_id, "best"))
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @app.post("/api/projects/{project_id}/export/selected")
    async def export_selected(project_id: str) -> dict[str, object]:
        with database_connection(app_paths.database) as connection:
            assets = [
                asset
                for asset in repository.list_assets(connection, project_id)
                if asset.get("final_disposition") == "keep" and asset.get("review_path")
            ]
        if not assets:
            raise HTTPException(409, "Нет доступных отобранных превью")
        export_dir = app_paths.cache_dir / project_id / "export"
        export_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        archive = export_dir / "selected-review-previews.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
            for index, asset in enumerate(assets, start=1):
                source = ensure_within(Path(str(asset["review_path"])), app_paths.cache_dir)
                if source.is_file():
                    output.write(source, f"{index:04d}-{source.name}")
        return {
            "count": len(assets),
            "url": f"/downloads/{project_id}/selected-review-previews.zip",
        }

    @app.get("/downloads/{project_id}/selected-review-previews.zip")
    async def download_selected(project_id: str):
        _ensure_project_exists(app_paths.database, project_id)
        archive = ensure_within(
            app_paths.cache_dir / project_id / "export" / "selected-review-previews.zip",
            app_paths.cache_dir,
        )
        if not archive.is_file():
            raise HTTPException(404, "Сначала подготовьте экспорт")
        return FileResponse(archive, filename=f"PhotoCurator-{project_id}-Selected.zip")

    @app.post("/api/projects/{project_id}/publish/apply")
    async def publish_apply(project_id: str, payload: PublishApplyRequest) -> dict[str, object]:
        if not payload.confirmed:
            raise HTTPException(400, "Explicit confirmation required")
        if not publisher:
            raise HTTPException(503, "Publisher unavailable")
        with database_connection(app_paths.database) as connection:
            publish = repository.get_publish(connection, payload.publish_id)
        if publish["project_id"] != project_id:
            raise HTTPException(404, "Publish not found")
        try:
            return _public_publish(publisher.apply(payload.publish_id))
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @app.post("/api/projects/{project_id}/cache/clean")
    async def clean_cache(project_id: str) -> dict[str, str]:
        _ensure_project_exists(app_paths.database, project_id)
        project_cache = app_paths.cache_dir / project_id
        if project_cache.exists():
            safe_rmtree(project_cache, app_paths.cache_dir)
        with database_connection(app_paths.database) as connection:
            connection.execute(
                """
                UPDATE assets SET review_path=NULL, thumbnail_path=NULL, cache_state='pending',
                    source_fingerprint=NULL WHERE project_id=?
                """,
                (project_id,),
            )
            repository.set_project_state(connection, project_id, "cache_cleaned")
        return {"status": "cleaned"}

    return app


def _context(request: Request, **values: object) -> dict[str, object]:
    return {
        "request": request,
        "version": __version__,
        "disposition_labels": {
            "keep": "Отобрано",
            "review": "Проверить",
            "reject": "Исключено",
        },
        "flag_labels": {
            "possible_blur": "возможный смаз",
            "underexposed": "слишком темно",
            "overexposed": "пересвет",
            "low_contrast": "низкий контраст",
            "exact_duplicate": "точная копия",
            "near_duplicate": "похожий кадр",
            "duplicate_leader": "лучший в серии",
            "duplicate_loser": "слабее в серии",
            "favorite_protected": "избранное Photos",
            "edited_protected": "отредактировано",
            "best_candidate": "лучшее",
            "missing_preview": "нет превью",
            "analysis_error": "ошибка анализа",
            "ambiguous_duplicate": "неуверенная серия",
            "diversity_limit": "похожая сцена уже представлена",
        },
        **values,
    }


def _try_real_provider() -> PhotosProvider | None:
    try:
        return OSXPhotosProvider()
    except Exception:
        return None


def _require_provider(provider: PhotosProvider | None) -> None:
    if provider is None:
        raise HTTPException(503, "Photos Library недоступна; откройте Doctor")


def _ensure_demo_project(
    paths: ApplicationPaths,
    provider: PhotosProvider,
    coordinator: PipelineCoordinator,
) -> None:
    with database_connection(paths.database) as connection:
        projects = repository.list_projects(connection)
        demo = next(
            (project for project in projects if project["album_id"] == FakePhotosProvider.ALBUM_ID),
            None,
        )
        if not demo:
            album = provider.list_regular_albums()[0]
            project_id = repository.create_project(
                connection,
                project_id="demo-project",
                name="Черногория",
                library=provider.get_current_library(),
                album=album,
            )
        else:
            project_id = str(demo["id"])
            if demo["state"] == "ready":
                return
    coordinator.run(project_id)


def _project_data(database_path: Path, project_id: str):
    with database_connection(database_path) as connection:
        try:
            project = repository.get_project(connection, project_id)
        except KeyError as error:
            raise HTTPException(404, "Project not found") from error
        return (
            project,
            repository.project_summary(connection, project_id),
            repository.latest_jobs(connection, project_id),
        )


def _ensure_project_exists(database_path: Path, project_id: str) -> None:
    with database_connection(database_path) as connection:
        repository.get_project(connection, project_id)


def _stage_views(
    jobs: list[dict[str, object]],
    summary: dict[str, object],
    publish: dict[str, object] | None,
) -> list[dict[str, object]]:
    latest = {str(job["stage"]): job for job in jobs}
    analysis_jobs = [
        latest.get(code, {}) for code in ("metrics", "duplicates", "vision", "decisions")
    ]
    analysis_status = _combined_status(analysis_jobs)
    analysis = {
        "stage": "metrics",
        "status": analysis_status,
        "processed_items": sum(int(job.get("processed_items") or 0) for job in analysis_jobs),
        "total_items": sum(int(job.get("total_items") or 0) for job in analysis_jobs),
        "warning_count": sum(int(job.get("warning_count") or 0) for job in analysis_jobs),
        "error_count": sum(int(job.get("error_count") or 0) for job in analysis_jobs),
        "current_message": "Качество, серии, лица и итоговый отбор",
        "started_at": next((job.get("started_at") for job in analysis_jobs if job), None),
        "finished_at": next(
            (job.get("finished_at") for job in reversed(analysis_jobs) if job), None
        ),
    }
    total = int(summary.get("total") or 0)
    reviewed = int(summary.get("reviewed") or 0)
    review_status = (
        "done"
        if total and reviewed >= total
        else ("active" if analysis_status == "done" else "pending")
    )
    publish_status = str(publish.get("status")) if publish else "pending"
    publish_stage_status = {
        "applied": "done",
        "dry_run_ok": "active",
        "dry_run_failed": "warning",
        "apply_failed": "error",
    }.get(publish_status, "pending")
    stages = [
        {
            "stage": "doctor",
            "status": "done",
            "processed_items": 1,
            "total_items": 1,
            "current_message": "Среда проекта зафиксирована",
        },
        latest.get("inventory", {"stage": "inventory"}),
        latest.get("previews", {"stage": "previews"}),
        analysis,
        {
            "stage": "review",
            "status": review_status,
            "processed_items": reviewed,
            "total_items": total,
            "current_message": f"Ручные решения: {reviewed} из {total}",
        },
        {
            "stage": "publish",
            "status": publish_stage_status,
            "processed_items": int(publish.get("asset_count") or 0) if publish else 0,
            "total_items": int(summary.get("reject") or 0),
            "current_message": f"Publish: {publish_status}",
        },
    ]
    labels = {
        "doctor": "Проверка среды",
        "inventory": "Инвентаризация",
        "previews": "Подготовка preview",
        "metrics": "Анализ · Поиск дубликатов",
        "review": "Проверка результата",
        "publish": "Альбом с результатом",
    }
    return [
        _stage_view(index, stage, labels[str(stage["stage"])])
        for index, stage in enumerate(stages, 1)
    ]


def _stage_view(number: int, job: dict[str, object], label: str) -> dict[str, object]:
    total = int(job.get("total_items") or 0)
    processed = int(job.get("processed_items") or 0)
    progress = (
        int(processed / total * 100) if total else (100 if job.get("status") == "done" else 0)
    )
    elapsed = _elapsed_seconds(job.get("started_at"), job.get("finished_at"))
    status = str(job.get("status", "pending"))
    return {
        "number": number,
        "code": job.get("stage"),
        "name": label,
        "status": status,
        "status_label": {
            "pending": "Ожидает",
            "running": "В работе",
            "active": "Доступно",
            "done": "Готово",
            "warning": "Внимание",
            "error": "Ошибка",
            "interrupted": "Прервано",
        }.get(status, status),
        "progress": min(progress, 100),
        "processed": processed,
        "total": total,
        "message": job.get("current_message") or "Ожидает",
        "warnings": int(job.get("warning_count") or 0),
        "errors": int(job.get("error_count") or 0),
        "elapsed": _duration_label(elapsed),
        "throughput": f"{processed / elapsed:.1f}/с" if processed and elapsed else "—",
        "retryable": job.get("stage")
        in {"inventory", "previews", "metrics", "duplicates", "decisions"},
    }


def _combined_status(jobs: list[dict[str, object]]) -> str:
    statuses = [str(job.get("status")) for job in jobs if job]
    # Stages are sequential. A running job belongs to the current attempt, while
    # an error in a later stage can be retained from the previous attempt until
    # that stage is recreated.
    for status in ("running", "error", "interrupted", "warning"):
        if status in statuses:
            return status
    return "done" if statuses and all(status == "done" for status in statuses) else "pending"


def _album_json(album, *, disabled: bool) -> dict[str, object]:
    return {
        "id": album.id,
        "name": album.name,
        "full_path": album.full_path,
        "photo_count": album.photo_count,
        "video_count": album.video_count,
        "shared": album.is_shared,
        "disabled": disabled,
    }


def _public_asset(asset: dict[str, object]) -> dict[str, object]:
    hidden = {
        "source_path",
        "review_path",
        "thumbnail_path",
        "metadata_json",
        "apple_scores_json",
        "library_path",
        "database_path",
    }
    public = {key: value for key, value in asset.items() if key not in hidden}
    project_id, uuid = str(asset["project_id"]), str(asset["asset_uuid"])
    public["thumbnail_url"] = f"/media/{project_id}/thumbnail/{uuid}"
    public["review_url"] = f"/media/{project_id}/review/{uuid}"
    return public


def _public_publish(publish: dict[str, object]) -> dict[str, object]:
    hidden = {
        "uuid_file",
        "dry_run_stdout",
        "dry_run_stderr",
        "apply_stdout",
        "apply_stderr",
    }
    return {key: value for key, value in publish.items() if key not in hidden}


def _public_job(job: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in job.items() if key != "error_text"}


def _safe_album_lists(provider: PhotosProvider | None) -> tuple[list, list, str | None]:
    if provider is None:
        return [], [], "Photos provider недоступен"
    try:
        return provider.list_regular_albums(), provider.list_shared_albums(), None
    except Exception:
        return [], [], "Нет доступа к Photos Library. Откройте Doctor для инструкции."


def _home_environment(
    provider: PhotosProvider | None, albums: list, provider_error: str | None
) -> dict[str, str]:
    try:
        version = importlib.metadata.version("osxphotos")
    except importlib.metadata.PackageNotFoundError:
        version = "не установлен"
    library = None
    if provider and not provider_error:
        with suppress(Exception):
            library = provider.get_current_library()
    return {
        "library": library.library_path if library else "недоступна",
        "database_version": library.database_version or "—" if library else "—",
        "osxphotos_version": version,
        "compatibility": "готово" if library and albums else "требует внимания",
    }


def _filter_assets(assets: list[dict[str, object]], category: str) -> list[dict[str, object]]:
    if category == "all":
        return assets
    if category in {"keep", "review", "reject"}:
        return [asset for asset in assets if asset.get("final_disposition") == category]
    if category == "duplicates":
        return [asset for asset in assets if asset.get("group_id")]
    if category == "favorite":
        return [asset for asset in assets if asset.get("favorite")]
    if category == "missing":
        return [asset for asset in assets if asset.get("cache_state") == "missing"]
    if category == "errors":
        return [asset for asset in assets if "error" in str(asset.get("cache_state"))]
    if category == "best":
        return [asset for asset in assets if "best_candidate" in asset.get("flags", [])]
    if category == "resolution":
        return [
            asset
            for asset in assets
            if any("resolution" in str(flag) for flag in asset.get("flags", []))
        ]
    return assets


def _asset_sort_key(asset: dict[str, object], sort: str) -> tuple[bool, object]:
    if sort == "technical_quality":
        value = asset.get("technical_quality")
    elif sort == "filename":
        value = str(asset.get("current_filename") or "").casefold()
    elif sort == "resolution":
        value = int(asset.get("width") or 0) * int(asset.get("height") or 0)
    else:
        value = str(asset.get("taken_at") or "")
    return value is None, value if value is not None else ""


def _quality_distribution(assets: list[dict[str, object]]) -> list[int]:
    buckets = [0] * 10
    for asset in assets:
        value = asset.get("technical_quality")
        if value is not None:
            buckets[min(9, max(0, int(float(value) * 10)))] += 1
    return buckets


def _directory_size(root: Path) -> int:
    return sum(file_stat.st_size for file_stat in _cache_file_stats(root))


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _last_access(root: Path) -> str:
    file_stats = _cache_file_stats(root)
    if not file_stats:
        return "—"
    timestamp = max(file_stat.st_atime for file_stat in file_stats)
    return datetime.fromtimestamp(timestamp).astimezone().strftime("%d.%m.%Y %H:%M")


def _cache_file_stats(root: Path) -> list[Any]:
    if not root.is_dir():
        return []
    file_stats = []
    try:
        for path in root.rglob("*"):
            try:
                file_stat = path.stat()
            except OSError:
                # Atomic preview writes may rename a temporary file during this scan.
                continue
            if stat.S_ISREG(file_stat.st_mode):
                file_stats.append(file_stat)
    except OSError:
        # Cache metrics are informational and must never break the dashboard.
        pass
    return file_stats


def _elapsed_seconds(started: object, finished: object) -> float:
    if not started:
        return 0.0
    try:
        start = datetime.fromisoformat(str(started))
        end = datetime.fromisoformat(str(finished)) if finished else datetime.now(UTC)
        return max(0.0, (end - start).total_seconds())
    except ValueError:
        return 0.0


def _duration_label(seconds: float) -> str:
    if seconds < 1:
        return "<1 с" if seconds else "—"
    minutes, remainder = divmod(int(seconds), 60)
    return f"{minutes} мин {remainder} с" if minutes else f"{remainder} с"
