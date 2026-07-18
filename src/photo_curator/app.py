from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
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
            distribution = _quality_distribution(repository.list_assets(connection, project_id))
        summary["cache_size"] = _human_size(_directory_size(app_paths.cache_dir / project_id))
        return templates.TemplateResponse(
            request,
            "project.html",
            _context(
                request,
                demo=demo,
                project=project,
                summary=summary,
                distribution=distribution,
                stages=_stage_views(jobs),
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
    async def publish_page(request: Request, project_id: str):
        project, summary, _ = _project_data(app_paths.database, project_id)
        validation = publisher.validate(project_id) if publisher else None
        with database_connection(app_paths.database) as connection:
            latest = repository.latest_publish(connection, project_id)
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
            )
        return {"id": project_id, "url": f"/projects/{project_id}"}

    @app.get("/api/projects/{project_id}")
    async def project_api(project_id: str) -> dict[str, object]:
        project, summary, jobs = _project_data(app_paths.database, project_id)
        project.pop("library_path", None)
        project.pop("database_path", None)
        return {"project": project, "summary": summary, "jobs": jobs}

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
        try:
            coordinator.start(project_id)
        except RuntimeError as error:
            raise HTTPException(409, str(error)) from error
        return {"status": "started", "project_id": project_id}

    @app.post("/api/projects/{project_id}/pipeline/resume", status_code=202)
    async def resume_pipeline(project_id: str) -> dict[str, str]:
        if not coordinator:
            raise HTTPException(503, "Photos provider unavailable")
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
        try:
            coordinator.start(project_id, from_stage=stage)
        except ValueError as error:
            raise HTTPException(400, "Unknown stage") from error
        return {"status": "started", "stage": stage}

    @app.post("/api/projects/{project_id}/retry-missing", status_code=202)
    async def retry_missing(project_id: str) -> dict[str, str]:
        if not coordinator:
            raise HTTPException(503, "Photos provider unavailable")
        coordinator.start(project_id, from_stage="previews")
        return {"status": "started", "stage": "previews"}

    @app.get("/api/jobs/{job_id}")
    async def job_api(job_id: str) -> dict[str, object]:
        with database_connection(app_paths.database) as connection:
            return repository.get_job(connection, job_id)

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
        project_cache = app_paths.cache_dir / project_id
        if project_cache.exists():
            safe_rmtree(project_cache, app_paths.cache_dir)
        with database_connection(app_paths.database) as connection:
            repository.get_project(connection, project_id)
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
    return {"request": request, "version": __version__, **values}


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


def _stage_views(jobs: list[dict[str, object]]) -> list[dict[str, object]]:
    labels = [
        ("inventory", "Инвентаризация"),
        ("previews", "Подготовка preview"),
        ("metrics", "Технический анализ"),
        ("duplicates", "Поиск дубликатов"),
        ("decisions", "Решения"),
    ]
    latest = {str(job["stage"]): job for job in jobs}
    views = []
    for number, (code, label) in enumerate(labels, start=1):
        job = latest.get(code, {})
        total = int(job.get("total_items") or 0)
        processed = int(job.get("processed_items") or 0)
        progress = (
            int(processed / total * 100) if total else (100 if job.get("status") == "done" else 0)
        )
        views.append(
            {
                "number": number,
                "code": code,
                "name": label,
                "status": job.get("status", "pending"),
                "progress": progress,
                "processed": processed,
                "total": total,
                "message": job.get("current_message") or "Ожидает",
                "warnings": int(job.get("warning_count") or 0),
                "errors": int(job.get("error_count") or 0),
            }
        )
    return views


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


def _safe_album_lists(provider: PhotosProvider | None) -> tuple[list, list, str | None]:
    if provider is None:
        return [], [], "Photos provider недоступен"
    try:
        return provider.list_regular_albums(), provider.list_shared_albums(), None
    except Exception:
        return [], [], "Нет доступа к Photos Library. Откройте Doctor для инструкции."


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
    flag = "best_candidate" if category == "best" else "low_resolution"
    if category in {"best", "resolution"}:
        return [asset for asset in assets if flag in asset.get("flags", [])]
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
    if not root.is_dir():
        return 0
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"
