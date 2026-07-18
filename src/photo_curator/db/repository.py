from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable

from photo_curator.photos.provider import PhotoAlbum, PhotoAsset, PhotoLibrary
from photo_curator.utils.identifiers import new_id
from photo_curator.utils.timestamps import utc_now


def mark_running_jobs_interrupted(connection: sqlite3.Connection) -> int:
    cursor = connection.execute(
        """
        UPDATE jobs
        SET status = 'interrupted', current_message = 'Приложение было перезапущено'
        WHERE status = 'running'
        """
    )
    return cursor.rowcount


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
) -> str:
    project_id = project_id or new_id()
    now = utc_now()
    connection.execute(
        """
        INSERT INTO projects (
            id, name, library_path, database_path, library_fingerprint,
            album_id, album_name, album_folder_path, album_full_path,
            state, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'created', ?, ?)
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
    connection.execute("DELETE FROM projects WHERE id = ?", (project_id,))


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
                asset.burst_key,
                int(asset.burst_default_pick),
                int(asset.is_missing),
                str(asset.source_path) if asset.source_path else None,
                json.dumps(asset.apple_scores) if asset.apple_scores else None,
                now,
                now,
            )
        )
    connection.executemany(
        """
        INSERT INTO assets (
            project_id, asset_uuid, original_filename, current_filename, taken_at,
            date_added, width, height, original_width, original_height, orientation,
            favorite, hidden, has_adjustments, is_live_photo, is_burst, burst_key,
            burst_default_pick, is_missing, source_path, apple_scores_json, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
            source_path=excluded.source_path,
            apple_scores_json=excluded.apple_scores_json,
            updated_at=excluded.updated_at
        """,
        rows,
    )
    return len(rows)


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
) -> None:
    connection.execute(
        """
        UPDATE assets SET source_kind=?, source_size=?, source_mtime=?, source_fingerprint=?,
            review_path=?, thumbnail_path=?, cache_state=?, updated_at=?
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
            utc_now(),
            project_id,
            asset_uuid,
        ),
    )


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
            contrast_percentile=?, technical_quality=?
        WHERE project_id=? AND asset_uuid=?
        """,
        [
            (
                data.get("sharpness_percentile"),
                data.get("gradient_percentile"),
                data.get("contrast_percentile"),
                data.get("technical_quality"),
                project_id,
                uuid,
            )
            for uuid, data in values.items()
        ],
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
            m.asset_uuid, m.is_leader, m.resolution_ratio, m.similarity, m.evidence_json
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
                    a.thumbnail_path, d.final_disposition
                FROM duplicate_members m
                JOIN assets a USING (project_id, asset_uuid)
                LEFT JOIN decisions d USING (project_id, asset_uuid)
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
    connection.execute(
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
    result = {"total": 0, "ready": 0, "missing": 0, "keep": 0, "review": 0, "reject": 0}
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
    return result


def create_publish(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    album_name: str,
    asset_count: int,
    uuid_file: str,
) -> str:
    publish_id = new_id()
    connection.execute(
        """
        INSERT INTO publishes (
            id, project_id, album_name, asset_count, uuid_file, status, created_at
        ) VALUES (?, ?, ?, ?, ?, 'prepared', ?)
        """,
        (publish_id, project_id, album_name, asset_count, uuid_file, utc_now()),
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


def latest_publish(connection: sqlite3.Connection, project_id: str) -> dict[str, object] | None:
    row = connection.execute(
        "SELECT * FROM publishes WHERE project_id=? ORDER BY created_at DESC LIMIT 1",
        (project_id,),
    ).fetchone()
    return dict(row) if row else None


def _decode_asset_row(row: dict[str, object]) -> dict[str, object]:
    for source, target, default in (
        ("histogram_json", "histogram", []),
        ("flags_json", "flags", []),
        ("reasons_json", "reasons", []),
    ):
        raw = row.get(source)
        row[target] = json.loads(str(raw)) if raw else default
    return row
