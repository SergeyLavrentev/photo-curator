from __future__ import annotations

import io
import json
from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest

from photo_curator.db import repository
from photo_curator.db.connection import database_connection
from photo_curator.native_worker import NativeWorker, run_native_worker
from tests.test_pipeline import build_pipeline


def test_native_worker_exposes_projects_ranked_assets_and_decisions_without_http(
    tmp_path: Path,
) -> None:
    paths, provider, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)
    worker = NativeWorker(paths, provider=provider, coordinator=coordinator)

    assert worker.dispatch("status", {})["status"] == "ready"
    assert worker.dispatch("albums", {})["regular"][0]["photo_count"] == 12
    assert worker.dispatch("projects", {})[0]["id"] == project_id
    project = worker.dispatch("project", {"project_id": project_id})
    assets = worker.dispatch("assets", {"project_id": project_id})
    changed = worker.dispatch(
        "decision",
        {
            "project_id": project_id,
            "asset_uuid": assets["items"][0]["asset_uuid"],
            "disposition": "keep",
        },
    )

    assert project["project"]["state"] == "ready"
    assert assets["total"] == 12
    assert assets["items"][0]["swipe_score"] >= assets["items"][-1]["swipe_score"]
    assert changed["final_disposition"] == "keep"
    assert changed["manual_disposition"] == "keep"
    assert "source_path" not in changed

    second_page = worker.dispatch("assets", {"project_id": project_id, "offset": 5, "limit": 3})
    assert second_page["offset"] == 5
    assert len(second_page["items"]) == 3
    assert second_page["items"][0]["asset_uuid"] == assets["items"][5]["asset_uuid"]
    kept = worker.dispatch(
        "assets",
        {"project_id": project_id, "disposition": "keep", "limit": 5000},
    )
    assert kept["total"] > 0
    assert all(item["final_disposition"] == "keep" for item in kept["items"])
    batch_ids = [item["asset_uuid"] for item in assets["items"][:2]]
    batch = worker.dispatch(
        "decisions_batch",
        {
            "project_id": project_id,
            "asset_uuids": batch_ids,
            "disposition": "reject",
        },
    )
    assert batch["updated"] == 2
    rejected_ids = {
        item["asset_uuid"]
        for item in worker.dispatch(
            "assets",
            {"project_id": project_id, "disposition": "reject", "limit": 5000},
        )["items"]
    }
    assert set(batch_ids) <= rejected_ids


def test_native_worker_migrates_legacy_review_decisions_to_binary_buckets(
    tmp_path: Path,
) -> None:
    paths, provider, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)
    worker = NativeWorker(paths, provider=provider, coordinator=coordinator)
    with database_connection(paths.database) as connection:
        asset_uuid = str(repository.list_assets(connection, project_id)[0]["asset_uuid"])
        connection.execute(
            """
            UPDATE decisions
            SET auto_disposition='review', manual_disposition='review',
                final_disposition='review', manual_override=1
            WHERE project_id=? AND asset_uuid=?
            """,
            (project_id, asset_uuid),
        )

    migrated = worker.dispatch("binary_decisions", {"project_id": project_id})

    assert migrated["resolved"] == 1
    assert migrated["summary"]["review"] == 0
    assert migrated["summary"]["keep"] + migrated["summary"]["reject"] == 12
    with database_connection(paths.database) as connection:
        asset = repository.get_asset(connection, project_id, asset_uuid)
    assert asset["final_disposition"] in {"keep", "reject"}
    assert asset["manual_disposition"] is None


def test_jsonl_worker_protocol_is_versioned_correlated_and_stops_cleanly(tmp_path: Path) -> None:
    paths, provider, coordinator, _ = build_pipeline(tmp_path)
    worker = NativeWorker(paths, provider=provider, coordinator=coordinator)
    requests = [
        {"schema_version": 1, "id": "one", "method": "status", "params": {}},
        {"schema_version": 1, "id": "two", "method": "unknown", "params": {}},
        {"schema_version": 1, "id": "three", "method": "shutdown", "params": {}},
    ]
    input_stream = io.StringIO("".join(json.dumps(request) + "\n" for request in requests))
    output_stream = io.StringIO()

    result = run_native_worker(
        paths, input_stream=input_stream, output_stream=output_stream, worker=worker
    )
    responses = [json.loads(line) for line in output_stream.getvalue().splitlines()]

    assert result == 0
    assert [response["id"] for response in responses] == ["one", "two", "three"]
    assert responses[0]["result"]["worker_schema_version"] == 2
    assert responses[1]["error"]["type"] == "NativeWorkerError"
    assert responses[2]["result"] == {"status": "bye"}


def test_jsonl_worker_can_boot_with_packaged_demo_provider(tmp_path: Path) -> None:
    paths, _, _, _ = build_pipeline(tmp_path)
    requests = [
        {"schema_version": 1, "id": "albums", "method": "albums", "params": {}},
        {"schema_version": 1, "id": "bye", "method": "shutdown", "params": {}},
    ]
    input_stream = io.StringIO("".join(json.dumps(request) + "\n" for request in requests))
    output_stream = io.StringIO()

    assert (
        run_native_worker(paths, input_stream=input_stream, output_stream=output_stream, demo=True)
        == 0
    )
    response = json.loads(output_stream.getvalue().splitlines()[0])
    assert response["result"]["regular"][0]["photo_count"] == 12


def test_native_worker_fails_closed_without_photokit_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _, _, _ = build_pipeline(tmp_path)
    monkeypatch.delenv("PHOTO_CURATOR_PHOTOKIT_HELPER", raising=False)

    with pytest.raises(RuntimeError, match="PhotoKit source helper"):
        run_native_worker(paths, input_stream=io.StringIO(), output_stream=io.StringIO())


def test_native_worker_can_create_project_directly_from_shared_album(tmp_path: Path) -> None:
    paths, provider, coordinator, _ = build_pipeline(tmp_path)
    worker = NativeWorker(paths, provider=provider, coordinator=coordinator)

    created = worker.dispatch(
        "create_project",
        {"album_id": "demo-shared-album", "selection_density": "compact"},
    )

    assert created["album_id"] == "demo-shared-album"
    assert created["album_name"] == "Семейный Shared Album"


def test_native_worker_keeps_history_until_explicit_project_delete(tmp_path: Path) -> None:
    paths, provider, coordinator, preserved_project_id = build_pipeline(tmp_path)
    worker = NativeWorker(paths, provider=provider, coordinator=coordinator)
    abandoned = worker.dispatch(
        "create_project",
        {"album_id": provider.ALBUM_ID, "selection_density": "balanced"},
    )
    preserved_cache = paths.cache_dir / preserved_project_id
    abandoned_cache = paths.cache_dir / abandoned["id"]
    preserved_cache.mkdir(parents=True, exist_ok=True)
    abandoned_cache.mkdir(parents=True, exist_ok=True)
    (abandoned_cache / "preview.jpg").touch()
    shared_cache = paths.cache_dir / "photokit-renders"
    shared_cache.mkdir(parents=True, exist_ok=True)
    (shared_cache / "render.jpg").touch()

    result = worker.dispatch("cleanup_abandoned_projects", {})

    assert result["removed_project_ids"] == []
    assert preserved_cache.is_dir()
    assert abandoned_cache.is_dir()
    assert shared_cache.is_dir()
    assert len(worker.dispatch("projects", {})) == 2

    deleted = worker.dispatch("delete_project", {"project_id": abandoned["id"]})

    assert deleted == {"status": "deleted", "project_id": abandoned["id"]}
    assert preserved_cache.is_dir()
    assert not abandoned_cache.exists()
    assert shared_cache.is_dir()
    assert [project["id"] for project in worker.dispatch("projects", {})] == [preserved_project_id]


def test_native_worker_restart_removes_only_incomplete_cache_files(tmp_path: Path) -> None:
    paths, provider, coordinator, project_id = build_pipeline(tmp_path)
    project_cache = paths.cache_dir / project_id
    project_cache.mkdir(parents=True, exist_ok=True)
    completed = project_cache / "completed.jpg"
    incomplete_preview = project_cache / ".preview-orphan.jpg"
    incomplete_sips = project_cache / ".001.jpg.sips.jpg"
    native_request = paths.cache_dir / "_native_vision/request.input.json"
    completed.touch()
    incomplete_preview.touch()
    incomplete_sips.touch()
    native_request.parent.mkdir(parents=True, exist_ok=True)
    native_request.touch()

    NativeWorker(paths, provider=provider, coordinator=coordinator)

    assert completed.is_file()
    assert not incomplete_preview.exists()
    assert not incomplete_sips.exists()
    assert not native_request.parent.exists()


def test_native_taste_pairs_train_and_rerank_ready_project(tmp_path: Path) -> None:
    paths, provider, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)
    worker = NativeWorker(paths, provider=provider, coordinator=coordinator)
    seen: set[tuple[str, str]] = set()

    for _ in range(3):
        result = worker.dispatch("taste_pair", {"project_id": project_id})
        pair = result["pair"]
        assert pair and result["remaining"] > 0
        left = pair["left"]["asset_uuid"]
        right = pair["right"]["asset_uuid"]
        key = tuple(sorted((left, right)))
        assert key not in seen
        seen.add(key)
        worker.dispatch(
            "taste_preference",
            {
                "project_id": project_id,
                "left_uuid": left,
                "right_uuid": right,
                "preferred_uuid": left,
            },
        )

    profile = worker.dispatch("taste_train", {})
    assert profile["status"] == "ready"
    assert profile["training_examples"] == 3
    coordinator.run(project_id, from_stage="decisions")
    reranked = worker.dispatch("assets", {"project_id": project_id})["items"]
    assert any(abs(item["personal_delta"] or 0) > 0.1 for item in reranked)
    next_pair = worker.dispatch("taste_pair", {"project_id": project_id})["pair"]
    held_out = worker.dispatch(
        "taste_preference",
        {
            "project_id": project_id,
            "left_uuid": next_pair["left"]["asset_uuid"],
            "right_uuid": next_pair["right"]["asset_uuid"],
            "preferred_uuid": next_pair["left"]["asset_uuid"],
            "split": "calibration",
        },
    )
    assert held_out["split"] == "held_out"
    assert held_out["profile"]["calibration_count"] == 3
    assert held_out["profile"]["held_out_count"] == 1
    assert held_out["profile"]["status"] == "ready"
    evaluated = worker.dispatch("taste_train", {})
    assert evaluated["evidence"]["held_out_pairs"] == 1
    assert evaluated["evidence"]["held_out_accuracy"] in {0.0, 1.0}
    exported = worker.dispatch("taste_export", {})
    assert exported["schema_version"] == 1
    assert len(exported["examples"]) == 4
    assert exported["profile"]["weights_base64"]
    worker.dispatch(
        "decision",
        {
            "project_id": project_id,
            "asset_uuid": reranked[0]["asset_uuid"],
            "disposition": "keep",
        },
    )
    quality = worker.dispatch("quality_export", {"project_id": project_id})
    assert quality["manifest"]["assets"][0]["expected_disposition"] == "keep"
    assert len(quality["manifest"]["preference_pairs"]) == 4
    assert quality["summary"]["manual_labels"] == 1
    assert quality["summary"]["held_out_pairs"] == 1
    assert len(quality["score_snapshot"]["scores"]) == 12
    report = worker.dispatch(
        "quality_evaluate",
        {
            "project_id": project_id,
            "manifest": quality["manifest"],
            "score_snapshot": quality["score_snapshot"],
        },
    )
    assert report["labelled_assets"] == 1
    assert report["release_eligible"] is False
    assert worker.dispatch("taste_status", {"paused": True})["status"] == "paused"
    assert worker.dispatch("taste_reset", {}) == {"status": "deleted"}
    assert worker.dispatch("taste_profile", {})["preference_count"] == 0


def test_native_taste_onboarding_trains_from_explicit_positive_and_negative_rounds(
    tmp_path: Path,
) -> None:
    paths, provider, coordinator, _ = build_pipeline(tmp_path)
    originals = list(provider._assets)
    provider._assets = [
        replace(
            originals[index % len(originals)],
            uuid=f"taste-{index:02d}",
            taken_at=f"2026-07-{index + 1:02d}T12:00:00+03:00",
        )
        for index in range(30)
    ]
    worker = NativeWorker(paths, provider=provider, coordinator=coordinator)
    seen: set[str] = set()
    pending = worker.dispatch(
        "taste_round_prepare",
        {"album_id": provider.ALBUM_ID},
    )
    assert pending["round"]["round_number"] == 1
    cancelled = worker.dispatch("taste_round_cancel", {})
    assert cancelled["status"] == "cancelled"
    assert cancelled["profile"]["onboarding_rounds_completed"] == 0

    for expected_round in range(1, 4):
        prepared = worker.dispatch(
            "taste_round_prepare",
            {"album_id": provider.ALBUM_ID},
        )
        round_value = prepared["round"]
        assert round_value["round_number"] == expected_round
        assert len(round_value["photos"]) == 10
        resumed = worker.dispatch(
            "taste_round_prepare",
            {"album_id": provider.ALBUM_ID},
        )
        assert resumed["round"]["id"] == round_value["id"]
        photo_ids = [photo["asset_uuid"] for photo in round_value["photos"]]
        assert not seen.intersection(photo_ids)
        seen.update(photo_ids)

        submitted = worker.dispatch(
            "taste_round_submit",
            {
                "round_id": round_value["id"],
                "selected_uuids": photo_ids[:3],
                "rejected_uuids": photo_ids[3:6],
            },
        )
        profile = submitted["profile"]
        assert profile["onboarding_rounds_completed"] == expected_round

    assert profile["onboarding_complete"] is True
    assert profile["status"] == "ready"
    assert profile["calibration_count"] == 18
    assert profile["held_out_count"] == 9
    assert profile["training_examples"] == 18
    assert profile["evidence"]["onboarding_version"] == 2

    restored = worker.dispatch("taste_profile", {})
    assert restored["onboarding_complete"] is True
    assert restored["preference_count"] == 27
    worker.dispatch("taste_reset", {})
    reset = worker.dispatch("taste_profile", {})
    assert reset["onboarding_rounds_completed"] == 0
    assert reset["onboarding_complete"] is False
    with database_connection(paths.database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM taste_rounds").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM taste_assets").fetchone()[0] == 0


def test_native_taste_adjustment_uses_only_explicitly_rejected_photos(tmp_path: Path) -> None:
    paths, provider, coordinator, _ = build_pipeline(tmp_path)
    originals = list(provider._assets)
    provider._assets = [
        replace(
            originals[index % len(originals)],
            uuid=f"taste-adjust-{index:02d}",
            taken_at=f"2026-07-{index + 1:02d}T12:00:00+03:00",
        )
        for index in range(40)
    ]
    worker = NativeWorker(paths, provider=provider, coordinator=coordinator)

    for _ in range(3):
        prepared = worker.dispatch("taste_round_prepare", {"album_id": provider.ALBUM_ID})
        photo_ids = [photo["asset_uuid"] for photo in prepared["round"]["photos"]]
        worker.dispatch(
            "taste_round_submit",
            {
                "round_id": prepared["round"]["id"],
                "selected_uuids": photo_ids[:3],
                "rejected_uuids": photo_ids[3:6],
            },
        )

    adjustment = worker.dispatch(
        "taste_round_prepare",
        {"album_id": provider.ALBUM_ID, "mode": "adjust"},
    )
    round_value = adjustment["round"]
    assert round_value["is_adjustment"] is True
    photo_ids = [photo["asset_uuid"] for photo in round_value["photos"]]
    updated = worker.dispatch(
        "taste_round_submit",
        {
            "round_id": round_value["id"],
            "selected_uuids": photo_ids[:3],
            "rejected_uuids": photo_ids[3:6],
        },
    )
    assert updated["profile"]["onboarding_complete"] is True
    assert updated["profile"]["preference_count"] == 36
    with database_connection(paths.database) as connection:
        completed = repository.list_taste_rounds(connection)[-1]
    assert completed["selected_uuids"] == photo_ids[:3]
    assert completed["rejected_uuids"] == photo_ids[3:6]


def test_native_worker_rejects_unknown_resume_stage(tmp_path: Path) -> None:
    paths, provider, coordinator, project_id = build_pipeline(tmp_path)
    worker = NativeWorker(paths, provider=provider, coordinator=coordinator)

    with pytest.raises(ValueError, match="Unknown pipeline stage"):
        worker.dispatch("start_analysis", {"project_id": project_id, "from_stage": "unknown"})


def test_native_worker_marks_restart_interrupted_and_resumes_same_stage(tmp_path: Path) -> None:
    paths, provider, _, project_id = build_pipeline(tmp_path)
    with database_connection(paths.database) as connection:
        repository.create_job(connection, project_id, "metrics", 12)
        repository.set_project_state(connection, project_id, "running")

    class RecordingCoordinator:
        def __init__(self) -> None:
            self.started = []

        def start(self, received_project_id, from_stage=None):
            self.started.append((received_project_id, from_stage))

        def is_running(self, received_project_id):
            return False

        def cancel(self, received_project_id):
            return received_project_id == project_id

    coordinator = RecordingCoordinator()
    worker = NativeWorker(paths, provider=provider, coordinator=coordinator)
    state = worker.dispatch("project", {"project_id": project_id})

    assert state["project"]["state"] == "interrupted"
    assert state["jobs"][-1]["status"] == "interrupted"
    resumed = worker.dispatch("resume_analysis", {"project_id": project_id})
    assert resumed["from_stage"] == "metrics"
    assert coordinator.started == [(project_id, "metrics")]
    assert worker.dispatch("cancel_analysis", {"project_id": project_id})["status"] == "cancelling"


def test_native_worker_resume_is_idempotent_while_cancellation_unwinds(tmp_path: Path) -> None:
    paths, provider, coordinator, project_id = build_pipeline(tmp_path)
    entered = Event()
    release = Event()
    original_list_assets = provider.list_assets

    def blocking_list_assets(album_id: str):
        entered.set()
        assert release.wait(timeout=5)
        return original_list_assets(album_id)

    provider.list_assets = blocking_list_assets
    worker = NativeWorker(paths, provider=provider, coordinator=coordinator)
    worker.dispatch("start_analysis", {"project_id": project_id})
    assert entered.wait(timeout=5)
    assert worker.dispatch("cancel_analysis", {"project_id": project_id})["status"] == "cancelling"

    resumed = worker.dispatch("resume_analysis", {"project_id": project_id})

    assert resumed == {"status": "already_running", "project_id": project_id}
    release.set()
    coordinator._futures[project_id].result(timeout=5)


def test_native_worker_returns_only_latest_job_for_each_stage(tmp_path: Path) -> None:
    paths, provider, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)
    coordinator.run(project_id, from_stage="decisions")
    worker = NativeWorker(paths, provider=provider, coordinator=coordinator)

    jobs = worker.dispatch("project", {"project_id": project_id})["jobs"]

    assert [job["stage"] for job in jobs].count("decisions") == 1
    assert len(jobs) == len({job["stage"] for job in jobs})
    assert jobs[-1]["stage"] == "decisions"


def test_native_worker_persists_top_k_order_and_human_series_leader(tmp_path: Path) -> None:
    paths, provider, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)
    worker = NativeWorker(paths, provider=provider, coordinator=coordinator)
    assets = worker.dispatch("assets", {"project_id": project_id})["items"]

    first, second, third = assets[:3]
    for asset in (first, second, third):
        worker.dispatch(
            "quality_top_k",
            {"project_id": project_id, "asset_uuid": asset["asset_uuid"], "selected": True},
        )
    worker.dispatch(
        "quality_top_k",
        {"project_id": project_id, "asset_uuid": second["asset_uuid"], "selected": False},
    )
    refreshed = worker.dispatch("assets", {"project_id": project_id})["items"]
    ranks = {
        asset["asset_uuid"]: asset["quality_top_k_rank"]
        for asset in refreshed
        if asset["quality_top_k_rank"] is not None
    }
    assert ranks == {first["asset_uuid"]: 1, third["asset_uuid"]: 2}

    grouped = next(asset for asset in refreshed if asset["duplicate_group"])
    decision_result = worker.dispatch(
        "decision",
        {
            "project_id": project_id,
            "asset_uuid": grouped["asset_uuid"],
            "disposition": "keep",
        },
    )
    assert decision_result["duplicate_group"] == grouped["duplicate_group"]
    same_group = [
        asset for asset in refreshed if asset["duplicate_group"] == grouped["duplicate_group"]
    ]
    labelled = worker.dispatch(
        "quality_series",
        {
            "project_id": project_id,
            "group_id": grouped["duplicate_group"],
            "leader_uuid": grouped["asset_uuid"],
        },
    )
    assert labelled["leader_uuid"] == grouped["asset_uuid"]
    assert sorted(labelled["members"]) == sorted(asset["asset_uuid"] for asset in same_group)
    after_series = worker.dispatch("assets", {"project_id": project_id})["items"]
    labelled_assets = [
        asset
        for asset in after_series
        if asset["quality_duplicate_group"] == labelled["duplicate_group"]
    ]
    assert len(labelled_assets) == len(same_group)
    assert sum(asset["quality_expected_leader"] for asset in labelled_assets) == 1

    ungrouped = [asset for asset in after_series if asset["duplicate_group"] is None][:2]
    assert len(ungrouped) == 2
    custom = worker.dispatch(
        "quality_custom_series",
        {
            "project_id": project_id,
            "member_uuids": [asset["asset_uuid"] for asset in ungrouped],
            "leader_uuid": ungrouped[1]["asset_uuid"],
        },
    )
    assert custom["leader_uuid"] == ungrouped[1]["asset_uuid"]
    assert custom["duplicate_group"].startswith("human-")
    status = worker.dispatch("quality_status", {"project_id": project_id})
    assert status["manual_labels"] == 1
    assert status["expected_top_k"] == 2
    assert status["release_ready"] is False
