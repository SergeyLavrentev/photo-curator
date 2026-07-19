from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from photo_curator.analysis.taste import (
    TasteProfileError,
    capture_preference,
    load_taste_model,
    train_taste_profile,
)
from photo_curator.app import create_app
from photo_curator.db import repository
from photo_curator.db.connection import database_connection
from photo_curator.web.security import CSRF_COOKIE, SESSION_COOKIE, SessionSecrets
from tests.test_pipeline import build_pipeline


def _capture_training_pairs(connection, project_id: str) -> None:
    for left, right in (
        ("demo-012", "demo-001"),
        ("demo-011", "demo-002"),
        ("demo-010", "demo-003"),
        ("demo-009", "demo-004"),
    ):
        capture_preference(
            connection,
            project_id=project_id,
            left_uuid=left,
            right_uuid=right,
            preferred_uuid=left,
        )


def test_pairwise_taste_profile_trains_persists_and_scores_future_assets(tmp_path: Path) -> None:
    paths, _, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)
    with database_connection(paths.database) as connection:
        _capture_training_pairs(connection, project_id)
        capture_preference(
            connection,
            project_id=project_id,
            left_uuid="demo-008",
            right_uuid="demo-005",
            preferred_uuid="demo-008",
            split="held_out",
        )
        profile = train_taste_profile(connection)
        model = load_taste_model(connection)
        signals = repository.analysis_signals_by_asset(connection, project_id)

    assert profile["status"] == "ready"
    assert profile["training_examples"] == 4
    assert profile["evidence"]["calibration_accuracy"] == 1.0
    assert profile["evidence"]["held_out_pairs"] == 1
    assert model is not None
    assert model.personal_delta(signals["demo-012"]["feature_print"]) > model.personal_delta(
        signals["demo-001"]["feature_print"]
    )

    coordinator.run(project_id, from_stage="decisions")
    with database_connection(paths.database) as connection:
        scores = repository.list_swipe_scores(connection, project_id)
    assert any(abs(float(score["personal_delta"])) >= 2 for score in scores)
    assert all("personal_taste" in score["model_versions"] for score in scores)


def test_preference_vectors_survive_project_deletion_and_reset_is_complete(tmp_path: Path) -> None:
    paths, _, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)
    with database_connection(paths.database) as connection:
        _capture_training_pairs(connection, project_id)
        train_taste_profile(connection)
        repository.delete_project(connection, project_id)
        model = load_taste_model(connection)
        examples = repository.list_preference_examples(connection)
        repository.reset_taste_profile(connection)
        remaining = repository.list_preference_examples(connection)

    assert model is not None
    assert len(examples) == 4
    assert remaining == []


def test_taste_training_requires_explicit_valid_comparisons(tmp_path: Path) -> None:
    paths, _, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)
    with database_connection(paths.database) as connection:
        capture_preference(
            connection,
            project_id=project_id,
            left_uuid="demo-012",
            right_uuid="demo-001",
            preferred_uuid="demo-012",
        )
        with pytest.raises(TasteProfileError, match="минимум 3"):
            train_taste_profile(connection)
        with pytest.raises(ValueError, match="уже была"):
            capture_preference(
                connection,
                project_id=project_id,
                left_uuid="demo-001",
                right_uuid="demo-012",
                preferred_uuid="demo-012",
            )


def test_taste_profile_api_covers_calibration_train_pause_export_and_reset(
    tmp_path: Path,
) -> None:
    paths, provider, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)
    secrets = SessionSecrets("startup", "session", "csrf")
    app = create_app(
        demo=False,
        paths=paths,
        provider=provider,
        session_secrets=secrets,
    )
    headers = {"X-CSRF-Token": "csrf"}
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, "session")
        client.cookies.set(CSRF_COOKIE, "csrf")
        for left, right in (
            ("demo-012", "demo-001"),
            ("demo-011", "demo-002"),
            ("demo-010", "demo-003"),
        ):
            response = client.post(
                "/api/taste-profile/preferences",
                headers=headers,
                json={
                    "project_id": project_id,
                    "left_uuid": left,
                    "right_uuid": right,
                    "preferred_uuid": left,
                    "split": "calibration",
                },
            )
            assert response.status_code == 201
        trained = client.post("/api/taste-profile/train", headers=headers)
        paused = client.patch("/api/taste-profile/status", headers=headers, json={"paused": True})
        exported = client.get("/api/taste-profile/export")
        deleted = client.delete("/api/taste-profile", headers=headers)
        after = client.get("/api/taste-profile")

    assert trained.status_code == 200
    assert trained.json()["status"] == "ready"
    assert paused.json()["status"] == "paused"
    assert len(exported.json()["examples"]) == 3
    assert exported.json()["profile"]["weights_base64"]
    assert deleted.json() == {"status": "deleted"}
    assert after.json()["preference_count"] == 0
