from pathlib import Path

from fastapi.testclient import TestClient

from photo_curator.app import create_app
from photo_curator.paths import default_application_paths
from photo_curator.web.security import SESSION_COOKIE, SessionSecrets


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
        response = client.get("/")

    assert response.status_code == 200
    assert "Черногория" in response.text
    assert "Ход обработки" in response.text
    assert "Публикация Reject-альбома" in response.text


def test_api_status_does_not_expose_local_paths(tmp_path: Path) -> None:
    with TestClient(make_app(tmp_path)) as client:
        client.cookies.set(SESSION_COOKIE, "session-secret")
        response = client.get("/api/status")

    assert response.status_code == 200
    assert response.json()["mode"] == "demo"
    assert str(tmp_path) not in response.text
