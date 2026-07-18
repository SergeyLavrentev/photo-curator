from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

from photo_curator import __version__
from photo_curator.db.connection import database_connection
from photo_curator.db.migrations import SCHEMA_VERSION, migrate
from photo_curator.db.repository import mark_running_jobs_interrupted
from photo_curator.paths import ApplicationPaths, default_application_paths
from photo_curator.web.security import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    SessionSecrets,
    has_valid_session,
)
from photo_curator.web.view_models import DEMO_STAGES, DEMO_SUMMARY

PACKAGE_ROOT = Path(__file__).parent
templates = Jinja2Templates(directory=PACKAGE_ROOT / "web" / "templates")


def create_app(
    *,
    demo: bool = False,
    paths: ApplicationPaths | None = None,
    session_secrets: SessionSecrets | None = None,
) -> FastAPI:
    app_paths = paths or default_application_paths()
    secrets_ = session_secrets or SessionSecrets.generate()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        app_paths.ensure()
        with database_connection(app_paths.database) as connection:
            migrate(connection)
            mark_running_jobs_interrupted(connection)
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
        if startup_token and secrets_compare(startup_token, secrets_.startup_token):
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
        return await call_next(request)

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request):
        context = {
            "request": request,
            "version": __version__,
            "demo": demo,
            "stages": DEMO_STAGES if demo else _empty_stages(),
            "summary": DEMO_SUMMARY if demo else [],
            "csrf_token": secrets_.csrf_token,
        }
        return templates.TemplateResponse(request, "home.html", context)

    @app.get("/doctor", response_class=HTMLResponse)
    async def doctor(request: Request):
        checks = [
            ("Режим", "OK", "Demo provider" if demo else "Ожидает PhotosProvider"),
            ("Loopback server", "OK", "127.0.0.1"),
            ("SQLite schema", "OK", f"version {SCHEMA_VERSION}"),
            ("Cache", "OK", "Локальный каталог доступен"),
        ]
        return templates.TemplateResponse(
            request,
            "doctor.html",
            {"request": request, "checks": checks, "demo": demo, "version": __version__},
        )

    @app.get("/api/status", response_class=JSONResponse)
    async def status() -> dict[str, Any]:
        return {
            "status": "ready",
            "mode": "demo" if demo else "local",
            "version": __version__,
            "schema_version": SCHEMA_VERSION,
        }

    return app


def secrets_compare(candidate: str, expected: str) -> bool:
    import secrets

    return secrets.compare_digest(candidate, expected)


def _empty_stages() -> list[dict[str, Any]]:
    names = [
        "Проверка среды",
        "Инвентаризация",
        "Подготовка preview",
        "Анализ",
        "Ручное ревью",
        "Публикация Reject-альбома",
    ]
    return [
        {
            "number": index,
            "name": name,
            "status": "pending",
            "status_label": "Ожидает",
            "detail": "Создайте проект, чтобы начать",
            "progress": 0,
        }
        for index, name in enumerate(names, start=1)
    ]
