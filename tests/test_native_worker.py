from __future__ import annotations

import io
import json
from pathlib import Path

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
    assert responses[0]["result"]["worker_schema_version"] == 1
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
    assert worker.dispatch("taste_status", {"paused": True})["status"] == "paused"
    assert worker.dispatch("taste_reset", {}) == {"status": "deleted"}
    assert worker.dispatch("taste_profile", {})["preference_count"] == 0


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
