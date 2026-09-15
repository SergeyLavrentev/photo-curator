"""Immutable, single-use review plans. Only the native host performs PhotoKit deletion."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from photo_curator.analysis.culling import culling_category, effective_culling_sql
from photo_curator.db import repository


def prepare_deletion(connection, project_id: str, asset_ids: list[str] | None) -> dict:
    project = repository.get_project(connection, project_id)
    if project["state"] != "ready":
        raise ValueError("Сначала завершите анализ")
    if json.loads(project["settings_json"]).get("source_provenance") != "photokit":
        raise ValueError(
            "Этот анализ содержит локальные или Shared-копии. "
            "Для удаления оригиналов выберите обычный альбом Apple Photos "
            "и выполните новый анализ."
        )
    scope = _deletion_scope(project)
    require_candidate = asset_ids is None
    if asset_ids is None:
        asset_ids = [
            r[0]
            for r in connection.execute(
                f"""SELECT a.asset_uuid FROM assets a JOIN decisions d USING(project_id,asset_uuid)
                WHERE a.project_id=? AND a.no_longer_exists=0
                AND ({effective_culling_sql()})='reject' ORDER BY a.asset_uuid""",
                (project_id,),
            )
        ]
    if not asset_ids or len(asset_ids) > 10000 or len(set(asset_ids)) != len(asset_ids):
        raise ValueError("Выберите от 1 до 10000 разных фотографий")
    if any(not isinstance(value, str) or not value for value in asset_ids):
        raise ValueError("Некорректный идентификатор фотографии")
    assets = repository.assets_by_uuid(connection, project_id, set(asset_ids))
    if set(assets) != set(asset_ids):
        raise ValueError("Состав фотографий изменился. Обновите анализ.")
    items = [
        _plan_item(assets[asset_id], require_candidate=require_candidate)
        for asset_id in sorted(asset_ids)
    ]
    plan = {
        "id": repository.new_id(),
        "project_id": project_id,
        "album_id": project["album_id"],
        "album_name": project["album_name"],
        "library_fingerprint": project["library_fingerprint"],
        "items": items,
        "require_candidate": require_candidate,
        "scope": scope,
    }
    now = repository.utc_now()
    connection.execute(
        """INSERT INTO photo_deletion_plans
        (id,project_id,payload_json,status,created_at,updated_at)
        VALUES(?,?,?,'prepared',?,?)""",
        (plan["id"], project_id, json.dumps(plan), now, now),
    )
    return plan


def _deletion_scope(project: dict) -> str:
    settings = json.loads(project["settings_json"])
    return "shared_album" if settings.get("source_album_shared") is True else "library"


def _plan_item(asset: dict, *, require_candidate: bool = True) -> dict:
    if asset.get("no_longer_exists"):
        raise ValueError("Фотография больше не доступна. Обновите анализ.")
    if require_candidate and culling_category(asset) != "reject":
        raise ValueError("Список «К удалению» изменился. Проверьте его ещё раз.")
    if (
        asset.get("cache_state") != "ready"
        or not asset.get("review_path")
        or not Path(str(asset["review_path"])).is_file()
    ):
        raise ValueError("Нет изображения для проверки. Сначала восстановите анализ.")
    if not asset.get("source_revision"):
        raise ValueError(
            "Старый анализ не содержит версию оригинала. Выполните новый анализ перед удалением."
        )
    return {
        "id": asset["asset_uuid"],
        "filename": asset.get("current_filename") or asset["asset_uuid"],
        "source_revision": asset["source_revision"],
        "favorite": bool(asset.get("favorite")),
        "has_adjustments": bool(asset.get("has_adjustments")),
        "mutation_generation": asset.get("manual_mutation_generation") or 0,
        "manual_disposition": asset.get("manual_disposition"),
        "manual_selection": asset.get("manual_selection"),
        "review_path": asset["review_path"],
    }


def get_plan(connection, plan_id: str) -> tuple[dict, dict]:
    row = connection.execute("SELECT * FROM photo_deletion_plans WHERE id=?", (plan_id,)).fetchone()
    if row is None:
        raise ValueError("План удаления не найден")
    return dict(row), json.loads(row["payload_json"])


def begin_deletion(connection, plan_id: str, *, confirmed: bool) -> dict:
    if confirmed is not True:
        raise ValueError("Удаление требует confirmed=true")
    row, plan = get_plan(connection, plan_id)
    if row["status"] != "prepared":
        raise ValueError("Этот запрос уже был начат. Автоматический повтор удаления запрещён.")
    if datetime.now(UTC) - datetime.fromisoformat(row["created_at"]) > timedelta(minutes=15):
        raise ValueError("План устарел. Проверьте состав удаления ещё раз.")
    project = repository.get_project(connection, row["project_id"])
    if (
        _deletion_scope(project) != plan.get("scope", "library")
        or project["state"] != "ready"
        or project["album_id"] != plan["album_id"]
        or project["library_fingerprint"] != plan["library_fingerprint"]
        or json.loads(project["settings_json"]).get("source_provenance") != "photokit"
    ):
        raise ValueError("Анализ изменился. Подготовьте новый план.")
    assets = repository.assets_by_uuid(
        connection, row["project_id"], {item["id"] for item in plan["items"]}
    )
    if (
        len(assets) != len(plan["items"])
        or [
            _plan_item(assets[item["id"]], require_candidate=plan.get("require_candidate", True))
            for item in plan["items"]
        ]
        != plan["items"]
    ):
        raise ValueError("Фотографии или решения изменились. Подготовьте новый план.")
    connection.execute(
        "UPDATE photo_deletion_plans SET status='started', updated_at=? WHERE id=?",
        (repository.utc_now(), plan_id),
    )
    return plan


def finish_deletion(connection, plan_id: str, deleted_ids: list[str], error: str | None) -> dict:
    row, plan = get_plan(connection, plan_id)
    if row["status"] != "started":
        raise ValueError("Нет начатого запроса удаления")
    expected = {item["id"] for item in plan["items"]}
    deleted = set(deleted_ids)
    if len(deleted) != len(deleted_ids) or not deleted <= expected:
        raise ValueError("Результат PhotoKit не соответствует подтверждённому списку")
    status = "completed" if deleted == expected else "partial" if deleted else "cancelled_or_failed"
    for asset_id in deleted:
        # Keep review files, decisions and human evidence; only hide actually absent assets.
        if plan.get("scope", "library") == "shared_album":
            # Album removal is not proof of disappearance from any personal library.
            connection.execute(
                """UPDATE assets SET no_longer_exists=1 WHERE asset_uuid=? AND project_id IN
                (SELECT id FROM projects WHERE album_id=? AND
                 json_extract(settings_json,'$.source_provenance')='photokit' AND
                 json_extract(settings_json,'$.source_album_shared')=1)""",
                (asset_id, plan["album_id"]),
            )
        else:
            connection.execute(
                """UPDATE assets SET no_longer_exists=1 WHERE asset_uuid=? AND project_id IN
                (SELECT id FROM projects WHERE
                 json_extract(settings_json,'$.source_provenance')='photokit' AND
                 COALESCE(json_extract(settings_json,'$.source_album_shared'),0)=0)""",
                (asset_id,),
            )
    connection.execute(
        "UPDATE photo_deletion_plans SET status=?,updated_at=?,error_text=? WHERE id=?",
        (status, repository.utc_now(), error, plan_id),
    )
    return {
        "status": status,
        "deleted_count": len(deleted),
        "summary": repository.project_summary(connection, row["project_id"]),
    }
