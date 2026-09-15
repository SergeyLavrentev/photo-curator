from dataclasses import replace
from pathlib import Path

import pytest

from photo_curator.analysis.culling import automatic_culling
from photo_curator.analysis.decision_engine import DecisionResult
from photo_curator.db import repository
from photo_curator.db.connection import database_connection
from photo_curator.photos.deletion import begin_deletion, finish_deletion, prepare_deletion
from tests.test_pipeline import build_pipeline


def test_culling_never_calls_low_rank_a_defect_and_preserves_distinct_moments():
    decision = DecisionResult("keep", 0.9, 2, {}, [], [])
    assert automatic_culling({}, decision, None)[0] == "keep"
    duplicate = {"kind": "scene", "confidence": 0.97, "is_leader": False, "recommended_pick": False}
    assert automatic_culling({}, decision, duplicate) == ("reject", "redundant_frame")
    assert automatic_culling({}, decision, {**duplicate, "recommended_pick": True})[0] == "keep"
    assert automatic_culling({}, decision, {**duplicate, "is_leader": True})[0] == "keep"
    assert automatic_culling({}, decision, {**duplicate, "confidence": 0.7})[0] == "keep"
    for flag in [
        "duplicate_leader",
        "favorite_protected",
        "edited_protected",
        "ambiguous_duplicate",
        "analysis_error",
        "resolution_inversion",
    ]:
        assert automatic_culling({}, replace(decision, flags=[flag]), duplicate)[0] == "keep"
    assert (
        automatic_culling({}, replace(decision, flags=["relative_low_sharpness"]), None)[0]
        == "keep"
    )
    assert (
        automatic_culling({}, replace(decision, flags=["possible_blur", "defocus_blur"]), None)[0]
        == "reject"
    )


def _prepare_fixture(tmp_path):
    paths, _, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)
    with database_connection(paths.database) as connection:
        # A synthetic provider stands in for PhotoKit; these tests never use real Photos.
        connection.execute(
            "UPDATE projects SET settings_json="
            "json_set(settings_json,'$.source_provenance','photokit') WHERE id=?",
            (project_id,),
        )
        connection.execute(
            "UPDATE assets SET source_revision='fixture-revision' WHERE project_id=?", (project_id,)
        )
        repository.set_manual_decision(connection, project_id, "demo-001", "reject")
        repository.set_manual_decision(connection, project_id, "demo-002", "reject")
    return paths, project_id


def test_two_buckets_page_count_manual_override_and_undo_agree(tmp_path: Path):
    paths, project_id = _prepare_fixture(tmp_path)
    with database_connection(paths.database) as connection:
        for manual in ["keep", "reject", None]:
            repository.set_manual_decision(connection, project_id, "demo-001", manual)
            keep = repository.list_assets_page(
                connection, project_id, limit=100, selection="cull_keep"
            )
            reject = repository.list_assets_page(
                connection, project_id, limit=100, selection="cull_reject"
            )
            assert len(keep) + len(reject) == 12
            assert not {r["asset_uuid"] for r in keep} & {r["asset_uuid"] for r in reject}
            summary = repository.project_summary(connection, project_id)
            assert summary.get("culling_keep", 0) == len(keep)
            assert summary.get("culling_reject", 0) == len(reject)
            assert repository.count_assets(connection, project_id, selection="cull_reject") == len(
                reject
            )
            if manual == "keep":
                assert "demo-001" in {r["asset_uuid"] for r in keep}
            if manual == "reject":
                assert "demo-001" in {r["asset_uuid"] for r in reject}


def test_deletion_requires_confirmation_blocks_drift_and_has_no_retry(tmp_path: Path):
    paths, project_id = _prepare_fixture(tmp_path)
    with database_connection(paths.database) as connection:
        plan = prepare_deletion(connection, project_id, ["demo-001"])
        with pytest.raises(ValueError, match="confirmed=true"):
            begin_deletion(connection, plan["id"], confirmed=False)
        repository.set_manual_decision(connection, project_id, "demo-001", "keep")
        with pytest.raises(ValueError, match="изменились"):
            begin_deletion(connection, plan["id"], confirmed=True)
        repository.set_manual_decision(connection, project_id, "demo-001", "reject")
        plan = prepare_deletion(connection, project_id, ["demo-001"])
        assert begin_deletion(connection, plan["id"], confirmed=True) == plan
        with pytest.raises(ValueError, match="повтор"):
            begin_deletion(connection, plan["id"], confirmed=True)
        with pytest.raises(ValueError, match="списку"):
            finish_deletion(connection, plan["id"], ["unrelated"], None)
        result = finish_deletion(connection, plan["id"], [], "user cancelled")
        assert result["status"] == "cancelled_or_failed"
        assert not repository.get_asset(connection, project_id, "demo-001")["no_longer_exists"]


def test_deletion_marks_only_confirmed_absence_and_preserves_review_files(tmp_path: Path):
    paths, project_id = _prepare_fixture(tmp_path)
    with database_connection(paths.database) as connection:
        plan = prepare_deletion(connection, project_id, ["demo-001", "demo-002"])
        begin_deletion(connection, plan["id"], confirmed=True)
        result = finish_deletion(connection, plan["id"], ["demo-001"], "partial")
        assert result["status"] == "partial"
        assert repository.get_asset(connection, project_id, "demo-001")["no_longer_exists"]
        assert not repository.get_asset(connection, project_id, "demo-002")["no_longer_exists"]
        assert all(Path(item["review_path"]).is_file() for item in plan["items"])


def test_deletion_refuses_local_copy_and_changed_source(tmp_path: Path):
    paths, project_id = _prepare_fixture(tmp_path)
    with database_connection(paths.database) as connection:
        plan = prepare_deletion(connection, project_id, ["demo-001"])
        connection.execute(
            "UPDATE assets SET source_revision='different' "
            "WHERE project_id=? AND asset_uuid='demo-001'",
            (project_id,),
        )
        with pytest.raises(ValueError, match="изменились"):
            begin_deletion(connection, plan["id"], confirmed=True)
        connection.execute(
            "UPDATE projects SET settings_json="
            "json_set(settings_json,'$.source_provenance','service_shared_copy') WHERE id=?",
            (project_id,),
        )
        with pytest.raises(ValueError, match="копии"):
            prepare_deletion(connection, project_id, None)


def test_explicit_deletion_allows_kept_favorites_without_changing_decision(tmp_path: Path):
    paths, project_id = _prepare_fixture(tmp_path)
    with database_connection(paths.database) as connection:
        repository.set_manual_decision(connection, project_id, "demo-001", "keep")
        connection.execute(
            "UPDATE assets SET favorite=1,has_adjustments=1 WHERE project_id=? "
            "AND asset_uuid='demo-001'",
            (project_id,),
        )
        batch = prepare_deletion(connection, project_id, None)
        assert batch["require_candidate"] is True
        assert "demo-001" not in {item["id"] for item in batch["items"]}
        plan = prepare_deletion(connection, project_id, ["demo-001"])
        assert plan["require_candidate"] is False
        assert plan["items"][0]["favorite"] and plan["items"][0]["has_adjustments"]
        begin_deletion(connection, plan["id"], confirmed=True)
        finish_deletion(connection, plan["id"], [], "user cancelled")
        asset = repository.get_asset(connection, project_id, "demo-001")
        assert asset["manual_disposition"] == "keep"
        assert not asset["no_longer_exists"]


def test_bulk_candidate_plan_does_not_survive_a_keep_decision(tmp_path: Path):
    paths, project_id = _prepare_fixture(tmp_path)
    with database_connection(paths.database) as connection:
        plan = prepare_deletion(connection, project_id, None)
        repository.set_manual_decision(connection, project_id, "demo-001", "keep")
        with pytest.raises(ValueError, match="К удалению"):
            begin_deletion(connection, plan["id"], confirmed=True)


def test_shared_album_removal_preserves_personal_and_other_album_entries(tmp_path: Path):
    import json

    paths, project_id = _prepare_fixture(tmp_path)
    with database_connection(paths.database) as connection:
        connection.execute(
            "UPDATE projects SET settings_json=json_set(settings_json,'$.source_album_shared',"
            "json('true')) WHERE id=?",
            (project_id,),
        )
        original = repository.get_project(connection, project_id)
        # Same Photos identifier in several cached contexts: membership removal must
        # affect only the source shared album, never a personal-library entry.
        for clone_id, shared, album_id in [
            ("same-shared", True, original["album_id"]),
            ("personal", False, "personal-album"),
            ("other-shared", True, "other-shared-album"),
        ]:
            project = dict(original)
            project.update(id=clone_id, album_id=album_id)
            settings = json.loads(project["settings_json"])
            settings["source_album_shared"] = shared
            project["settings_json"] = json.dumps(settings)
            columns = [r[1] for r in connection.execute("PRAGMA table_info(projects)")]
            connection.execute(
                f"INSERT INTO projects ({','.join(columns)}) "
                f"VALUES ({','.join('?' for _ in columns)})",
                [project[column] for column in columns],
            )
            asset = dict(
                connection.execute(
                    "SELECT * FROM assets WHERE project_id=? AND asset_uuid='demo-001'",
                    (project_id,),
                ).fetchone()
            )
            asset["project_id"] = clone_id
            columns = list(asset)
            connection.execute(
                f"INSERT INTO assets ({','.join(columns)}) "
                f"VALUES ({','.join('?' for _ in columns)})",
                list(asset.values()),
            )
        plan = prepare_deletion(connection, project_id, ["demo-001"])
        assert plan["scope"] == "shared_album"
        begin_deletion(connection, plan["id"], confirmed=True)
        assert finish_deletion(connection, plan["id"], ["demo-001"], None)["status"] == "completed"
        hidden = dict(
            connection.execute(
                "SELECT project_id,no_longer_exists FROM assets WHERE asset_uuid='demo-001'"
            )
        )
        assert hidden == {project_id: 1, "same-shared": 1, "personal": 0, "other-shared": 0}


def test_deletion_scope_cannot_change_after_confirmation_preview(tmp_path: Path):
    paths, project_id = _prepare_fixture(tmp_path)
    with database_connection(paths.database) as connection:
        plan = prepare_deletion(connection, project_id, ["demo-001"])
        assert plan["scope"] == "library"
        connection.execute(
            "UPDATE projects SET settings_json=json_set(settings_json,'$.source_album_shared',"
            "json('true')) WHERE id=?",
            (project_id,),
        )
        with pytest.raises(ValueError, match="Анализ изменился"):
            begin_deletion(connection, plan["id"], confirmed=True)
