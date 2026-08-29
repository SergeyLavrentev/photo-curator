from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from photo_curator.db import repository
from photo_curator.db.connection import database_connection
from photo_curator.paths import ApplicationPaths
from photo_curator.photos.native_publisher import NativePhotosImporter
from photo_curator.photos.provider import PhotosProvider
from photo_curator.utils.safe_paths import ensure_within
from photo_curator.utils.subprocesses import CommandResult, find_executable, run_command

LOGGER = logging.getLogger(__name__)


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
        local_importer: NativePhotosImporter | None = None,
        legacy_cli_enabled: bool = True,
    ) -> None:
        self.database_path = database_path
        self.paths = paths
        self.provider = provider
        self.runner = runner
        self.executable = executable or find_executable("osxphotos") if legacy_cli_enabled else None
        self.enabled = enabled
        self.local_importer = local_importer or NativePhotosImporter(paths, enabled=enabled)
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

    def validate(self, project_id: str, kind: str = "reject") -> PublishValidation:
        if kind not in {"best", "reject"}:
            raise ValueError("Неизвестный тип альбома")
        with database_connection(self.database_path) as connection:
            project = repository.get_project(connection, project_id)
            assets = repository.list_assets(connection, project_id)
            groups = repository.list_duplicate_groups(connection, project_id)
        if self._is_local_project(project):
            return self._validate_local(assets, kind)
        candidates = [
            asset
            for asset in assets
            if (
                asset.get("final_selection") == "pick"
                if kind == "best"
                else asset.get("final_disposition") == "reject"
            )
        ]
        try:
            refreshed = {
                asset.uuid: asset
                for asset in self.provider.refresh_assets(
                    [str(asset["asset_uuid"]) for asset in candidates]
                )
            }
            current_library = self.provider.get_current_library()
        except Exception:
            return PublishValidation(
                [], blockers=["Photos Library недоступна; повторите Doctor/read gate"]
            )
        accepted, removed, blockers, warnings = [], [], [], []
        if current_library.fingerprint != project["library_fingerprint"]:
            warnings.append("Photos Library fingerprint изменился после инвентаризации")
        for row in candidates:
            uuid = str(row["asset_uuid"])
            current = refreshed.get(uuid)
            try:
                still_in_album = self.provider.asset_still_in_album(str(project["album_id"]), uuid)
            except Exception:
                blockers.append("Photos Library стала недоступна во время revalidation")
                break
            if not current or not still_in_album:
                removed.append(uuid)
                continue
            if kind == "best":
                if row.get("cache_state") != "ready":
                    blockers.append(f"{uuid}: preview отсутствует")
                    continue
                accepted.append(uuid)
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
        for group in groups if kind == "reject" else []:
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
            full_resolution_kept = any(
                member.get("final_disposition") == "keep"
                and int(member.get("width") or 0) * int(member.get("height") or 0) == max_pixels
                for member in members
            )
            for high in high_rejects:
                # Rejecting one render-identical copy is safe when another
                # full-resolution copy from the group is still kept.
                if "exact_duplicate" in set(high.get("flags") or []) and full_resolution_kept:
                    continue
                if lower_kept:
                    message = f"{high['asset_uuid']}: resolution inversion в {group['group_id']}"
                    if high.get("manual_override"):
                        warnings.append(message + " подтверждён вручную")
                    else:
                        blockers.append(message)
        if not accepted:
            blockers.append(
                "Нет отобранных фотографий"
                if kind == "best"
                else "Нет подтверждённых reject-assets"
            )
        if self._is_photokit_project(project):
            if not self.local_importer.capability_available:
                blockers.append("Нативная публикация через PhotoKit недоступна")
        elif not self.capability_available:
            blockers.append("osxphotos CLI недоступен")
        return PublishValidation(sorted(accepted), sorted(removed), blockers, warnings)

    def dry_run(self, project_id: str, kind: str = "reject") -> dict[str, object]:
        validation = self.validate(project_id, kind)
        if validation.blockers:
            raise ValueError("; ".join(validation.blockers))
        with database_connection(self.database_path) as connection:
            project = repository.get_project(connection, project_id)
        album_name = self._next_album_name(project_id, str(project["album_name"]), kind)
        publish_dir = self.paths.cache_dir / project_id / "publish"
        publish_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        uuid_file = publish_dir / f"{kind}-uuids-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.txt"
        uuid_file.write_text("\n".join(validation.asset_uuids) + "\n", encoding="utf-8")
        with database_connection(self.database_path) as connection:
            publish_id = repository.create_publish(
                connection,
                project_id=project_id,
                album_name=album_name,
                asset_count=len(validation.asset_uuids),
                uuid_file=str(uuid_file),
                kind=kind,
            )
        if self._is_local_project(project) or self._is_photokit_project(project):
            result = CommandResult(
                ["photokit-publish", "--dry-run"],
                0,
                json.dumps(
                    {
                        "mode": "dry-run",
                        "album_name": album_name,
                        "asset_count": len(validation.asset_uuids),
                    },
                    ensure_ascii=False,
                ),
                "",
            )
        else:
            result = self.runner(self._command(uuid_file, album_name, dry_run=True))
        LOGGER.info(
            "Publish dry-run project=%s publish=%s assets=%d return_code=%d",
            project_id,
            publish_id,
            len(validation.asset_uuids),
            result.returncode,
        )
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

    def _next_album_name(self, project_id: str, source_album: str, kind: str) -> str:
        base = unique_album_name(source_album, kind=kind)
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
        validation = self.validate(str(publish["project_id"]), str(publish.get("kind") or "reject"))
        prepared = sorted(
            line for line in uuid_file.read_text(encoding="utf-8").splitlines() if line
        )
        if validation.blockers or prepared != validation.asset_uuids:
            raise ValueError("Source или решения изменились после dry-run; выполните новый dry-run")
        with database_connection(self.database_path) as connection:
            project = repository.get_project(connection, str(publish["project_id"]))
            assets = repository.list_assets(connection, str(publish["project_id"]))
        destination_album_id = None
        if self._is_local_project(project):
            by_uuid = {str(asset["asset_uuid"]): asset for asset in assets}
            files = [Path(str(by_uuid[uuid]["source_path"])) for uuid in prepared]
            try:
                progress_args = {"progress": progress} if progress else {}
                native_result = self.local_importer.publish(
                    str(publish["album_name"]), files, **progress_args
                )
                destination_album_id = str(native_result["album_identifier"])
                result = CommandResult(
                    ["photokit-publish"],
                    0,
                    json.dumps(native_result, ensure_ascii=False),
                    "",
                )
            except Exception as error:
                result = CommandResult(["photokit-publish"], 1, "", str(error)[:500])
        elif self._is_photokit_project(project):
            try:
                progress_args = {"progress": progress} if progress else {}
                native_result = self.local_importer.add_assets(
                    str(publish["album_name"]), prepared, **progress_args
                )
                destination_album_id = str(native_result["album_identifier"])
                result = CommandResult(
                    ["photokit-publish"],
                    0,
                    json.dumps(native_result, ensure_ascii=False),
                    "",
                )
            except Exception as error:
                result = CommandResult(["photokit-publish"], 1, "", str(error)[:500])
        else:
            result = self.runner(
                self._command(uuid_file, str(publish["album_name"]), dry_run=False)
            )
        LOGGER.info(
            "Publish apply project=%s publish=%s assets=%d return_code=%d",
            publish["project_id"],
            publish_id,
            len(prepared),
            result.returncode,
        )
        with database_connection(self.database_path) as connection:
            repository.record_apply(
                connection,
                publish_id,
                stdout=result.stdout,
                stderr=result.stderr,
                return_code=result.returncode,
                destination_album_id=destination_album_id,
            )
            return repository.get_publish(connection, publish_id)

    @staticmethod
    def _is_local_project(project: dict[str, object]) -> bool:
        return str(project["album_id"]).startswith("local-")

    @staticmethod
    def _is_photokit_project(project: dict[str, object]) -> bool:
        return str(project.get("library_path") or "").startswith("photokit://")

    def _validate_local(self, assets: list[dict[str, object]], kind: str) -> PublishValidation:
        if kind != "best":
            return PublishValidation(
                [], blockers=["Для дискового проекта в Photos публикуется только финальный Best"]
            )
        candidates = [asset for asset in assets if asset.get("final_selection") == "pick"]
        accepted: list[str] = []
        blockers: list[str] = []
        root = self.paths.data_dir / "local_albums"
        for row in candidates:
            uuid = str(row["asset_uuid"])
            value = row.get("source_path")
            if row.get("cache_state") != "ready" or not value:
                blockers.append(f"{uuid}: локальный источник отсутствует")
                continue
            try:
                path = ensure_within(Path(str(value)), root)
                stat = path.stat()
            except (OSError, ValueError):
                blockers.append(f"{uuid}: локальный источник недоступен")
                continue
            expected_size = row.get("source_size")
            expected_mtime = row.get("source_mtime")
            if expected_size is not None and stat.st_size != int(expected_size):
                blockers.append(f"{uuid}: файл изменился после анализа")
                continue
            if expected_mtime is not None and abs(stat.st_mtime - float(expected_mtime)) > 0.01:
                blockers.append(f"{uuid}: файл изменился после анализа")
                continue
            accepted.append(uuid)
        if not accepted:
            blockers.append("Нет отобранных фотографий")
        if not self.local_importer.capability_available:
            blockers.append("Нативная публикация через PhotoKit недоступна")
        return PublishValidation(
            sorted(accepted),
            blockers=blockers,
            warnings=[
                "В Photos будут импортированы локальные копии Shared Album; "
                "их разрешение может быть ниже оригиналов"
            ],
        )

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


def unique_album_name(
    source_album: str, now: datetime | None = None, *, kind: str = "reject"
) -> str:
    now = now or datetime.now()
    source = re.sub(r"[/:\n\r\t]+", " — ", source_album).strip(" —") or "Album"
    timestamp = now.strftime("%Y%m%d-%H%M%S")
    label = "Best" if kind == "best" else "Reject"
    suffix = f" — {label} — {timestamp}"
    prefix = "PhotoCurator — "
    max_source = max(1, 120 - len(prefix) - len(suffix))
    return f"{prefix}{source[:max_source]}{suffix}"
