from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any

ALLOWED_DISPOSITIONS = {"keep", "review", "reject"}
DEFAULT_THRESHOLDS = {
    "duplicate_precision": 0.999,
    "duplicate_recall": 0.95,
    "leader_accuracy": 0.85,
    "false_exclusion_rate": 0.005,
    "pairwise_accuracy": 0.65,
    "top_k_overlap": 0.60,
}
MIN_HELD_OUT_PAIRS = 10
MIN_TOP_K = 5


class AcceptanceManifestError(ValueError):
    """The human-labelled manifest cannot produce a trustworthy score."""


def build_manifest_template(project_id: str, assets: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema_version": 2,
        "project_id": project_id,
        "thresholds": DEFAULT_THRESHOLDS.copy(),
        "preference_pairs": [],
        "expected_top_k": [],
        "assets": [
            {
                "asset_uuid": str(asset["asset_uuid"]),
                "filename": asset.get("current_filename"),
                "expected_disposition": None,
                "duplicate_group": None,
                "expected_leader": False,
            }
            for asset in assets
            if not asset.get("no_longer_exists")
        ],
    }


def build_score_snapshot(
    project_id: str,
    assets: list[dict[str, object]],
    *,
    engine_name: str,
    engine_version: str,
    score_field: str = "selection_score",
) -> dict[str, object]:
    scores: dict[str, float] = {}
    for asset in assets:
        if asset.get("no_longer_exists"):
            continue
        asset_uuid = str(asset["asset_uuid"])
        score = asset.get(score_field)
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            raise AcceptanceManifestError(
                f"Проект не содержит завершённый {score_field} для {asset_uuid}"
            )
        scores[asset_uuid] = float(score)
    return {
        "schema_version": 1,
        "project_id": project_id,
        "engine": {"name": engine_name, "version": engine_version},
        "scores": scores,
    }


def compare_acceptance_scores(
    manifest: dict[str, object],
    assets: list[dict[str, object]],
    predicted_groups: list[dict[str, object]],
    *,
    project_id: str,
    candidate_snapshot: dict[str, object],
    baseline_snapshot: dict[str, object],
    min_pairwise_uplift: float = 0.05,
    min_top_k_uplift: float = 0.10,
) -> dict[str, object]:
    """Require absolute quality gates and measurable uplift on one held-out corpus."""

    candidate = evaluate_acceptance(
        manifest,
        assets,
        predicted_groups,
        project_id=project_id,
        score_snapshot=candidate_snapshot,
    )
    baseline = evaluate_acceptance(
        manifest,
        assets,
        predicted_groups,
        project_id=project_id,
        score_snapshot=baseline_snapshot,
    )
    candidate_metrics = candidate["metrics"]
    baseline_metrics = baseline["metrics"]
    assert isinstance(candidate_metrics, dict)
    assert isinstance(baseline_metrics, dict)
    pairwise_uplift = float(candidate_metrics["pairwise_accuracy"]) - float(
        baseline_metrics["pairwise_accuracy"]
    )
    top_k_uplift = float(candidate_metrics["top_k_overlap"]) - float(
        baseline_metrics["top_k_overlap"]
    )
    checks = {
        "candidate_absolute_gates": bool(candidate["passed"]),
        "pairwise_uplift": pairwise_uplift >= min_pairwise_uplift,
        "top_k_uplift": top_k_uplift >= min_top_k_uplift,
    }
    return {
        "schema_version": 1,
        "project_id": project_id,
        "passed": all(checks.values()),
        "checks": checks,
        "thresholds": {
            "min_pairwise_uplift": min_pairwise_uplift,
            "min_top_k_uplift": min_top_k_uplift,
        },
        "uplift": {
            "pairwise_accuracy": pairwise_uplift,
            "top_k_overlap": top_k_uplift,
        },
        "candidate": candidate,
        "baseline": baseline,
    }


def build_native_quality_evidence(
    project_id: str,
    assets: list[dict[str, object]],
    preference_examples: list[dict[str, object]],
    signals: dict[str, dict[str, dict[str, object]]] | None = None,
) -> dict[str, object]:
    """Build an honest, portable quality corpus from explicit native-app feedback."""
    active_assets = [asset for asset in assets if not asset.get("no_longer_exists")]
    manually_labelled = [
        asset
        for asset in active_assets
        if bool(asset.get("manual_override"))
        and asset.get("manual_disposition") in ALLOWED_DISPOSITIONS
        and bool(asset.get("quality_lab_sampled", True))
    ]
    manually_labelled_ids = {str(asset["asset_uuid"]) for asset in manually_labelled}
    annotated_groups: dict[str, list[dict[str, object]]] = defaultdict(list)
    for asset in active_assets:
        group = asset.get("quality_duplicate_group")
        if isinstance(group, str) and group:
            annotated_groups[group].append(asset)
    complete_groups = {
        group
        for group, members in annotated_groups.items()
        if len(members) >= 2
        and {str(member["asset_uuid"]) for member in members} <= manually_labelled_ids
        and sum(bool(member.get("quality_expected_leader")) for member in members) == 1
    }
    top_k = [
        str(asset["asset_uuid"])
        for asset in sorted(
            (
                asset
                for asset in active_assets
                if isinstance(asset.get("quality_top_k_rank"), int)
                and not isinstance(asset.get("quality_top_k_rank"), bool)
            ),
            key=lambda asset: (int(asset["quality_top_k_rank"]), str(asset["asset_uuid"])),
        )
    ]
    project_ids = {str(asset["asset_uuid"]) for asset in active_assets}
    pairs = [
        {
            "left_uuid": str(example["left_uuid"]),
            "right_uuid": str(example["right_uuid"]),
            "preferred_uuid": str(example["preferred_uuid"]),
            "split": str(example["split"]),
        }
        for example in preference_examples
        if example.get("project_id") == project_id
        and example.get("left_uuid") in project_ids
        and example.get("right_uuid") in project_ids
    ]
    manifest = {
        "schema_version": 2,
        "project_id": project_id,
        "thresholds": DEFAULT_THRESHOLDS.copy(),
        "preference_pairs": pairs,
        "expected_top_k": top_k,
        "assets": [
            {
                "asset_uuid": str(asset["asset_uuid"]),
                "filename": asset.get("current_filename"),
                "expected_disposition": asset["manual_disposition"],
                "duplicate_group": (
                    asset.get("quality_duplicate_group")
                    if asset.get("quality_duplicate_group") in complete_groups
                    else None
                ),
                "expected_leader": bool(asset.get("quality_expected_leader"))
                if asset.get("quality_duplicate_group") in complete_groups
                else False,
                "defect_codes": _quality_defect_codes(asset),
                "defect_severity": asset.get("quality_defect_severity"),
                "defect_confidence": asset.get("quality_defect_confidence"),
                "quality_note": asset.get("quality_note"),
            }
            for asset in manually_labelled
        ],
    }

    score_rows: dict[str, float] = {}
    schema_versions: set[int] = set()
    model_versions: set[str] = set()
    for asset in active_assets:
        score = asset.get("swipe_score")
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            raise AcceptanceManifestError(
                f"Проект не содержит завершённый Swipe Score для {asset['asset_uuid']}"
            )
        score_rows[str(asset["asset_uuid"])] = float(score)
        schema = asset.get("swipe_schema_version")
        if isinstance(schema, int) and not isinstance(schema, bool):
            schema_versions.add(schema)
        models = asset.get("swipe_model_versions")
        if isinstance(models, dict):
            model_versions.add(json.dumps(models, sort_keys=True, separators=(",", ":")))
    provenance = {
        "score_schema_versions": sorted(schema_versions),
        "model_versions": [json.loads(value) for value in sorted(model_versions)],
    }
    fingerprint = hashlib.sha256(
        json.dumps(provenance, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]
    snapshot = {
        "schema_version": 1,
        "project_id": project_id,
        "engine": {"name": "swipe-score", "version": f"native-{fingerprint}"},
        "provenance": provenance,
        "scores": score_rows,
    }
    apple_scores: dict[str, float] = {}
    generic_scores: dict[str, float] = {}
    signals = signals or {}
    for asset in active_assets:
        asset_uuid = str(asset["asset_uuid"])
        generic = asset.get("swipe_generic_score")
        if isinstance(generic, (int, float)) and not isinstance(generic, bool):
            generic_scores[asset_uuid] = float(generic)
        aesthetic = signals.get(asset_uuid, {}).get("aesthetics")
        value = aesthetic.get("value") if aesthetic and aesthetic.get("status") == "ready" else None
        apple = value.get("overall_score") if isinstance(value, dict) else None
        if isinstance(apple, (int, float)) and not isinstance(apple, bool):
            apple_scores[asset_uuid] = (float(apple) + 1.0) * 50.0
    baseline_snapshots = {
        "current": snapshot,
        "non_personalized": {
            "schema_version": 1,
            "project_id": project_id,
            "engine": {"name": "swipe-score-generic", "version": f"native-{fingerprint}"},
            "scores": generic_scores,
        },
        "apple_only": {
            "schema_version": 1,
            "project_id": project_id,
            "engine": {"name": "apple-vision-aesthetics", "version": "persisted-native"},
            "scores": apple_scores,
        },
    }
    held_out = sum(pair["split"] == "held_out" for pair in pairs)
    defect_labels = sum(bool(_quality_defect_codes(asset)) for asset in manually_labelled)
    release_ready = (
        50 <= len(manually_labelled) <= 100
        and held_out >= MIN_HELD_OUT_PAIRS
        and len(top_k) >= MIN_TOP_K
        and bool(complete_groups)
    )
    return {
        "schema_version": 1,
        "project_id": project_id,
        "manifest": manifest,
        "score_snapshot": snapshot,
        "baseline_snapshots": baseline_snapshots,
        "summary": {
            "manual_labels": len(manually_labelled),
            "preference_pairs": len(pairs),
            "held_out_pairs": held_out,
            "expected_top_k": len(top_k),
            "human_duplicate_groups": len(complete_groups),
            "defect_labels": defect_labels,
            "incomplete_human_duplicate_groups": len(annotated_groups) - len(complete_groups),
            "required_manual_labels_min": 50,
            "required_manual_labels_max": 100,
            "required_held_out_pairs": MIN_HELD_OUT_PAIRS,
            "required_top_k": MIN_TOP_K,
            "release_ready": release_ready,
        },
    }


def _quality_defect_codes(asset: dict[str, object]) -> list[str]:
    raw = asset.get("quality_defect_codes")
    if isinstance(raw, list):
        return [str(value) for value in raw if isinstance(value, str)]
    try:
        decoded = json.loads(str(asset.get("quality_defect_codes_json") or "[]"))
    except json.JSONDecodeError:
        return []
    return (
        [str(value) for value in decoded if isinstance(value, str)]
        if isinstance(decoded, list)
        else []
    )


def load_manifest(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AcceptanceManifestError(f"Не удалось прочитать manifest: {error}") from error
    if not isinstance(payload, dict):
        raise AcceptanceManifestError("Корень manifest должен быть JSON-объектом")
    return payload


def load_score_snapshot(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AcceptanceManifestError(f"Не удалось прочитать score snapshot: {error}") from error
    if not isinstance(payload, dict):
        raise AcceptanceManifestError("Корень score snapshot должен быть JSON-объектом")
    return payload


def evaluate_acceptance(
    manifest: dict[str, object],
    assets: list[dict[str, object]],
    predicted_groups: list[dict[str, object]],
    *,
    project_id: str,
    score_snapshot: dict[str, object] | None = None,
) -> dict[str, object]:
    labels, thresholds, preferences = _validate_manifest(manifest, assets, project_id)
    labelled_ids = set(labels)
    asset_by_uuid = {str(asset["asset_uuid"]): asset for asset in assets}
    incomplete_results = sorted(
        asset_uuid
        for asset_uuid in labelled_ids
        if _automatic_disposition(asset_by_uuid[asset_uuid]) not in ALLOWED_DISPOSITIONS
    )
    if incomplete_results:
        raise AcceptanceManifestError(
            "Проект не завершил решения для размеченных фото: " + ", ".join(incomplete_results[:5])
        )
    truth_pairs = _truth_pairs(labels)
    predicted_pairs = _predicted_pairs(predicted_groups, labelled_ids)
    true_positive_pairs = truth_pairs & predicted_pairs

    precision = _safe_ratio(
        len(true_positive_pairs), len(predicted_pairs), empty_value=not truth_pairs
    )
    recall = _safe_ratio(len(true_positive_pairs), len(truth_pairs), empty_value=True)
    leader_correct, leader_total, leader_details = _leader_accuracy(labels, predicted_groups)
    leader_accuracy = _safe_ratio(leader_correct, leader_total, empty_value=False)

    protected = [
        asset_uuid
        for asset_uuid, label in labels.items()
        if label["expected_disposition"] in {"keep", "review"}
    ]
    false_exclusions = [
        asset_uuid
        for asset_uuid in protected
        if _automatic_disposition(asset_by_uuid[asset_uuid]) == "reject"
    ]
    false_exclusion_rate = _safe_ratio(len(false_exclusions), len(protected), empty_value=True)

    preference_metrics: dict[str, float] = {}
    preference_counts: dict[str, int] = {}
    preference_details: dict[str, object] = {}
    scorer: dict[str, str] | None = None
    if preferences is not None:
        score_map, scorer = _validated_score_map(
            score_snapshot, asset_by_uuid, labelled_ids, project_id
        )
        preference_metrics, preference_counts, preference_details = _preference_metrics(
            preferences, score_map
        )

    calibration_metrics, calibration_details = _decision_calibration(labels, asset_by_uuid)
    selection_states = [
        asset.get("auto_selection") or asset.get("final_selection") for asset in assets
    ]
    selection_reduction = (
        1.0 - sum(state == "pick" for state in selection_states) / len(selection_states)
        if selection_states
        and all(state in {"pick", "alternative", "review", "reject"} for state in selection_states)
        else None
    )

    metrics = {
        "duplicate_precision": precision,
        "duplicate_recall": recall,
        "leader_accuracy": leader_accuracy,
        "false_exclusion_rate": false_exclusion_rate,
        **calibration_metrics,
        **(
            {"selection_reduction_ratio": selection_reduction}
            if selection_reduction is not None
            else {}
        ),
        **preference_metrics,
    }
    checks = {
        "duplicate_precision": precision >= thresholds["duplicate_precision"],
        "duplicate_recall": recall >= thresholds["duplicate_recall"],
        "leader_accuracy": leader_accuracy >= thresholds["leader_accuracy"],
        "false_exclusion_rate": false_exclusion_rate <= thresholds["false_exclusion_rate"],
        **{
            key: preference_metrics[key] >= thresholds[key]
            for key in ("pairwise_accuracy", "top_k_overlap")
            if key in preference_metrics
        },
    }
    release_eligible = 50 <= len(labels) <= 100 and bool(truth_pairs) and leader_total > 0
    if preferences is not None:
        release_eligible = (
            release_eligible
            and preference_counts["held_out_pairs"] >= MIN_HELD_OUT_PAIRS
            and preference_counts["expected_top_k"] >= MIN_TOP_K
        )
    return {
        "schema_version": int(manifest["schema_version"]),
        "project_id": project_id,
        "scorer": scorer,
        "labelled_assets": len(labels),
        "release_eligible": release_eligible,
        "passed": release_eligible and all(checks.values()),
        "thresholds": thresholds,
        "metrics": metrics,
        "checks": checks,
        "counts": {
            "truth_duplicate_pairs": len(truth_pairs),
            "predicted_duplicate_pairs": len(predicted_pairs),
            "true_positive_pairs": len(true_positive_pairs),
            "truth_groups": leader_total,
            "correct_leaders": leader_correct,
            "protected_from_exclusion": len(protected),
            "false_exclusions": len(false_exclusions),
            **preference_counts,
        },
        "details": {
            "false_exclusion_uuids": sorted(false_exclusions),
            "leader_groups": leader_details,
            **calibration_details,
            **preference_details,
        },
    }


def format_report(report: dict[str, object]) -> str:
    metrics = report["metrics"]
    counts = report["counts"]
    checks = report["checks"]
    assert isinstance(metrics, dict)
    assert isinstance(counts, dict)
    assert isinstance(checks, dict)
    lines = [
        f"Acceptance project: {report['project_id']}",
        f"Размечено: {report['labelled_assets']} фото",
        f"Release fixture: {'да' if report['release_eligible'] else 'нет'}",
    ]
    labels = {
        "duplicate_precision": "Duplicate precision",
        "duplicate_recall": "Duplicate recall",
        "leader_accuracy": "Series leader accuracy",
        "false_exclusion_rate": "False exclusion rate",
        "pairwise_accuracy": "Held-out pairwise accuracy",
        "top_k_overlap": "Top-K agreement",
        "decision_brier": "Decision Brier score",
        "decision_ece": "Decision calibration ECE",
        "selection_reduction_ratio": "Selection reduction",
    }
    scorer = report.get("scorer")
    if isinstance(scorer, dict):
        lines.append(f"Scorer: {scorer['name']} @ {scorer['version']}")
    for key, label in labels.items():
        if key not in metrics:
            continue
        value = float(metrics[key])
        status = checks.get(key)
        prefix = f"[{'PASS' if status else 'FAIL'}] " if status is not None else ""
        lines.append(f"{prefix}{label}: {value:.1%}")
    lines.extend(
        [
            f"Пары: {counts['true_positive_pairs']} верно / "
            f"{counts['predicted_duplicate_pairs']} найдено / "
            f"{counts['truth_duplicate_pairs']} размечено",
            f"Лидеры: {counts['correct_leaders']} / {counts['truth_groups']}",
            f"Ложные исключения: {counts['false_exclusions']} / "
            f"{counts['protected_from_exclusion']}",
            *(
                [
                    f"Held-out пары: {counts['correct_preference_pairs']} верно + "
                    f"{counts['tied_preference_pairs']} ничьих / {counts['held_out_pairs']}",
                    f"Top-K: {counts['top_k_matches']} / {counts['expected_top_k']}",
                ]
                if "held_out_pairs" in counts
                else []
            ),
            f"Итог: {'PASS' if report['passed'] else 'FAIL'}",
        ]
    )
    return "\n".join(lines)


def _validate_manifest(
    manifest: dict[str, object], assets: list[dict[str, object]], project_id: str
) -> tuple[
    dict[str, dict[str, object]],
    dict[str, float],
    dict[str, object] | None,
]:
    schema_version = manifest.get("schema_version")
    if schema_version not in {1, 2}:
        raise AcceptanceManifestError("Поддерживаются schema_version=1 и schema_version=2")
    if manifest.get("project_id") != project_id:
        raise AcceptanceManifestError("project_id manifest не совпадает с проектом")
    raw_assets = manifest.get("assets")
    if not isinstance(raw_assets, list) or not raw_assets:
        raise AcceptanceManifestError("Manifest должен содержать непустой массив assets")
    project_ids = {str(asset["asset_uuid"]) for asset in assets}
    labels: dict[str, dict[str, object]] = {}
    for index, raw_label in enumerate(raw_assets):
        if not isinstance(raw_label, dict):
            raise AcceptanceManifestError(f"assets[{index}] должен быть объектом")
        asset_uuid = raw_label.get("asset_uuid")
        if not isinstance(asset_uuid, str) or not asset_uuid:
            raise AcceptanceManifestError(f"assets[{index}].asset_uuid обязателен")
        if asset_uuid in labels:
            raise AcceptanceManifestError(f"Повторный asset_uuid: {asset_uuid}")
        if asset_uuid not in project_ids:
            raise AcceptanceManifestError(f"Фото отсутствует в проекте: {asset_uuid}")
        disposition = raw_label.get("expected_disposition")
        if disposition not in ALLOWED_DISPOSITIONS:
            raise AcceptanceManifestError(
                f"{asset_uuid}: expected_disposition должен быть keep, review или reject"
            )
        group = raw_label.get("duplicate_group")
        if group is not None and (not isinstance(group, str) or not group.strip()):
            raise AcceptanceManifestError(
                f"{asset_uuid}: duplicate_group должен быть строкой или null"
            )
        expected_leader = raw_label.get("expected_leader", False)
        if not isinstance(expected_leader, bool):
            raise AcceptanceManifestError(f"{asset_uuid}: expected_leader должен быть boolean")
        if expected_leader and group is None:
            raise AcceptanceManifestError(f"{asset_uuid}: лидер должен входить в duplicate_group")
        labels[asset_uuid] = {
            "expected_disposition": disposition,
            "duplicate_group": group,
            "expected_leader": expected_leader,
        }

    truth_groups: dict[str, list[dict[str, object]]] = defaultdict(list)
    for label in labels.values():
        if label["duplicate_group"] is not None:
            truth_groups[str(label["duplicate_group"])].append(label)
    for group_id, members in truth_groups.items():
        if len(members) < 2:
            raise AcceptanceManifestError(f"duplicate_group {group_id} содержит меньше двух фото")
        if sum(bool(member["expected_leader"]) for member in members) != 1:
            raise AcceptanceManifestError(
                f"duplicate_group {group_id} должен иметь ровно одного expected_leader"
            )

    raw_thresholds = manifest.get("thresholds", DEFAULT_THRESHOLDS)
    if not isinstance(raw_thresholds, dict):
        raise AcceptanceManifestError("thresholds должен быть объектом")
    thresholds: dict[str, float] = {}
    required_thresholds = list(DEFAULT_THRESHOLDS)
    if schema_version == 1:
        required_thresholds = required_thresholds[:4]
    for key in required_thresholds:
        default = DEFAULT_THRESHOLDS[key]
        value = raw_thresholds.get(key, default)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1:
            raise AcceptanceManifestError(f"thresholds.{key} должен быть числом от 0 до 1")
        thresholds[key] = float(value)
    preferences = _validate_preferences(manifest, project_ids) if schema_version == 2 else None
    return labels, thresholds, preferences


def _validate_preferences(manifest: dict[str, object], project_ids: set[str]) -> dict[str, object]:
    raw_pairs = manifest.get("preference_pairs")
    if not isinstance(raw_pairs, list):
        raise AcceptanceManifestError("preference_pairs должен быть массивом")
    pairs: list[dict[str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for index, raw_pair in enumerate(raw_pairs):
        if not isinstance(raw_pair, dict):
            raise AcceptanceManifestError(f"preference_pairs[{index}] должен быть объектом")
        left = raw_pair.get("left_uuid")
        right = raw_pair.get("right_uuid")
        preferred = raw_pair.get("preferred_uuid")
        split = raw_pair.get("split", "held_out")
        if not all(isinstance(value, str) and value for value in (left, right, preferred)):
            raise AcceptanceManifestError(
                f"preference_pairs[{index}] требует left_uuid, right_uuid и preferred_uuid"
            )
        if left == right:
            raise AcceptanceManifestError(f"preference_pairs[{index}] сравнивает фото с собой")
        if left not in project_ids or right not in project_ids:
            raise AcceptanceManifestError(f"preference_pairs[{index}] содержит неизвестное фото")
        if preferred not in {left, right}:
            raise AcceptanceManifestError(
                f"preference_pairs[{index}].preferred_uuid должен быть одним из пары"
            )
        if split not in {"calibration", "held_out"}:
            raise AcceptanceManifestError(
                f"preference_pairs[{index}].split должен быть calibration или held_out"
            )
        key = tuple(sorted((left, right)))
        if key in seen_pairs:
            raise AcceptanceManifestError(f"Повторная preference pair: {left}, {right}")
        seen_pairs.add(key)
        pairs.append(
            {
                "left_uuid": left,
                "right_uuid": right,
                "preferred_uuid": preferred,
                "split": split,
            }
        )

    raw_top_k = manifest.get("expected_top_k")
    if not isinstance(raw_top_k, list):
        raise AcceptanceManifestError("expected_top_k должен быть массивом")
    top_k: list[str] = []
    for index, value in enumerate(raw_top_k):
        if not isinstance(value, str) or value not in project_ids:
            raise AcceptanceManifestError(f"expected_top_k[{index}] содержит неизвестное фото")
        if value in top_k:
            raise AcceptanceManifestError(f"Повтор в expected_top_k: {value}")
        top_k.append(value)
    return {"pairs": pairs, "expected_top_k": top_k}


def _validated_score_map(
    snapshot: dict[str, object] | None,
    asset_by_uuid: dict[str, dict[str, object]],
    labelled_ids: set[str],
    project_id: str,
) -> tuple[dict[str, float], dict[str, str]]:
    if snapshot is None:
        raw_scores = {
            asset_uuid: asset.get("selection_score") for asset_uuid, asset in asset_by_uuid.items()
        }
        scorer = {"name": "selection_score", "version": "legacy-db-v1"}
    else:
        if snapshot.get("schema_version") != 1:
            raise AcceptanceManifestError("Score snapshot требует schema_version=1")
        if snapshot.get("project_id") != project_id:
            raise AcceptanceManifestError("project_id score snapshot не совпадает с проектом")
        engine = snapshot.get("engine")
        raw_scores = snapshot.get("scores")
        if not isinstance(engine, dict) or not isinstance(raw_scores, dict):
            raise AcceptanceManifestError("Score snapshot требует engine и scores")
        name, version = engine.get("name"), engine.get("version")
        if not isinstance(name, str) or not name or not isinstance(version, str) or not version:
            raise AcceptanceManifestError("Score snapshot engine требует name и version")
        scorer = {"name": name, "version": version}
    unknown = set(raw_scores) - set(asset_by_uuid)
    if unknown:
        raise AcceptanceManifestError(f"Score snapshot содержит неизвестное фото: {min(unknown)}")
    missing_labels = labelled_ids - set(raw_scores)
    if missing_labels:
        raise AcceptanceManifestError(f"Нет числового score для {min(missing_labels)}")
    scores: dict[str, float] = {}
    for asset_uuid, value in raw_scores.items():
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
        ):
            raise AcceptanceManifestError(f"Нет числового score для {asset_uuid}")
        scores[asset_uuid] = float(value)
    return scores, scorer


def _preference_metrics(
    preferences: dict[str, object], scores: dict[str, float]
) -> tuple[dict[str, float], dict[str, int], dict[str, object]]:
    raw_pairs = preferences["pairs"]
    expected_top_k = preferences["expected_top_k"]
    assert isinstance(raw_pairs, list)
    assert isinstance(expected_top_k, list)
    held_out = [pair for pair in raw_pairs if pair["split"] == "held_out"]
    correct = 0
    tied = 0
    pair_details = []
    for pair in held_out:
        left, right = pair["left_uuid"], pair["right_uuid"]
        left_score, right_score = scores[left], scores[right]
        predicted = (
            left if left_score > right_score else right if right_score > left_score else None
        )
        correct += int(predicted == pair["preferred_uuid"])
        tied += int(predicted is None)
        pair_details.append(
            {
                "left_uuid": left,
                "right_uuid": right,
                "preferred_uuid": pair["preferred_uuid"],
                "predicted_uuid": predicted,
                "correct": predicted == pair["preferred_uuid"],
            }
        )
    pairwise_accuracy = (correct + tied * 0.5) / len(held_out) if held_out else 0.0
    k = len(expected_top_k)
    predicted_top_k = sorted(scores, key=lambda uuid: (-scores[uuid], uuid))[:k]
    matches = len(set(expected_top_k) & set(predicted_top_k))
    return (
        {
            "pairwise_accuracy": pairwise_accuracy,
            "top_k_overlap": _safe_ratio(matches, k, empty_value=False),
        },
        {
            "calibration_pairs": len(raw_pairs) - len(held_out),
            "held_out_pairs": len(held_out),
            "correct_preference_pairs": correct,
            "tied_preference_pairs": tied,
            "expected_top_k": k,
            "top_k_matches": matches,
        },
        {
            "preference_pairs": pair_details,
            "expected_top_k": expected_top_k,
            "predicted_top_k": predicted_top_k,
        },
    )


def _truth_pairs(labels: dict[str, dict[str, object]]) -> set[tuple[str, str]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for asset_uuid, label in labels.items():
        if label["duplicate_group"] is not None:
            groups[str(label["duplicate_group"])].append(asset_uuid)
    return {tuple(sorted(pair)) for members in groups.values() for pair in combinations(members, 2)}


def _predicted_pairs(
    groups: list[dict[str, object]], labelled_ids: set[str]
) -> set[tuple[str, str]]:
    result = set()
    for group in groups:
        members = {
            str(member["asset_uuid"])
            for member in _group_members(group)
            if str(member["asset_uuid"]) in labelled_ids
        }
        result.update(tuple(sorted(pair)) for pair in combinations(sorted(members), 2))
    return result


def _leader_accuracy(
    labels: dict[str, dict[str, object]], groups: list[dict[str, object]]
) -> tuple[int, int, list[dict[str, object]]]:
    truth_groups: dict[str, set[str]] = defaultdict(set)
    expected_leaders: dict[str, str] = {}
    for asset_uuid, label in labels.items():
        group_id = label["duplicate_group"]
        if group_id is None:
            continue
        truth_groups[str(group_id)].add(asset_uuid)
        if label["expected_leader"]:
            expected_leaders[str(group_id)] = asset_uuid

    details = []
    correct = 0
    for truth_group_id, truth_members in sorted(truth_groups.items()):
        candidates = []
        for predicted in groups:
            predicted_members = {str(member["asset_uuid"]) for member in _group_members(predicted)}
            overlap = len(truth_members & predicted_members)
            if overlap >= 2:
                candidates.append((overlap, str(predicted.get("group_id", "")), predicted))
        max_overlap = max((candidate[0] for candidate in candidates), default=0)
        best_matches = [candidate for candidate in candidates if candidate[0] == max_overlap]
        matched = best_matches[0] if len(best_matches) == 1 else None
        raw_predicted_leader = matched[2].get("leader_uuid") if matched else None
        predicted_leader = str(raw_predicted_leader) if raw_predicted_leader is not None else None
        expected_leader = expected_leaders[truth_group_id]
        is_correct = predicted_leader == expected_leader
        correct += int(is_correct)
        details.append(
            {
                "truth_group": truth_group_id,
                "expected_leader": expected_leader,
                "predicted_group": matched[1] if matched else None,
                "predicted_leader": predicted_leader,
                "correct": is_correct,
            }
        )
    return correct, len(truth_groups), details


def _group_members(group: dict[str, object]) -> list[dict[str, Any]]:
    members = group.get("members", [])
    return members if isinstance(members, list) else []


def _automatic_disposition(asset: dict[str, object]) -> object:
    automatic = asset.get("auto_disposition")
    return automatic if automatic in ALLOWED_DISPOSITIONS else asset.get("final_disposition")


def _decision_calibration(
    labels: dict[str, dict[str, object]],
    assets: dict[str, dict[str, object]],
) -> tuple[dict[str, float], dict[str, object]]:
    observations: list[tuple[float, int]] = []
    for asset_uuid, label in labels.items():
        confidence = assets[asset_uuid].get("confidence")
        if (
            not isinstance(confidence, (int, float))
            or isinstance(confidence, bool)
            or not math.isfinite(float(confidence))
            or not 0 <= float(confidence) <= 1
        ):
            continue
        correct = int(_automatic_disposition(assets[asset_uuid]) == label["expected_disposition"])
        observations.append((float(confidence), correct))
    if not observations:
        return {}, {}
    brier = sum((confidence - correct) ** 2 for confidence, correct in observations) / len(
        observations
    )
    bins = []
    weighted_gap = 0.0
    for index in range(10):
        lower, upper = index / 10.0, (index + 1) / 10.0
        bucket = [
            item
            for item in observations
            if lower <= item[0] < upper or (index == 9 and item[0] == 1.0)
        ]
        if not bucket:
            continue
        mean_confidence = sum(item[0] for item in bucket) / len(bucket)
        accuracy = sum(item[1] for item in bucket) / len(bucket)
        weighted_gap += len(bucket) / len(observations) * abs(mean_confidence - accuracy)
        bins.append(
            {
                "lower": lower,
                "upper": upper,
                "count": len(bucket),
                "mean_confidence": mean_confidence,
                "accuracy": accuracy,
            }
        )
    return (
        {"decision_brier": brier, "decision_ece": weighted_gap},
        {"decision_calibration": {"count": len(observations), "bins": bins}},
    )


def _safe_ratio(numerator: int, denominator: int, *, empty_value: bool) -> float:
    return numerator / denominator if denominator else float(empty_value)
