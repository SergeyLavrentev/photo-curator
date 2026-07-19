from __future__ import annotations

import io
import json
from pathlib import Path

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
