from pathlib import Path

from photo_curator.db import repository
from photo_curator.db.connection import database_connection
from photo_curator.photos.demo_publisher import DemoPhotosPublisher
from tests.test_pipeline import build_pipeline


def test_demo_publish_preserves_dry_run_confirmation_contract(tmp_path: Path) -> None:
    paths, _provider, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)
    publisher = DemoPhotosPublisher(database_path=paths.database, paths=paths)

    dry_run = publisher.dry_run(project_id, "best")
    progress: list[tuple[str, int, int]] = []
    applied = publisher.apply(
        str(dry_run["id"]),
        progress=lambda phase, processed, total: progress.append((phase, processed, total)),
    )

    assert dry_run["status"] == "dry_run_ok"
    assert applied["status"] == "applied"
    assert str(applied["destination_album_id"]).startswith("demo-")
    assert progress[-1][0] == "commit"
    with database_connection(paths.database) as connection:
        expected = sorted(
            str(asset["asset_uuid"])
            for asset in repository.list_assets(connection, project_id)
            if asset["final_selection"] == "pick"
        )
    assert Path(str(dry_run["uuid_file"])).read_text().splitlines() == expected


def test_demo_apply_revalidates_exact_asset_set(tmp_path: Path) -> None:
    paths, _provider, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)
    publisher = DemoPhotosPublisher(database_path=paths.database, paths=paths)
    dry_run = publisher.dry_run(project_id, "best")

    with database_connection(paths.database) as connection:
        first = next(
            asset
            for asset in repository.list_assets(connection, project_id)
            if asset["final_selection"] == "pick"
        )
        repository.set_manual_decision(connection, project_id, str(first["asset_uuid"]), "reject")

    try:
        publisher.apply(str(dry_run["id"]))
    except ValueError as error:
        assert "изменились после dry-run" in str(error)
    else:
        raise AssertionError("demo apply accepted a changed asset set")
