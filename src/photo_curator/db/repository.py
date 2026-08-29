from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable

from photo_curator.analysis.decision_engine import binary_disposition
from photo_curator.db import gallery_repository
from photo_curator.photos.provider import PhotoAlbum, PhotoAsset, PhotoLibrary
from photo_curator.utils.identifiers import new_id
from photo_curator.utils.timestamps import utc_now


def mark_running_jobs_interrupted(connection: sqlite3.Connection) -> int:
    project_ids = [
        str(row["project_id"])
        for row in connection.execute(
            "SELECT DISTINCT project_id FROM jobs WHERE status='running'"
        ).fetchall()
    ]
    cursor = connection.execute(
        """
        UPDATE jobs
        SET status = 'interrupted', current_message = 'Приложение было перезапущено'
        WHERE status = 'running'
        """
    )
    if project_ids:
        connection.executemany(
            "UPDATE projects SET state='interrupted', updated_at=? WHERE id=?",
            [(utc_now(), project_id) for project_id in project_ids],
        )
    return cursor.rowcount


def cancel_running_jobs(connection: sqlite3.Connection, project_id: str) -> int:
    cursor = connection.execute(
        """
        UPDATE jobs
        SET status='cancelled', current_message='Анализ остановлен', finished_at=?
        WHERE project_id=? AND status='running'
        """,
        (utc_now(), project_id),
    )
    return cursor.rowcount


def mark_running_shared_copies_interrupted(connection: sqlite3.Connection) -> int:
    return connection.execute(
        """
        UPDATE shared_copy_jobs
        SET status='interrupted', current_message='Приложение было перезапущено',
            finished_at=?
        WHERE status='running'
        """,
        (utc_now(),),
    ).rowcount


def create_shared_copy_job(
    connection: sqlite3.Connection,
    *,
    shared_album_id: str,
    shared_album_name: str,
    destination_album_name: str,
    mode: str,
    asset_uuids: list[str],
    skipped_videos: int,
) -> str:
    job_id = new_id()
    connection.execute(
        """
        INSERT INTO shared_copy_jobs (
            id, shared_album_id, shared_album_name, destination_album_name,
            mode, status, total_items, skipped_videos, current_message,
            asset_uuids_json, created_at
        ) VALUES (?, ?, ?, ?, ?, 'planned', ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            shared_album_id,
            shared_album_name,
            destination_album_name,
            mode,
            len(asset_uuids),
            skipped_videos,
            "Готово к созданию локальной копии",
            json.dumps(asset_uuids),
            utc_now(),
        ),
    )
    return job_id


def get_shared_copy_job(connection: sqlite3.Connection, job_id: str) -> dict[str, object]:
    row = connection.execute("SELECT * FROM shared_copy_jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        raise KeyError(job_id)
    return dict(row)


def list_shared_copy_jobs(connection: sqlite3.Connection) -> list[dict[str, object]]:
    rows = connection.execute("SELECT * FROM shared_copy_jobs ORDER BY created_at DESC").fetchall()
    return [dict(row) for row in rows]


def completed_shared_copy_for_album(
    connection: sqlite3.Connection, album_id: str
) -> dict[str, object] | None:
    row = connection.execute(
        """
        SELECT * FROM shared_copy_jobs
        WHERE destination_album_id=? AND status='done'
        ORDER BY finished_at DESC LIMIT 1
        """,
        (album_id,),
    ).fetchone()
    return dict(row) if row else None


def update_shared_copy_job(
    connection: sqlite3.Connection,
    job_id: str,
    *,
    status: str | None = None,
    processed: int | None = None,
    imported: int | None = None,
    reused: int | None = None,
    destination_album_id: str | None = None,
    warnings: int | None = None,
    errors: int | None = None,
    message: str | None = None,
    error_text: str | None = None,
    clear_error: bool = False,
    started: bool = False,
    finished: bool = False,
    reset_finished: bool = False,
) -> None:
    cursor = connection.execute(
        """
        UPDATE shared_copy_jobs SET
            status=COALESCE(?, status), processed_items=COALESCE(?, processed_items),
            imported_items=COALESCE(?, imported_items), reused_items=COALESCE(?, reused_items),
            destination_album_id=COALESCE(?, destination_album_id),
            warning_count=COALESCE(?, warning_count), error_count=COALESCE(?, error_count),
            current_message=COALESCE(?, current_message),
            error_text=CASE WHEN ? THEN NULL ELSE COALESCE(?, error_text) END,
            started_at=CASE WHEN ? THEN COALESCE(started_at, ?) ELSE started_at END,
            finished_at=CASE WHEN ? THEN NULL WHEN ? THEN ? ELSE finished_at END
        WHERE id=?
        """,
        (
            status,
            processed,
            imported,
            reused,
            destination_album_id,
            warnings,
            errors,
            message,
            int(clear_error),
            error_text,
            int(started),
            utc_now(),
            int(reset_finished),
            int(finished),
            utc_now(),
            job_id,
        ),
    )
    if not cursor.rowcount:
        raise KeyError(job_id)


def fail_running_jobs(connection: sqlite3.Connection, project_id: str, detail: str) -> int:
    cursor = connection.execute(
        """
        UPDATE jobs SET status='error', error_count=error_count+1,
            current_message='Этап завершился с ошибкой', error_text=?, finished_at=?
        WHERE project_id=? AND status='running'
        """,
        (detail[:2000], utc_now(), project_id),
    )
    return cursor.rowcount


def create_project(
    connection: sqlite3.Connection,
    *,
    name: str,
    library: PhotoLibrary,
    album: PhotoAlbum,
    project_id: str | None = None,
    selection_density: str = "balanced",
    source_provenance: str = "regular_album",
    analysis_mode: str = "local",
    local_engines: dict[str, bool] | None = None,
) -> str:
    project_id = project_id or new_id()
    now = utc_now()
    local_engines = local_engines or {
        "apple": True,
        "nima": True,
        "mobileclip": True,
        "musiq": True,
    }
    connection.execute(
        """
        INSERT INTO projects (
            id, name, library_path, database_path, library_fingerprint,
            album_id, album_name, album_folder_path, album_full_path,
            album_snapshot_hash, state, settings_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'created', ?, ?, ?)
        """,
        (
            project_id,
            name,
            library.library_path,
            library.database_path,
            library.fingerprint,
            album.id,
            album.name,
            album.folder_path,
            album.full_path,
            hashlib.sha256(
                f"{album.id}|{album.photo_count}|{album.video_count}".encode()
            ).hexdigest(),
            json.dumps(
                {
                    "photo_count": album.photo_count,
                    "video_count": album.video_count,
                    "source_album_shared": album.is_shared,
                    "selection_density": selection_density,
                    "source_provenance": source_provenance,
                    "analysis_mode": analysis_mode,
                    "local_engines": local_engines,
                },
                sort_keys=True,
            ),
            now,
            now,
        ),
    )
    return project_id


def list_projects(connection: sqlite3.Connection) -> list[dict[str, object]]:
    rows = connection.execute("SELECT * FROM projects ORDER BY updated_at DESC").fetchall()
    return [dict(row) for row in rows]


def get_project(connection: sqlite3.Connection, project_id: str) -> dict[str, object]:
    row = connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not row:
        raise KeyError(project_id)
    return dict(row)


def set_project_state(connection: sqlite3.Connection, project_id: str, state: str) -> None:
    connection.execute(
        "UPDATE projects SET state = ?, updated_at = ? WHERE id = ?",
        (state, utc_now(), project_id),
    )


def update_project_settings(
    connection: sqlite3.Connection,
    project_id: str,
    updates: dict[str, object],
) -> None:
    project = get_project(connection, project_id)
    settings = json.loads(str(project.get("settings_json") or "{}"))
    settings.update(updates)
    connection.execute(
        "UPDATE projects SET settings_json=?, updated_at=? WHERE id=?",
        (json.dumps(settings, sort_keys=True), utc_now(), project_id),
    )


def stage_fingerprint(connection: sqlite3.Connection, project_id: str, stage: str) -> str | None:
    row = connection.execute(
        "SELECT input_fingerprint FROM stage_fingerprints WHERE project_id=? AND stage=?",
        (project_id, stage),
    ).fetchone()
    return str(row[0]) if row else None


def record_stage_fingerprint(
    connection: sqlite3.Connection,
    project_id: str,
    stage: str,
    input_fingerprint: str,
) -> None:
    connection.execute(
        """
        INSERT INTO stage_fingerprints (project_id, stage, input_fingerprint, completed_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(project_id, stage) DO UPDATE SET
            input_fingerprint=excluded.input_fingerprint,
            completed_at=excluded.completed_at
        """,
        (project_id, stage, input_fingerprint, utc_now()),
    )


def clear_stage_fingerprints_from(
    connection: sqlite3.Connection,
    project_id: str,
    stages: tuple[str, ...],
) -> None:
    if not stages:
        return
    connection.execute(
        "DELETE FROM stage_fingerprints WHERE project_id=? AND stage IN ({})".format(
            ",".join("?" for _ in stages)
        ),
        (project_id, *stages),
    )


def delete_project(connection: sqlite3.Connection, project_id: str) -> None:
    cursor = connection.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    if not cursor.rowcount:
        raise KeyError(project_id)


def upsert_assets(
    connection: sqlite3.Connection,
    project_id: str,
    assets: Iterable[PhotoAsset],
) -> int:
    now = utc_now()
    rows = []
    for asset in assets:
        rows.append(
            (
                project_id,
                asset.uuid,
                asset.original_filename,
                asset.current_filename,
                asset.taken_at,
                asset.date_added,
                asset.width,
                asset.height,
                asset.original_width,
                asset.original_height,
                asset.orientation,
                int(asset.favorite),
                int(asset.hidden),
                int(asset.has_adjustments),
                int(asset.is_live_photo),
                int(asset.is_burst),
                _normalized_burst_key(asset.burst_key),
                int(asset.burst_default_pick),
                int(asset.is_missing),
                0,
                str(asset.source_path) if asset.source_path else None,
                json.dumps(
                    {
                        "local_path_available": bool(asset.source_path),
                        "edited_path_available": bool(asset.edited_path),
                        "derivative_count": len(asset.derivative_paths),
                        "album_membership": True,
                        "provider_error": asset.provider_error,
                    },
                    sort_keys=True,
                ),
                json.dumps(asset.apple_scores) if asset.apple_scores else None,
                now,
                now,
            )
        )
    connection.execute("UPDATE assets SET no_longer_exists=1 WHERE project_id=?", (project_id,))
    connection.executemany(
        """
        INSERT INTO assets (
            project_id, asset_uuid, original_filename, current_filename, taken_at,
            date_added, width, height, original_width, original_height, orientation,
            favorite, hidden, has_adjustments, is_live_photo, is_burst, burst_key,
            burst_default_pick, is_missing, no_longer_exists, source_path, metadata_json,
            apple_scores_json, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(project_id, asset_uuid) DO UPDATE SET
            original_filename=excluded.original_filename,
            current_filename=excluded.current_filename,
            taken_at=excluded.taken_at,
            date_added=excluded.date_added,
            width=excluded.width,
            height=excluded.height,
            original_width=excluded.original_width,
            original_height=excluded.original_height,
            orientation=excluded.orientation,
            favorite=excluded.favorite,
            hidden=excluded.hidden,
            has_adjustments=excluded.has_adjustments,
            is_live_photo=excluded.is_live_photo,
            is_burst=excluded.is_burst,
            burst_key=excluded.burst_key,
            burst_default_pick=excluded.burst_default_pick,
            is_missing=excluded.is_missing,
            no_longer_exists=0,
            source_path=excluded.source_path,
            metadata_json=excluded.metadata_json,
            apple_scores_json=excluded.apple_scores_json,
            updated_at=excluded.updated_at
        """,
        rows,
    )
    return len(rows)


def _normalized_burst_key(value: object) -> str | None:
    if value in (None, False, 0, "", "0"):
        return None
    return str(value)


def update_asset_preview(
    connection: sqlite3.Connection,
    project_id: str,
    asset_uuid: str,
    *,
    source_kind: str,
    source_size: int | None,
    source_mtime: float | None,
    source_fingerprint: str | None,
    review_path: str | None,
    thumbnail_path: str | None,
    cache_state: str,
    render_warning: str | None = None,
) -> None:
    cursor = connection.execute(
        """
        UPDATE assets SET source_kind=?, source_size=?, source_mtime=?, source_fingerprint=?,
            review_path=?, thumbnail_path=?, cache_state=?,
            metadata_json=json_set(metadata_json, '$.render_warning', ?), updated_at=?
        WHERE project_id=? AND asset_uuid=?
        """,
        (
            source_kind,
            source_size,
            source_mtime,
            source_fingerprint,
            review_path,
            thumbnail_path,
            cache_state,
            render_warning,
            utc_now(),
            project_id,
            asset_uuid,
        ),
    )
    if not cursor.rowcount:
        raise KeyError(asset_uuid)


def invalidate_asset_analysis(
    connection: sqlite3.Connection, project_id: str, asset_uuid: str
) -> None:
    connection.execute(
        "DELETE FROM metrics WHERE project_id=? AND asset_uuid=?", (project_id, asset_uuid)
    )
    connection.execute(
        "DELETE FROM analysis_signals WHERE project_id=? AND asset_uuid=?",
        (project_id, asset_uuid),
    )
    connection.execute(
        "DELETE FROM swipe_scores WHERE project_id=? AND asset_uuid=?",
        (project_id, asset_uuid),
    )
    connection.execute(
        "DELETE FROM decisions WHERE project_id=? AND asset_uuid=? AND manual_override=0",
        (project_id, asset_uuid),
    )


def set_asset_analysis_error(
    connection: sqlite3.Connection, project_id: str, asset_uuid: str
) -> None:
    connection.execute(
        "UPDATE assets SET cache_state='analysis_error', updated_at=? "
        "WHERE project_id=? AND asset_uuid=?",
        (utc_now(), project_id, asset_uuid),
    )


METRIC_COLUMNS = [
    "laplacian_variance",
    "gradient_energy",
    "edge_density",
    "luma_mean",
    "luma_std",
    "luma_p01",
    "luma_p05",
    "luma_p50",
    "luma_p95",
    "luma_p99",
    "black_clipped_ratio",
    "white_clipped_ratio",
    "contrast_std",
    "dynamic_range",
    "entropy",
    "dominant_horizon_degrees",
    "horizon_support",
    "sharpness_percentile",
    "gradient_percentile",
    "contrast_percentile",
    "apple_overall_percentile",
    "dhash",
    "phash",
    "normalized_pixel_hash",
    "histogram_json",
    "technical_quality",
    "face_count",
    "face_capture_quality",
    "eyes_detected",
]


def upsert_metrics(
    connection: sqlite3.Connection,
    project_id: str,
    asset_uuid: str,
    metrics: dict[str, object],
) -> None:
    columns = ["project_id", "asset_uuid", *METRIC_COLUMNS, "calculated_at"]
    values = [project_id, asset_uuid]
    for column in METRIC_COLUMNS:
        value = metrics.get(column)
        values.append(
            json.dumps(value) if column == "histogram_json" and value is not None else value
        )
    values.append(utc_now())
    placeholders = ",".join("?" for _ in columns)
    updates = ",".join(f"{column}=excluded.{column}" for column in METRIC_COLUMNS)
    connection.execute(
        f"INSERT INTO metrics ({','.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT(project_id, asset_uuid) DO UPDATE SET {updates}, "
        "calculated_at=excluded.calculated_at",
        values,
    )


def update_metric_percentiles(
    connection: sqlite3.Connection,
    project_id: str,
    values: dict[str, dict[str, float | None]],
) -> None:
    connection.executemany(
        """
        UPDATE metrics SET sharpness_percentile=?, gradient_percentile=?,
            contrast_percentile=?, apple_overall_percentile=?, technical_quality=?
        WHERE project_id=? AND asset_uuid=?
        """,
        [
            (
                data.get("sharpness_percentile"),
                data.get("gradient_percentile"),
                data.get("contrast_percentile"),
                data.get("apple_overall_percentile"),
                data.get("technical_quality"),
                project_id,
                uuid,
            )
            for uuid, data in values.items()
        ],
    )


def update_vision_metrics(
    connection: sqlite3.Connection,
    project_id: str,
    asset_uuid: str,
    *,
    face_count: int,
    face_capture_quality: float | None,
    eyes_detected: int,
) -> None:
    connection.execute(
        """
        UPDATE metrics SET face_count=?, face_capture_quality=?, eyes_detected=?
        WHERE project_id=? AND asset_uuid=?
        """,
        (face_count, face_capture_quality, eyes_detected, project_id, asset_uuid),
    )


def upsert_analysis_signal(
    connection: sqlite3.Connection,
    project_id: str,
    asset_uuid: str,
    *,
    signal_kind: str,
    schema_version: int,
    engine_name: str,
    engine_version: str,
    request_revision: int | None,
    source_fingerprint: str | None,
    status: str,
    value: dict[str, object] | None,
    duration_ms: float | None,
    error_text: str | None,
) -> None:
    if status not in {"ready", "error", "unavailable"}:
        raise ValueError(f"Некорректный signal status: {status}")
    connection.execute(
        """
        INSERT INTO analysis_signals (
            project_id, asset_uuid, signal_kind, schema_version,
            engine_name, engine_version, request_revision, source_fingerprint,
            status, value_json, duration_ms, error_text, calculated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(project_id, asset_uuid, signal_kind) DO UPDATE SET
            schema_version=excluded.schema_version,
            engine_name=excluded.engine_name,
            engine_version=excluded.engine_version,
            request_revision=excluded.request_revision,
            source_fingerprint=excluded.source_fingerprint,
            status=excluded.status,
            value_json=excluded.value_json,
            duration_ms=excluded.duration_ms,
            error_text=excluded.error_text,
            calculated_at=excluded.calculated_at
        """,
        (
            project_id,
            asset_uuid,
            signal_kind,
            schema_version,
            engine_name,
            engine_version,
            request_revision,
            source_fingerprint,
            status,
            json.dumps(value, sort_keys=True) if value is not None else None,
            duration_ms,
            error_text,
            utc_now(),
        ),
    )


def list_analysis_signals(
    connection: sqlite3.Connection, project_id: str, asset_uuid: str | None = None
) -> list[dict[str, object]]:
    clause = " AND asset_uuid=?" if asset_uuid is not None else ""
    parameters = (project_id, asset_uuid) if asset_uuid is not None else (project_id,)
    rows = connection.execute(
        f"""
        SELECT * FROM analysis_signals
        WHERE project_id=?{clause}
        ORDER BY asset_uuid, signal_kind
        """,
        parameters,
    ).fetchall()
    result = []
    for raw in rows:
        row = dict(raw)
        row["value"] = json.loads(str(row["value_json"])) if row.get("value_json") else None
        result.append(row)
    return result


def analysis_signals_by_asset(
    connection: sqlite3.Connection, project_id: str
) -> dict[str, dict[str, dict[str, object]]]:
    result: dict[str, dict[str, dict[str, object]]] = {}
    for signal in list_analysis_signals(connection, project_id):
        result.setdefault(str(signal["asset_uuid"]), {})[str(signal["signal_kind"])] = signal
    return result


def upsert_swipe_score(
    connection: sqlite3.Connection,
    project_id: str,
    asset_uuid: str,
    *,
    schema_version: int,
    score: float,
    generic_score: float,
    personal_delta: float,
    confidence: float,
    components: dict[str, float],
    reasons: list[dict[str, object]],
    model_versions: dict[str, str],
    source_fingerprint: str | None,
) -> None:
    connection.execute(
        """
        INSERT INTO swipe_scores (
            project_id, asset_uuid, schema_version, score, generic_score,
            personal_delta, confidence, components_json, reasons_json,
            model_versions_json, source_fingerprint, calculated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(project_id, asset_uuid) DO UPDATE SET
            schema_version=excluded.schema_version,
            score=excluded.score,
            generic_score=excluded.generic_score,
            personal_delta=excluded.personal_delta,
            confidence=excluded.confidence,
            components_json=excluded.components_json,
            reasons_json=excluded.reasons_json,
            model_versions_json=excluded.model_versions_json,
            source_fingerprint=excluded.source_fingerprint,
            calculated_at=excluded.calculated_at
        """,
        (
            project_id,
            asset_uuid,
            schema_version,
            score,
            generic_score,
            personal_delta,
            confidence,
            json.dumps(components, sort_keys=True),
            json.dumps(reasons, sort_keys=True),
            json.dumps(model_versions, sort_keys=True),
            source_fingerprint,
            utc_now(),
        ),
    )


def list_swipe_scores(connection: sqlite3.Connection, project_id: str) -> list[dict[str, object]]:
    rows = connection.execute(
        "SELECT * FROM swipe_scores WHERE project_id=? ORDER BY score DESC, asset_uuid",
        (project_id,),
    ).fetchall()
    result = []
    for raw in rows:
        row = dict(raw)
        row["components"] = json.loads(str(row["components_json"]))
        row["reasons"] = json.loads(str(row["reasons_json"]))
        row["model_versions"] = json.loads(str(row["model_versions_json"]))
        result.append(row)
    return result


def ensure_taste_profile(
    connection: sqlite3.Connection, profile_id: str = "default", name: str = "Мой вкус"
) -> dict[str, object]:
    now = utc_now()
    connection.execute(
        """
        INSERT INTO taste_profiles (
            id, name, status, schema_version, created_at, updated_at
        ) VALUES (?, ?, 'collecting', 1, ?, ?)
        ON CONFLICT(id) DO NOTHING
        """,
        (profile_id, name, now, now),
    )
    return get_taste_profile(connection, profile_id)


def get_taste_profile(
    connection: sqlite3.Connection, profile_id: str = "default"
) -> dict[str, object]:
    row = connection.execute("SELECT * FROM taste_profiles WHERE id=?", (profile_id,)).fetchone()
    if not row:
        raise KeyError(profile_id)
    result = dict(row)
    result["evidence"] = json.loads(str(result["evidence_json"]))
    return result


def add_preference_example(
    connection: sqlite3.Connection,
    *,
    profile_id: str,
    project_id: str | None,
    left_uuid: str,
    right_uuid: str,
    preferred_uuid: str,
    split: str,
    feature_schema: str,
    left_feature_base64: str,
    right_feature_base64: str,
) -> str:
    if split not in {"calibration", "held_out"}:
        raise ValueError("split должен быть calibration или held_out")
    if left_uuid == right_uuid or preferred_uuid not in {left_uuid, right_uuid}:
        raise ValueError("Некорректная preference pair")
    ensure_taste_profile(connection, profile_id)
    duplicate = connection.execute(
        """
        SELECT id FROM preference_examples
        WHERE profile_id=? AND ((left_uuid=? AND right_uuid=?) OR
              (left_uuid=? AND right_uuid=?))
        LIMIT 1
        """,
        (profile_id, left_uuid, right_uuid, right_uuid, left_uuid),
    ).fetchone()
    if duplicate:
        raise ValueError("Эта пара уже была оценена")
    example_id = new_id()
    now = utc_now()
    connection.execute(
        """
        INSERT INTO preference_examples (
            id, profile_id, project_id, left_uuid, right_uuid, preferred_uuid,
            split, feature_schema, left_feature_base64, right_feature_base64, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            example_id,
            profile_id,
            project_id,
            left_uuid,
            right_uuid,
            preferred_uuid,
            split,
            feature_schema,
            left_feature_base64,
            right_feature_base64,
            now,
        ),
    )
    connection.execute(
        """
        UPDATE taste_profiles
        SET status=CASE
                WHEN ?='held_out' AND weights_base64 IS NOT NULL THEN status
                WHEN weights_base64 IS NULL THEN 'collecting'
                ELSE 'stale'
            END,
            updated_at=?
        WHERE id=?
        """,
        (split, now, profile_id),
    )
    return example_id


def list_preference_examples(
    connection: sqlite3.Connection, profile_id: str = "default"
) -> list[dict[str, object]]:
    rows = connection.execute(
        """
        SELECT * FROM preference_examples
        WHERE profile_id=? ORDER BY created_at, id
        """,
        (profile_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def add_quality_preference_example(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    left_uuid: str,
    right_uuid: str,
    preferred_uuid: str,
) -> str:
    if left_uuid == right_uuid or preferred_uuid not in {left_uuid, right_uuid}:
        raise ValueError("Некорректная проверочная пара")
    known = assets_by_uuid(connection, project_id, {left_uuid, right_uuid})
    if set(known) != {left_uuid, right_uuid}:
        raise ValueError("Пара содержит фото из другого или удалённого проекта")
    ordered_left, ordered_right = sorted((left_uuid, right_uuid))
    existing = connection.execute(
        """
        SELECT id FROM quality_preference_examples
        WHERE project_id=? AND left_uuid=? AND right_uuid=?
        """,
        (project_id, ordered_left, ordered_right),
    ).fetchone()
    if existing:
        raise ValueError("Эта пара уже была оценена")
    example_id = new_id()
    connection.execute(
        """
        INSERT INTO quality_preference_examples (
            id, project_id, left_uuid, right_uuid, preferred_uuid, split, created_at
        ) VALUES (?, ?, ?, ?, ?, 'held_out', ?)
        """,
        (
            example_id,
            project_id,
            ordered_left,
            ordered_right,
            preferred_uuid,
            utc_now(),
        ),
    )
    return example_id


def list_quality_preference_examples(
    connection: sqlite3.Connection, project_id: str
) -> list[dict[str, object]]:
    rows = connection.execute(
        """
        SELECT * FROM quality_preference_examples
        WHERE project_id=? ORDER BY created_at, id
        """,
        (project_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def delete_incompatible_preference_examples(
    connection: sqlite3.Connection,
    feature_schema: str,
    profile_id: str = "default",
) -> int:
    cursor = connection.execute(
        """
        DELETE FROM preference_examples
        WHERE profile_id=? AND feature_schema<>?
        """,
        (profile_id, feature_schema),
    )
    return int(cursor.rowcount)


def save_taste_model(
    connection: sqlite3.Connection,
    profile_id: str,
    *,
    feature_schema: str,
    model_version: str,
    weights_base64: str,
    dimension: int,
    training_examples: int,
    evidence: dict[str, object],
) -> None:
    cursor = connection.execute(
        """
        UPDATE taste_profiles SET
            status='ready', feature_schema=?, model_version=?, weights_base64=?,
            dimension=?, training_examples=?, evidence_json=?, updated_at=?
        WHERE id=?
        """,
        (
            feature_schema,
            model_version,
            weights_base64,
            dimension,
            training_examples,
            json.dumps(evidence, sort_keys=True),
            utc_now(),
            profile_id,
        ),
    )
    if not cursor.rowcount:
        raise KeyError(profile_id)


def reset_taste_profile(connection: sqlite3.Connection, profile_id: str = "default") -> None:
    connection.execute("DELETE FROM taste_profiles WHERE id=?", (profile_id,))


def list_taste_rounds(
    connection: sqlite3.Connection, profile_id: str = "default"
) -> list[dict[str, object]]:
    rows = connection.execute(
        """
        SELECT * FROM taste_rounds
        WHERE profile_id=? ORDER BY round_index, created_at
        """,
        (profile_id,),
    ).fetchall()
    result = []
    for row in rows:
        value = dict(row)
        selected_json = value.pop("selected_json")
        rejected_json = value.pop("rejected_json")
        value["candidate_uuids"] = json.loads(str(value.pop("candidate_json")))
        value["selected_uuids"] = json.loads(str(selected_json)) if selected_json else []
        value["rejected_uuids"] = json.loads(str(rejected_json)) if rejected_json else []
        result.append(value)
    return result


def create_taste_round(
    connection: sqlite3.Connection,
    *,
    album_id: str,
    album_name: str,
    round_index: int,
    candidate_uuids: list[str],
    profile_id: str = "default",
) -> dict[str, object]:
    if len(candidate_uuids) != 10 or len(set(candidate_uuids)) != 10:
        raise ValueError("Taste round требует 10 уникальных фотографий")
    ensure_taste_profile(connection, profile_id)
    round_id = new_id()
    now = utc_now()
    connection.execute(
        """
        INSERT INTO taste_rounds (
            id, profile_id, album_id, album_name, round_index, status,
            candidate_json, selected_json, rejected_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'pending', ?, NULL, NULL, ?, ?)
        """,
        (
            round_id,
            profile_id,
            album_id,
            album_name,
            round_index,
            json.dumps(candidate_uuids),
            now,
            now,
        ),
    )
    return next(
        item for item in list_taste_rounds(connection, profile_id) if item["id"] == round_id
    )


def discard_pending_taste_round(
    connection: sqlite3.Connection, profile_id: str = "default"
) -> list[str]:
    pending = connection.execute(
        """
        SELECT candidate_json FROM taste_rounds
        WHERE profile_id=? AND status='pending'
        """,
        (profile_id,),
    ).fetchall()
    candidate_uuids = [
        str(asset_uuid) for row in pending for asset_uuid in json.loads(str(row["candidate_json"]))
    ]
    paths: list[str] = []
    if candidate_uuids:
        assets = taste_assets(connection, candidate_uuids, profile_id)
        paths = [str(asset["review_path"]) for asset in assets.values()]
        placeholders = ",".join("?" for _ in candidate_uuids)
        connection.execute(
            f"""
            DELETE FROM taste_assets
            WHERE profile_id=? AND asset_uuid IN ({placeholders})
            """,
            (profile_id, *candidate_uuids),
        )
    connection.execute(
        "DELETE FROM taste_rounds WHERE profile_id=? AND status='pending'",
        (profile_id,),
    )
    return paths


def complete_taste_round(
    connection: sqlite3.Connection,
    round_id: str,
    selected_uuids: list[str],
    rejected_uuids: list[str],
    profile_id: str = "default",
) -> dict[str, object]:
    rounds = list_taste_rounds(connection, profile_id)
    round_value = next((item for item in rounds if item["id"] == round_id), None)
    if not round_value:
        raise KeyError(round_id)
    if round_value["status"] != "pending":
        raise ValueError("Taste round уже завершён")
    candidates = set(round_value["candidate_uuids"])
    if len(selected_uuids) != 3 or len(set(selected_uuids)) != 3:
        raise ValueError("Нужно выбрать ровно 3 фотографии")
    if not set(selected_uuids).issubset(candidates):
        raise ValueError("Выбрана фотография вне текущего раунда")
    if len(rejected_uuids) != 3 or len(set(rejected_uuids)) != 3:
        raise ValueError("Нужно отметить ровно 3 неподходящие фотографии")
    if not set(rejected_uuids).issubset(candidates):
        raise ValueError("Отмечена фотография вне текущего раунда")
    if set(selected_uuids).intersection(rejected_uuids):
        raise ValueError("Фотография не может одновременно нравиться и не нравиться")
    connection.execute(
        """
        UPDATE taste_rounds
        SET status='completed', selected_json=?, rejected_json=?, updated_at=?
        WHERE id=? AND profile_id=?
        """,
        (json.dumps(selected_uuids), json.dumps(rejected_uuids), utc_now(), round_id, profile_id),
    )
    return next(
        item for item in list_taste_rounds(connection, profile_id) if item["id"] == round_id
    )


def upsert_taste_asset(
    connection: sqlite3.Connection,
    *,
    asset_uuid: str,
    album_id: str,
    filename: str | None,
    taken_at: str | None,
    review_path: str,
    feature_schema: str,
    feature_base64: str,
    profile_id: str = "default",
) -> None:
    ensure_taste_profile(connection, profile_id)
    now = utc_now()
    connection.execute(
        """
        INSERT INTO taste_assets (
            profile_id, asset_uuid, album_id, filename, taken_at, review_path,
            feature_schema, feature_base64, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(profile_id, asset_uuid) DO UPDATE SET
            album_id=excluded.album_id,
            filename=excluded.filename,
            taken_at=excluded.taken_at,
            review_path=excluded.review_path,
            feature_schema=excluded.feature_schema,
            feature_base64=excluded.feature_base64,
            updated_at=excluded.updated_at
        """,
        (
            profile_id,
            asset_uuid,
            album_id,
            filename,
            taken_at,
            review_path,
            feature_schema,
            feature_base64,
            now,
            now,
        ),
    )


def taste_assets(
    connection: sqlite3.Connection,
    asset_uuids: list[str],
    profile_id: str = "default",
) -> dict[str, dict[str, object]]:
    if not asset_uuids:
        return {}
    placeholders = ",".join("?" for _ in asset_uuids)
    rows = connection.execute(
        f"""
        SELECT * FROM taste_assets
        WHERE profile_id=? AND asset_uuid IN ({placeholders})
        """,
        (profile_id, *asset_uuids),
    ).fetchall()
    return {str(row["asset_uuid"]): dict(row) for row in rows}


def update_taste_evidence(
    connection: sqlite3.Connection,
    updates: dict[str, object],
    profile_id: str = "default",
) -> dict[str, object]:
    profile = get_taste_profile(connection, profile_id)
    evidence = dict(profile.get("evidence") or {})
    evidence.update(updates)
    connection.execute(
        "UPDATE taste_profiles SET evidence_json=?, updated_at=? WHERE id=?",
        (json.dumps(evidence, sort_keys=True), utc_now(), profile_id),
    )
    return get_taste_profile(connection, profile_id)


def set_taste_profile_paused(
    connection: sqlite3.Connection, paused: bool, profile_id: str = "default"
) -> dict[str, object]:
    profile = ensure_taste_profile(connection, profile_id)
    status = "paused" if paused else "ready" if profile.get("weights_base64") else "collecting"
    connection.execute(
        "UPDATE taste_profiles SET status=?, updated_at=? WHERE id=?",
        (status, utc_now(), profile_id),
    )
    return get_taste_profile(connection, profile_id)


def set_taste_profile_compatibility(
    connection: sqlite3.Connection,
    *,
    compatible: bool,
    observed_feature_schemas: list[str],
    profile_id: str = "default",
) -> dict[str, object]:
    profile = get_taste_profile(connection, profile_id)
    evidence = dict(profile.get("evidence") or {})
    evidence["compatibility"] = {
        "compatible": compatible,
        "profile_feature_schema": profile.get("feature_schema"),
        "observed_feature_schemas": observed_feature_schemas,
    }
    status = str(profile["status"])
    if not compatible:
        status = "incompatible"
    elif status == "incompatible":
        status = "ready"
    connection.execute(
        "UPDATE taste_profiles SET status=?, evidence_json=?, updated_at=? WHERE id=?",
        (status, json.dumps(evidence, sort_keys=True), utc_now(), profile_id),
    )
    return get_taste_profile(connection, profile_id)


def mark_taste_profile_stale(
    connection: sqlite3.Connection,
    *,
    reason: str,
    detail: str,
    profile_id: str = "default",
) -> dict[str, object]:
    profile = get_taste_profile(connection, profile_id)
    evidence = dict(profile.get("evidence") or {})
    evidence["migration"] = {
        "required": True,
        "reason": reason,
        "detail": detail[:500],
    }
    connection.execute(
        "UPDATE taste_profiles SET status='stale', evidence_json=?, updated_at=? WHERE id=?",
        (json.dumps(evidence, sort_keys=True), utc_now(), profile_id),
    )
    return get_taste_profile(connection, profile_id)


def list_assets(connection: sqlite3.Connection, project_id: str) -> list[dict[str, object]]:
    rows = connection.execute(
        """
        SELECT a.*, m.*, d.auto_disposition, d.manual_disposition, d.final_disposition,
            d.auto_selection, d.manual_selection, d.final_selection,
            d.confidence, d.flags_json, d.reasons_json, d.manual_override,
            d.manual_note, d.manual_rating, d.reviewed, s.score AS swipe_score,
            s.generic_score AS swipe_generic_score, s.personal_delta AS swipe_personal_delta,
            s.confidence AS swipe_confidence, s.components_json AS swipe_components_json,
            s.reasons_json AS swipe_reasons_json, s.model_versions_json AS swipe_models_json,
            s.schema_version AS swipe_schema_version,
            q.top_k_rank AS quality_top_k_rank,
            q.duplicate_group AS quality_duplicate_group,
            q.expected_leader AS quality_expected_leader,
            q.defect_codes_json AS quality_defect_codes_json,
            q.defect_severity AS quality_defect_severity,
            q.defect_confidence AS quality_defect_confidence,
            q.quality_note AS quality_note
            , q.lab_sampled AS quality_lab_sampled
        FROM assets a
        LEFT JOIN metrics m USING (project_id, asset_uuid)
        LEFT JOIN decisions d USING (project_id, asset_uuid)
        LEFT JOIN swipe_scores s USING (project_id, asset_uuid)
        LEFT JOIN quality_asset_labels q USING (project_id, asset_uuid)
        WHERE a.project_id = ?
        ORDER BY a.taken_at, a.asset_uuid
        """,
        (project_id,),
    ).fetchall()
    return [_decode_asset_row(dict(row)) for row in rows]


def list_assets_page(
    connection: sqlite3.Connection,
    project_id: str,
    *,
    limit: int,
    offset: int = 0,
    disposition: str | None = None,
    selection: str | None = None,
    cursor_score: float | None = None,
    cursor_asset_uuid: str | None = None,
) -> list[dict[str, object]]:
    return gallery_repository.list_assets_page(
        connection,
        project_id,
        limit=limit,
        decode=_decode_asset_row,
        offset=offset,
        disposition=disposition,
        selection=selection,
        cursor_score=cursor_score,
        cursor_asset_uuid=cursor_asset_uuid,
    )


def count_assets(
    connection: sqlite3.Connection,
    project_id: str,
    *,
    disposition: str | None = None,
    selection: str | None = None,
) -> int:
    return gallery_repository.count_assets(
        connection,
        project_id,
        disposition=disposition,
        selection=selection,
    )


def assets_by_uuid(
    connection: sqlite3.Connection, project_id: str, asset_uuids: set[str]
) -> dict[str, dict[str, object]]:
    if not asset_uuids:
        return {}
    placeholders = ",".join("?" for _ in asset_uuids)
    rows = connection.execute(
        f"""
        SELECT a.*, m.*, d.auto_disposition, d.manual_disposition, d.final_disposition,
            d.auto_selection, d.manual_selection, d.final_selection,
            d.confidence, d.flags_json, d.reasons_json, d.manual_override,
            d.manual_note, d.manual_rating, d.reviewed, s.score AS swipe_score,
            s.generic_score AS swipe_generic_score, s.personal_delta AS swipe_personal_delta,
            s.confidence AS swipe_confidence, s.components_json AS swipe_components_json,
            s.reasons_json AS swipe_reasons_json, s.model_versions_json AS swipe_models_json,
            s.schema_version AS swipe_schema_version,
            q.top_k_rank AS quality_top_k_rank,
            q.duplicate_group AS quality_duplicate_group,
            q.expected_leader AS quality_expected_leader,
            q.defect_codes_json AS quality_defect_codes_json,
            q.defect_severity AS quality_defect_severity,
            q.defect_confidence AS quality_defect_confidence,
            q.quality_note AS quality_note
            , q.lab_sampled AS quality_lab_sampled
        FROM assets a
        LEFT JOIN metrics m USING (project_id, asset_uuid)
        LEFT JOIN decisions d USING (project_id, asset_uuid)
        LEFT JOIN swipe_scores s USING (project_id, asset_uuid)
        LEFT JOIN quality_asset_labels q USING (project_id, asset_uuid)
        WHERE a.project_id=? AND a.asset_uuid IN ({placeholders})
        """,
        (project_id, *sorted(asset_uuids)),
    ).fetchall()
    decoded = [_decode_asset_row(dict(row)) for row in rows]
    return {str(row["asset_uuid"]): row for row in decoded}


def set_quality_top_k(
    connection: sqlite3.Connection,
    project_id: str,
    asset_uuid: str,
    selected: bool,
) -> list[str]:
    get_asset(connection, project_id, asset_uuid)
    now = utc_now()
    if selected:
        row = connection.execute(
            "SELECT COALESCE(MAX(top_k_rank), 0) + 1 FROM quality_asset_labels WHERE project_id=?",
            (project_id,),
        ).fetchone()
        next_rank = int(row[0])
        connection.execute(
            """
            INSERT INTO quality_asset_labels (project_id, asset_uuid, top_k_rank, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(project_id, asset_uuid) DO UPDATE SET
                top_k_rank=excluded.top_k_rank, updated_at=excluded.updated_at
            """,
            (project_id, asset_uuid, next_rank, now),
        )
    else:
        connection.execute(
            """
            UPDATE quality_asset_labels SET top_k_rank=NULL, updated_at=?
            WHERE project_id=? AND asset_uuid=?
            """,
            (now, project_id, asset_uuid),
        )
    rows = connection.execute(
        """
        SELECT asset_uuid FROM quality_asset_labels
        WHERE project_id=? AND top_k_rank IS NOT NULL
        ORDER BY top_k_rank, asset_uuid
        """,
        (project_id,),
    ).fetchall()
    ordered = [str(row["asset_uuid"]) for row in rows]
    for rank, current_uuid in enumerate(ordered, start=1):
        connection.execute(
            """
            UPDATE quality_asset_labels SET top_k_rank=?, updated_at=?
            WHERE project_id=? AND asset_uuid=?
            """,
            (rank, now, project_id, current_uuid),
        )
    return ordered


QUALITY_DEFECT_CODES = {
    "motion_blur",
    "defocus_blur",
    "underexposed",
    "overexposed",
    "low_contrast",
    "poor_face_capture",
    "extreme_horizon",
    "bad_angle",
    "blocked_subject",
    "exact_duplicate",
    "near_duplicate",
    "other",
}


def set_quality_label(
    connection: sqlite3.Connection,
    project_id: str,
    asset_uuid: str,
    *,
    disposition: str,
    defect_codes: list[str],
    defect_severity: int | None,
    defect_confidence: float | None,
    note: str | None,
) -> None:
    if disposition not in {"keep", "review", "reject"}:
        raise ValueError("Некорректное ручное решение")
    codes = sorted(set(defect_codes))
    if any(code not in QUALITY_DEFECT_CODES for code in codes):
        raise ValueError("Неизвестный тип дефекта")
    if disposition == "reject" and not codes:
        raise ValueError("Для отклонённого фото укажите хотя бы один дефект")
    if disposition != "reject":
        codes = []
        defect_severity = None
        defect_confidence = None
    if defect_severity is not None and defect_severity not in {1, 2, 3}:
        raise ValueError("Тяжесть дефекта должна быть от 1 до 3")
    if defect_confidence is not None and not 0 <= defect_confidence <= 1:
        raise ValueError("Уверенность должна быть от 0 до 1")
    set_manual_decision(connection, project_id, asset_uuid, disposition, note)
    connection.execute(
        """
        INSERT INTO quality_asset_labels (
            project_id, asset_uuid, defect_codes_json, defect_severity,
            defect_confidence, quality_note, lab_sampled, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 1, ?)
        ON CONFLICT(project_id, asset_uuid) DO UPDATE SET
            defect_codes_json=excluded.defect_codes_json,
            defect_severity=excluded.defect_severity,
            defect_confidence=excluded.defect_confidence,
            quality_note=excluded.quality_note,
            lab_sampled=1,
            updated_at=excluded.updated_at
        """,
        (
            project_id,
            asset_uuid,
            json.dumps(codes, sort_keys=True),
            defect_severity,
            defect_confidence,
            note.strip() if note and note.strip() else None,
            utc_now(),
        ),
    )


def label_quality_duplicate_group(
    connection: sqlite3.Connection,
    project_id: str,
    predicted_group_id: str,
    leader_uuid: str,
) -> dict[str, object]:
    row = connection.execute(
        """
        SELECT 1 FROM duplicate_members
        WHERE project_id=? AND group_id=? AND asset_uuid=?
        """,
        (project_id, predicted_group_id, leader_uuid),
    ).fetchone()
    if not row:
        raise ValueError("Выбранный лидер не входит в эту серию")
    members = [
        str(item["asset_uuid"])
        for item in connection.execute(
            """
            SELECT asset_uuid FROM duplicate_members
            WHERE project_id=? AND group_id=? ORDER BY asset_uuid
            """,
            (project_id, predicted_group_id),
        ).fetchall()
    ]
    return label_quality_custom_group(connection, project_id, members, leader_uuid)


def label_quality_custom_group(
    connection: sqlite3.Connection,
    project_id: str,
    member_uuids: list[str],
    leader_uuid: str,
) -> dict[str, object]:
    members = sorted(set(member_uuids))
    if len(members) < 2:
        raise ValueError("Серия должна содержать не менее двух разных фото")
    if leader_uuid not in members:
        raise ValueError("Выбранный лидер не входит в эту серию")
    known = {
        str(row["asset_uuid"])
        for row in connection.execute(
            """
            SELECT asset_uuid FROM assets
            WHERE project_id=? AND asset_uuid IN ({})
            """.format(",".join("?" for _ in members)),
            (project_id, *members),
        ).fetchall()
    }
    if known != set(members):
        raise ValueError("Серия содержит фото из другого или удалённого проекта")
    digest = hashlib.sha256("\n".join(members).encode()).hexdigest()[:16]
    human_group = f"human-{digest}"
    now = utc_now()
    previous_groups = [
        str(row["duplicate_group"])
        for row in connection.execute(
            """
            SELECT DISTINCT duplicate_group FROM quality_asset_labels
            WHERE project_id=? AND asset_uuid IN ({}) AND duplicate_group IS NOT NULL
            """.format(",".join("?" for _ in members)),
            (project_id, *members),
        ).fetchall()
    ]
    for previous_group in previous_groups:
        connection.execute(
            """
            UPDATE quality_asset_labels
            SET duplicate_group=NULL, expected_leader=0, updated_at=?
            WHERE project_id=? AND duplicate_group=?
            """,
            (now, project_id, previous_group),
        )
    for member_uuid in members:
        connection.execute(
            """
            INSERT INTO quality_asset_labels (
                project_id, asset_uuid, duplicate_group, expected_leader, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(project_id, asset_uuid) DO UPDATE SET
                duplicate_group=excluded.duplicate_group,
                expected_leader=excluded.expected_leader,
                updated_at=excluded.updated_at
            """,
            (project_id, member_uuid, human_group, int(member_uuid == leader_uuid), now),
        )
    return {"duplicate_group": human_group, "leader_uuid": leader_uuid, "members": members}


def get_asset(
    connection: sqlite3.Connection, project_id: str, asset_uuid: str
) -> dict[str, object]:
    assets = assets_by_uuid(connection, project_id, {asset_uuid})
    if asset_uuid not in assets:
        raise KeyError(asset_uuid)
    return assets[asset_uuid]


def replace_duplicate_groups(connection: sqlite3.Connection, project_id: str, groups: list) -> None:
    connection.execute("DELETE FROM duplicate_groups WHERE project_id = ?", (project_id,))
    now = utc_now()
    for group in groups:
        connection.execute(
            """
            INSERT INTO duplicate_groups (
                project_id, group_id, kind, confidence, leader_uuid, member_count,
                flags_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                group.group_id,
                group.kind,
                group.confidence,
                group.leader_uuid,
                len(group.members),
                json.dumps(group.flags),
                now,
                now,
            ),
        )
        connection.executemany(
            """
            INSERT INTO duplicate_members (
                project_id, group_id, asset_uuid, is_leader, similarity,
                quality_score, resolution_ratio, evidence_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    project_id,
                    group.group_id,
                    member.asset_uuid,
                    int(member.is_leader),
                    member.similarity,
                    member.quality_score,
                    member.resolution_ratio,
                    json.dumps(member.evidence),
                )
                for member in group.members
            ],
        )


def update_duplicate_group_leader(
    connection: sqlite3.Connection,
    project_id: str,
    group_id: str,
    leader_uuid: str,
) -> None:
    member = connection.execute(
        "SELECT 1 FROM duplicate_members WHERE project_id=? AND group_id=? AND asset_uuid=?",
        (project_id, group_id, leader_uuid),
    ).fetchone()
    if not member:
        raise KeyError((project_id, group_id, leader_uuid))
    connection.execute(
        "UPDATE duplicate_members SET is_leader=(asset_uuid=?) WHERE project_id=? AND group_id=?",
        (leader_uuid, project_id, group_id),
    )
    connection.execute(
        "UPDATE duplicate_groups SET leader_uuid=?, updated_at=? WHERE project_id=? AND group_id=?",
        (leader_uuid, utc_now(), project_id, group_id),
    )


def duplicate_context(
    connection: sqlite3.Connection,
    project_id: str,
    asset_uuids: set[str] | None = None,
) -> dict[str, dict[str, object]]:
    clauses = ["g.project_id = ?"]
    parameters: list[object] = [project_id]
    if asset_uuids is not None:
        if not asset_uuids:
            return {}
        clauses.append("m.asset_uuid IN ({})".format(",".join("?" for _ in asset_uuids)))
        parameters.extend(sorted(asset_uuids))
    rows = connection.execute(
        f"""
        SELECT g.group_id, g.kind, g.confidence, g.flags_json,
            m.asset_uuid, m.is_leader, m.resolution_ratio, m.similarity, m.evidence_json,
            m.quality_score,
            g.leader_uuid,
            leader.quality_score AS leader_quality,
            (
                SELECT COUNT(*) FROM duplicate_members counted
                WHERE counted.project_id=g.project_id AND counted.group_id=g.group_id
            ) AS member_count
        FROM duplicate_groups g
        JOIN duplicate_members m USING (project_id, group_id)
        LEFT JOIN duplicate_members leader
            ON leader.project_id=g.project_id
            AND leader.group_id=g.group_id
            AND leader.asset_uuid=g.leader_uuid
        WHERE {" AND ".join(clauses)}
        """,
        parameters,
    ).fetchall()
    result = {}
    for row in rows:
        evidence = json.loads(row["evidence_json"] or "{}")
        flags = json.loads(row["flags_json"])
        if not row["is_leader"]:
            flags.append("exact_duplicate" if evidence.get("exact") else "near_duplicate")
        if float(row["resolution_ratio"] or 1.0) < 0.75:
            flags.append("lower_resolution_copy")
        result[str(row["asset_uuid"])] = {
            "group_id": row["group_id"],
            "kind": "exact" if evidence.get("exact") else row["kind"],
            "confidence": row["similarity"] if not row["is_leader"] else row["confidence"],
            "flags": sorted(set(flags)),
            "is_leader": bool(row["is_leader"]),
            "leader_uuid": row["leader_uuid"],
            "resolution_ratio": row["resolution_ratio"],
            "quality_margin": float(row["leader_quality"] or 0) - float(row["quality_score"] or 0),
            "time_delta_seconds": evidence.get("time_delta"),
            "pair_evidence": evidence,
            "recommended_pick": bool(evidence.get("recommended_pick")),
            "member_count": int(row["member_count"] or 0),
        }
    return result


def list_duplicate_groups(
    connection: sqlite3.Connection, project_id: str
) -> list[dict[str, object]]:
    groups = []
    for group_row in connection.execute(
        "SELECT * FROM duplicate_groups WHERE project_id=? ORDER BY group_id", (project_id,)
    ).fetchall():
        group = dict(group_row)
        group["flags"] = json.loads(group.pop("flags_json"))
        group["members"] = [
            dict(row)
            for row in connection.execute(
                """
                SELECT m.*, a.width, a.height, a.favorite, a.has_adjustments,
                    a.thumbnail_path, d.final_disposition, x.sharpness_percentile
                FROM duplicate_members m
                JOIN assets a USING (project_id, asset_uuid)
                LEFT JOIN decisions d USING (project_id, asset_uuid)
                LEFT JOIN metrics x USING (project_id, asset_uuid)
                WHERE m.project_id=? AND m.group_id=? ORDER BY m.is_leader DESC, m.asset_uuid
                """,
                (project_id, group["group_id"]),
            ).fetchall()
        ]
        groups.append(group)
    return groups


def upsert_decision(
    connection: sqlite3.Connection,
    project_id: str,
    asset_uuid: str,
    *,
    disposition: str,
    selection: str,
    confidence: float,
    flags: list[str],
    reasons: list[dict[str, object]],
) -> None:
    if disposition not in {"keep", "review", "reject"}:
        raise ValueError("Invalid disposition")
    if selection not in {"pick", "alternative", "review", "reject"}:
        raise ValueError("Invalid selection")
    connection.execute(
        """
        INSERT INTO decisions (
            project_id, asset_uuid, auto_disposition, final_disposition,
            auto_selection, final_selection, confidence, flags_json, reasons_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(project_id, asset_uuid) DO UPDATE SET
            auto_disposition=excluded.auto_disposition,
            final_disposition=CASE
                WHEN decisions.manual_override=1 THEN decisions.manual_disposition
                ELSE excluded.auto_disposition END,
            auto_selection=excluded.auto_selection,
            final_selection=CASE
                WHEN decisions.manual_override=1 THEN decisions.manual_selection
                ELSE excluded.auto_selection END,
            confidence=excluded.confidence,
            flags_json=excluded.flags_json,
            reasons_json=excluded.reasons_json,
            updated_at=excluded.updated_at
        """,
        (
            project_id,
            asset_uuid,
            disposition,
            disposition,
            selection,
            selection,
            confidence,
            json.dumps(flags),
            json.dumps(reasons),
            utc_now(),
        ),
    )


def set_manual_decision(
    connection: sqlite3.Connection,
    project_id: str,
    asset_uuid: str,
    disposition: str | None,
    note: str | None = None,
) -> None:
    if disposition not in {None, "keep", "review", "reject"}:
        raise ValueError("Invalid disposition")
    selection = {"keep": "pick", "review": "review", "reject": "reject"}.get(disposition)
    cursor = connection.execute(
        """
        UPDATE decisions SET manual_disposition=?,
            final_disposition=COALESCE(?, auto_disposition),
            manual_selection=?, final_selection=COALESCE(?, auto_selection),
            manual_override=?, manual_note=?, reviewed=1, updated_at=?
        WHERE project_id=? AND asset_uuid=?
        """,
        (
            disposition,
            disposition,
            selection,
            selection,
            int(disposition is not None),
            note,
            utc_now(),
            project_id,
            asset_uuid,
        ),
    )
    if not cursor.rowcount:
        raise KeyError(asset_uuid)


def set_manual_rating(
    connection: sqlite3.Connection,
    project_id: str,
    asset_uuid: str,
    rating: int | None,
) -> None:
    if rating is not None and (isinstance(rating, bool) or not 1 <= rating <= 5):
        raise ValueError("Rating must be between 1 and 5")
    cursor = connection.execute(
        """
        UPDATE decisions SET manual_rating=?, reviewed=1, updated_at=?
        WHERE project_id=? AND asset_uuid=?
        """,
        (rating, utc_now(), project_id, asset_uuid),
    )
    if not cursor.rowcount:
        raise KeyError(asset_uuid)


def resolve_legacy_review_decisions(
    connection: sqlite3.Connection,
    project_id: str,
) -> int:
    """Convert stored three-way decisions to the current binary workflow."""
    project = get_project(connection, project_id)
    density = str(project.get("selection_density") or "balanced")
    rows = connection.execute(
        """
        SELECT d.asset_uuid, d.auto_disposition, d.manual_disposition,
            d.final_disposition, d.flags_json, s.score
        FROM decisions d
        LEFT JOIN swipe_scores s USING (project_id, asset_uuid)
        WHERE d.project_id=?
          AND (
            d.auto_disposition='review'
            OR d.manual_disposition='review'
            OR d.final_disposition='review'
          )
        """,
        (project_id,),
    ).fetchall()
    now = utc_now()
    for row in rows:
        flags = json.loads(row["flags_json"] or "[]")
        resolved = binary_disposition(int(row["score"] or 0), density, flags)
        automatic = (
            resolved if row["auto_disposition"] == "review" else str(row["auto_disposition"])
        )
        manual = None if row["manual_disposition"] == "review" else row["manual_disposition"]
        override = manual in {"keep", "reject"}
        final = str(manual) if override else automatic
        connection.execute(
            """
            UPDATE decisions
            SET auto_disposition=?, manual_disposition=?, final_disposition=?,
                manual_override=?, reviewed=CASE WHEN ? THEN reviewed ELSE 0 END,
                updated_at=?
            WHERE project_id=? AND asset_uuid=?
            """,
            (
                automatic,
                manual,
                final,
                int(override),
                int(override),
                now,
                project_id,
                row["asset_uuid"],
            ),
        )
    return len(rows)


def mark_best_candidates(
    connection: sqlite3.Connection, project_id: str, asset_uuids: set[str]
) -> None:
    rows = connection.execute(
        "SELECT asset_uuid, flags_json FROM decisions WHERE project_id=?", (project_id,)
    ).fetchall()
    for row in rows:
        flags = set(json.loads(row["flags_json"] or "[]"))
        flags.discard("best_candidate")
        if row["asset_uuid"] in asset_uuids:
            flags.add("best_candidate")
        connection.execute(
            "UPDATE decisions SET flags_json=?, updated_at=? WHERE project_id=? AND asset_uuid=?",
            (json.dumps(sorted(flags)), utc_now(), project_id, row["asset_uuid"]),
        )


def set_group_leader(
    connection: sqlite3.Connection, project_id: str, group_id: str, asset_uuid: str
) -> None:
    exists = connection.execute(
        """
        SELECT 1 FROM duplicate_members WHERE project_id=? AND group_id=? AND asset_uuid=?
        """,
        (project_id, group_id, asset_uuid),
    ).fetchone()
    if not exists:
        raise KeyError(asset_uuid)
    connection.execute(
        "UPDATE duplicate_members SET is_leader=(asset_uuid=?) WHERE project_id=? AND group_id=?",
        (asset_uuid, project_id, group_id),
    )
    connection.execute(
        """
        UPDATE duplicate_groups SET leader_uuid=?, leader_is_manual=1, updated_at=?
        WHERE project_id=? AND group_id=?
        """,
        (asset_uuid, utc_now(), project_id, group_id),
    )


def create_job(connection: sqlite3.Connection, project_id: str, stage: str, total: int) -> str:
    job_id = new_id()
    connection.execute(
        """
        INSERT INTO jobs (id, project_id, stage, status, total_items, started_at)
        VALUES (?, ?, ?, 'running', ?, ?)
        """,
        (job_id, project_id, stage, total, utc_now()),
    )
    return job_id


def update_job(
    connection: sqlite3.Connection,
    job_id: str,
    *,
    status: str | None = None,
    processed: int | None = None,
    warnings: int | None = None,
    errors: int | None = None,
    message: str | None = None,
    error_text: str | None = None,
) -> None:
    assignments, values = [], []
    for column, value in (
        ("status", status),
        ("processed_items", processed),
        ("warning_count", warnings),
        ("error_count", errors),
        ("current_message", message),
        ("error_text", error_text),
    ):
        if value is not None:
            assignments.append(f"{column}=?")
            values.append(value)
    if status in {"done", "warning", "error", "cancelled"}:
        assignments.append("finished_at=?")
        values.append(utc_now())
    values.append(job_id)
    connection.execute(f"UPDATE jobs SET {','.join(assignments)} WHERE id=?", values)


def get_job(connection: sqlite3.Connection, job_id: str) -> dict[str, object]:
    row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        raise KeyError(job_id)
    return dict(row)


def latest_jobs(connection: sqlite3.Connection, project_id: str) -> list[dict[str, object]]:
    rows = connection.execute(
        "SELECT * FROM jobs WHERE project_id=? ORDER BY started_at, id", (project_id,)
    ).fetchall()
    latest_by_stage: dict[str, dict[str, object]] = {}
    for row in rows:
        job = dict(row)
        latest_by_stage[str(job["stage"])] = job
    return sorted(
        latest_by_stage.values(),
        key=lambda job: (str(job["started_at"]), str(job["id"])),
    )


def project_summary(connection: sqlite3.Connection, project_id: str) -> dict[str, int]:
    result = {
        "total": 0,
        "ready": 0,
        "missing": 0,
        "keep": 0,
        "review": 0,
        "reject": 0,
        "reviewed": 0,
        "pick": 0,
        "alternative": 0,
        "selection_review": 0,
        "selection_reject": 0,
    }
    row = connection.execute(
        """
        SELECT COUNT(*) total,
            SUM(cache_state='ready') ready,
            SUM(cache_state!='ready') missing
        FROM assets WHERE project_id=?
        """,
        (project_id,),
    ).fetchone()
    if row:
        result.update({key: int(row[key] or 0) for key in ("total", "ready", "missing")})
    for row in connection.execute(
        """
        SELECT final_disposition disposition, COUNT(*) count
        FROM decisions WHERE project_id=? GROUP BY final_disposition
        """,
        (project_id,),
    ).fetchall():
        result[str(row["disposition"])] = int(row["count"])
    for row in connection.execute(
        """
        SELECT final_selection selection, COUNT(*) count
        FROM decisions WHERE project_id=? GROUP BY final_selection
        """,
        (project_id,),
    ).fetchall():
        key = str(row["selection"])
        result[key if key in {"pick", "alternative"} else f"selection_{key}"] = int(row["count"])
    result["reviewed"] = int(
        connection.execute(
            "SELECT COUNT(*) FROM decisions WHERE project_id=? AND reviewed=1", (project_id,)
        ).fetchone()[0]
    )
    result["duplicate_groups"] = int(
        connection.execute(
            "SELECT COUNT(*) FROM duplicate_groups WHERE project_id=?", (project_id,)
        ).fetchone()[0]
    )
    for kind, count in connection.execute(
        "SELECT kind, COUNT(*) FROM duplicate_groups WHERE project_id=? GROUP BY kind",
        (project_id,),
    ).fetchall():
        result[f"{kind}_groups"] = int(count)
    for engine, status, count in connection.execute(
        """
        SELECT engine_name, status, COUNT(DISTINCT asset_uuid)
        FROM analysis_signals
        WHERE project_id=?
          AND engine_name IN ('apple-vision-native', 'codex-cli-chatgpt')
        GROUP BY engine_name, status
        """,
        (project_id,),
    ).fetchall():
        prefix = "apple_vision" if engine == "apple-vision-native" else "codex"
        result[f"{prefix}_{status}"] = int(count)
    counts = connection.execute(
        """
        SELECT SUM(a.favorite), SUM(a.has_adjustments),
            SUM(d.flags_json LIKE '%best_candidate%'),
            SUM(d.flags_json LIKE '%resolution%')
        FROM assets a LEFT JOIN decisions d USING(project_id, asset_uuid)
        WHERE a.project_id=?
        """,
        (project_id,),
    ).fetchone()
    result.update(
        {
            "favorites": int(counts[0] or 0),
            "edited": int(counts[1] or 0),
            "best": int(counts[2] or 0),
            "resolution_warnings": int(counts[3] or 0),
        }
    )
    settings_row = connection.execute(
        "SELECT settings_json FROM projects WHERE id=?", (project_id,)
    ).fetchone()
    settings = json.loads(settings_row[0] or "{}") if settings_row else {}
    result["videos_skipped"] = int(settings.get("video_count") or 0)
    return result


def publish_summary(connection: sqlite3.Connection, project_id: str) -> dict[str, int]:
    row = connection.execute(
        """
        SELECT
            SUM(d.auto_disposition='reject') auto_reject,
            SUM(d.final_disposition='reject' AND d.manual_override=1) manual_reject,
            SUM(
                d.final_disposition='reject' AND d.flags_json LIKE '%duplicate_loser%'
            ) duplicate_losers,
            SUM(d.final_disposition='reject' AND a.favorite=1) favorites,
            SUM(d.final_disposition='reject' AND a.has_adjustments=1) edited,
            SUM(d.final_disposition='reject' AND d.flags_json LIKE '%resolution%') resolution,
            SUM(d.final_disposition='reject' AND a.cache_state!='ready') missing,
            SUM(d.final_disposition='reject' AND d.reviewed=0) unreviewed
        FROM assets a LEFT JOIN decisions d USING(project_id, asset_uuid)
        WHERE a.project_id=?
        """,
        (project_id,),
    ).fetchone()
    return {
        key: int(row[key] or 0)
        for key in (
            "auto_reject",
            "manual_reject",
            "duplicate_losers",
            "favorites",
            "edited",
            "resolution",
            "missing",
            "unreviewed",
        )
    }


def create_publish(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    album_name: str,
    asset_count: int,
    uuid_file: str,
    kind: str = "reject",
) -> str:
    publish_id = new_id()
    connection.execute(
        """
        INSERT INTO publishes (
            id, project_id, album_name, asset_count, uuid_file, kind, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'prepared', ?)
        """,
        (publish_id, project_id, album_name, asset_count, uuid_file, kind, utc_now()),
    )
    return publish_id


def record_dry_run(
    connection: sqlite3.Connection,
    publish_id: str,
    *,
    stdout: str,
    stderr: str,
    return_code: int,
) -> None:
    connection.execute(
        """
        UPDATE publishes SET dry_run_stdout=?, dry_run_stderr=?, dry_run_return_code=?, status=?
        WHERE id=?
        """,
        (
            stdout,
            stderr,
            return_code,
            "dry_run_ok" if return_code == 0 else "dry_run_failed",
            publish_id,
        ),
    )


def record_apply(
    connection: sqlite3.Connection,
    publish_id: str,
    *,
    stdout: str,
    stderr: str,
    return_code: int,
    destination_album_id: str | None = None,
) -> None:
    connection.execute(
        """
        UPDATE publishes SET apply_stdout=?, apply_stderr=?, apply_return_code=?,
            destination_album_id=COALESCE(?, destination_album_id),
            status=?, applied_at=? WHERE id=?
        """,
        (
            stdout,
            stderr,
            return_code,
            destination_album_id,
            "applied" if return_code == 0 else "apply_failed",
            utc_now() if return_code == 0 else None,
            publish_id,
        ),
    )


def get_publish(connection: sqlite3.Connection, publish_id: str) -> dict[str, object]:
    row = connection.execute("SELECT * FROM publishes WHERE id=?", (publish_id,)).fetchone()
    if not row:
        raise KeyError(publish_id)
    return dict(row)


def latest_publish(
    connection: sqlite3.Connection, project_id: str, kind: str | None = None
) -> dict[str, object] | None:
    kind_clause = " AND kind=?" if kind else ""
    parameters = (project_id, kind) if kind else (project_id,)
    row = connection.execute(
        f"SELECT * FROM publishes WHERE project_id=?{kind_clause} ORDER BY created_at DESC LIMIT 1",
        parameters,
    ).fetchone()
    return dict(row) if row else None


def _decode_asset_row(row: dict[str, object]) -> dict[str, object]:
    for source, target, default in (
        ("metadata_json", "metadata", {}),
        ("apple_scores_json", "apple_scores", None),
        ("histogram_json", "histogram", []),
        ("flags_json", "flags", []),
        ("reasons_json", "reasons", []),
    ):
        raw = row.get(source)
        row[target] = json.loads(str(raw)) if raw else default
    score_reason = next(
        (reason for reason in row["reasons"] if reason.get("code") == "selection_score"), {}
    )
    row["selection_score"] = score_reason.get("score")
    row["score_components"] = score_reason.get("components", {})
    row["swipe_components"] = (
        json.loads(str(row["swipe_components_json"])) if row.get("swipe_components_json") else {}
    )
    row["swipe_reasons"] = (
        json.loads(str(row["swipe_reasons_json"])) if row.get("swipe_reasons_json") else []
    )
    row["swipe_model_versions"] = (
        json.loads(str(row["swipe_models_json"])) if row.get("swipe_models_json") else {}
    )
    return row
