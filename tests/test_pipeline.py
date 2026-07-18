from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from photo_curator.db import repository
from photo_curator.db.connection import database_connection
from photo_curator.db.migrations import migrate
from photo_curator.paths import default_application_paths
from photo_curator.photos.fake_provider import FakePhotosProvider
from photo_curator.pipeline.coordinator import PipelineCoordinator


def build_pipeline(tmp_path: Path):
    paths = default_application_paths(tmp_path)
    paths.ensure()
    provider = FakePhotosProvider(paths.cache_dir / "sources")
    with database_connection(paths.database) as connection:
        migrate(connection)
        project_id = repository.create_project(
            connection,
            name="Demo",
            library=provider.get_current_library(),
            album=provider.list_regular_albums()[0],
        )
    coordinator = PipelineCoordinator(database_path=paths.database, paths=paths, provider=provider)
    return paths, provider, coordinator, project_id


def test_full_pipeline_persists_assets_metrics_groups_and_decisions(tmp_path: Path) -> None:
    paths, _, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)

    with database_connection(paths.database) as connection:
        summary = repository.project_summary(connection, project_id)
        assets = repository.list_assets(connection, project_id)
        jobs = repository.latest_jobs(connection, project_id)
        project = repository.get_project(connection, project_id)

    assert project["state"] == "ready"
    assert summary["total"] == 12
    assert summary["ready"] == 12
    assert summary["duplicate_groups"] >= 1
    assert summary["reject"] >= 1
    assert all(asset["phash"] and asset["final_disposition"] for asset in assets)
    assert {job["stage"] for job in jobs} == {
        "inventory",
        "previews",
        "metrics",
        "duplicates",
        "decisions",
    }
    assert all(job["status"] in {"done", "warning"} for job in jobs)


def test_manual_override_survives_reanalysis(tmp_path: Path) -> None:
    paths, _, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)
    with database_connection(paths.database) as connection:
        repository.set_manual_decision(connection, project_id, "demo-002", "keep", "важный кадр")

    coordinator.run(project_id, from_stage="decisions")

    with database_connection(paths.database) as connection:
        asset = repository.get_asset(connection, project_id, "demo-002")
    assert asset["auto_disposition"] == "reject"
    assert asset["manual_disposition"] == "keep"
    assert asset["final_disposition"] == "keep"
    assert asset["manual_note"] == "важный кадр"


def test_preview_cache_is_reused_then_invalidated_when_source_changes(tmp_path: Path) -> None:
    paths, provider, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)
    with database_connection(paths.database) as connection:
        before = repository.get_asset(connection, project_id, "demo-012")
    review_path = Path(str(before["review_path"]))
    first_mtime = review_path.stat().st_mtime_ns
    first_fingerprint = before["source_fingerprint"]

    coordinator.run(project_id, from_stage="previews")
    assert review_path.stat().st_mtime_ns == first_mtime

    source = next(
        asset.source_path
        for asset in provider.list_assets(provider.ALBUM_ID)
        if asset.uuid == "demo-012"
    )
    assert source is not None
    with Image.open(source) as image:
        changed = image.copy()
    ImageDraw.Draw(changed).rectangle((0, 0, 100, 100), fill=(255, 0, 0))
    changed.save(source, "JPEG", quality=92)
    coordinator.run(project_id, from_stage="previews")

    with database_connection(paths.database) as connection:
        after = repository.get_asset(connection, project_id, "demo-012")
    assert after["source_fingerprint"] != first_fingerprint
    assert after["phash"]


def test_stage_failure_marks_running_job_error_and_preserves_completed_stage(
    tmp_path: Path,
) -> None:
    class FailingProvider(FakePhotosProvider):
        calls = 0

        def list_assets(self, album_id: str):
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError("synthetic provider failure")
            return super().list_assets(album_id)

    paths = default_application_paths(tmp_path)
    paths.ensure()
    provider = FailingProvider(paths.cache_dir / "sources")
    with database_connection(paths.database) as connection:
        migrate(connection)
        project_id = repository.create_project(
            connection,
            name="Failure",
            library=provider.get_current_library(),
            album=provider.list_regular_albums()[0],
        )
    coordinator = PipelineCoordinator(database_path=paths.database, paths=paths, provider=provider)

    with pytest.raises(RuntimeError, match="synthetic provider failure"):
        coordinator.run(project_id)

    with database_connection(paths.database) as connection:
        project = repository.get_project(connection, project_id)
        jobs = repository.latest_jobs(connection, project_id)
    assert project["state"] == "error"
    assert jobs[0]["stage"] == "inventory" and jobs[0]["status"] == "done"
    assert jobs[1]["stage"] == "previews" and jobs[1]["status"] == "error"
