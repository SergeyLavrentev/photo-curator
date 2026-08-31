import base64
import struct
from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest
from PIL import Image, ImageDraw

from photo_curator.db import repository
from photo_curator.db.connection import database_connection
from photo_curator.db.migrations import migrate
from photo_curator.paths import default_application_paths
from photo_curator.photos.fake_provider import FakePhotosProvider
from photo_curator.photos.provider import PhotoAsset
from photo_curator.pipeline.coordinator import PipelineCoordinator
from photo_curator.pipeline.previews import analysis_preview_is_eligible


class FakeNativeVisionEngine:
    def analyze(self, assets, *, warmup_iterations=0, measured_iterations=1):
        del warmup_iterations, measured_iterations
        return {
            "schema_version": 1,
            "engine": {"name": "apple-vision-native", "version": "test-v1"},
            "assets": [
                {
                    "asset_uuid": asset_uuid,
                    "aesthetics": {
                        "overall_score": (index - 6) / 12,
                        "is_utility": False,
                        "revision": 1,
                    },
                    "feature_print": {
                        "revision": 2,
                        "element_count": 2,
                        "element_type": 1,
                        "data_base64": base64.b64encode(
                            struct.pack("<ff", index / 11, 1 - index / 11)
                        ).decode("ascii"),
                    },
                    "attention_saliency": {
                        "revision": 2,
                        "heatmap_width": 68,
                        "heatmap_height": 68,
                        "salient_objects": [{"x": 0.2, "y": 0.2, "width": 0.6, "height": 0.6}],
                    },
                    "faces": {
                        "face_count": 0,
                        "eyes_detected": 0,
                        "best_capture_quality": None,
                        "landmark_revision": 3,
                        "quality_revision": 3,
                    },
                    "durations_ms": {
                        "aesthetics": 10.0,
                        "feature_print": 5.0,
                        "attention_saliency": 20.0,
                        "faces": 8.0,
                    },
                    "errors": {},
                }
                for index, (asset_uuid, _) in enumerate(assets)
            ],
        }


class FakeLocalModelEngine:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def analyze(self, assets, *, engines, cancelled=None):
        if cancelled and cancelled():
            raise RuntimeError("unexpected cancellation")
        self.calls.append([asset_uuid for asset_uuid, _ in assets])
        return {
            "schema_version": 1,
            "engine": {"name": "photo-curator-local-models", "version": "test-v1"},
            "enabled_engines": sorted(engines),
            "assets": [
                {
                    "asset_uuid": asset_uuid,
                    "nima": {"aesthetic_score": 40.0 + index, "raw_score": 5.0},
                    "mobileclip": {
                        "aesthetic_score": 45.0 + index,
                        "genre": "portrait" if index % 2 else "landscape",
                        "genre_confidence": 0.75,
                    },
                    "musiq": {"quality_score": 55.0 + index, "raw_score": 55.0 + index},
                    "durations_ms": {"nima": 12.0, "mobileclip": 5.0, "musiq": 25.0},
                    "errors": {},
                }
                for index, (asset_uuid, _) in enumerate(assets)
            ],
        }


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
    coordinator = PipelineCoordinator(
        database_path=paths.database,
        paths=paths,
        provider=provider,
        vision_engine=FakeNativeVisionEngine(),
        model_engine=FakeLocalModelEngine(),
    )
    return paths, provider, coordinator, project_id


def test_full_pipeline_persists_assets_metrics_groups_and_decisions(tmp_path: Path) -> None:
    paths, _, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)

    with database_connection(paths.database) as connection:
        materialized = repository.list_assets(connection, project_id)
    assert all(
        paths.project_artifacts_dir in Path(str(asset["review_path"])).parents
        for asset in materialized
    )
    assert all(
        Path(str(asset["review_path"])).parent
        == paths.project_artifacts_dir / project_id / "review"
        for asset in materialized
    )

    with database_connection(paths.database) as connection:
        summary = repository.project_summary(connection, project_id)
        assets = repository.list_assets(connection, project_id)
        jobs = repository.latest_jobs(connection, project_id)
        project = repository.get_project(connection, project_id)
        signals = repository.list_analysis_signals(connection, project_id)
        swipe_scores = repository.list_swipe_scores(connection, project_id)
        shadow = repository.latest_engine_shadow_run(connection, project_id)

    assert project["state"] == "ready"
    assert summary["total"] == 12
    assert summary["ready"] == 12
    assert summary["duplicate_groups"] >= 1
    assert summary["reject"] >= 1
    assert 0 < summary["pick"] < summary["keep"]
    assert (
        summary["pick"]
        + summary["alternative"]
        + summary["selection_review"]
        + summary["selection_reject"]
        == 12
    )
    assert all(asset["phash"] and asset["final_disposition"] for asset in assets)
    assert all(asset["final_selection"] for asset in assets)
    assert len(signals) == 96
    assert {signal["signal_kind"] for signal in signals} == {
        "aesthetics",
        "feature_print",
        "attention_saliency",
        "faces",
        "subject_quality",
        "nima_aesthetics",
        "mobileclip",
        "musiq_quality",
    }
    assert all(signal["status"] == "ready" for signal in signals)
    assert all(signal["source_fingerprint"] for signal in signals)
    assert len(swipe_scores) == 12
    assert all(score["schema_version"] == 2 for score in swipe_scores)
    assert all("model_disagreement" in score["components"] for score in swipe_scores)
    assert all("generic_aesthetics" in score["components"] for score in swipe_scores)
    assert any(score["components"]["diversity_value"] != 50 for score in swipe_scores)
    assert all(asset["swipe_score"] is not None for asset in assets)
    assert all(asset["swipe_personal_delta"] == 0 for asset in assets)
    assert shadow is not None
    assert shadow["engine_version"] == "3.1.0-shadow"
    assert shadow["config"]["mutates_product_decisions"] is False
    assert shadow["summary"]["asset_count"] == 12
    assert {job["stage"] for job in jobs} == {
        "inventory",
        "previews",
        "metrics",
        "duplicates",
        "vision",
        "models",
        "scene_shadow",
        "decisions",
    }
    assert all(job["status"] in {"done", "warning"} for job in jobs)


def test_degraded_photokit_previews_remain_visible_but_never_reach_models_or_codex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class DegradedReviewProvider(FakePhotosProvider):
        def __init__(self, fixture_root: Path) -> None:
            super().__init__(fixture_root)
            tiny = fixture_root / "photokit-degraded-48x64.jpg"
            Image.new("RGB", (48, 64), (72, 94, 118)).save(tiny, "JPEG", quality=70)
            self._assets = [
                replace(
                    asset,
                    source_path=tiny,
                    width=48,
                    height=64,
                    review_render=True,
                    provider_error="CloudPhotoLibraryErrorDomain error 1005",
                )
                for asset in self._assets
            ]

    def unexpected_codex_status():
        raise AssertionError("Codex status must not be checked without eligible previews")

    monkeypatch.setattr("photo_curator.pipeline.coordinator.codex_status", unexpected_codex_status)
    paths = default_application_paths(tmp_path)
    paths.ensure()
    provider = DegradedReviewProvider(paths.cache_dir / "sources")
    with database_connection(paths.database) as connection:
        migrate(connection)
        project_id = repository.create_project(
            connection,
            name="Degraded PhotoKit previews",
            library=provider.get_current_library(),
            album=provider.list_regular_albums()[0],
            analysis_mode="codex",
        )
    model_engine = FakeLocalModelEngine()
    coordinator = PipelineCoordinator(
        database_path=paths.database,
        paths=paths,
        provider=provider,
        vision_engine=FakeNativeVisionEngine(),
        model_engine=model_engine,
    )

    coordinator.run(project_id)

    with database_connection(paths.database) as connection:
        project = repository.get_project(connection, project_id)
        assets = repository.list_assets(connection, project_id)
        jobs = repository.latest_jobs(connection, project_id)
        signals = repository.list_analysis_signals(connection, project_id)

    assert project["state"] == "ready"
    assert len(assets) == 12
    assert all(asset["cache_state"] == "degraded" for asset in assets)
    assert all(Path(str(asset["review_path"])).is_file() for asset in assets)
    assert all(Path(str(asset["thumbnail_path"])).is_file() for asset in assets)
    assert all(
        "preview_too_small:48x64" in str(asset["metadata"].get("render_warning"))
        for asset in assets
    )
    assert all(asset["phash"] is None for asset in assets)
    assert all(asset["final_selection"] == "review" for asset in assets)
    assert signals == []
    assert model_engine.calls == []
    preview_job = next(job for job in jobs if job["stage"] == "previews")
    codex_job = next(job for job in jobs if job["stage"] == "codex")
    assert preview_job["status"] == "warning"
    assert preview_job["warning_count"] == 12
    assert codex_job["status"] == "done"
    assert codex_job["processed_items"] == 0
    assert "нет preview достаточного разрешения" in codex_job["current_message"]


def test_same_process_preview_repair_reloads_only_degraded_photokit_assets(
    tmp_path: Path,
) -> None:
    class RepairingProvider(FakePhotosProvider):
        def __init__(self, fixture_root: Path) -> None:
            super().__init__(fixture_root)
            self.full_assets = [
                replace(asset, review_render=True, source_revision=f"source-{asset.uuid}")
                for asset in self._assets
            ]
            tiny = fixture_root / "photokit-degraded-48x64.jpg"
            Image.new("RGB", (48, 64), (72, 94, 118)).save(tiny, "JPEG", quality=70)
            self._assets = [
                replace(
                    asset,
                    source_path=tiny,
                    review_render=True,
                    source_revision=f"source-{asset.uuid}",
                    provider_error="CloudPhotoLibraryErrorDomain error 1005",
                )
                for asset in self.full_assets
            ]
            self.repair_calls: list[list[str]] = []

        def repair_assets(self, asset_uuids: list[str]) -> list[PhotoAsset]:
            self.repair_calls.append(list(asset_uuids))
            wanted = set(asset_uuids)
            repaired = [asset for asset in self.full_assets if asset.uuid in wanted]
            self._assets = list(self.full_assets)
            return repaired

    paths = default_application_paths(tmp_path)
    paths.ensure()
    provider = RepairingProvider(paths.cache_dir / "sources")
    with database_connection(paths.database) as connection:
        migrate(connection)
        project_id = repository.create_project(
            connection,
            name="Repair degraded PhotoKit previews",
            library=provider.get_current_library(),
            album=provider.list_regular_albums()[0],
        )
    coordinator = PipelineCoordinator(
        database_path=paths.database,
        paths=paths,
        provider=provider,
        vision_engine=FakeNativeVisionEngine(),
        model_engine=FakeLocalModelEngine(),
    )

    coordinator.run(project_id)
    with database_connection(paths.database) as connection:
        degraded = repository.list_assets(connection, project_id)
    assert all(asset["cache_state"] == "degraded" for asset in degraded)
    assert provider.repair_calls == []

    coordinator.run(project_id, from_stage="previews")
    with database_connection(paths.database) as connection:
        repaired = repository.list_assets(connection, project_id)
        signals = repository.list_analysis_signals(connection, project_id)

    assert provider.repair_calls == [[f"demo-{index:03d}" for index in range(1, 13)]]
    assert all(asset["cache_state"] == "ready" for asset in repaired)
    assert all(analysis_preview_is_eligible(Path(str(asset["review_path"]))) for asset in repaired)
    assert len(signals) == 96


def test_stage_fingerprints_skip_unchanged_expensive_analysis(tmp_path: Path) -> None:
    paths, _, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)
    with database_connection(paths.database) as connection:
        first_jobs = int(connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])
        first_signals = {
            (row["asset_uuid"], row["signal_kind"]): row["calculated_at"]
            for row in repository.list_analysis_signals(connection, project_id)
        }
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM stage_fingerprints WHERE project_id=?",
                (project_id,),
            ).fetchone()[0]
            == 9
        )

    coordinator.run(project_id)

    with database_connection(paths.database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == first_jobs + 2
        second_signals = {
            (row["asset_uuid"], row["signal_kind"]): row["calculated_at"]
            for row in repository.list_analysis_signals(connection, project_id)
        }
    assert second_signals == first_signals


def test_scene_shadow_runs_are_immutable_and_never_mutate_product_decisions(
    tmp_path: Path,
) -> None:
    paths, _, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)
    with database_connection(paths.database) as connection:
        before_decisions = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT asset_uuid, auto_disposition, manual_disposition, final_disposition,
                    auto_selection, manual_selection, final_selection
                FROM decisions WHERE project_id=? ORDER BY asset_uuid
                """,
                (project_id,),
            ).fetchall()
        ]
        first_run = repository.latest_engine_shadow_run(connection, project_id)
        assert first_run is not None
        first_nodes = repository.engine_shadow_nodes(connection, str(first_run["id"]))

    coordinator._stage_scene_shadow(project_id)
    with database_connection(paths.database) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM engine_shadow_runs WHERE project_id=?", (project_id,)
            ).fetchone()[0]
            == 1
        )
        connection.execute(
            """
            UPDATE metrics SET technical_quality=technical_quality * 0.5
            WHERE project_id=? AND asset_uuid='demo-012'
            """,
            (project_id,),
        )

    coordinator._stage_scene_shadow(project_id)
    with database_connection(paths.database) as connection:
        after_decisions = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT asset_uuid, auto_disposition, manual_disposition, final_disposition,
                    auto_selection, manual_selection, final_selection
                FROM decisions WHERE project_id=? ORDER BY asset_uuid
                """,
                (project_id,),
            ).fetchall()
        ]
        runs = connection.execute(
            "SELECT id FROM engine_shadow_runs WHERE project_id=? ORDER BY created_at, id",
            (project_id,),
        ).fetchall()
        persisted_first_nodes = repository.engine_shadow_nodes(connection, str(first_run["id"]))

    assert len(runs) == 2
    assert after_decisions == before_decisions
    assert persisted_first_nodes == first_nodes


def test_core_ml_stage_reuses_per_asset_inference_after_stage_cache_reset(
    tmp_path: Path,
) -> None:
    paths, _, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)
    model_engine = coordinator.model_engine
    assert isinstance(model_engine, FakeLocalModelEngine)
    assert len(model_engine.calls) == 1
    assert len(model_engine.calls[0]) == 12
    with database_connection(paths.database) as connection:
        repository.clear_stage_fingerprints_from(connection, project_id, ("models", "decisions"))

    coordinator.run(project_id, from_stage="models")

    assert len(model_engine.calls) == 1
    with database_connection(paths.database) as connection:
        job = repository.latest_jobs(connection, project_id)[-2]
    assert job["stage"] == "models"
    assert job["processed_items"] == 0
    assert job["current_message"] == "Core ML inference cache актуален"


def test_pipeline_inventory_does_not_render_until_preview_stage(tmp_path: Path) -> None:
    class SplitInventoryProvider(FakePhotosProvider):
        metadata_calls = 0
        render_calls = 0

        def list_asset_metadata_with_progress(self, album_id, progress):
            self.metadata_calls += 1
            assets = [
                replace(asset, source_path=None, review_render=False)
                for asset in super().list_assets(album_id)
            ]
            for index in range(len(assets)):
                progress(index + 1, len(assets))
            return assets

        def list_assets_with_progress(self, album_id, progress):
            self.render_calls += 1
            assets = [replace(asset, review_render=True) for asset in super().list_assets(album_id)]
            for index in range(len(assets)):
                progress(index + 1, len(assets))
            return assets

    paths = default_application_paths(tmp_path)
    paths.ensure()
    provider = SplitInventoryProvider(paths.cache_dir / "sources")
    with database_connection(paths.database) as connection:
        migrate(connection)
        project_id = repository.create_project(
            connection,
            name="Split inventory",
            library=provider.get_current_library(),
            album=provider.list_regular_albums()[0],
        )
    coordinator = PipelineCoordinator(
        database_path=paths.database,
        paths=paths,
        provider=provider,
    )

    coordinator._stage_inventory(project_id)
    with database_connection(paths.database) as connection:
        inventoried = repository.list_assets(connection, project_id)
    assert provider.metadata_calls == 1
    assert provider.render_calls == 0
    assert all(asset["source_path"] is None for asset in inventoried)

    coordinator._stage_previews(project_id)
    with database_connection(paths.database) as connection:
        previewed = repository.list_assets(connection, project_id)
    assert provider.render_calls == 1
    assert all(asset["cache_state"] == "ready" for asset in previewed)


def test_manual_override_survives_reanalysis(tmp_path: Path) -> None:
    paths, _, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)
    with database_connection(paths.database) as connection:
        repository.set_manual_decision(connection, project_id, "demo-002", "keep", "важный кадр")
        repository.set_manual_selection(connection, project_id, "demo-002", "alternative")

    restarted = PipelineCoordinator(
        database_path=paths.database,
        paths=paths,
        provider=FakePhotosProvider(paths.cache_dir / "sources"),
    )
    restarted.run(project_id, from_stage="decisions")

    with database_connection(paths.database) as connection:
        asset = repository.get_asset(connection, project_id, "demo-002")
    assert asset["auto_disposition"] == "reject"
    assert asset["manual_disposition"] == "keep"
    assert asset["final_disposition"] == "keep"
    assert asset["manual_selection"] == "alternative"
    assert asset["final_selection"] == "alternative"
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


def test_native_vision_failure_is_persisted_without_breaking_review_pipeline(
    tmp_path: Path,
) -> None:
    class FailingVisionEngine:
        def analyze(self, assets, *, warmup_iterations=0, measured_iterations=1):
            raise OSError("synthetic Vision capability failure")

    paths, provider, _, project_id = build_pipeline(tmp_path)
    coordinator = PipelineCoordinator(
        database_path=paths.database,
        paths=paths,
        provider=provider,
        vision_engine=FailingVisionEngine(),
    )

    coordinator.run(project_id)

    with database_connection(paths.database) as connection:
        signals = repository.list_analysis_signals(connection, project_id)
        jobs = repository.latest_jobs(connection, project_id)
        assets = repository.list_assets(connection, project_id)
    apple_signals = [
        signal
        for signal in signals
        if signal["signal_kind"]
        in {"aesthetics", "feature_print", "attention_saliency", "faces", "subject_quality"}
    ]
    model_signals = [signal for signal in signals if signal not in apple_signals]
    assert len(apple_signals) == 60
    assert len(model_signals) == 36
    assert all(signal["status"] == "unavailable" for signal in signals)
    assert all("synthetic Vision" in str(signal["error_text"]) for signal in apple_signals)
    assert all("Core ML model helper" in str(signal["error_text"]) for signal in model_signals)
    assert next(job for job in jobs if job["stage"] == "vision")["status"] == "warning"
    assert all(asset["final_disposition"] for asset in assets)


def test_inventory_snapshot_tracks_render_metadata_video_count_and_removed_assets(
    tmp_path: Path,
) -> None:
    paths, provider, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)
    with database_connection(paths.database) as connection:
        edited = repository.get_asset(connection, project_id, "demo-009")
        scored = repository.get_asset(connection, project_id, "demo-010")
        summary = repository.project_summary(connection, project_id)

    assert summary["videos_skipped"] == 2
    assert edited["metadata"]["local_path_available"] is True
    assert edited["metadata"]["edited_path_available"] is False
    assert edited["metadata"]["album_membership"] is True
    assert "edited_render_missing" in edited["flags"]
    assert scored["apple_overall_percentile"] == 0.5

    provider._assets = [asset for asset in provider._assets if asset.uuid != "demo-012"]
    coordinator.run(project_id, from_stage="inventory")
    with database_connection(paths.database) as connection:
        removed = repository.get_asset(connection, project_id, "demo-012")
        groups = repository.list_duplicate_groups(connection, project_id)
        summary = repository.project_summary(connection, project_id)
        gallery_total = repository.count_assets(connection, project_id)
    assert removed["no_longer_exists"] == 1
    assert removed["phash"] is None
    assert all(
        "demo-012" not in {str(member["asset_uuid"]) for member in group["members"]}
        for group in groups
    )
    assert summary["total"] == 11
    assert gallery_total == 11


def test_inventory_snapshots_video_but_never_sends_it_to_analysis(tmp_path: Path) -> None:
    paths, provider, coordinator, project_id = build_pipeline(tmp_path)
    video_path = tmp_path / "clip.mov"
    video_path.write_bytes(b"synthetic-video")
    provider._assets.append(
        PhotoAsset(
            uuid="demo-video-001",
            original_filename="clip.mov",
            current_filename="clip.mov",
            taken_at="2026-06-12T10:30:00.125+03:00",
            creation_timestamp=1781249400.125,
            modification_timestamp=1781249401.5,
            width=1920,
            height=1080,
            is_photo=False,
            media_type="video",
            media_subtypes=0,
            source_revision="video-revision-1",
            source_path=video_path,
        )
    )

    coordinator.run(project_id)

    with database_connection(paths.database) as connection:
        snapshot = repository.latest_album_snapshot(connection, project_id)
        assert snapshot is not None
        items = repository.album_snapshot_items(connection, str(snapshot["id"]))
        active_uuids = {
            str(asset["asset_uuid"])
            for asset in repository.list_assets(connection, project_id)
            if not asset["no_longer_exists"]
        }
        video_signals = connection.execute(
            "SELECT COUNT(*) FROM analysis_signals WHERE project_id=? AND asset_uuid=?",
            (project_id, "demo-video-001"),
        ).fetchone()[0]

    assert snapshot["item_count"] == 13
    assert snapshot["photo_count"] == 12
    assert snapshot["skipped_video_count"] == 1
    assert items[-1]["asset_uuid"] == "demo-video-001"
    assert items[-1]["media_type"] == "video"
    assert items[-1]["creation_timestamp"] == 1781249400.125
    assert "demo-video-001" not in active_uuids
    assert video_signals == 0


def test_source_revision_change_invalidates_only_affected_asset_analysis(tmp_path: Path) -> None:
    paths, provider, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)
    provider._assets = list(
        reversed(
            [
                replace(
                    asset,
                    modification_timestamp=1781249999.25,
                    latitude=55.7558,
                    longitude=37.6173,
                    source_revision="edited-revision-2",
                    has_adjustments=True,
                    edit_state="adjusted",
                )
                if asset.uuid == "demo-010"
                else asset
                for asset in provider._assets
            ]
        )
    )

    with database_connection(paths.database) as connection:
        repository.create_album_snapshot(connection, project_id, provider._assets)
        repository.upsert_assets(connection, project_id, provider._assets)
        changed_metric = connection.execute(
            "SELECT 1 FROM metrics WHERE project_id=? AND asset_uuid='demo-010'",
            (project_id,),
        ).fetchone()
        unchanged_metric = connection.execute(
            "SELECT 1 FROM metrics WHERE project_id=? AND asset_uuid='demo-011'",
            (project_id,),
        ).fetchone()
        changed_signals = connection.execute(
            "SELECT COUNT(*) FROM analysis_signals WHERE project_id=? AND asset_uuid='demo-010'",
            (project_id,),
        ).fetchone()[0]
        unchanged_signals = connection.execute(
            "SELECT COUNT(*) FROM analysis_signals WHERE project_id=? AND asset_uuid='demo-011'",
            (project_id,),
        ).fetchone()[0]
        changed_swipe = connection.execute(
            "SELECT 1 FROM swipe_scores WHERE project_id=? AND asset_uuid='demo-010'",
            (project_id,),
        ).fetchone()
        unchanged_swipe = connection.execute(
            "SELECT 1 FROM swipe_scores WHERE project_id=? AND asset_uuid='demo-011'",
            (project_id,),
        ).fetchone()
        snapshots = connection.execute(
            "SELECT id FROM album_snapshots WHERE project_id=? ORDER BY captured_at, id",
            (project_id,),
        ).fetchall()
        first_items = repository.album_snapshot_items(connection, str(snapshots[0]["id"]))
        second_items = repository.album_snapshot_items(connection, str(snapshots[-1]["id"]))

    assert changed_metric is None
    assert unchanged_metric is not None
    assert changed_signals == 0
    assert unchanged_signals > 0
    assert changed_swipe is None
    assert unchanged_swipe is not None
    assert [item["asset_uuid"] for item in second_items] == [
        asset.uuid for asset in provider._assets
    ]
    first_revision = next(
        item["revision_fingerprint"] for item in first_items if item["asset_uuid"] == "demo-010"
    )
    second_revision = next(
        item["revision_fingerprint"] for item in second_items if item["asset_uuid"] == "demo-010"
    )
    assert first_revision != second_revision
    changed_item = next(item for item in second_items if item["asset_uuid"] == "demo-010")
    assert (changed_item["latitude"], changed_item["longitude"]) == (55.7558, 37.6173)


def test_startup_interruption_updates_both_job_and_project_state(tmp_path: Path) -> None:
    paths, _, _, project_id = build_pipeline(tmp_path)
    with database_connection(paths.database) as connection:
        repository.create_job(connection, project_id, "inventory", 12)
        repository.set_project_state(connection, project_id, "running")

    with database_connection(paths.database) as connection:
        count = repository.mark_running_jobs_interrupted(connection)

    with database_connection(paths.database) as connection:
        project = repository.get_project(connection, project_id)
        jobs = repository.latest_jobs(connection, project_id)
    assert count == 1
    assert project["state"] == "interrupted"
    assert jobs[0]["status"] == "interrupted"


def test_resume_from_duplicates_reuses_previews_and_metrics(tmp_path: Path) -> None:
    paths, _, coordinator, project_id = build_pipeline(tmp_path)
    coordinator.run(project_id)
    with database_connection(paths.database) as connection:
        before = repository.get_asset(connection, project_id, "demo-001")
    preview_mtime = Path(str(before["review_path"])).stat().st_mtime_ns
    calculated_at = before["calculated_at"]

    coordinator.run(project_id, from_stage="duplicates")

    with database_connection(paths.database) as connection:
        after = repository.get_asset(connection, project_id, "demo-001")
    assert Path(str(after["review_path"])).stat().st_mtime_ns == preview_mtime
    assert after["calculated_at"] == calculated_at
    assert after["selection_score"] is not None
    assert after["swipe_confidence"] >= 0
    assert after["score_components"]["generic_aesthetics"] >= 0


def test_pipeline_cancels_cooperatively_and_resumes_from_cancelled_stage(tmp_path: Path) -> None:
    paths, provider, coordinator, project_id = build_pipeline(tmp_path)
    entered = Event()
    release = Event()
    original_list_assets = provider.list_assets

    def blocking_list_assets(album_id: str):
        entered.set()
        assert release.wait(timeout=5)
        return original_list_assets(album_id)

    provider.list_assets = blocking_list_assets
    coordinator.start(project_id)
    assert entered.wait(timeout=5)
    assert coordinator.cancel(project_id)
    release.set()
    coordinator._futures[project_id].result(timeout=5)

    with database_connection(paths.database) as connection:
        project = repository.get_project(connection, project_id)
        jobs = repository.latest_jobs(connection, project_id)
    assert project["state"] == "interrupted"
    assert jobs[-1]["stage"] == "inventory"
    assert jobs[-1]["status"] == "cancelled"

    provider.list_assets = original_list_assets
    coordinator.run(project_id, from_stage="inventory")
    with database_connection(paths.database) as connection:
        assert repository.get_project(connection, project_id)["state"] == "ready"
