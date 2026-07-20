import json
from argparse import Namespace
from pathlib import Path

import pytest

from photo_curator.acceptance import (
    AcceptanceManifestError,
    build_manifest_template,
    build_native_quality_evidence,
    build_score_snapshot,
    evaluate_acceptance,
    format_report,
    load_manifest,
    load_score_snapshot,
)
from photo_curator.cli import build_parser, run_acceptance_command
from photo_curator.db import repository
from photo_curator.db.connection import database_connection
from tests.test_pipeline import build_pipeline


def _assets(count: int = 50) -> list[dict[str, object]]:
    return [
        {
            "asset_uuid": f"asset-{index:02d}",
            "current_filename": f"IMG_{index:04d}.JPG",
            "final_disposition": "keep",
            "no_longer_exists": 0,
            "selection_score": 100 - index,
        }
        for index in range(count)
    ]


def _manifest() -> dict[str, object]:
    template = build_manifest_template("trip", _assets())
    rows = template["assets"]
    assert isinstance(rows, list)
    for row in rows:
        row["expected_disposition"] = "keep"
    rows[0].update(duplicate_group="view-1", expected_leader=True)
    rows[1].update(duplicate_group="view-1")
    template["preference_pairs"] = [
        {
            "left_uuid": f"asset-{index:02d}",
            "right_uuid": f"asset-{index + 20:02d}",
            "preferred_uuid": f"asset-{index:02d}",
            "split": "held_out",
        }
        for index in range(10)
    ]
    template["expected_top_k"] = [f"asset-{index:02d}" for index in range(5)]
    return template


def _group(group_id: str, members: list[str], leader: str) -> dict[str, object]:
    return {
        "group_id": group_id,
        "leader_uuid": leader,
        "members": [{"asset_uuid": asset_uuid} for asset_uuid in members],
    }


def test_perfect_human_labelled_fixture_passes_release_thresholds() -> None:
    report = evaluate_acceptance(
        _manifest(),
        _assets(),
        [_group("predicted-1", ["asset-00", "asset-01"], "asset-00")],
        project_id="trip",
    )

    assert report["release_eligible"] is True
    assert report["passed"] is True
    assert report["metrics"] == {
        "duplicate_precision": 1.0,
        "duplicate_recall": 1.0,
        "leader_accuracy": 1.0,
        "false_exclusion_rate": 0.0,
        "pairwise_accuracy": 1.0,
        "top_k_overlap": 1.0,
    }
    assert "Итог: PASS" in format_report(report)


def test_evaluator_reports_false_pair_wrong_leader_and_false_exclusions() -> None:
    assets = _assets()
    for row in assets[:3]:
        row["final_disposition"] = "reject"
    report = evaluate_acceptance(
        _manifest(),
        assets,
        [
            _group("predicted-1", ["asset-00", "asset-01"], "asset-01"),
            _group("predicted-2", ["asset-02", "asset-03"], "asset-02"),
        ],
        project_id="trip",
    )

    assert report["passed"] is False
    assert report["metrics"]["duplicate_precision"] == 0.5
    assert report["metrics"]["duplicate_recall"] == 1.0
    assert report["metrics"]["leader_accuracy"] == 0.0
    assert report["metrics"]["false_exclusion_rate"] == 0.06
    assert report["metrics"]["pairwise_accuracy"] == 1.0
    assert report["details"]["false_exclusion_uuids"] == [
        "asset-00",
        "asset-01",
        "asset-02",
    ]


def test_template_is_not_accepted_until_a_human_fills_every_label() -> None:
    with pytest.raises(AcceptanceManifestError, match="expected_disposition"):
        evaluate_acceptance(
            build_manifest_template("trip", _assets()),
            _assets(),
            [],
            project_id="trip",
        )

    incomplete_assets = _assets()
    incomplete_assets[0]["final_disposition"] = None
    with pytest.raises(AcceptanceManifestError, match="не завершил решения"):
        evaluate_acceptance(_manifest(), incomplete_assets, [], project_id="trip")


def test_manifest_rejects_unknown_assets_and_ambiguous_truth_groups() -> None:
    unknown = _manifest()
    unknown["assets"][0]["asset_uuid"] = "absent"
    with pytest.raises(AcceptanceManifestError, match="отсутствует в проекте"):
        evaluate_acceptance(unknown, _assets(), [], project_id="trip")

    ambiguous = _manifest()
    ambiguous["assets"][1]["expected_leader"] = True
    with pytest.raises(AcceptanceManifestError, match="ровно одного"):
        evaluate_acceptance(ambiguous, _assets(), [], project_id="trip")


def test_manifest_loader_and_cli_commands_are_available(tmp_path: Path) -> None:
    path = tmp_path / "labels.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(AcceptanceManifestError, match="JSON-объект"):
        load_manifest(path)
    with pytest.raises(AcceptanceManifestError, match="JSON-объект"):
        load_score_snapshot(path)

    parser = build_parser()
    template = parser.parse_args(["acceptance-template", "--project-id", "trip"])
    evaluate = parser.parse_args(
        [
            "acceptance-evaluate",
            "--project-id",
            "trip",
            "--labels",
            str(path),
            "--scores",
            str(path),
            "--json",
        ]
    )
    assert template.command == "acceptance-template"
    assert evaluate.command == "acceptance-evaluate"
    assert evaluate.labels == path
    assert evaluate.scores == path
    assert evaluate.json is True


def test_score_snapshot_freezes_engine_identity_and_current_scores() -> None:
    snapshot = build_score_snapshot(
        "trip", _assets(3), engine_name="technical-first", engine_version="legacy-v1"
    )

    assert snapshot == {
        "schema_version": 1,
        "project_id": "trip",
        "engine": {"name": "technical-first", "version": "legacy-v1"},
        "scores": {"asset-00": 100.0, "asset-01": 99.0, "asset-02": 98.0},
    }

    incomplete = _assets(3)
    incomplete[1]["selection_score"] = None
    with pytest.raises(AcceptanceManifestError, match="selection_score"):
        build_score_snapshot(
            "trip", incomplete, engine_name="technical-first", engine_version="legacy-v1"
        )


def test_native_quality_export_never_promotes_predictions_to_human_truth() -> None:
    assets = _assets(3)
    for index, asset in enumerate(assets):
        asset.update(
            swipe_score=90 - index,
            swipe_schema_version=2,
            swipe_model_versions={"generic": "vision-v2"},
            manual_override=index == 0,
            manual_disposition="keep" if index == 0 else None,
            final_disposition="reject" if index == 1 else "keep",
        )
    evidence = build_native_quality_evidence(
        "trip",
        assets,
        [
            {
                "project_id": "trip",
                "left_uuid": "asset-00",
                "right_uuid": "asset-01",
                "preferred_uuid": "asset-00",
                "split": "held_out",
            },
            {
                "project_id": "another-project",
                "left_uuid": "asset-01",
                "right_uuid": "asset-02",
                "preferred_uuid": "asset-02",
                "split": "calibration",
            },
        ],
    )

    assert evidence["manifest"]["assets"] == [
        {
            "asset_uuid": "asset-00",
            "filename": "IMG_0000.JPG",
            "expected_disposition": "keep",
            "duplicate_group": None,
            "expected_leader": False,
        }
    ]
    assert len(evidence["manifest"]["preference_pairs"]) == 1
    assert evidence["manifest"]["expected_top_k"] == []
    assert evidence["score_snapshot"]["scores"]["asset-01"] == 89.0
    assert evidence["score_snapshot"]["engine"]["version"].startswith("native-")
    assert evidence["summary"]["manual_labels"] == 1
    assert evidence["summary"]["release_ready"] is False
    report = evaluate_acceptance(
        evidence["manifest"],
        assets,
        [],
        project_id="trip",
        score_snapshot=evidence["score_snapshot"],
    )
    assert report["labelled_assets"] == 1
    assert report["metrics"]["pairwise_accuracy"] == 1.0
    assert report["release_eligible"] is False


def test_cli_exports_template_from_analyzed_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)
    monkeypatch.setattr("photo_curator.cli.default_application_paths", lambda: paths)
    output = tmp_path / "human-labels.json"

    result = run_acceptance_command(
        Namespace(
            command="acceptance-template",
            project_id=project_id,
            labels=None,
            scores=None,
            output=output,
            json=False,
            engine_name="technical-first-selection-score",
            engine_version="legacy-v1",
        )
    )
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert result == 0
    assert payload["project_id"] == project_id
    assert len(payload["assets"]) == 12
    assert all(row["expected_disposition"] is None for row in payload["assets"])


def test_cli_evaluates_database_results_but_rejects_too_small_release_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    paths, _, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)
    with database_connection(paths.database) as connection:
        assets = repository.list_assets(connection, project_id)
        groups = repository.list_duplicate_groups(connection, project_id)
    manifest = build_manifest_template(project_id, assets)
    labels = {row["asset_uuid"]: row for row in manifest["assets"]}
    for asset in assets:
        labels[asset["asset_uuid"]]["expected_disposition"] = asset["final_disposition"]
    for index, group in enumerate(groups):
        for member in group["members"]:
            label = labels[member["asset_uuid"]]
            label["duplicate_group"] = f"truth-{index}"
            label["expected_leader"] = member["asset_uuid"] == group["leader_uuid"]
    path = tmp_path / "labels.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr("photo_curator.cli.default_application_paths", lambda: paths)

    result = run_acceptance_command(
        Namespace(
            command="acceptance-evaluate",
            project_id=project_id,
            labels=path,
            scores=None,
            output=None,
            json=False,
            engine_name="technical-first-selection-score",
            engine_version="legacy-v1",
        )
    )

    assert result == 1
    assert "Release fixture: нет" in capsys.readouterr().out


def test_cli_exports_current_score_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)
    monkeypatch.setattr("photo_curator.cli.default_application_paths", lambda: paths)
    output = tmp_path / "baseline-scores.json"

    result = run_acceptance_command(
        Namespace(
            command="acceptance-score-export",
            project_id=project_id,
            labels=None,
            scores=None,
            output=output,
            json=False,
            engine_name="technical-first-selection-score",
            engine_version="legacy-v1",
        )
    )
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert result == 0
    assert payload["project_id"] == project_id
    assert payload["engine"]["version"] == "legacy-v1"
    assert len(payload["scores"]) == 12


def test_external_score_snapshot_can_compare_a_new_engine() -> None:
    scores = {f"asset-{index:02d}": float(index) for index in range(50)}
    snapshot = {
        "schema_version": 1,
        "project_id": "trip",
        "engine": {"name": "vision-aesthetics", "version": "1.0"},
        "scores": scores,
    }

    report = evaluate_acceptance(
        _manifest(),
        _assets(),
        [_group("predicted-1", ["asset-00", "asset-01"], "asset-00")],
        project_id="trip",
        score_snapshot=snapshot,
    )

    assert report["scorer"] == {"name": "vision-aesthetics", "version": "1.0"}
    assert report["metrics"]["pairwise_accuracy"] == 0.0
    assert report["metrics"]["top_k_overlap"] == 0.0
    assert report["passed"] is False


def test_v2_manifest_requires_valid_preferences_and_complete_scores() -> None:
    manifest = _manifest()
    manifest["preference_pairs"][0]["preferred_uuid"] = "asset-49"
    with pytest.raises(AcceptanceManifestError, match="preferred_uuid"):
        evaluate_acceptance(manifest, _assets(), [], project_id="trip")

    manifest = _manifest()
    assets = _assets()
    assets[0]["selection_score"] = None
    with pytest.raises(AcceptanceManifestError, match="Нет числового score"):
        evaluate_acceptance(manifest, assets, [], project_id="trip")


def test_schema_v1_manifest_remains_evaluable_without_preference_labels() -> None:
    manifest = _manifest()
    manifest["schema_version"] = 1
    manifest.pop("preference_pairs")
    manifest.pop("expected_top_k")

    report = evaluate_acceptance(
        manifest,
        _assets(),
        [_group("predicted-1", ["asset-00", "asset-01"], "asset-00")],
        project_id="trip",
    )

    assert report["schema_version"] == 1
    assert "pairwise_accuracy" not in report["metrics"]
    assert report["passed"] is True
