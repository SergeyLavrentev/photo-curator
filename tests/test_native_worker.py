from __future__ import annotations

import io
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Event, Lock

import pytest

import photo_curator.native_worker as native_worker_module
from photo_curator.db import repository
from photo_curator.db.connection import database_connection
from photo_curator.native_worker import (
    NativeWorker,
    NativeWorkerError,
    _asset_payload,
    run_native_worker,
)
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
            "mutation_generation": 7,
        },
    )

    assert project["project"]["state"] == "ready"
    assert assets["total"] == 12
    assert assets["schema_version"] == 1
    assert all(item["payload_kind"] == "asset_card" for item in assets["items"])
    assert all(len(item["reasons"]) <= 2 for item in assets["items"])
    assert assets["items"][0]["swipe_score"] >= assets["items"][-1]["swipe_score"]
    assert changed["final_disposition"] == "keep"
    assert changed["manual_disposition"] == "keep"
    assert changed["mutation_generation"] == 7
    assert "source_path" not in changed
    alternative = worker.dispatch(
        "selection",
        {
            "project_id": project_id,
            "asset_uuid": changed["asset_uuid"],
            "selection": "alternative",
            "mutation_generation": 8,
        },
    )
    assert alternative["final_disposition"] == "keep"
    assert alternative["manual_disposition"] == "keep"
    assert alternative["final_selection"] == "alternative"
    assert alternative["manual_selection"] == "alternative"
    assert alternative["mutation_generation"] == 8
    stale = worker.dispatch(
        "decision",
        {
            "project_id": project_id,
            "asset_uuid": changed["asset_uuid"],
            "disposition": "reject",
            "mutation_generation": 7,
        },
    )
    assert stale["final_disposition"] == "keep"
    assert stale["final_selection"] == "alternative"
    assert stale["mutation_generation"] == 8
    details = worker.dispatch(
        "asset_details",
        {"project_id": project_id, "asset_uuid": changed["asset_uuid"]},
    )
    assert details["payload_kind"] == "asset_details"
    assert details["payload_schema_version"] == 1
    rated = worker.dispatch(
        "rating",
        {
            "project_id": project_id,
            "asset_uuid": changed["asset_uuid"],
            "rating": 4,
        },
    )
    assert rated["manual_rating"] == 4
    assert (
        worker.dispatch(
            "rating",
            {
                "project_id": project_id,
                "asset_uuid": changed["asset_uuid"],
                "rating": None,
            },
        )["manual_rating"]
        is None
    )
    with pytest.raises(ValueError, match="between 1 and 5"):
        worker.dispatch(
            "rating",
            {
                "project_id": project_id,
                "asset_uuid": changed["asset_uuid"],
                "rating": 6,
            },
        )

    second_page = worker.dispatch("assets", {"project_id": project_id, "offset": 5, "limit": 3})
    assert second_page["offset"] == 5
    assert len(second_page["items"]) == 3
    assert second_page["items"][0]["asset_uuid"] == assets["items"][5]["asset_uuid"]
    first_cursor_page = worker.dispatch("assets", {"project_id": project_id, "limit": 5})
    assert first_cursor_page["next_cursor"]
    next_cursor_page = worker.dispatch(
        "assets",
        {
            "project_id": project_id,
            "limit": 3,
            "cursor": first_cursor_page["next_cursor"],
        },
    )
    assert [item["asset_uuid"] for item in next_cursor_page["items"]] == [
        item["asset_uuid"] for item in assets["items"][5:8]
    ]
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


def test_native_worker_series_is_complete_across_page_and_bucket_boundaries(
    tmp_path: Path,
) -> None:
    paths, provider, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)
    worker = NativeWorker(paths, provider=provider, coordinator=coordinator)
    all_items = worker.dispatch("assets", {"project_id": project_id, "limit": 5000})["items"]
    grouped = max(
        (item for item in all_items if item["duplicate_group"]),
        key=lambda item: item["duplicate_member_count"],
    )
    group_id = grouped["duplicate_group"]
    with database_connection(paths.database) as connection:
        member_ids = [
            str(row[0])
            for row in connection.execute(
                """
                SELECT asset_uuid FROM duplicate_members
                WHERE project_id=? AND group_id=? ORDER BY asset_uuid
                """,
                (project_id, group_id),
            ).fetchall()
        ]
        connection.execute(
            """
            UPDATE decisions SET final_selection='alternative'
            WHERE project_id=? AND asset_uuid IN ({})
            """.format(",".join("?" for _ in member_ids)),
            (project_id, *member_ids),
        )
        connection.execute(
            """
            UPDATE decisions SET final_selection='pick'
            WHERE project_id=? AND asset_uuid=?
            """,
            (project_id, member_ids[0]),
        )

    pick_page = worker.dispatch(
        "assets",
        {"project_id": project_id, "selection": "pick", "limit": 1},
    )
    series = worker.dispatch(
        "series",
        {"project_id": project_id, "group_id": group_id},
    )

    assert len(pick_page["items"]) == 1
    assert series["payload_kind"] == "series"
    assert series["schema_version"] == 1
    assert series["group_id"] == group_id
    assert series["member_count"] == len(member_ids) > len(pick_page["items"])
    assert {item["asset_uuid"] for item in series["items"]} == set(member_ids)
    assert {item["final_selection"] for item in series["items"]} == {
        "pick",
        "alternative",
    }
    assert all(item["duplicate_group"] == group_id for item in series["items"])
    with pytest.raises(NativeWorkerError, match="Series not found"):
        worker.dispatch(
            "series",
            {"project_id": project_id, "group_id": "not-a-series"},
        )


def test_native_worker_series_excludes_inactive_and_video_members(tmp_path: Path) -> None:
    paths, provider, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)
    worker = NativeWorker(paths, provider=provider, coordinator=coordinator)
    grouped = next(
        item
        for item in worker.dispatch("assets", {"project_id": project_id})["items"]
        if item["duplicate_group"]
    )
    group_id = grouped["duplicate_group"]
    with database_connection(paths.database) as connection:
        member_ids = [
            str(row[0])
            for row in connection.execute(
                """
                SELECT asset_uuid FROM duplicate_members
                WHERE project_id=? AND group_id=? ORDER BY asset_uuid
                """,
                (project_id, group_id),
            ).fetchall()
        ]
        connection.execute(
            "UPDATE assets SET no_longer_exists=1 WHERE project_id=? AND asset_uuid=?",
            (project_id, member_ids[0]),
        )
        if len(member_ids) > 1:
            connection.execute(
                "UPDATE assets SET media_type='video' WHERE project_id=? AND asset_uuid=?",
                (project_id, member_ids[1]),
            )

    series = worker.dispatch(
        "series",
        {"project_id": project_id, "group_id": group_id},
    )

    excluded = set(member_ids[:2])
    assert not ({item["asset_uuid"] for item in series["items"]} & excluded)
    assert series["member_count"] == max(0, len(member_ids) - len(excluded))


def test_native_worker_exposes_final_decision_reasons_not_score_highlights(
    tmp_path: Path,
) -> None:
    paths, provider, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)
    worker = NativeWorker(paths, provider=provider, coordinator=coordinator)
    with database_connection(paths.database) as connection:
        decision_asset = next(
            asset
            for asset in repository.list_assets(connection, project_id)
            if asset["final_disposition"] == "reject"
        )

    visible = next(
        item
        for item in worker.dispatch(
            "assets", {"project_id": project_id, "disposition": "reject", "limit": 5000}
        )["items"]
        if item["asset_uuid"] == decision_asset["asset_uuid"]
    )
    visible = worker.dispatch(
        "asset_details",
        {"project_id": project_id, "asset_uuid": visible["asset_uuid"]},
    )

    assert [reason["code"] for reason in visible["reasons"]] == [
        reason["code"] for reason in decision_asset["reasons"]
    ]
    weaker_duplicate = next(
        reason for reason in visible["reasons"] if reason["code"] == "weaker_duplicate"
    )
    assert weaker_duplicate["duplicate_kind"] in {"exact", "burst", "scene"}
    assert visible["confidence"] == decision_asset["confidence"]
    assert visible["reasons"] != decision_asset["swipe_reasons"]


def test_native_worker_exposes_duplicate_leader_and_technical_reason_details(
    tmp_path: Path,
) -> None:
    paths, provider, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)
    worker = NativeWorker(paths, provider=provider, coordinator=coordinator)

    rejected = worker.dispatch(
        "assets", {"project_id": project_id, "disposition": "reject", "limit": 5000}
    )["items"]
    duplicate = next(item for item in rejected if item["duplicate_leader_uuid"])
    duplicate = worker.dispatch(
        "asset_details",
        {"project_id": project_id, "asset_uuid": duplicate["asset_uuid"]},
    )

    assert duplicate["duplicate_leader_uuid"] != duplicate["asset_uuid"]
    assert duplicate["duplicate_leader"]["asset_uuid"] == duplicate["duplicate_leader_uuid"]
    duplicate_reason = next(
        reason for reason in duplicate["reasons"] if reason["code"] == "weaker_duplicate"
    )
    assert duplicate_reason["duplicate_kind"] in {"exact", "burst", "scene"}
    detailed_rejected = [
        worker.dispatch(
            "asset_details",
            {"project_id": project_id, "asset_uuid": item["asset_uuid"]},
        )
        for item in rejected
    ]
    below_cutoff = next(
        item
        for item in detailed_rejected
        if any(reason["code"] == "below_album_cutoff" for reason in item["reasons"])
    )
    assert len(below_cutoff["album_references"]) == 3
    payload = _asset_payload(
        {
            "asset_uuid": "technical",
            "flags": ["possible_blur", "underexposed"],
            "reasons": [{"code": "technical_penalty", "value": 26.0}],
        }
    )
    technical = payload["reasons"][0]
    assert technical["technical_defect_codes"] == ["possible_blur", "underexposed"]
    uncertainty = _asset_payload(
        {
            "asset_uuid": "uncertainty",
            "swipe_confidence": 0.41,
            "swipe_components": {
                "evidence_coverage": 74.0,
                "model_count": 3.0,
                "model_disagreement": 28.0,
            },
        }
    )
    assert uncertainty["evidence_coverage"] == 0.74
    assert uncertainty["model_count"] == 3
    assert uncertainty["model_disagreement"] == 0.28
    duplicate_only = _asset_payload(
        {
            "asset_uuid": "duplicate-only",
            "reasons": [{"code": "technical_penalty", "value": 45.0}],
        }
    )
    assert duplicate_only["reasons"] == []


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
    by_id = {response["id"]: response for response in responses}
    assert set(by_id) == {"one", "two", "three"}
    assert by_id["one"]["result"]["worker_schema_version"] == 3
    assert by_id["two"]["error"]["type"] == "NativeWorkerError"
    assert by_id["three"]["result"] == {"status": "bye"}


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
    response = next(
        json.loads(line)
        for line in output_stream.getvalue().splitlines()
        if json.loads(line)["id"] == "albums"
    )
    assert response["result"]["regular"][0]["photo_count"] == 12


def test_jsonl_worker_multiplexes_a_read_while_another_request_is_waiting(
    tmp_path: Path,
) -> None:
    fast_seen = Event()

    class ConcurrentWorker:
        def dispatch(self, method, params, *, progress=None):
            del params, progress
            if method == "slow":
                return {"fast_completed": fast_seen.wait(timeout=1)}
            if method == "fast":
                fast_seen.set()
                return {"status": "fast"}
            raise ValueError(method)

    requests = [
        {"schema_version": 1, "id": "slow", "method": "slow", "params": {}},
        {"schema_version": 1, "id": "fast", "method": "fast", "params": {}},
        {"schema_version": 1, "id": "bye", "method": "shutdown", "params": {}},
    ]
    output = io.StringIO()

    assert (
        run_native_worker(
            tmp_path,
            input_stream=io.StringIO("".join(json.dumps(row) + "\n" for row in requests)),
            output_stream=output,
            worker=ConcurrentWorker(),
        )
        == 0
    )
    responses = {row["id"]: row for row in map(json.loads, output.getvalue().splitlines())}

    assert responses["fast"]["result"] == {"status": "fast"}
    assert responses["slow"]["result"] == {"fast_completed": True}


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
    with database_connection(paths.database) as connection:
        stored = repository.get_project(connection, str(created["id"]))
    assert json.loads(str(stored["settings_json"]))["source_album_shared"] is True

    coordinator.run(str(created["id"]))
    candidates = worker.dispatch("quality_candidates", {"project_id": created["id"], "limit": 4})
    assert candidates["requested"] == 4
    assert candidates["available"] == 4
    assert all(item["quality_disposition"] is None for item in candidates["items"])
    assert all("manual_disposition" not in item for item in candidates["items"])

    missing_id = candidates["items"][0]["asset_uuid"]
    with database_connection(paths.database) as connection:
        missing_asset = repository.get_asset(connection, str(created["id"]), str(missing_id))
    Path(str(missing_asset["review_path"])).unlink()
    project = worker.dispatch("project", {"project_id": created["id"]})
    assert project["summary"]["unavailable_preview_files"] == 1
    refreshed = worker.dispatch("quality_candidates", {"project_id": created["id"], "limit": 4})
    assert missing_id not in {item["asset_uuid"] for item in refreshed["items"]}


def test_native_worker_persists_independent_local_engine_switches(tmp_path: Path) -> None:
    paths, provider, coordinator, _ = build_pipeline(tmp_path)
    worker = NativeWorker(paths, provider=provider, coordinator=coordinator)

    created = worker.dispatch(
        "create_project",
        {
            "album_id": provider.ALBUM_ID,
            "engine_apple": False,
            "engine_nima": True,
            "engine_mobileclip": False,
            "engine_musiq": True,
        },
    )

    with database_connection(paths.database) as connection:
        project = repository.get_project(connection, str(created["id"]))
    settings = json.loads(str(project["settings_json"]))
    assert settings["local_engines"] == {
        "apple": False,
        "nima": True,
        "mobileclip": False,
        "musiq": True,
    }


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

    with pytest.raises(NativeWorkerError, match="confirmed=true"):
        worker.dispatch("delete_project", {"project_id": abandoned["id"]})

    deleted = worker.dispatch("delete_project", {"project_id": abandoned["id"], "confirmed": True})

    assert deleted["status"] == "deleted"
    assert deleted["project_id"] == abandoned["id"]
    assert Path(deleted["backup_path"]).is_file()
    audit = (paths.data_dir / "destructive-actions.jsonl").read_text()
    assert '"status": "requested"' in audit
    assert '"status": "completed"' in audit
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
    calibration_accuracy = float(profile["evidence"]["calibration_accuracy"])
    if calibration_accuracy > 0.5:
        assert any(abs(item["personal_delta"] or 0) > 0.1 for item in reranked)
    else:
        # A worse-than-chance profile is persisted for transparency but cannot
        # silently influence ranking before better preference evidence exists.
        assert all(float(item["personal_delta"] or 0) == 0 for item in reranked)
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
    assert quality["manifest"]["assets"] == []
    worker.dispatch(
        "quality_label",
        {
            "project_id": project_id,
            "asset_uuid": reranked[0]["asset_uuid"],
            "disposition": "keep",
            "defect_codes": [],
        },
    )
    quality = worker.dispatch("quality_export", {"project_id": project_id})
    assert quality["manifest"]["assets"][0]["expected_disposition"] == "keep"
    assert set(quality["baseline_snapshots"]) == {
        "current",
        "non_personalized",
        "apple_only",
    }
    assert len(quality["baseline_snapshots"]["apple_only"]["scores"]) == 12
    assert quality["manifest"]["preference_pairs"] == []
    assert quality["summary"]["manual_labels"] == 1
    assert quality["summary"]["held_out_pairs"] == 0
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
    assert labelled["source_kind"] == "predicted_group"
    assert labelled["coherence_status"] == "verified"
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
    assert custom["source_snapshot_id"]
    assert custom["source_kind"] == "manual_album_order"
    status = worker.dispatch("quality_status", {"project_id": project_id})
    assert status["manual_labels"] == 0
    assert status["expected_top_k"] == 2
    assert status["release_ready"] is False


def test_quality_wizard_is_blind_and_keeps_held_out_pairs_out_of_taste_profile(
    tmp_path: Path,
) -> None:
    paths, provider, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)
    worker = NativeWorker(paths, provider=provider, coordinator=coordinator)

    candidates = worker.dispatch("quality_candidates", {"project_id": project_id, "limit": 12})
    assert candidates["available"] == 12
    first = candidates["items"][0]
    assert "swipe_score" not in first
    assert "reasons" not in first
    assert "final_disposition" not in first
    assert "manual_disposition" not in first

    with database_connection(paths.database) as connection:
        product_decision_before = repository.get_asset(connection, project_id, first["asset_uuid"])

    with pytest.raises(ValueError, match="укажите хотя бы один дефект"):
        worker.dispatch(
            "quality_label",
            {
                "project_id": project_id,
                "asset_uuid": first["asset_uuid"],
                "disposition": "reject",
                "defect_codes": [],
            },
        )
    labelled = worker.dispatch(
        "quality_label",
        {
            "project_id": project_id,
            "asset_uuid": first["asset_uuid"],
            "disposition": "reject",
            "defect_codes": ["motion_blur"],
            "defect_severity": 3,
            "defect_confidence": 0.9,
            "note": "Смаз заметен на лице",
        },
    )
    assert labelled["quality_disposition"] == "reject"
    assert "manual_disposition" not in labelled
    assert labelled["quality_defect_codes"] == ["motion_blur"]
    with database_connection(paths.database) as connection:
        product_decision_after = repository.get_asset(connection, project_id, first["asset_uuid"])
    assert (
        product_decision_after["manual_disposition"]
        == product_decision_before["manual_disposition"]
    )
    assert product_decision_after["manual_selection"] == product_decision_before["manual_selection"]
    assert (
        product_decision_after["final_disposition"] == product_decision_before["final_disposition"]
    )
    assert product_decision_after["final_selection"] == product_decision_before["final_selection"]
    second = candidates["items"][1]
    worker.dispatch(
        "quality_label",
        {
            "project_id": project_id,
            "asset_uuid": second["asset_uuid"],
            "disposition": "keep",
            "defect_codes": [],
        },
    )

    pair = worker.dispatch("quality_pair", {"project_id": project_id})["pair"]
    assert pair is not None
    preference = worker.dispatch(
        "quality_preference",
        {
            "project_id": project_id,
            "left_uuid": pair["left"]["asset_uuid"],
            "right_uuid": pair["right"]["asset_uuid"],
            "preferred_uuid": pair["left"]["asset_uuid"],
        },
    )
    assert preference["split"] == "held_out"
    with database_connection(paths.database) as connection:
        assert repository.list_preference_examples(connection) == []
        assert len(repository.list_quality_preference_examples(connection, project_id)) == 1
    evidence = worker.dispatch("quality_export", {"project_id": project_id})
    assert evidence["summary"]["held_out_pairs"] == 1
    assert evidence["summary"]["defect_labels"] == 1
    assert any(asset["defect_codes"] == ["motion_blur"] for asset in evidence["manifest"]["assets"])


def test_delete_project_fails_closed_when_database_backup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, provider, coordinator, project_id = build_pipeline(tmp_path)
    worker = NativeWorker(paths, provider=provider, coordinator=coordinator)

    def fail_backup(*_args, **_kwargs):
        raise OSError("backup volume unavailable")

    monkeypatch.setattr("photo_curator.native_worker.create_database_backup", fail_backup)

    with pytest.raises(OSError, match="backup volume unavailable"):
        worker.dispatch("delete_project", {"project_id": project_id, "confirmed": True})

    assert [item["id"] for item in worker.dispatch("projects", {})] == [project_id]


def test_delete_project_cannot_race_pipeline_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, provider, coordinator, project_id = build_pipeline(tmp_path)
    entered = Event()
    release = Event()

    def blocked_run(received_project_id: str, *, from_stage: str | None = None) -> None:
        del from_stage
        assert received_project_id == project_id
        entered.set()
        assert release.wait(timeout=5)
        with database_connection(paths.database) as connection:
            repository.set_project_state(connection, project_id, "ready")

    monkeypatch.setattr(coordinator, "run", blocked_run)
    worker = NativeWorker(paths, provider=provider, coordinator=coordinator)

    worker.dispatch("start_analysis", {"project_id": project_id})
    assert entered.wait(timeout=5)
    with pytest.raises(NativeWorkerError, match="остановите выполняющийся анализ"):
        worker.dispatch("delete_project", {"project_id": project_id, "confirmed": True})

    with database_connection(paths.database) as connection:
        assert repository.get_project(connection, project_id)["state"] == "running"
    assert not (paths.data_dir / "destructive-actions.jsonl").exists()

    release.set()
    coordinator._futures[project_id].result(timeout=5)


def test_publish_dry_run_is_blocked_while_pipeline_future_is_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, provider, coordinator, project_id = build_pipeline(tmp_path)
    entered = Event()
    release = Event()

    def blocked_run(received_project_id: str, *, from_stage: str | None = None) -> None:
        del from_stage
        assert received_project_id == project_id
        entered.set()
        assert release.wait(timeout=5)
        with database_connection(paths.database) as connection:
            repository.set_project_state(connection, project_id, "ready")

    class UnexpectedPublisher:
        def dry_run(self, *_args, **_kwargs):
            raise AssertionError("publisher must not run during analysis")

    monkeypatch.setattr(coordinator, "run", blocked_run)
    worker = NativeWorker(
        paths,
        provider=provider,
        coordinator=coordinator,
        publisher=UnexpectedPublisher(),
    )
    worker.dispatch("start_analysis", {"project_id": project_id})
    assert entered.wait(timeout=5)

    with pytest.raises(NativeWorkerError, match="остановите выполняющийся анализ"):
        worker.dispatch("publish_dry_run", {"project_id": project_id, "kind": "best"})

    release.set()
    coordinator._futures[project_id].result(timeout=5)


def test_parallel_publish_apply_is_serialized_per_project(tmp_path: Path) -> None:
    paths, provider, coordinator, project_id = build_pipeline(tmp_path)
    uuid_file = tmp_path / "publish.txt"
    uuid_file.write_text("demo-001\n", encoding="utf-8")
    with database_connection(paths.database) as connection:
        publish_id = repository.create_publish(
            connection,
            project_id=project_id,
            album_name="Best",
            asset_count=1,
            uuid_file=str(uuid_file),
            kind="best",
        )
        repository.record_dry_run(
            connection,
            publish_id,
            stdout="ok",
            stderr="",
            return_code=0,
        )

    first_entered = Event()
    second_attempted = Event()
    second_entered = Event()
    release = Event()

    class BlockingPublisher:
        def __init__(self) -> None:
            self.calls = 0
            self.lock = Lock()

        def apply(self, received_publish_id, *, progress=None):
            del progress
            assert received_publish_id == publish_id
            with self.lock:
                self.calls += 1
                call = self.calls
            (first_entered if call == 1 else second_entered).set()
            if call == 1:
                assert release.wait(timeout=5)
            with database_connection(paths.database) as connection:
                return repository.get_publish(connection, publish_id)

    publisher = BlockingPublisher()
    worker = NativeWorker(
        paths,
        provider=provider,
        coordinator=coordinator,
        publisher=publisher,
    )

    def second_apply():
        second_attempted.set()
        return worker.dispatch("publish_apply", {"publish_id": publish_id, "confirmed": True})

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            worker.dispatch,
            "publish_apply",
            {"publish_id": publish_id, "confirmed": True},
        )
        assert first_entered.wait(timeout=5)
        second = executor.submit(second_apply)
        assert second_attempted.wait(timeout=5)
        assert not second_entered.wait(timeout=0.2)
        release.set()
        first.result(timeout=5)
        second.result(timeout=5)

    assert publisher.calls == 2


def test_native_worker_photokit_acceptance_requires_confirmation_and_audits_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, provider, coordinator, project_id = build_pipeline(tmp_path)
    worker = NativeWorker(paths, provider=provider, coordinator=coordinator)
    expected = {
        "schema_version": 2,
        "passed": True,
        "project_id": project_id,
        "album_name": "PhotoCurator Acceptance Best — test",
        "album_identifier": "acceptance-album",
        "asset_identifier": "asset-1",
        "acceptance_album_removed": True,
        "source_asset_preserved_after_cleanup": True,
    }
    monkeypatch.setattr(
        native_worker_module,
        "run_photokit_acceptance",
        lambda _paths, *, project_id: {**expected, "project_id": project_id},
    )

    with pytest.raises(NativeWorkerError, match="confirmed=true"):
        worker.dispatch("photokit_acceptance", {"project_id": project_id})

    assert (
        worker.dispatch("photokit_acceptance", {"project_id": project_id, "confirmed": True})
        == expected
    )
    audit = (paths.data_dir / "photokit-acceptance.jsonl").read_text()
    record = json.loads(audit)
    assert record["passed"] is True
    assert record["album_identifier"] == "acceptance-album"
    assert record["acceptance_album_removed"] is True


def test_native_worker_photokit_acceptance_two_phase_gui_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, provider, coordinator, project_id = build_pipeline(tmp_path)
    worker = NativeWorker(paths, provider=provider, coordinator=coordinator)
    prepared = {
        "prepared_schema_version": 1,
        "project_id": project_id,
        "album_name": "PhotoCurator Acceptance Best — test",
        "album_identifier": "acceptance-album",
        "source_album_identifier": "source-album",
        "asset_identifier": "asset-1",
    }
    expected = {
        "schema_version": 2,
        "passed": True,
        "project_id": project_id,
        "album_name": prepared["album_name"],
        "album_identifier": prepared["album_identifier"],
        "acceptance_album_removed": True,
        "source_asset_preserved_after_cleanup": True,
    }
    monkeypatch.setattr(
        native_worker_module,
        "prepare_photokit_acceptance",
        lambda _paths, *, project_id: {**prepared, "project_id": project_id},
    )
    monkeypatch.setattr(
        native_worker_module,
        "finalize_photokit_acceptance",
        lambda _paths, *, prepared: {**expected, "project_id": prepared["project_id"]},
    )

    with pytest.raises(NativeWorkerError, match="confirmed=true"):
        worker.dispatch(
            "photokit_acceptance_prepare", {"project_id": project_id, "confirmed": False}
        )
    assert worker.dispatch(
        "photokit_acceptance_prepare", {"project_id": project_id, "confirmed": True}
    ) == {**prepared, "project_id": project_id}
    assert (
        worker.dispatch("photokit_acceptance_finalize", {"prepared": prepared, "confirmed": True})
        == expected
    )
    audit = (paths.data_dir / "photokit-acceptance.jsonl").read_text()
    assert json.loads(audit)["passed"] is True
