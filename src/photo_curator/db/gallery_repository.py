from __future__ import annotations

import sqlite3
from collections.abc import Callable

AssetDecoder = Callable[[dict[str, object]], dict[str, object]]


def list_assets_page(
    connection: sqlite3.Connection,
    project_id: str,
    *,
    limit: int,
    decode: AssetDecoder,
    offset: int = 0,
    disposition: str | None = None,
    selection: str | None = None,
    cursor_score: float | None = None,
    cursor_asset_uuid: str | None = None,
) -> list[dict[str, object]]:
    if disposition is not None or selection is not None:
        return _list_ranked_decision_page(
            connection,
            project_id,
            limit=limit,
            decode=decode,
            offset=offset,
            disposition=disposition,
            selection=selection,
            cursor_score=cursor_score,
            cursor_asset_uuid=cursor_asset_uuid,
        )

    clauses = ["a.project_id = ?", "a.no_longer_exists = 0", "a.media_type = 'image'"]
    parameters: list[object] = [project_id]
    if disposition is not None:
        clauses.append("d.final_disposition = ?")
        parameters.append(disposition)
    if selection is not None:
        clauses.append("d.final_selection = ?")
        parameters.append(selection)
    if cursor_score is not None and cursor_asset_uuid is not None:
        clauses.append(
            "(COALESCE(s.score, -1.0) < ? OR (COALESCE(s.score, -1.0) = ? AND a.asset_uuid > ?))"
        )
        parameters.extend((cursor_score, cursor_score, cursor_asset_uuid))
    parameters.extend((limit, offset))
    rows = connection.execute(
        f"""
        SELECT a.*, m.*, d.auto_disposition, d.manual_disposition, d.final_disposition,
            d.auto_selection, d.manual_selection, d.final_selection,
            d.confidence, d.flags_json, d.reasons_json, d.manual_override,
            d.manual_note, d.manual_rating, d.manual_mutation_generation,
            d.reviewed, s.score AS swipe_score,
            s.generic_score AS swipe_generic_score, s.personal_delta AS swipe_personal_delta,
            s.confidence AS swipe_confidence, s.components_json AS swipe_components_json,
            s.reasons_json AS swipe_reasons_json, s.model_versions_json AS swipe_models_json,
            s.schema_version AS swipe_schema_version,
            q.top_k_rank AS quality_top_k_rank,
            q.duplicate_group AS quality_duplicate_group,
            q.expected_leader AS quality_expected_leader,
            q.expected_disposition AS quality_expected_disposition
        FROM assets a
        LEFT JOIN metrics m USING (project_id, asset_uuid)
        LEFT JOIN decisions d USING (project_id, asset_uuid)
        LEFT JOIN swipe_scores s USING (project_id, asset_uuid)
        LEFT JOIN quality_asset_labels q USING (project_id, asset_uuid)
        WHERE {" AND ".join(clauses)}
        ORDER BY COALESCE(s.score, -1.0) DESC, a.asset_uuid
        LIMIT ? OFFSET ?
        """,
        parameters,
    ).fetchall()
    return [decode(dict(row)) for row in rows]


def _list_ranked_decision_page(
    connection: sqlite3.Connection,
    project_id: str,
    *,
    limit: int,
    decode: AssetDecoder,
    offset: int,
    disposition: str | None,
    selection: str | None,
    cursor_score: float | None,
    cursor_asset_uuid: str | None,
) -> list[dict[str, object]]:
    """Page a completed gallery by score without sorting the whole album.

    Decision-filtered galleries are only exposed after scoring has completed, so
    ``swipe_scores`` is the correct driving table.  Keeping the ordered, limited
    scan inside a CTE lets SQLite use ``swipe_scores_project_score_uuid`` and
    probe the decision index for the small number of candidates required by the
    page.  Starting from ``assets`` made every cursor page sort the full album.
    """
    clauses = [
        "s.project_id = ?",
        """EXISTS (
            SELECT 1 FROM assets active_a
            WHERE active_a.project_id=s.project_id
              AND active_a.asset_uuid=s.asset_uuid
              AND active_a.no_longer_exists=0
              AND active_a.media_type='image'
        )""",
    ]
    parameters: list[object] = [project_id]
    if cursor_score is not None and cursor_asset_uuid is not None:
        clauses.append("(s.score < ? OR (s.score = ? AND s.asset_uuid > ?))")
        parameters.extend((cursor_score, cursor_score, cursor_asset_uuid))

    decision_clauses = [
        "filter_d.project_id = s.project_id",
        "filter_d.asset_uuid = s.asset_uuid",
    ]
    if disposition is not None:
        decision_clauses.append("filter_d.final_disposition = ?")
        parameters.append(disposition)
    if selection is not None:
        decision_clauses.append("filter_d.final_selection = ?")
        parameters.append(selection)
    clauses.append(
        f"EXISTS (SELECT 1 FROM decisions filter_d WHERE {' AND '.join(decision_clauses)})"
    )
    parameters.extend((limit, offset))

    rows = connection.execute(
        f"""
        WITH ranked_scores AS (
            SELECT s.*
            FROM swipe_scores s INDEXED BY swipe_scores_project_score_uuid
            WHERE {" AND ".join(clauses)}
            ORDER BY s.score DESC, s.asset_uuid
            LIMIT ? OFFSET ?
        )
        SELECT a.*, m.*, d.auto_disposition, d.manual_disposition, d.final_disposition,
            d.auto_selection, d.manual_selection, d.final_selection,
            d.confidence, d.flags_json, d.reasons_json, d.manual_override,
            d.manual_note, d.manual_rating, d.manual_mutation_generation,
            d.reviewed, s.score AS swipe_score,
            s.generic_score AS swipe_generic_score, s.personal_delta AS swipe_personal_delta,
            s.confidence AS swipe_confidence, s.components_json AS swipe_components_json,
            s.reasons_json AS swipe_reasons_json, s.model_versions_json AS swipe_models_json,
            s.schema_version AS swipe_schema_version,
            q.top_k_rank AS quality_top_k_rank,
            q.duplicate_group AS quality_duplicate_group,
            q.expected_leader AS quality_expected_leader,
            q.expected_disposition AS quality_expected_disposition
        FROM ranked_scores s
        JOIN assets a USING (project_id, asset_uuid)
        LEFT JOIN metrics m USING (project_id, asset_uuid)
        JOIN decisions d USING (project_id, asset_uuid)
        LEFT JOIN quality_asset_labels q USING (project_id, asset_uuid)
        ORDER BY s.score DESC, s.asset_uuid
        """,
        parameters,
    ).fetchall()
    return [decode(dict(row)) for row in rows]


def count_assets(
    connection: sqlite3.Connection,
    project_id: str,
    *,
    disposition: str | None = None,
    selection: str | None = None,
) -> int:
    clauses = ["a.project_id = ?", "a.no_longer_exists = 0", "a.media_type = 'image'"]
    parameters: list[object] = [project_id]
    if disposition is not None:
        clauses.append("d.final_disposition = ?")
        parameters.append(disposition)
    if selection is not None:
        clauses.append("d.final_selection = ?")
        parameters.append(selection)
    return int(
        connection.execute(
            f"""
            SELECT COUNT(*)
            FROM assets a LEFT JOIN decisions d USING (project_id, asset_uuid)
            WHERE {" AND ".join(clauses)}
            """,
            parameters,
        ).fetchone()[0]
    )
