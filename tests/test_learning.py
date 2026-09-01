from __future__ import annotations

from pathlib import Path

import pytest

from photo_curator.analysis.taste import load_taste_model
from photo_curator.db import repository
from photo_curator.db.connection import database_connection
from photo_curator.learning import (
    LearningCorpusError,
    export_learning_corpus,
    import_learning_corpus,
    learning_status,
    migrate_project_learning,
)
from photo_curator.native_worker import NativeWorker, NativeWorkerError
from tests.test_pipeline import build_pipeline


def _worker(tmp_path: Path) -> tuple[NativeWorker, str]:
    paths, provider, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)
    return NativeWorker(paths, provider=provider, coordinator=coordinator), project_id


def _label(worker: NativeWorker, project_id: str, asset_uuid: str) -> None:
    worker.dispatch(
        "quality_label",
        {
            "project_id": project_id,
            "asset_uuid": asset_uuid,
            "disposition": "keep",
            "defect_codes": [],
        },
    )


def _training_pairs(worker: NativeWorker, project_id: str, count: int = 3) -> None:
    for _ in range(count):
        pair = worker.dispatch("quality_pair", {"project_id": project_id})["pair"]
        assert pair is not None
        worker.dispatch(
            "quality_preference",
            {
                "project_id": project_id,
                "left_uuid": pair["left"]["asset_uuid"],
                "right_uuid": pair["right"]["asset_uuid"],
                "preferred_uuid": pair["left"]["asset_uuid"],
            },
        )


def test_training_round_updates_versioned_ranker_and_survives_project_delete(
    tmp_path: Path,
) -> None:
    worker, project_id = _worker(tmp_path)
    started = worker.dispatch(
        "quality_round_start", {"project_id": project_id, "split": "training"}
    )
    assert started["attempt_index"] == 1
    candidates = worker.dispatch("quality_candidates", {"project_id": project_id, "limit": 12})
    for item in candidates["items"][:6]:
        _label(worker, project_id, item["asset_uuid"])
    _training_pairs(worker, project_id)

    before = worker.dispatch("quality_learning_status", {"project_id": project_id})
    assert before["training_contexts"] == 1
    assert before["held_out_contexts"] == 0
    assert before["training_examples"] >= 3
    assert before["ranker_version"]

    deleted = worker.dispatch("delete_project", {"project_id": project_id, "confirmed": True})
    assert deleted["learning_migration"]["status"] == "completed"
    after = worker.dispatch("quality_learning_status", {})
    assert after["training_contexts"] == 1
    assert after["training_examples"] >= 3
    with database_connection(worker.paths.database) as connection:
        assert load_taste_model(connection) is not None
        assert connection.execute("SELECT COUNT(*) FROM learning_assets").fetchone()[0] >= 6


def test_legacy_quality_lab_is_locked_held_out_and_never_trains(tmp_path: Path) -> None:
    worker, project_id = _worker(tmp_path)
    candidates = worker.dispatch("quality_candidates", {"project_id": project_id, "limit": 12})
    for item in candidates["items"][:4]:
        _label(worker, project_id, item["asset_uuid"])
    _training_pairs(worker, project_id, count=1)

    status = worker.dispatch("quality_learning_status", {"project_id": project_id})
    assert status["training_contexts"] == 0
    assert status["held_out_contexts"] == 1
    assert status["active_round"]["split"] == "held_out"
    with database_connection(worker.paths.database) as connection:
        assert repository.list_preference_examples(connection) == []
        assert (
            connection.execute("SELECT DISTINCT split FROM learning_preferences").fetchone()[0]
            == "held_out"
        )


def test_restart_creates_new_attempt_without_erasing_previous_learning(tmp_path: Path) -> None:
    worker, project_id = _worker(tmp_path)
    first = worker.dispatch("quality_round_start", {"project_id": project_id, "split": "training"})
    candidates = worker.dispatch("quality_candidates", {"project_id": project_id, "limit": 12})
    for item in candidates["items"][:2]:
        _label(worker, project_id, item["asset_uuid"])
    second = worker.dispatch("quality_round_start", {"project_id": project_id, "split": "training"})

    assert second["attempt_index"] == 2
    assert second["round_id"] != first["round_id"]
    with database_connection(worker.paths.database) as connection:
        rounds = connection.execute(
            "SELECT id, status FROM learning_rounds ORDER BY attempt_index"
        ).fetchall()
        assert [tuple(row) for row in rounds] == [
            (first["round_id"], "superseded"),
            (second["round_id"], "in_progress"),
        ]
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM learning_assets WHERE round_id=?", (first["round_id"],)
            ).fetchone()[0]
            == 2
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM quality_asset_labels WHERE project_id=?", (project_id,)
            ).fetchone()[0]
            == 0
        )


def test_project_delete_is_blocked_and_audited_when_feature_provenance_drifts(
    tmp_path: Path,
) -> None:
    worker, project_id = _worker(tmp_path)
    candidate = worker.dispatch("quality_candidates", {"project_id": project_id, "limit": 12})[
        "items"
    ][0]
    _label(worker, project_id, candidate["asset_uuid"])
    with database_connection(worker.paths.database) as connection:
        connection.execute(
            """
            UPDATE analysis_signals SET source_fingerprint=NULL
            WHERE project_id=? AND asset_uuid=? AND signal_kind='feature_print'
            """,
            (project_id, candidate["asset_uuid"]),
        )
        connection.execute(
            "UPDATE quality_asset_labels SET quality_note='changed' WHERE project_id=?",
            (project_id,),
        )

    with pytest.raises(NativeWorkerError, match="migration blocked"):
        worker.dispatch("delete_project", {"project_id": project_id, "confirmed": True})

    with database_connection(worker.paths.database) as connection:
        assert repository.get_project(connection, project_id)["id"] == project_id
        failed = connection.execute(
            "SELECT status, error_text FROM learning_migration_audit "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    assert failed["status"] == "failed"
    assert "provenance" in failed["error_text"]


def test_migration_is_idempotent_and_export_import_rejects_predictions(tmp_path: Path) -> None:
    worker, project_id = _worker(tmp_path)
    candidate = worker.dispatch("quality_candidates", {"project_id": project_id, "limit": 12})[
        "items"
    ][0]
    _label(worker, project_id, candidate["asset_uuid"])
    with database_connection(worker.paths.database) as connection:
        first = migrate_project_learning(connection, project_id)
        second = migrate_project_learning(connection, project_id)
        assert first["round_id"] == second["round_id"]
        assert connection.execute("SELECT COUNT(*) FROM learning_assets").fetchone()[0] == 1
        corpus = export_learning_corpus(connection)
        corpus["predictions_are_truth"] = True
        with pytest.raises(LearningCorpusError, match="human-truth"):
            import_learning_corpus(connection, corpus)


def test_full_learning_delete_requires_confirmation_and_keeps_project(tmp_path: Path) -> None:
    worker, project_id = _worker(tmp_path)
    worker.dispatch("quality_round_start", {"project_id": project_id, "split": "training"})
    candidate = worker.dispatch("quality_candidates", {"project_id": project_id, "limit": 12})[
        "items"
    ][0]
    _label(worker, project_id, candidate["asset_uuid"])

    with pytest.raises(NativeWorkerError, match="confirmed=true"):
        worker.dispatch("quality_learning_delete", {})
    deleted = worker.dispatch("quality_learning_delete", {"confirmed": True})
    assert deleted["status"] == "deleted"
    assert Path(deleted["backup_path"]).exists()
    with database_connection(worker.paths.database) as connection:
        assert repository.get_project(connection, project_id)["id"] == project_id
        assert learning_status(connection)["training_contexts"] == 0
        assert connection.execute("SELECT COUNT(*) FROM learning_assets").fetchone()[0] == 0
