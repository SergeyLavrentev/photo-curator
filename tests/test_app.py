from pathlib import Path

from fastapi.testclient import TestClient

from photo_curator.app import _directory_size, _last_access, _stage_views, create_app
from photo_curator.db.connection import database_connection
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


def test_settings_can_request_managed_backend_shutdown(tmp_path: Path) -> None:
    calls: list[str] = []
    app = create_app(
        demo=True,
        paths=default_application_paths(tmp_path),
        session_secrets=SessionSecrets("startup-secret", "session-secret", "csrf-secret"),
        shutdown_callback=lambda: calls.append("shutdown"),
    )
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, "session-secret")
        client.cookies.set(CSRF_COOKIE, "csrf-secret")
        page = client.get("/settings")
        response = client.post(
            "/api/system/shutdown",
            headers={"X-CSRF-Token": "csrf-secret"},
        )

    assert page.status_code == 200
    assert "Backend запущен" in page.text
    assert "Остановить backend" in page.text
    assert response.status_code == 202
    assert response.json() == {"status": "stopping"}
    assert calls == ["shutdown"]


def test_demo_dashboard_is_rendered_after_login(tmp_path: Path) -> None:
    with TestClient(make_app(tmp_path)) as client:
        client.cookies.set(SESSION_COOKIE, "session-secret")
        home = client.get("/")
        response = client.get("/projects/demo-project")

    assert home.status_code == 200
    assert "Черногория" in home.text
    assert response.status_code == 200
    assert "Черногория" in response.text
    assert "Проверьте предложенный отбор" in response.text
    assert "Статус и детали анализа" in response.text
    assert "Серий / дублей" in response.text
    assert "Проверка среды" in response.text
    assert "Проверка результата" in response.text
    assert "Альбом с результатом" in response.text
    assert "Отобрано" in response.text
    assert "pipeline-rail" in response.text
    assert "active-stage-detail" in response.text
    assert "data-active-stage-health" in response.text
    assert "data-active-stage-rate" in response.text
    assert "preview-cache" in response.text
    assert "Видео пока не анализируются" in response.text
    assert "Готово" in response.text
    assert "Photos Library" in home.text
    assert "osxphotos" in home.text
    assert "видео пропустим" in home.text
    assert "Выбрать и перейти к анализу" in home.text
    assert home.text.count('class="summary-card') == 0
    assert '<meta name="csrf-token" content="csrf-secret">' in response.text


def test_static_ui_contract_is_dark_compact_and_uses_one_pipeline_detail(tmp_path: Path) -> None:
    with TestClient(make_app(tmp_path)) as client:
        client.cookies.set(SESSION_COOKIE, "session-secret")
        css = client.get("/static/app.css").text
        home = client.get("/").text
        dashboard = client.get("/projects/demo-project").text

    assert "color-scheme: dark" in css
    assert "grid-template-columns: repeat(6" in css
    assert "Выберите альбом" in home
    assert "Настроить стиль отбора" in home
    assert "Статус и технические детали" in home
    assert 'class="journey"' in home
    assert "big-action" in home
    assert "project-card" not in home
    assert "environment-grid" not in home
    assert dashboard.count('class="active-stage-detail"') == 1
    assert dashboard.count("summary-card") == 4


def test_project_name_defaults_to_selected_album(tmp_path: Path) -> None:
    with TestClient(make_app(tmp_path)) as client:
        client.cookies.set(SESSION_COOKIE, "session-secret")
        client.cookies.set(CSRF_COOKIE, "csrf-secret")
        created = client.post(
            "/api/projects",
            headers={"X-CSRF-Token": "csrf-secret"},
            json={"album_id": FakePhotosProvider.ALBUM_ID, "name": ""},
        )
        payload = client.get(f"/api/projects/{created.json()['id']}").json()

    assert created.status_code == 201
    assert payload["project"]["name"] == "Черногория"


def test_mobile_ui_keeps_global_navigation_and_explains_horizontal_scroll(tmp_path: Path) -> None:
    with TestClient(make_app(tmp_path)) as client:
        client.cookies.set(SESSION_COOKIE, "session-secret")
        css = client.get("/static/app.css").text
        dashboard = client.get("/projects/demo-project").text
        review = client.get("/projects/demo-project/review?category=keep").text
        javascript = client.get("/static/app.js").text

    assert ".brand-name" in css
    assert ".topbar nav { display: flex" in css
    assert "scroll-snap-type: x proximity" in css
    assert "scrollbar-width: thin" in css
    assert ".review-toolbar { top: 52px; grid-template-columns: repeat(4, minmax(0, 1fr)); }" in css
    assert ".review-toolbar span { grid-column: 1 / -1; }" in css
    assert ".duplicate-members { grid-template-columns: repeat(2, minmax(0, 1fr)); }" in css
    assert ".pagination { align-items: flex-start; flex-wrap: wrap; }" in css
    assert "project-journey" in dashboard
    assert "Дополнительные фильтры и сортировка" in review
    assert "category=resolution" in review
    assert "payload.stages.forEach" in javascript


def test_pipeline_api_exposes_truthful_aggregated_analysis_stage(tmp_path: Path) -> None:
    with TestClient(make_app(tmp_path)) as client:
        client.cookies.set(SESSION_COOKIE, "session-secret")
        payload = client.get("/api/projects/demo-project").json()

    assert len(payload["stages"]) == 6
    analysis = next(stage for stage in payload["stages"] if stage["code"] == "metrics")
    assert analysis["status"] == "done"
    assert analysis["processed"] == analysis["total"]
    assert analysis["warnings"] >= 0
    assert "throughput" in analysis


def test_aggregated_analysis_prefers_current_running_state_over_stale_done_jobs() -> None:
    jobs = [
        {"stage": "metrics", "status": "done", "processed_items": 10, "total_items": 10},
        {
            "stage": "duplicates",
            "status": "running",
            "processed_items": 2,
            "total_items": 10,
            "warning_count": 1,
        },
        {"stage": "vision", "status": "done", "processed_items": 10, "total_items": 10},
        {"stage": "decisions", "status": "error", "processed_items": 10, "total_items": 10},
    ]

    stages = _stage_views(jobs, {"total": 10, "reviewed": 0, "reject": 0}, None)
    analysis = next(stage for stage in stages if stage["code"] == "metrics")

    assert analysis["status"] == "running"
    assert analysis["processed"] == 32
    assert analysis["total"] == 40
    assert analysis["warnings"] == 1


def test_resolution_warning_gallery_filter_uses_real_resolution_flags(tmp_path: Path) -> None:
    with TestClient(make_app(tmp_path)) as client:
        client.cookies.set(SESSION_COOKIE, "session-secret")
        response = client.get("/projects/demo-project/review?category=resolution")

    assert response.status_code == 200
    assert "category=resolution" in response.text
    assert any(
        flag in response.text
        for flag in ("lower_resolution_copy", "leader_lower_resolution", "resolution_inversion")
    )


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


def test_missing_preview_has_a_visible_retry_action(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, "session-secret")
        with database_connection(default_application_paths(tmp_path).database) as connection:
            connection.execute(
                "UPDATE assets SET cache_state='missing' "
                "WHERE project_id='demo-project' AND asset_uuid='demo-001'"
            )
        dashboard = client.get("/projects/demo-project")
        javascript = client.get("/static/app.js")

    assert 'data-action="retry-missing"' in dashboard.text
    assert "Повторить недоступные preview" in dashboard.text
    assert "/retry-missing" in javascript.text


def test_interrupted_project_exposes_resume_and_active_stage_retry(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, "session-secret")
        with database_connection(default_application_paths(tmp_path).database) as connection:
            connection.execute("UPDATE projects SET state='interrupted' WHERE id='demo-project'")
            connection.execute(
                "UPDATE jobs SET status='interrupted' "
                "WHERE id=(SELECT id FROM jobs WHERE project_id='demo-project' "
                "ORDER BY started_at DESC LIMIT 1)"
            )
        dashboard = client.get("/projects/demo-project")
        javascript = client.get("/static/app.js")

    assert 'data-action="pipeline-resume"' in dashboard.text
    assert "Продолжить анализ" in dashboard.text
    assert "Повторить с этого этапа" in dashboard.text
    assert "/pipeline/${action}" in javascript.text


def test_user_started_pipeline_refreshes_even_when_it_finishes_before_first_poll(
    tmp_path: Path,
) -> None:
    with TestClient(make_app(tmp_path)) as client:
        client.cookies.set(SESSION_COOKIE, "session-secret")
        javascript = client.get("/static/app.js").text

    assert "pollProject(root, button, true)" in javascript
    assert "reloadOnTerminal || previousState !== state" in javascript


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


def test_shared_album_copy_page_and_confirmed_plan_are_exposed(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, "session-secret")
        client.cookies.set(CSRF_COOKIE, "csrf-secret")
        page = client.get("/shared/demo-shared-album/copy")
        preview = client.get("/shared-media/demo-shared-album/demo-001")
        planned = client.post(
            "/api/shared/demo-shared-album/copies",
            headers={"X-CSRF-Token": "csrf-secret"},
            json={
                "mode": "sample",
                "sample_size": 3,
                "destination_album_name": "PhotoCurator Shared Test",
            },
        )
        plan_page = client.get(planned.json()["url"])
        app.state.shared_copier.run(planned.json()["id"])
        copy_status = client.get(f"/api/shared-copies/{planned.json()['id']}").json()
        local_album_id = copy_status["destination_album_id"]
        albums = client.get("/api/albums").json()
        created = client.post(
            "/api/projects",
            headers={"X-CSRF-Token": "csrf-secret"},
            json={"album_id": local_album_id, "name": "Disk copy analysis"},
        )
        project = client.get(f"/api/projects/{created.json()['id']}").json()
        publish = client.get(f"/projects/{created.json()['id']}/publish?kind=best")
        local_root = app.state.paths.data_dir / "local_albums" / local_album_id
        assert local_root.is_dir()
        deleted = client.delete(
            f"/api/projects/{created.json()['id']}",
            headers={"X-CSRF-Token": "csrf-secret"},
        )
        albums_after_delete = client.get("/api/albums").json()

    assert page.status_code == 200
    assert "Первые фотографии по времени" in page.text
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/jpeg"
    assert planned.status_code == 201
    assert planned.json()["status"] == "planned"
    assert planned.json()["total_items"] == 3
    assert "Создать локальную копию" in plan_page.text
    assert copy_status["status"] == "done"
    assert any(album["id"] == local_album_id for album in albums["regular"])
    assert created.status_code == 201
    assert project["project"]["settings"]["source_provenance"] == "service_shared_copy"
    assert "импортирует принятые локальные копии" in publish.text
    assert "Нет отобранных фотографий" in publish.text
    assert deleted.status_code == 204
    assert not local_root.exists()
    assert all(album["id"] != local_album_id for album in albums_after_delete["regular"])


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


def test_reject_publish_explains_manual_photos_deletion_semantics(tmp_path: Path) -> None:
    with TestClient(make_app(tmp_path)) as client:
        client.cookies.set(SESSION_COOKIE, "session-secret")
        reject = client.get("/projects/demo-project/publish?kind=reject")
        best = client.get("/projects/demo-project/publish?kind=best")

    assert "Command + Delete" in reject.text
    assert "Recently Deleted" in reject.text
    assert "Delete</strong> убирает фото только из альбома" in reject.text
    assert "Command + Delete" not in best.text


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


def test_manual_shared_copy_provenance_is_persisted_and_explained(tmp_path: Path) -> None:
    with TestClient(make_app(tmp_path)) as client:
        client.cookies.set(SESSION_COOKIE, "session-secret")
        client.cookies.set(CSRF_COOKIE, "csrf-secret")
        created = client.post(
            "/api/projects",
            headers={"X-CSRF-Token": "csrf-secret"},
            json={
                "album_id": FakePhotosProvider.ALBUM_ID,
                "name": "Shared copy",
                "selection_density": "compact",
                "source_provenance": "manual_shared_copy",
            },
        )
        project_id = created.json()["id"]
        dashboard = client.get(created.json()["url"])
        payload = client.get(f"/api/projects/{project_id}").json()

    assert created.status_code == 201
    assert "разрешение может быть ниже оригиналов" in dashboard.text
    assert payload["project"]["settings"]["source_provenance"] == "manual_shared_copy"
    assert payload["project"]["settings"]["selection_density"] == "compact"


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
