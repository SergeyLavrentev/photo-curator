from datetime import datetime
from pathlib import Path

import pytest

from photo_curator.db import repository
from photo_curator.db.connection import database_connection
from photo_curator.photos.publisher import PhotosPublisher, unique_album_name
from photo_curator.utils.subprocesses import CommandResult
from tests.test_pipeline import build_pipeline


def test_unique_album_name_is_sanitized_and_bounded() -> None:
    name = unique_album_name("Folder/Album:\n" + "x" * 200, datetime(2026, 7, 18, 15, 30, 0))
    assert "/" not in name
    assert "\n" not in name
    assert name.endswith("Reject — 20260718-153000")
    assert len(name) <= 120


def test_dry_run_must_succeed_before_apply(tmp_path: Path) -> None:
    paths, provider, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)
    calls = []

    def runner(args: list[str]) -> CommandResult:
        calls.append(args)
        if "--help" in args:
            return CommandResult(args, 0, "--uuid-from-file --add-to-album --dry-run", "")
        return CommandResult(args, 0, "ok", "")

    publisher = PhotosPublisher(
        database_path=paths.database,
        paths=paths,
        provider=provider,
        runner=runner,
        executable="/usr/bin/true",
    )
    dry_run = publisher.dry_run(project_id)
    applied = publisher.apply(str(dry_run["id"]))

    assert calls[0][-1] == "--help"
    assert "--dry-run" in calls[1]
    assert "--dry-run" not in calls[2]
    assert Path(str(dry_run["uuid_file"])).read_text().splitlines() == sorted(
        Path(str(dry_run["uuid_file"])).read_text().splitlines()
    )
    assert applied["status"] == "applied"


def test_apply_without_successful_dry_run_is_refused(tmp_path: Path) -> None:
    paths, provider, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)
    with database_connection(paths.database) as connection:
        publish_id = repository.create_publish(
            connection,
            project_id=project_id,
            album_name="Reject",
            asset_count=1,
            uuid_file=str(tmp_path / "uuids.txt"),
        )
    publisher = PhotosPublisher(
        database_path=paths.database,
        paths=paths,
        provider=provider,
        executable="/usr/bin/true",
    )
    with pytest.raises(ValueError, match="dry-run"):
        publisher.apply(publish_id)


def test_empty_reject_set_is_blocked(tmp_path: Path) -> None:
    paths, provider, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)
    with database_connection(paths.database) as connection:
        for asset in repository.list_assets(connection, project_id):
            if asset["final_disposition"] == "reject":
                repository.set_manual_decision(
                    connection, project_id, str(asset["asset_uuid"]), "keep"
                )
    publisher = PhotosPublisher(
        database_path=paths.database,
        paths=paths,
        provider=provider,
        executable="/usr/bin/true",
    )
    assert "Нет подтверждённых reject-assets" in publisher.validate(project_id).blockers


def test_publish_is_disabled_when_required_cli_flags_are_missing(tmp_path: Path) -> None:
    paths, provider, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)

    def runner(args: list[str]) -> CommandResult:
        return CommandResult(args, 0, "batch-edit help without required options", "")

    publisher = PhotosPublisher(
        database_path=paths.database,
        paths=paths,
        provider=provider,
        runner=runner,
        executable="/usr/bin/true",
    )
    assert "osxphotos CLI недоступен" in publisher.validate(project_id).blockers


def test_apply_requires_new_dry_run_after_source_drift(tmp_path: Path) -> None:
    paths, provider, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)

    def runner(args: list[str]) -> CommandResult:
        output = "--uuid-from-file --add-to-album --dry-run" if "--help" in args else "ok"
        return CommandResult(args, 0, output, "")

    publisher = PhotosPublisher(
        database_path=paths.database,
        paths=paths,
        provider=provider,
        runner=runner,
        executable="/usr/bin/true",
    )
    dry_run = publisher.dry_run(project_id)
    reject_uuid = Path(str(dry_run["uuid_file"])).read_text(encoding="utf-8").splitlines()[0]
    provider._assets = [asset for asset in provider._assets if asset.uuid != reject_uuid]

    with pytest.raises(ValueError, match="новый dry-run"):
        publisher.apply(str(dry_run["id"]))


def test_best_album_publish_contains_selected_assets(tmp_path: Path) -> None:
    paths, provider, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)

    def runner(args: list[str]) -> CommandResult:
        output = "--uuid-from-file --add-to-album --dry-run" if "--help" in args else "ok"
        return CommandResult(args, 0, output, "")

    publisher = PhotosPublisher(
        database_path=paths.database,
        paths=paths,
        provider=provider,
        runner=runner,
        executable="/usr/bin/true",
    )
    result = publisher.dry_run(project_id, "best")
    with database_connection(paths.database) as connection:
        selected = {
            str(asset["asset_uuid"])
            for asset in repository.list_assets(connection, project_id)
            if asset["final_disposition"] == "keep"
        }

    assert result["kind"] == "best"
    assert " — Best — " in str(result["album_name"])
    assert set(Path(str(result["uuid_file"])).read_text().splitlines()) == selected


def test_resolution_inversion_blocks_publish_until_explicit_manual_confirmation(
    tmp_path: Path,
) -> None:
    paths, provider, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)

    def runner(args: list[str]) -> CommandResult:
        return CommandResult(args, 0, "--uuid-from-file --add-to-album --dry-run", "")

    publisher = PhotosPublisher(
        database_path=paths.database,
        paths=paths,
        provider=provider,
        runner=runner,
        executable="/usr/bin/true",
    )
    with database_connection(paths.database) as connection:
        connection.execute(
            "UPDATE decisions SET final_disposition='reject', manual_override=0 "
            "WHERE project_id=? AND asset_uuid='demo-001'",
            (project_id,),
        )
        repository.set_manual_decision(connection, project_id, "demo-003", "keep")
        repository.set_manual_decision(connection, project_id, "demo-002", "keep")

    blocked = publisher.validate(project_id)
    assert any("resolution inversion" in blocker for blocker in blocked.blockers)

    with database_connection(paths.database) as connection:
        repository.set_manual_decision(connection, project_id, "demo-001", "reject")
    confirmed = publisher.validate(project_id)
    assert not any("resolution inversion" in blocker for blocker in confirmed.blockers)
    assert any("подтверждён вручную" in warning for warning in confirmed.warnings)


def test_provider_failure_becomes_publish_blocker(tmp_path: Path) -> None:
    paths, provider, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)

    def unavailable(_: list[str]):
        raise PermissionError("private library path")

    provider.refresh_assets = unavailable
    publisher = PhotosPublisher(
        database_path=paths.database,
        paths=paths,
        provider=provider,
        executable="/usr/bin/true",
    )

    validation = publisher.validate(project_id)
    assert validation.blockers == ["Photos Library недоступна; повторите Doctor/read gate"]
    assert "private library path" not in ";".join(validation.blockers)
