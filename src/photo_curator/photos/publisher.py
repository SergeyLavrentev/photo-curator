from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from photo_curator.db import repository
from photo_curator.db.connection import database_connection
from photo_curator.paths import ApplicationPaths
from photo_curator.photos.provider import PhotosProvider
from photo_curator.utils.subprocesses import CommandResult, find_executable, run_command


@dataclass(frozen=True, slots=True)
class PublishValidation:
    asset_uuids: list[str]
    removed_uuids: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class PhotosPublisher:
    def __init__(
        self,
        *,
        database_path: Path,
        paths: ApplicationPaths,
        provider: PhotosProvider,
        runner: Callable[[list[str]], CommandResult] = run_command,
        executable: str | None = None,
        enabled: bool = True,
    ) -> None:
        self.database_path = database_path
        self.paths = paths
        self.provider = provider
        self.runner = runner
        self.executable = executable or find_executable("osxphotos")
        self.enabled = enabled
        self._capability: bool | None = None

    @property
    def capability_available(self) -> bool:
        if not self.enabled or not self.executable or not Path(self.executable).is_file():
            return False
        if self._capability is None:
            try:
                result = self.runner([self.executable, "batch-edit", "--help"])
                help_text = f"{result.stdout}\n{result.stderr}"
                self._capability = result.returncode == 0 and all(
                    flag in help_text
                    for flag in ("--uuid-from-file", "--add-to-album", "--dry-run")
                )
            except Exception:
                self._capability = False
        return self._capability

    def validate(self, project_id: str) -> PublishValidation:
        with database_connection(self.database_path) as connection:
            project = repository.get_project(connection, project_id)
            assets = repository.list_assets(connection, project_id)
            groups = repository.list_duplicate_groups(connection, project_id)
        rejects = [asset for asset in assets if asset.get("final_disposition") == "reject"]
        refreshed = {
            asset.uuid: asset
            for asset in self.provider.refresh_assets(
                [str(asset["asset_uuid"]) for asset in rejects]
            )
        }
        accepted, removed, blockers, warnings = [], [], [], []
        current_library = self.provider.get_current_library()
        if current_library.fingerprint != project["library_fingerprint"]:
            warnings.append("Photos Library fingerprint изменился после инвентаризации")
        for row in rejects:
            uuid = str(row["asset_uuid"])
            current = refreshed.get(uuid)
            if not current or not self.provider.asset_still_in_album(
                str(project["album_id"]), uuid
            ):
                removed.append(uuid)
                continue
            flags = set(row.get("flags") or [])
            manual = bool(row.get("manual_override"))
            if flags & {"resolution_inversion", "leader_lower_resolution"} and not manual:
                blockers.append(f"{uuid}: resolution warning требует ручного решения")
                continue
            if (current.favorite or current.has_adjustments) and not manual:
                blockers.append(f"{uuid}: защищённый Favorite/edited asset")
                continue
            if row.get("cache_state") != "ready":
                blockers.append(f"{uuid}: preview отсутствует")
                continue
            if not row.get("reviewed") and manual:
                warnings.append(f"{uuid}: manual reject не отмечен reviewed")
            accepted.append(uuid)
        by_uuid = {str(asset["asset_uuid"]): asset for asset in assets}
        for group in groups:
            members = [by_uuid.get(str(member["asset_uuid"])) for member in group["members"]]
            members = [member for member in members if member]
            max_pixels = max(
                (
                    int(member.get("width") or 0) * int(member.get("height") or 0)
                    for member in members
                ),
                default=0,
            )
            high_rejects = [
                member
                for member in members
                if member.get("final_disposition") == "reject"
                and int(member.get("width") or 0) * int(member.get("height") or 0) == max_pixels
            ]
            lower_kept = any(
                member.get("final_disposition") == "keep"
                and int(member.get("width") or 0) * int(member.get("height") or 0) < max_pixels
                for member in members
            )
            for high in high_rejects:
                if lower_kept:
                    message = f"{high['asset_uuid']}: resolution inversion в {group['group_id']}"
                    if high.get("manual_override"):
                        warnings.append(message + " подтверждён вручную")
                    else:
                        blockers.append(message)
        if not accepted:
            blockers.append("Нет подтверждённых reject-assets")
        if not self.capability_available:
            blockers.append("osxphotos CLI недоступен")
        return PublishValidation(sorted(accepted), sorted(removed), blockers, warnings)

    def dry_run(self, project_id: str) -> dict[str, object]:
        validation = self.validate(project_id)
        if validation.blockers:
            raise ValueError("; ".join(validation.blockers))
        with database_connection(self.database_path) as connection:
            project = repository.get_project(connection, project_id)
        album_name = self._next_album_name(project_id, str(project["album_name"]))
        publish_dir = self.paths.cache_dir / project_id / "publish"
        publish_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        uuid_file = publish_dir / f"reject-uuids-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.txt"
        uuid_file.write_text("\n".join(validation.asset_uuids) + "\n", encoding="utf-8")
        with database_connection(self.database_path) as connection:
            publish_id = repository.create_publish(
                connection,
                project_id=project_id,
                album_name=album_name,
                asset_count=len(validation.asset_uuids),
                uuid_file=str(uuid_file),
            )
        result = self.runner(self._command(uuid_file, album_name, dry_run=True))
        with database_connection(self.database_path) as connection:
            repository.record_dry_run(
                connection,
                publish_id,
                stdout=result.stdout,
                stderr=result.stderr,
                return_code=result.returncode,
            )
            publish = repository.get_publish(connection, publish_id)
        return publish

    def _next_album_name(self, project_id: str, source_album: str) -> str:
        base = unique_album_name(source_album)
        with database_connection(self.database_path) as connection:
            existing = {
                str(row[0])
                for row in connection.execute(
                    "SELECT album_name FROM publishes WHERE project_id=?", (project_id,)
                ).fetchall()
            }
        if base not in existing:
            return base
        counter = 2
        while True:
            suffix = f" — {counter}"
            candidate = f"{base[: 120 - len(suffix)]}{suffix}"
            if candidate not in existing:
                return candidate
            counter += 1

    def apply(self, publish_id: str) -> dict[str, object]:
        with database_connection(self.database_path) as connection:
            publish = repository.get_publish(connection, publish_id)
        if publish.get("status") != "dry_run_ok" or publish.get("dry_run_return_code") != 0:
            raise ValueError("Успешный dry-run обязателен перед apply")
        uuid_file = Path(str(publish["uuid_file"]))
        if not uuid_file.is_file():
            raise ValueError("UUID file отсутствует")
        validation = self.validate(str(publish["project_id"]))
        prepared = sorted(
            line for line in uuid_file.read_text(encoding="utf-8").splitlines() if line
        )
        if validation.blockers or prepared != validation.asset_uuids:
            raise ValueError("Source или решения изменились после dry-run; выполните новый dry-run")
        result = self.runner(self._command(uuid_file, str(publish["album_name"]), dry_run=False))
        with database_connection(self.database_path) as connection:
            repository.record_apply(
                connection,
                publish_id,
                stdout=result.stdout,
                stderr=result.stderr,
                return_code=result.returncode,
            )
            return repository.get_publish(connection, publish_id)

    def _command(self, uuid_file: Path, album_name: str, *, dry_run: bool) -> list[str]:
        if not self.capability_available:
            raise ValueError("osxphotos CLI недоступен")
        args = [
            self.executable,
            "batch-edit",
            "--uuid-from-file",
            str(uuid_file),
            "--add-to-album",
            album_name,
        ]
        if dry_run:
            args.append("--dry-run")
        args.append("--verbose")
        return args


def unique_album_name(source_album: str, now: datetime | None = None) -> str:
    now = now or datetime.now()
    source = re.sub(r"[/:\n\r\t]+", " — ", source_album).strip(" —") or "Album"
    timestamp = now.strftime("%Y%m%d-%H%M%S")
    suffix = f" — Reject — {timestamp}"
    prefix = "PhotoCurator — "
    max_source = max(1, 120 - len(prefix) - len(suffix))
    return f"{prefix}{source[:max_source]}{suffix}"
