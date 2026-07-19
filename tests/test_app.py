from pathlib import Path

from fastapi.testclient import TestClient

from photo_curator.app import _directory_size, _last_access, create_app
from photo_curator.paths import default_application_paths
from photo_curator.photos.fake_provider import FakePhotosProvider
from photo_curator.web.security import CSRF_COOKIE, SESSION_COOKIE, SessionSecrets


def make_app(tmp_path: Path):
    secrets_ = SessionSecrets(
        startup_token="startup-secret",
        session_token="session-secret",
        csrf_token="csrf-secret",
    )
    return create_app(
        demo=True,
        paths=default_application_paths(tmp_path),
        session_secrets=secrets_,
    )


def test_health_is_available_without_session(tmp_path: Path) -> None:
    with TestClient(make_app(tmp_path)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_home_requires_startup_session(tmp_path: Path) -> None:
    with TestClient(make_app(tmp_path)) as client:
        response = client.get("/")

    assert response.status_code == 401
    assert "защищённую ссылку" in response.text


def test_startup_token_sets_strict_cookie_and_cleans_url(tmp_path: Path) -> None:
    with TestClient(make_app(tmp_path), follow_redirects=False) as client:
        response = client.get("/?token=startup-secret")

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert SESSION_COOKIE in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]
    assert "startup-secret" not in response.headers["location"]


def test_demo_dashboard_is_rendered_after_login(tmp_path: Path) -> None:
    with TestClient(make_app(tmp_path)) as client:
        client.cookies.set(SESSION_COOKIE, "session-secret")
        home = client.get("/")
        response = client.get("/projects/demo-project")

    assert home.status_code == 200
    assert "Черногория" in home.text
    assert response.status_code == 200
    assert "Черногория" in response.text
    assert "Ход анализа" in response.text
    assert "Серий / дублей" in response.text
    assert "Проверка среды" in response.text
    assert "Проверка результата" in response.text
    assert "Альбом с результатом" in response.text
    assert "Отобрано" in response.text
    assert "Photos Library" in home.text
    assert "osxphotos" in home.text
    assert '<meta name="csrf-token" content="csrf-secret">' in response.text


def test_cache_metrics_tolerate_atomic_file_replacement(tmp_path: Path, monkeypatch) -> None:
    preview = tmp_path / "preview.jpg"
    preview.write_bytes(b"jpeg")
    original_stat = Path.stat
    calls = 0

    def disappearing_stat(path: Path, *args, **kwargs):
        nonlocal calls
        if path == preview:
            calls += 1
            if calls == 2:
                raise FileNotFoundError(path)
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", disappearing_stat)
    assert _directory_size(tmp_path) == 4
    calls = 0
    assert _last_access(tmp_path) != "—"


def test_api_status_does_not_expose_local_paths(tmp_path: Path) -> None:
    with TestClient(make_app(tmp_path)) as client:
        client.cookies.set(SESSION_COOKIE, "session-secret")
        response = client.get("/api/status")

    assert response.status_code == 200
    assert response.json()["mode"] == "demo"
    assert str(tmp_path) not in response.text


def test_mutating_api_requires_matching_csrf_cookie_and_header(tmp_path: Path) -> None:
    with TestClient(make_app(tmp_path)) as client:
        client.cookies.set(SESSION_COOKIE, "session-secret")
        denied = client.patch(
            "/api/projects/demo-project/assets/demo-002/decision",
            json={"disposition": "keep"},
        )
        client.cookies.set(CSRF_COOKIE, "csrf-secret")
        allowed = client.patch(
            "/api/projects/demo-project/assets/demo-002/decision",
            headers={"X-CSRF-Token": "csrf-secret"},
            json={"disposition": "keep", "note": "manual"},
        )

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json()["final_disposition"] == "keep"
    assert allowed.json()["manual_override"] == 1


def test_shared_albums_are_returned_disabled(tmp_path: Path) -> None:
    with TestClient(make_app(tmp_path)) as client:
        client.cookies.set(SESSION_COOKIE, "session-secret")
        response = client.get("/api/albums")

    assert response.status_code == 200
    assert response.json()["regular"][0]["disabled"] is False
    assert response.json()["shared"][0]["disabled"] is True


def test_media_route_does_not_accept_filesystem_path(tmp_path: Path) -> None:
    with TestClient(make_app(tmp_path)) as client:
        client.cookies.set(SESSION_COOKIE, "session-secret")
        response = client.get("/media/demo-project/thumbnail/demo-001")
        traversal = client.get("/media/demo-project/thumbnail/../../etc/passwd")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert traversal.status_code in {404, 401}


def test_assets_api_filters_flags_and_sorts_without_paths(tmp_path: Path) -> None:
    with TestClient(make_app(tmp_path)) as client:
        client.cookies.set(SESSION_COOKIE, "session-secret")
        response = client.get(
            "/api/projects/demo-project/assets?flag=best_candidate&sort=resolution&order=desc"
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] >= 1
    assert all("best_candidate" in item["flags"] for item in payload["items"])
    assert str(tmp_path) not in response.text


def test_review_page_supports_server_side_categories(tmp_path: Path) -> None:
    with TestClient(make_app(tmp_path)) as client:
        client.cookies.set(SESSION_COOKIE, "session-secret")
        response = client.get("/projects/demo-project/review?category=reject")

    assert response.status_code == 200
    assert "страница 1 из 1" in response.text
    assert "disposition-reject" in response.text
    assert "disposition-keep" not in response.text


def test_duplicate_and_publish_pages_expose_manual_and_safety_controls(tmp_path: Path) -> None:
    with TestClient(make_app(tmp_path)) as client:
        client.cookies.set(SESSION_COOKIE, "session-secret")
        duplicates = client.get("/projects/demo-project/duplicates")
        publish = client.get("/projects/demo-project/publish")

    assert duplicates.status_code == 200
    assert "data-duplicate-decision" in duplicates.text
    assert "similarity" in duplicates.text
    assert "sharpness" in duplicates.text
    assert publish.status_code == 200
    assert "Сохранить в Photos" in publish.text
    assert "Best-альбом" in publish.text
    assert "Оригиналы и исходный альбом не меняются" in publish.text
    assert "Reject — исключённые" in publish.text


def test_selected_preview_export_is_prepared_and_downloaded(tmp_path: Path) -> None:
    with TestClient(make_app(tmp_path)) as client:
        client.cookies.set(SESSION_COOKIE, "session-secret")
        client.cookies.set(CSRF_COOKIE, "csrf-secret")
        prepared = client.post(
            "/api/projects/demo-project/export/selected",
            headers={"X-CSRF-Token": "csrf-secret"},
        )
        downloaded = client.get(prepared.json()["url"])

    assert prepared.status_code == 200
    assert prepared.json()["count"] >= 1
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"] == "application/zip"


class BrokenPhotosProvider(FakePhotosProvider):
    def list_regular_albums(self):
        raise PermissionError("private local path must not escape")

    def list_shared_albums(self):
        raise PermissionError("private local path must not escape")


def test_closed_photos_permission_degrades_to_doctor_message(tmp_path: Path) -> None:
    provider = BrokenPhotosProvider(tmp_path / "sources")
    secrets_ = SessionSecrets("startup-secret", "session-secret", "csrf-secret")
    app = create_app(
        paths=default_application_paths(tmp_path),
        session_secrets=secrets_,
        provider=provider,
    )
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, "session-secret")
        home = client.get("/")
        albums = client.get("/api/albums")

    assert home.status_code == 200
    assert "Photos Library пока недоступна" in home.text
    assert "private local path" not in home.text
    assert albums.status_code == 503
    assert "private local path" not in albums.text


def test_missing_resources_return_404_and_jobs_hide_error_detail(tmp_path: Path) -> None:
    with TestClient(make_app(tmp_path)) as client:
        client.cookies.set(SESSION_COOKIE, "session-secret")
        client.cookies.set(CSRF_COOKIE, "csrf-secret")
        headers = {"X-CSRF-Token": "csrf-secret"}
        missing_project = client.post("/api/projects/absent/pipeline/start", headers=headers)
        missing_asset = client.patch(
            "/api/projects/demo-project/assets/absent/decision",
            headers=headers,
            json={"disposition": "keep"},
        )
        project = client.get("/api/projects/demo-project")

    assert missing_project.status_code == 404
    assert missing_asset.status_code == 404
    assert all("error_text" not in job for job in project.json()["jobs"])
