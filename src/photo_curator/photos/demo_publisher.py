from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from photo_curator.db import repository
from photo_curator.db.connection import database_connection
from photo_curator.paths import ApplicationPaths
from photo_curator.photos.publisher import PublishValidation, unique_album_name


class DemoPhotosPublisher:
    """Exercise the complete publish contract without touching Apple Photos."""

    def __init__(self, *, database_path: Path, paths: ApplicationPaths) -> None:
        self.database_path = database_path
        self.paths = paths

    def validate(self, project_id: str, kind: str = "best") -> PublishValidation:
        if kind not in {"best", "reject"}:
            raise ValueError("Неизвестный тип альбома")
        with database_connection(self.database_path) as connection:
            repository.get_project(connection, project_id)
            assets = repository.list_assets(connection, project_id)
        candidates = sorted(
            str(asset["asset_uuid"])
            for asset in assets
            if (
                asset.get("final_selection") == "pick"
                if kind == "best"
                else asset.get("final_disposition") == "reject"
            )
        )
        blockers = [] if candidates else ["Нет отобранных фотографий"]
        return PublishValidation(
            candidates,
            blockers=blockers,
            warnings=["Demo: Apple Photos не изменяется"],
        )

    def dry_run(self, project_id: str, kind: str = "best") -> dict[str, object]:
        validation = self.validate(project_id, kind)
        if validation.blockers:
            raise ValueError("; ".join(validation.blockers))
        with database_connection(self.database_path) as connection:
            project = repository.get_project(connection, project_id)
        album_name = unique_album_name(str(project["album_name"]), kind=kind)
        publish_dir = self.paths.cache_dir / project_id / "publish"
        publish_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        uuid_file = publish_dir / (
            f"demo-{kind}-uuids-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.txt"
        )
        uuid_file.write_text("\n".join(validation.asset_uuids) + "\n", encoding="utf-8")
        dry_run_result = {
            "mode": "dry-run",
            "demo": True,
            "album_name": album_name,
            "asset_count": len(validation.asset_uuids),
            "warnings": validation.warnings,
        }
        with database_connection(self.database_path) as connection:
            publish_id = repository.create_publish(
                connection,
                project_id=project_id,
                album_name=album_name,
                asset_count=len(validation.asset_uuids),
                uuid_file=str(uuid_file),
                kind=kind,
            )
            repository.record_dry_run(
                connection,
                publish_id,
                stdout=json.dumps(dry_run_result, ensure_ascii=False),
                stderr="",
                return_code=0,
            )
            return repository.get_publish(connection, publish_id)

    def apply(
        self,
        publish_id: str,
        *,
        progress: Callable[[str, int, int], None] | None = None,
    ) -> dict[str, object]:
        with database_connection(self.database_path) as connection:
            publish = repository.get_publish(connection, publish_id)
        if (
            publish.get("status") not in {"dry_run_ok", "apply_failed"}
            or publish.get("dry_run_return_code") != 0
        ):
            raise ValueError("Успешный dry-run обязателен перед apply")
        uuid_file = Path(str(publish["uuid_file"]))
        if not uuid_file.is_file():
            raise ValueError("UUID file отсутствует")
        prepared = sorted(line for line in uuid_file.read_text().splitlines() if line)
        validation = self.validate(str(publish["project_id"]), str(publish.get("kind") or "best"))
        if validation.blockers or prepared != validation.asset_uuids:
            raise ValueError("Source или решения изменились после dry-run; выполните новый dry-run")
        if progress:
            progress("prepare", len(prepared), len(prepared))
            progress("commit", len(prepared), len(prepared))
        destination_album_id = f"demo-{publish_id}"
        result = {
            "demo": True,
            "album_identifier": destination_album_id,
            "added": len(prepared),
            "imported": 0,
            "reused": len(prepared),
        }
        with database_connection(self.database_path) as connection:
            repository.record_apply(
                connection,
                publish_id,
                stdout=json.dumps(result, ensure_ascii=False),
                stderr="",
                return_code=0,
                destination_album_id=destination_album_id,
            )
            return repository.get_publish(connection, publish_id)
