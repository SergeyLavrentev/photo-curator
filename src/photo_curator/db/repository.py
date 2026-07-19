from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable

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
) -> str:
    project_id = project_id or new_id()
    now = utc_now()
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
                    "selection_density": selection_density,
                    "source_provenance": source_provenance,
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


def list_assets(connection: sqlite3.Connection, project_id: str) -> list[dict[str, object]]:
    rows = connection.execute(
        """
        SELECT a.*, m.*, d.auto_disposition, d.manual_disposition, d.final_disposition,
            d.confidence, d.flags_json, d.reasons_json, d.manual_override,
            d.manual_note, d.reviewed
        FROM assets a
        LEFT JOIN metrics m USING (project_id, asset_uuid)
        LEFT JOIN decisions d USING (project_id, asset_uuid)
        WHERE a.project_id = ?
        ORDER BY a.taken_at, a.asset_uuid
        """,
        (project_id,),
    ).fetchall()
    return [_decode_asset_row(dict(row)) for row in rows]


def get_asset(
    connection: sqlite3.Connection, project_id: str, asset_uuid: str
) -> dict[str, object]:
    assets = [
        asset for asset in list_assets(connection, project_id) if asset["asset_uuid"] == asset_uuid
    ]
    if not assets:
        raise KeyError(asset_uuid)
    return assets[0]


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


def duplicate_context(
    connection: sqlite3.Connection, project_id: str
) -> dict[str, dict[str, object]]:
    rows = connection.execute(
        """
        SELECT g.group_id, g.kind, g.confidence, g.flags_json,
            m.asset_uuid, m.is_leader, m.resolution_ratio, m.similarity, m.evidence_json,
            m.quality_score,
            MAX(CASE WHEN m.is_leader=1 THEN m.quality_score END)
                OVER (PARTITION BY m.project_id, m.group_id) leader_quality
        FROM duplicate_groups g
        JOIN duplicate_members m USING (project_id, group_id)
        WHERE g.project_id = ?
        """,
        (project_id,),
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
            "resolution_ratio": row["resolution_ratio"],
            "quality_margin": float(row["leader_quality"] or 0) - float(row["quality_score"] or 0),
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
    confidence: float,
    flags: list[str],
    reasons: list[dict[str, object]],
) -> None:
    connection.execute(
        """
        INSERT INTO decisions (
            project_id, asset_uuid, auto_disposition, final_disposition, confidence,
            flags_json, reasons_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(project_id, asset_uuid) DO UPDATE SET
            auto_disposition=excluded.auto_disposition,
            final_disposition=CASE
                WHEN decisions.manual_override=1 THEN decisions.manual_disposition
                ELSE excluded.auto_disposition END,
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
    cursor = connection.execute(
        """
        UPDATE decisions SET manual_disposition=?,
            final_disposition=COALESCE(?, auto_disposition),
            manual_override=?, manual_note=?, reviewed=1, updated_at=?
        WHERE project_id=? AND asset_uuid=?
        """,
        (
            disposition,
            disposition,
            int(disposition is not None),
            note,
            utc_now(),
            project_id,
            asset_uuid,
        ),
    )
    if not cursor.rowcount:
        raise KeyError(asset_uuid)


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
        "SELECT * FROM jobs WHERE project_id=? ORDER BY started_at", (project_id,)
    ).fetchall()
    return [dict(row) for row in rows]


def project_summary(connection: sqlite3.Connection, project_id: str) -> dict[str, int]:
    result = {
        "total": 0,
        "ready": 0,
        "missing": 0,
        "keep": 0,
        "review": 0,
        "reject": 0,
        "reviewed": 0,
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
) -> None:
    connection.execute(
        """
        UPDATE publishes SET apply_stdout=?, apply_stderr=?, apply_return_code=?,
            status=?, applied_at=? WHERE id=?
        """,
        (
            stdout,
            stderr,
            return_code,
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
    return row
