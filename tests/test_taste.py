from __future__ import annotations

from pathlib import Path

import pytest

from photo_curator.analysis.taste import (
    TASTE_MODEL_VERSION,
    TasteProfileError,
    capture_preference,
    compatible_taste_model,
    load_taste_model,
    train_taste_profile,
)
from photo_curator.db import repository
from photo_curator.db.connection import database_connection
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
    assert 0 < model.reliability < 0.1
    assert model.personal_delta(signals["demo-012"]["feature_print"]) > model.personal_delta(
        signals["demo-001"]["feature_print"]
    )
    assert model.personal_delta({}) == 0.0
    assert model.personal_delta({"status": "unavailable"}) == 0.0

    coordinator.run(project_id, from_stage="decisions")
    with database_connection(paths.database) as connection:
        scores = repository.list_swipe_scores(connection, project_id)
    assert any(abs(float(score["personal_delta"])) > 0.05 for score in scores)
    assert all(float(score["components"]["personal_taste_reliability"]) < 10 for score in scores)
    assert all("personal_taste" in score["model_versions"] for score in scores)

    with database_connection(paths.database) as connection:
        connection.execute(
            "DELETE FROM analysis_signals "
            "WHERE project_id=? AND asset_uuid='demo-006' AND signal_kind='feature_print'",
            (project_id,),
        )
    coordinator.run(project_id, from_stage="decisions")
    with database_connection(paths.database) as connection:
        missing_feature_score = next(
            score
            for score in repository.list_swipe_scores(connection, project_id)
            if score["asset_uuid"] == "demo-006"
        )
    assert missing_feature_score["personal_delta"] == 0.0


def test_taste_model_is_explicitly_invalidated_when_vision_schema_changes(
    tmp_path: Path,
) -> None:
    paths, _, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)
    with database_connection(paths.database) as connection:
        _capture_training_pairs(connection, project_id)
        train_taste_profile(connection)
        original_version = connection.execute(
            "SELECT engine_version FROM analysis_signals "
            "WHERE project_id=? AND signal_kind='feature_print' LIMIT 1",
            (project_id,),
        ).fetchone()[0]
        connection.execute(
            "UPDATE analysis_signals SET engine_version='future-v2' "
            "WHERE project_id=? AND signal_kind='feature_print'",
            (project_id,),
        )
        signals = repository.analysis_signals_by_asset(connection, project_id)
        assert compatible_taste_model(connection, signals) is None
        profile = repository.get_taste_profile(connection)

    assert profile["status"] == "incompatible"
    assert profile["evidence"]["compatibility"]["compatible"] is False
    coordinator.run(project_id, from_stage="decisions")
    with database_connection(paths.database) as connection:
        scores = repository.list_swipe_scores(connection, project_id)
    assert all(float(score["personal_delta"]) == 0 for score in scores)
    assert all("personal_taste" not in score["model_versions"] for score in scores)

    with database_connection(paths.database) as connection:
        connection.execute(
            "UPDATE analysis_signals SET engine_version=? "
            "WHERE project_id=? AND signal_kind='feature_print'",
            (original_version, project_id),
        )
    coordinator.run(project_id, from_stage="decisions")
    with database_connection(paths.database) as connection:
        restored = repository.get_taste_profile(connection)
        restored_scores = repository.list_swipe_scores(connection, project_id)
    assert restored["status"] == "ready"
    assert any(abs(float(score["personal_delta"])) > 0.05 for score in restored_scores)


def test_legacy_taste_model_is_retrained_instead_of_silently_ignored(tmp_path: Path) -> None:
    paths, _, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)
    with database_connection(paths.database) as connection:
        _capture_training_pairs(connection, project_id)
        train_taste_profile(connection)
        connection.execute(
            "UPDATE taste_profiles SET model_version='pairwise-linear-v1' WHERE id='default'"
        )
        signals = repository.analysis_signals_by_asset(connection, project_id)
        migrated = compatible_taste_model(connection, signals)
        profile = repository.get_taste_profile(connection)

    assert migrated is not None
    assert migrated.model_version == TASTE_MODEL_VERSION
    assert profile["status"] == "ready"
    assert profile["model_version"] == TASTE_MODEL_VERSION


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
