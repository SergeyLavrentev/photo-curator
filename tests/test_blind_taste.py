import json
from pathlib import Path

import pytest

from photo_curator.blind_taste import answer_session, prepare_session, session_payload
from photo_curator.db import repository
from photo_curator.db.connection import database_connection
from photo_curator.learning import export_learning_corpus, migrate_project_learning, start_new_round
from tests.test_pipeline import build_pipeline


def test_blind_choices_resume_skip_and_survive_project_delete(tmp_path: Path) -> None:
    paths, _, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)
    with database_connection(paths.database) as connection:
        session = prepare_session(connection, project_id)
        assert 1 <= session["total"] <= 12
        assert prepare_session(connection, project_id) == session
        assert set(session["pair"]["left"]) == {"id", "review_path"}
        before = repository.project_summary(connection, project_id)
        skipped = answer_session(connection, session["id"], 0, "skip")
        assert skipped["completed"] == 1
        assert skipped["choices"] == 0
        assert not repository.list_preference_examples(connection)
        assert answer_session(connection, session["id"], 0, "skip") == skipped
        with pytest.raises(ValueError, match="изменилась"):
            answer_session(connection, session["id"], 0, "left")
        for index in range(1, min(4, session["total"])):
            answer_session(connection, session["id"], index, "left")
        evidence = export_learning_corpus(connection)
        assert evidence["learning_preferences"]
        assert all(p["split"] == "training" for p in evidence["learning_preferences"])
        assert repository.project_summary(connection, project_id) == before
        choices = min(4, session["total"]) - 1
        assert len(evidence["learning_preferences"]) == choices
        assert session_payload(connection, session["id"])["choices"] == choices
        # Resume after several answers, not just immediately after creation.
        if session["total"] > 4:
            assert prepare_session(connection, project_id) == session_payload(
                connection, session["id"]
            )
        repository.delete_project(connection, project_id)
        assert (
            export_learning_corpus(connection)["learning_preferences"]
            == evidence["learning_preferences"]
        )
        assert connection.execute("SELECT COUNT(*) FROM blind_taste_sessions").fetchone()[0] == 0


def test_blind_training_refuses_held_out_context(tmp_path: Path) -> None:
    paths, _, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)
    with database_connection(paths.database) as connection:
        start_new_round(connection, project_id, "held_out")
        with pytest.raises(ValueError, match="независимой проверкой"):
            prepare_session(connection, project_id)
        assert not repository.list_preference_examples(connection)


def test_blind_sample_does_not_depend_on_scores_or_auto_groups(tmp_path: Path) -> None:
    paths, _, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)
    with database_connection(paths.database) as connection:
        session = prepare_session(connection, project_id)
        pairs = connection.execute(
            "SELECT pairs_json FROM blind_taste_sessions WHERE id=?", (session["id"],)
        ).fetchone()[0]
        connection.execute("DELETE FROM blind_taste_sessions")
        connection.execute(
            "UPDATE swipe_scores SET score=100-score WHERE project_id=?", (project_id,)
        )
        connection.execute(
            "UPDATE decisions SET final_disposition='reject' WHERE project_id=?", (project_id,)
        )
        other = prepare_session(connection, project_id)
        assert (
            connection.execute(
                "SELECT pairs_json FROM blind_taste_sessions WHERE id=?", (other["id"],)
            ).fetchone()[0]
            == pairs
        )


def test_blind_session_rejects_feature_drift_before_accepting_answer(tmp_path: Path) -> None:
    paths, _, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)
    with database_connection(paths.database) as connection:
        session = prepare_session(connection, project_id)
        connection.execute(
            "UPDATE analysis_signals SET engine_version='changed' WHERE project_id=?", (project_id,)
        )
        with pytest.raises(ValueError, match="Признаки движка изменились"):
            answer_session(connection, session["id"], 0, "left")
        assert not repository.list_quality_preference_examples(connection, project_id)


def test_different_scene_is_not_a_preference_and_old_plans_cannot_resume(tmp_path):
    paths, _, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)
    with database_connection(paths.database) as connection:
        session = prepare_session(connection, project_id)
        answer = answer_session(connection, session["id"], 0, "different_scene")
        assert answer["choices"] == 0
        assert not repository.list_quality_preference_examples(connection, project_id)
        plan = json.loads(
            connection.execute(
                "SELECT pairs_json FROM blind_taste_sessions WHERE id=?", (session["id"],)
            ).fetchone()[0]
        )
        del plan["policy"]
        connection.execute(
            "UPDATE blind_taste_sessions SET pairs_json=? WHERE id=?",
            (json.dumps(plan), session["id"]),
        )
        with pytest.raises(ValueError, match="Правила сравнения обновлены"):
            answer_session(connection, session["id"], 1, "left")
        try:
            replacement = prepare_session(connection, project_id)
        except ValueError as error:
            assert "Новых пар одной сцены" in str(error)
        else:
            assert replacement["id"] != session["id"]


def test_legacy_blind_answers_are_retained_but_cannot_train_or_export(tmp_path):
    from photo_curator.analysis.taste import load_taste_model
    from photo_curator.db.blind_learning_migration import exclude_legacy_blind_answers

    paths, _, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)
    with database_connection(paths.database) as connection:
        session = prepare_session(connection, project_id)
        answer_session(connection, session["id"], 0, "left")
        before = export_learning_corpus(connection)
        assert len(before["learning_preferences"]) == 1
        plan = json.loads(
            connection.execute(
                "SELECT pairs_json FROM blind_taste_sessions WHERE id=?", (session["id"],)
            ).fetchone()[0]
        )
        del plan["policy"]
        connection.execute(
            "UPDATE blind_taste_sessions SET pairs_json=? WHERE id=?",
            (json.dumps(plan), session["id"]),
        )
        # Exercise the actual v30 -> v31 migration hook and its backup path.
        from photo_curator.db.migrations import migrate

        connection.execute("DROP TABLE learning_pair_exclusions")
        connection.execute("PRAGMA user_version = 30")
        connection.commit()
        migrate(connection)
        exclude_legacy_blind_answers(connection)
        assert (
            connection.execute("SELECT COUNT(*) FROM learning_pair_exclusions").fetchone()[0] == 1
        )
        assert connection.execute("SELECT COUNT(*) FROM learning_preferences").fetchone()[0] == 1
        assert not export_learning_corpus(connection)["learning_preferences"]
        assert not repository.list_preference_examples(connection)
        assert load_taste_model(connection) is None
        # Reprojection must not bring excluded preferences back into training.
        assert migrate_project_learning(connection, project_id)["status"] == "completed"
        assert not repository.list_preference_examples(connection)
        assert not export_learning_corpus(connection)["learning_preferences"]
        assert repository.list_quality_preference_examples(connection, project_id)
        repository.delete_project(connection, project_id)
        assert connection.execute("SELECT COUNT(*) FROM learning_preferences").fetchone()[0] == 1
        assert (
            connection.execute("SELECT COUNT(*) FROM learning_pair_exclusions").fetchone()[0] == 1
        )
