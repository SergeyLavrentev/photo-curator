from __future__ import annotations

import json
from collections import defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any

ALLOWED_DISPOSITIONS = {"keep", "review", "reject"}
DEFAULT_THRESHOLDS = {
    "duplicate_precision": 0.90,
    "duplicate_recall": 0.80,
    "leader_accuracy": 0.80,
    "false_exclusion_rate": 0.05,
}


class AcceptanceManifestError(ValueError):
    """The human-labelled manifest cannot produce a trustworthy score."""


def build_manifest_template(project_id: str, assets: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "project_id": project_id,
        "thresholds": DEFAULT_THRESHOLDS.copy(),
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


def load_manifest(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AcceptanceManifestError(f"Не удалось прочитать manifest: {error}") from error
    if not isinstance(payload, dict):
        raise AcceptanceManifestError("Корень manifest должен быть JSON-объектом")
    return payload


def evaluate_acceptance(
    manifest: dict[str, object],
    assets: list[dict[str, object]],
    predicted_groups: list[dict[str, object]],
    *,
    project_id: str,
) -> dict[str, object]:
    labels, thresholds = _validate_manifest(manifest, assets, project_id)
    labelled_ids = set(labels)
    asset_by_uuid = {str(asset["asset_uuid"]): asset for asset in assets}
    incomplete_results = sorted(
        asset_uuid
        for asset_uuid in labelled_ids
        if asset_by_uuid[asset_uuid].get("final_disposition") not in ALLOWED_DISPOSITIONS
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
        if asset_by_uuid[asset_uuid].get("final_disposition") == "reject"
    ]
    false_exclusion_rate = _safe_ratio(len(false_exclusions), len(protected), empty_value=True)

    metrics = {
        "duplicate_precision": precision,
        "duplicate_recall": recall,
        "leader_accuracy": leader_accuracy,
        "false_exclusion_rate": false_exclusion_rate,
    }
    checks = {
        "duplicate_precision": precision >= thresholds["duplicate_precision"],
        "duplicate_recall": recall >= thresholds["duplicate_recall"],
        "leader_accuracy": leader_accuracy >= thresholds["leader_accuracy"],
        "false_exclusion_rate": false_exclusion_rate <= thresholds["false_exclusion_rate"],
    }
    release_eligible = 50 <= len(labels) <= 100 and bool(truth_pairs) and leader_total > 0
    return {
        "schema_version": 1,
        "project_id": project_id,
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
        },
        "details": {
            "false_exclusion_uuids": sorted(false_exclusions),
            "leader_groups": leader_details,
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
    }
    for key, label in labels.items():
        value = float(metrics[key])
        lines.append(f"[{'PASS' if checks[key] else 'FAIL'}] {label}: {value:.1%}")
    lines.extend(
        [
            f"Пары: {counts['true_positive_pairs']} верно / "
            f"{counts['predicted_duplicate_pairs']} найдено / "
            f"{counts['truth_duplicate_pairs']} размечено",
            f"Лидеры: {counts['correct_leaders']} / {counts['truth_groups']}",
            f"Ложные исключения: {counts['false_exclusions']} / "
            f"{counts['protected_from_exclusion']}",
            f"Итог: {'PASS' if report['passed'] else 'FAIL'}",
        ]
    )
    return "\n".join(lines)


def _validate_manifest(
    manifest: dict[str, object], assets: list[dict[str, object]], project_id: str
) -> tuple[dict[str, dict[str, object]], dict[str, float]]:
    if manifest.get("schema_version") != 1:
        raise AcceptanceManifestError("Поддерживается только schema_version=1")
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
    for key, default in DEFAULT_THRESHOLDS.items():
        value = raw_thresholds.get(key, default)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1:
            raise AcceptanceManifestError(f"thresholds.{key} должен быть числом от 0 до 1")
        thresholds[key] = float(value)
    return labels, thresholds


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


def _safe_ratio(numerator: int, denominator: int, *, empty_value: bool) -> float:
    return numerator / denominator if denominator else float(empty_value)
