from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from itertools import pairwise

from photo_curator.analysis.taste import (
    MIN_CALIBRATION_PAIRS,
    TasteProfileError,
    feature_vector,
    train_taste_profile,
)
from photo_curator.db import repository

CORPUS_SCHEMA_VERSION = 1
LEARNING_MIGRATION_VERSION = 1


class LearningCorpusError(ValueError):
    """Durable human evidence cannot be accepted without complete provenance."""


@dataclass(frozen=True, slots=True)
class _AssetSnapshot:
    asset_key: str
    feature_schema: str
    feature_base64: str
    feature_provenance: dict[str, object]
    source_revision_hash: str


def _hash(*parts: object) -> str:
    encoded = "\0".join(str(part) for part in parts).encode()
    return hashlib.sha256(encoded).hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _project_identity(connection: sqlite3.Connection, project_id: str) -> dict[str, str]:
    project = repository.get_project(connection, project_id)
    snapshot = repository.latest_album_snapshot(connection, project_id)
    if snapshot is None:
        raise LearningCorpusError("Quality Lab learning requires an immutable album snapshot")
    album_hash = _hash("album", project["album_id"])
    snapshot_hash = _hash(
        "snapshot",
        snapshot["source_album_id"],
        snapshot["membership_hash"],
        snapshot["item_count"],
    )
    return {
        "project_hash": _hash("project", project_id),
        "album_hash": album_hash,
        "episode_hash": _hash("episode", album_hash, "album-wide"),
        "snapshot_hash": snapshot_hash,
    }


def _quality_rows(connection: sqlite3.Connection, project_id: str) -> dict[str, object]:
    labels = [
        dict(row)
        for row in connection.execute(
            "SELECT * FROM quality_asset_labels WHERE project_id=? ORDER BY asset_uuid",
            (project_id,),
        ).fetchall()
    ]
    preferences = repository.list_quality_preference_examples(connection, project_id)
    series = [
        dict(row)
        for row in connection.execute(
            "SELECT * FROM quality_series_labels WHERE project_id=? ORDER BY group_id",
            (project_id,),
        ).fetchall()
    ]
    return {"labels": labels, "preferences": preferences, "series": series}


def _input_fingerprint(rows: dict[str, object]) -> str:
    return _hash("quality-learning-input-v1", _json(rows))


def _referenced_uuids(rows: dict[str, object]) -> set[str]:
    labels = rows["labels"]
    preferences = rows["preferences"]
    assert isinstance(labels, list) and isinstance(preferences, list)
    result = {str(row["asset_uuid"]) for row in labels}
    for row in preferences:
        result.update((str(row["left_uuid"]), str(row["right_uuid"])))
    return result


def _asset_snapshots(
    connection: sqlite3.Connection,
    project_id: str,
    album_hash: str,
    asset_uuids: set[str],
) -> dict[str, _AssetSnapshot]:
    assets = repository.assets_by_uuid(connection, project_id, asset_uuids)
    snapshots: dict[str, _AssetSnapshot] = {}
    for asset_uuid in sorted(asset_uuids):
        signal = next(
            (
                s
                for s in repository.list_analysis_signals(connection, project_id, asset_uuid)
                if s["signal_kind"] == "feature_print"
            ),
            None,
        )
        asset = assets.get(asset_uuid)
        if signal is None or asset is None:
            raise LearningCorpusError(f"Missing feature provenance for labelled asset {asset_uuid}")
        if signal.get("status") != "ready":
            raise LearningCorpusError(
                f"Feature snapshot is not ready for labelled asset {asset_uuid}"
            )
        try:
            schema, _, encoded = feature_vector(signal)
        except TasteProfileError as error:
            raise LearningCorpusError(str(error)) from error
        required = ("engine_name", "engine_version", "schema_version", "source_fingerprint")
        if any(signal.get(key) in (None, "") for key in required):
            raise LearningCorpusError(
                f"Incomplete feature provenance for labelled asset {asset_uuid}"
            )
        source_revision = asset.get("source_revision") or asset.get("source_fingerprint")
        if not source_revision:
            raise LearningCorpusError(f"Missing source revision for labelled asset {asset_uuid}")
        snapshots[asset_uuid] = _AssetSnapshot(
            asset_key=_hash("asset", album_hash, asset_uuid),
            feature_schema=schema,
            feature_base64=encoded,
            feature_provenance={key: signal[key] for key in required},
            source_revision_hash=_hash("revision", source_revision),
        )
    schemas = {snapshot.feature_schema for snapshot in snapshots.values()}
    if len(schemas) > 1:
        raise LearningCorpusError("Quality Lab assets use incompatible feature schemas")
    return snapshots


def _ensure_context(
    connection: sqlite3.Connection,
    identity: dict[str, str],
    split: str,
    profile_id: str,
) -> dict[str, object]:
    if split not in {"training", "held_out"}:
        raise LearningCorpusError("Learning split must be training or held_out")
    repository.ensure_taste_profile(connection, profile_id)
    row = connection.execute(
        """
        SELECT * FROM learning_contexts
        WHERE profile_id=? AND album_context_hash=? AND episode_context_hash=?
        """,
        (profile_id, identity["album_hash"], identity["episode_hash"]),
    ).fetchone()
    if row:
        context = dict(row)
        if context["split"] != split:
            raise LearningCorpusError(
                "This album/episode context is already locked to " + str(context["split"])
            )
        return context
    now = repository.utc_now()
    context_id = repository.new_id()
    connection.execute(
        """
        INSERT INTO learning_contexts (
            id, profile_id, album_context_hash, episode_context_hash, split, locked,
            provenance_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
        """,
        (
            context_id,
            profile_id,
            identity["album_hash"],
            identity["episode_hash"],
            split,
            _json({"context_schema": "album-episode-v1", "pseudonymized": True}),
            now,
            now,
        ),
    )
    return dict(
        connection.execute("SELECT * FROM learning_contexts WHERE id=?", (context_id,)).fetchone()
    )


def _create_round(
    connection: sqlite3.Connection,
    context: dict[str, object],
    identity: dict[str, str],
    profile_id: str,
) -> dict[str, object]:
    next_attempt = int(
        connection.execute(
            "SELECT COALESCE(MAX(attempt_index), 0) + 1 FROM learning_rounds WHERE context_id=?",
            (context["id"],),
        ).fetchone()[0]
    )
    now = repository.utc_now()
    round_id = repository.new_id()
    connection.execute(
        """
        INSERT INTO learning_rounds (
            id, profile_id, context_id, attempt_index, status, source_project_hash,
            source_snapshot_hash, feature_schema, model_provenance_json, human_origin,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, 'in_progress', ?, ?, NULL, '{}', 1, ?, ?)
        """,
        (
            round_id,
            profile_id,
            context["id"],
            next_attempt,
            identity["project_hash"],
            identity["snapshot_hash"],
            now,
            now,
        ),
    )
    return dict(
        connection.execute("SELECT * FROM learning_rounds WHERE id=?", (round_id,)).fetchone()
    )


def _active_round(
    connection: sqlite3.Connection,
    project_id: str,
) -> dict[str, object] | None:
    row = connection.execute(
        """
        SELECT lr.*, lc.split
        FROM quality_round_bindings qb
        JOIN learning_rounds lr ON lr.id=qb.round_id
        JOIN learning_contexts lc ON lc.id=lr.context_id
        WHERE qb.project_id=?
        """,
        (project_id,),
    ).fetchone()
    return dict(row) if row else None


def _bind_round(connection: sqlite3.Connection, project_id: str, round_id: str) -> None:
    connection.execute(
        """
        INSERT INTO quality_round_bindings (project_id, round_id) VALUES (?, ?)
        ON CONFLICT(project_id) DO UPDATE SET round_id=excluded.round_id
        """,
        (project_id, round_id),
    )


def _derived_pairs(
    rows: dict[str, object], snapshots: dict[str, _AssetSnapshot]
) -> list[tuple[str, str, str]]:
    labels = rows["labels"]
    preferences = rows["preferences"]
    assert isinstance(labels, list) and isinstance(preferences, list)
    result: dict[tuple[str, str], str] = {}
    for row in preferences:
        left = snapshots[str(row["left_uuid"])].asset_key
        right = snapshots[str(row["right_uuid"])].asset_key
        preferred = snapshots[str(row["preferred_uuid"])].asset_key
        ordered = tuple(sorted((left, right)))
        result[(ordered[0], ordered[1])] = preferred
    top_k = sorted(
        (row for row in labels if row.get("top_k_rank") is not None),
        key=lambda row: int(row["top_k_rank"]),
    )
    for better, worse in pairwise(top_k):
        left = snapshots[str(better["asset_uuid"])].asset_key
        right = snapshots[str(worse["asset_uuid"])].asset_key
        ordered = tuple(sorted((left, right)))
        result.setdefault((ordered[0], ordered[1]), left)
    groups: dict[str, list[dict[str, object]]] = {}
    for row in labels:
        if row.get("duplicate_group"):
            groups.setdefault(str(row["duplicate_group"]), []).append(row)
    for members in groups.values():
        leaders = [row for row in members if bool(row.get("expected_leader"))]
        if len(leaders) != 1:
            continue
        leader = snapshots[str(leaders[0]["asset_uuid"])].asset_key
        for member in members:
            candidate = snapshots[str(member["asset_uuid"])].asset_key
            if candidate == leader:
                continue
            ordered = tuple(sorted((leader, candidate)))
            result.setdefault((ordered[0], ordered[1]), leader)
    return sorted((left, right, preferred) for (left, right), preferred in result.items())


def _replace_round_projection(
    connection: sqlite3.Connection,
    project_id: str,
    round_value: dict[str, object],
    identity: dict[str, str],
    rows: dict[str, object],
) -> dict[str, int]:
    asset_uuids = _referenced_uuids(rows)
    snapshots = _asset_snapshots(connection, project_id, identity["album_hash"], asset_uuids)
    # Reanalysis must never silently replace the features behind accepted answers.
    retained = {
        str(row["asset_key"]): dict(row)
        for row in connection.execute(
            "SELECT * FROM learning_assets WHERE round_id=?", (round_value["id"],)
        )
    }
    for snapshot in snapshots.values():
        old = retained.get(snapshot.asset_key)
        if old and (
            old["feature_schema"] != snapshot.feature_schema
            or old["feature_base64"] != snapshot.feature_base64
            or old["source_revision_hash"] != snapshot.source_revision_hash
            or json.loads(old["feature_provenance_json"]) != snapshot.feature_provenance
        ):
            raise LearningCorpusError(
                "Accepted learning snapshot changed; retained evidence was preserved. "
                "Use a new analysis for the changed source/model."
            )
    labels = rows["labels"]
    series = rows["series"]
    assert isinstance(labels, list) and isinstance(series, list)
    now = repository.utc_now()
    old_pair_ids = [
        str(row["id"])
        for row in connection.execute(
            "SELECT id FROM learning_preferences WHERE round_id=?", (round_value["id"],)
        ).fetchall()
    ]
    if old_pair_ids:
        connection.execute(
            "DELETE FROM preference_examples WHERE id IN ({})".format(
                ",".join("?" for _ in old_pair_ids)
            ),
            old_pair_ids,
        )
    connection.execute("DELETE FROM learning_preferences WHERE round_id=?", (round_value["id"],))
    connection.execute("DELETE FROM learning_series WHERE round_id=?", (round_value["id"],))
    connection.execute("DELETE FROM learning_assets WHERE round_id=?", (round_value["id"],))
    labels_by_uuid = {str(row["asset_uuid"]): row for row in labels}
    for asset_uuid, snapshot in snapshots.items():
        label = labels_by_uuid.get(asset_uuid, {})
        connection.execute(
            """
            INSERT INTO learning_assets (
                round_id, asset_key, feature_schema, feature_base64, feature_provenance_json,
                source_revision_hash, expected_disposition, defect_codes_json,
                defect_severity, defect_confidence, top_k_rank, human_note,
                human_origin, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                round_value["id"],
                snapshot.asset_key,
                snapshot.feature_schema,
                snapshot.feature_base64,
                _json(snapshot.feature_provenance),
                snapshot.source_revision_hash,
                label.get("expected_disposition"),
                label.get("defect_codes_json") or "[]",
                label.get("defect_severity"),
                label.get("defect_confidence"),
                label.get("top_k_rank"),
                label.get("quality_note"),
                now,
                now,
            ),
        )
    split = str(round_value["split"])
    pairs = _derived_pairs(rows, snapshots)
    for left, right, preferred in pairs:
        connection.execute(
            """
            INSERT INTO learning_preferences (
                id, round_id, left_asset_key, right_asset_key, preferred_asset_key,
                split, human_origin, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                _hash("learning-pair", round_value["id"], left, right),
                round_value["id"],
                left,
                right,
                preferred,
                split,
                now,
            ),
        )
    for item in series:
        group_id = str(item["group_id"])
        members = [row for row in labels if str(row.get("duplicate_group") or "") == group_id]
        member_keys = sorted(snapshots[str(row["asset_uuid"])].asset_key for row in members)
        leaders = [
            snapshots[str(row["asset_uuid"])].asset_key
            for row in members
            if bool(row.get("expected_leader"))
        ]
        if not member_keys or len(leaders) != 1:
            continue

        def mapped(raw: object) -> str | None:
            if not raw:
                return None
            values = json.loads(str(raw))
            return _json(sorted(snapshots[str(value)].asset_key for value in values))

        connection.execute(
            """
            INSERT INTO learning_series (
                round_id, group_key, member_asset_keys_json, leader_asset_key, target_budget,
                essential_asset_keys_json, redundant_good_asset_keys_json,
                leader_reason_codes_json, provenance_json, human_origin, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                round_value["id"],
                _hash("series", identity["album_hash"], group_id),
                _json(member_keys),
                leaders[0],
                item.get("target_budget"),
                mapped(item.get("essential_member_uuids_json")),
                mapped(item.get("redundant_good_member_uuids_json")),
                item.get("leader_reason_codes_json"),
                _json(
                    {
                        "source_kind": item.get("source_kind"),
                        "coherence_status": item.get("coherence_status"),
                        "member_fingerprint": item.get("member_fingerprint"),
                        "source_snapshot_hash": identity["snapshot_hash"],
                    }
                ),
                now,
            ),
        )
    schemas = {snapshot.feature_schema for snapshot in snapshots.values()}
    provenance = sorted({_json(snapshot.feature_provenance) for snapshot in snapshots.values()})
    connection.execute(
        """
        UPDATE learning_rounds
        SET feature_schema=?, model_provenance_json=?, source_snapshot_hash=?, updated_at=?
        WHERE id=?
        """,
        (
            next(iter(schemas)) if schemas else None,
            _json([json.loads(value) for value in provenance]),
            identity["snapshot_hash"],
            now,
            round_value["id"],
        ),
    )
    return {"assets": len(snapshots), "preferences": len(pairs), "series": len(series)}


def _sync_taste_examples(
    connection: sqlite3.Connection,
    round_id: str,
    album_hash: str,
    episode_hash: str,
) -> int:
    round_row = connection.execute(
        """
        SELECT lr.feature_schema, lc.split
        FROM learning_rounds lr JOIN learning_contexts lc ON lc.id=lr.context_id
        WHERE lr.id=?
        """,
        (round_id,),
    ).fetchone()
    if not round_row or not round_row["feature_schema"]:
        return 0
    if round_row["split"] != "training":
        return 0
    assets = {
        str(row["asset_key"]): dict(row)
        for row in connection.execute(
            "SELECT * FROM learning_assets WHERE round_id=?", (round_id,)
        ).fetchall()
    }
    inserted = 0
    for pair in connection.execute(
        "SELECT * FROM learning_preferences WHERE round_id=? "
        "AND id NOT IN (SELECT id FROM learning_pair_exclusions) ORDER BY id",
        (round_id,),
    ).fetchall():
        pair = dict(pair)
        left = assets[str(pair["left_asset_key"])]
        right = assets[str(pair["right_asset_key"])]
        existing = connection.execute(
            "SELECT 1 FROM preference_examples WHERE id=?", (pair["id"],)
        ).fetchone()
        if existing:
            continue
        connection.execute(
            """
            INSERT INTO preference_examples (
                id, profile_id, project_id, left_uuid, right_uuid, preferred_uuid,
                split, feature_schema, left_feature_base64, right_feature_base64,
                source_album_id, source_episode_key, created_at
            ) VALUES (?, 'default', NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pair["id"],
                pair["left_asset_key"],
                pair["right_asset_key"],
                pair["preferred_asset_key"],
                "calibration" if pair["split"] == "training" else "held_out",
                round_row["feature_schema"],
                left["feature_base64"],
                right["feature_base64"],
                album_hash,
                episode_hash,
                pair["created_at"],
            ),
        )
        inserted += 1
    return inserted


def migrate_project_learning(
    connection: sqlite3.Connection,
    project_id: str,
    *,
    default_split: str = "held_out",
) -> dict[str, object]:
    rows = _quality_rows(connection, project_id)
    fingerprint = _input_fingerprint(rows)
    if not any(rows.values()):
        return {
            "status": "completed",
            "round_id": None,
            "split": None,
            "counts": {"assets": 0, "preferences": 0, "series": 0},
            "ranker_trained": False,
        }
    identity: dict[str, str] | None = None
    try:
        identity = _project_identity(connection, project_id)
        active = _active_round(connection, project_id)
        if active is None:
            context = _ensure_context(connection, identity, default_split, "default")
            active = _create_round(connection, context, identity, "default")
            active["split"] = context["split"]
            _bind_round(connection, project_id, str(active["id"]))
        counts = _replace_round_projection(connection, project_id, active, identity, rows)
        taste_examples = _sync_taste_examples(
            connection, str(active["id"]), identity["album_hash"], identity["episode_hash"]
        )
        counts["taste_examples_added"] = taste_examples
        audit_id = _hash(identity["project_hash"], LEARNING_MIGRATION_VERSION, fingerprint)
        connection.execute(
            """
            INSERT INTO learning_migration_audit (
                id, source_project_hash, source_snapshot_hash, migration_version,
                input_fingerprint, status, counts_json, error_text, created_at
            ) VALUES (?, ?, ?, ?, ?, 'completed', ?, NULL, ?)
            ON CONFLICT(source_project_hash, migration_version, input_fingerprint)
            DO UPDATE SET status='completed', counts_json=excluded.counts_json,
                          error_text=NULL, created_at=excluded.created_at
            """,
            (
                audit_id,
                identity["project_hash"],
                identity["snapshot_hash"],
                LEARNING_MIGRATION_VERSION,
                fingerprint,
                _json(counts),
                repository.utc_now(),
            ),
        )
        calibration_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM preference_examples WHERE split='calibration' "
                "AND id NOT IN (SELECT id FROM learning_pair_exclusions)"
            ).fetchone()[0]
        )
        trained = False
        if calibration_count >= MIN_CALIBRATION_PAIRS:
            train_taste_profile(connection)
            trained = True
        return {
            "status": "completed",
            "round_id": active["id"],
            "split": active["split"],
            "counts": counts,
            "ranker_trained": trained,
        }
    except (KeyError, ValueError, TypeError, json.JSONDecodeError) as error:
        project_hash = identity["project_hash"] if identity else _hash("project", project_id)
        snapshot_hash = identity["snapshot_hash"] if identity else "unavailable"
        connection.execute(
            """
            INSERT INTO learning_migration_audit (
                id, source_project_hash, source_snapshot_hash, migration_version,
                input_fingerprint, status, counts_json, error_text, created_at
            ) VALUES (?, ?, ?, ?, ?, 'failed', '{}', ?, ?)
            ON CONFLICT(source_project_hash, migration_version, input_fingerprint)
            DO UPDATE SET status='failed', error_text=excluded.error_text,
                          created_at=excluded.created_at
            """,
            (
                _hash(project_hash, LEARNING_MIGRATION_VERSION, fingerprint),
                project_hash,
                snapshot_hash,
                LEARNING_MIGRATION_VERSION,
                fingerprint,
                str(error),
                repository.utc_now(),
            ),
        )
        return {"status": "failed", "error": str(error)}


def start_new_round(
    connection: sqlite3.Connection,
    project_id: str,
    split: str,
) -> dict[str, object]:
    rows = _quality_rows(connection, project_id)
    if any(rows.values()):
        migrated = migrate_project_learning(connection, project_id)
        if migrated["status"] != "completed":
            return migrated
    identity = _project_identity(connection, project_id)
    active = _active_round(connection, project_id)
    if active is not None:
        connection.execute(
            "UPDATE learning_rounds SET status='superseded', updated_at=? WHERE id=?",
            (repository.utc_now(), active["id"]),
        )
    context = _ensure_context(connection, identity, split, "default")
    round_value = _create_round(connection, context, identity, "default")
    _bind_round(connection, project_id, str(round_value["id"]))
    connection.execute("DELETE FROM quality_preference_examples WHERE project_id=?", (project_id,))
    connection.execute("DELETE FROM quality_asset_labels WHERE project_id=?", (project_id,))
    return {
        "status": "created",
        "round_id": round_value["id"],
        "attempt_index": round_value["attempt_index"],
        "split": split,
    }


def learning_status(
    connection: sqlite3.Connection, project_id: str | None = None
) -> dict[str, object]:
    repository.ensure_taste_profile(connection)
    profile = repository.get_taste_profile(connection)
    context_counts = {
        str(row["split"]): int(row["count"])
        for row in connection.execute(
            "SELECT split, COUNT(*) count FROM learning_contexts GROUP BY split"
        ).fetchall()
    }
    round_counts = {
        str(row["status"]): int(row["count"])
        for row in connection.execute(
            "SELECT status, COUNT(*) count FROM learning_rounds GROUP BY status"
        ).fetchall()
    }
    active = _active_round(connection, project_id) if project_id else None
    return {
        "schema_version": CORPUS_SCHEMA_VERSION,
        "training_contexts": context_counts.get("training", 0),
        "held_out_contexts": context_counts.get("held_out", 0),
        "round_counts": round_counts,
        "active_round": (
            {
                "id": active["id"],
                "attempt_index": active["attempt_index"],
                "split": active["split"],
                "status": active["status"],
            }
            if active
            else None
        ),
        "ranker_version": profile.get("model_version"),
        "ranker_status": profile.get("status"),
        "training_examples": int(profile.get("training_examples") or 0),
    }


def export_learning_corpus(connection: sqlite3.Connection) -> dict[str, object]:
    repository.ensure_taste_profile(connection)
    profile = repository.get_taste_profile(connection)
    tables = {}
    for table in (
        "learning_contexts",
        "learning_rounds",
        "learning_assets",
        "learning_preferences",
        "learning_series",
    ):
        condition = (
            " WHERE id NOT IN (SELECT id FROM learning_pair_exclusions)"
            if table == "learning_preferences"
            else ""
        )
        tables[table] = [
            dict(row) for row in connection.execute(f"SELECT * FROM {table}{condition}").fetchall()
        ]
    profile_export = {
        key: profile.get(key)
        for key in (
            "id",
            "status",
            "schema_version",
            "feature_schema",
            "model_version",
            "weights_base64",
            "dimension",
            "training_examples",
            "evidence_json",
            "updated_at",
        )
    }
    return {
        "schema_version": CORPUS_SCHEMA_VERSION,
        "origin": "human_quality_learning",
        "predictions_are_truth": False,
        "profile": profile_export,
        **tables,
    }


def import_learning_corpus(
    connection: sqlite3.Connection, payload: dict[str, object]
) -> dict[str, int]:
    if payload.get("schema_version") != CORPUS_SCHEMA_VERSION:
        raise LearningCorpusError("Unsupported learning corpus schema")
    if (
        payload.get("origin") != "human_quality_learning"
        or payload.get("predictions_are_truth") is not False
    ):
        raise LearningCorpusError("Import is not an explicit human-truth corpus")
    contexts = payload.get("learning_contexts")
    rounds = payload.get("learning_rounds")
    assets = payload.get("learning_assets")
    preferences = payload.get("learning_preferences")
    series = payload.get("learning_series")
    if not all(
        isinstance(value, list) for value in (contexts, rounds, assets, preferences, series)
    ):
        raise LearningCorpusError("Learning corpus tables must be arrays")
    for collection in (rounds, assets, preferences, series):
        if any(
            not isinstance(row, dict) or int(row.get("human_origin", 0)) != 1 for row in collection
        ):
            raise LearningCorpusError("Predictions cannot be imported as human truth")
    repository.ensure_taste_profile(connection)
    expected_columns = {
        "learning_contexts": (
            "id",
            "profile_id",
            "album_context_hash",
            "episode_context_hash",
            "split",
            "locked",
            "provenance_json",
            "created_at",
            "updated_at",
        ),
        "learning_rounds": (
            "id",
            "profile_id",
            "context_id",
            "attempt_index",
            "status",
            "source_project_hash",
            "source_snapshot_hash",
            "feature_schema",
            "model_provenance_json",
            "human_origin",
            "created_at",
            "updated_at",
        ),
        "learning_assets": (
            "round_id",
            "asset_key",
            "feature_schema",
            "feature_base64",
            "feature_provenance_json",
            "source_revision_hash",
            "expected_disposition",
            "defect_codes_json",
            "defect_severity",
            "defect_confidence",
            "top_k_rank",
            "human_note",
            "human_origin",
            "created_at",
            "updated_at",
        ),
        "learning_preferences": (
            "id",
            "round_id",
            "left_asset_key",
            "right_asset_key",
            "preferred_asset_key",
            "split",
            "human_origin",
            "created_at",
        ),
        "learning_series": (
            "round_id",
            "group_key",
            "member_asset_keys_json",
            "leader_asset_key",
            "target_budget",
            "essential_asset_keys_json",
            "redundant_good_asset_keys_json",
            "leader_reason_codes_json",
            "provenance_json",
            "human_origin",
            "created_at",
        ),
    }

    def validate_columns(table: str, row: dict[str, object]) -> None:
        expected = set(expected_columns[table])
        if set(row) != expected:
            raise LearningCorpusError(f"Imported {table} row has unexpected schema")

    for context in contexts:
        if not isinstance(context, dict):
            raise LearningCorpusError("Invalid learning context")
        validate_columns("learning_contexts", context)
        existing = connection.execute(
            """
            SELECT id, split FROM learning_contexts
            WHERE profile_id='default' AND album_context_hash=? AND episode_context_hash=?
            """,
            (context.get("album_context_hash"), context.get("episode_context_hash")),
        ).fetchone()
        if existing and existing["split"] != context.get("split"):
            raise LearningCorpusError("Import would leak one context across training and held_out")
    inserted = {"contexts": 0, "rounds": 0, "assets": 0, "preferences": 0, "series": 0}
    context_ids: dict[str, str] = {}
    for context in contexts:
        assert isinstance(context, dict)
        imported_id = str(context["id"])
        existing = connection.execute(
            """
            SELECT id FROM learning_contexts
            WHERE profile_id='default' AND album_context_hash=? AND episode_context_hash=?
            """,
            (context["album_context_hash"], context["episode_context_hash"]),
        ).fetchone()
        if existing:
            context_ids[imported_id] = str(existing["id"])
            continue
        columns = expected_columns["learning_contexts"]
        values = [context.get(column) for column in columns]
        values[1] = "default"
        placeholders = ",".join("?" for _ in columns)
        cursor = connection.execute(
            f"INSERT OR IGNORE INTO learning_contexts ({','.join(columns)}) "
            f"VALUES ({placeholders})",
            values,
        )
        inserted["contexts"] += int(cursor.rowcount)
        context_ids[imported_id] = imported_id
    round_ids: dict[str, str] = {}
    for raw_round in rounds:
        assert isinstance(raw_round, dict)
        validate_columns("learning_rounds", raw_round)
        imported_id = str(raw_round["id"])
        context_id = context_ids.get(str(raw_round["context_id"]))
        if context_id is None:
            raise LearningCorpusError("Imported round references an unknown context")
        existing = connection.execute(
            "SELECT id FROM learning_rounds WHERE context_id=? AND attempt_index=?",
            (context_id, raw_round["attempt_index"]),
        ).fetchone()
        if existing:
            round_ids[imported_id] = str(existing["id"])
            continue
        columns = expected_columns["learning_rounds"]
        values = [raw_round[column] for column in columns]
        values[columns.index("profile_id")] = "default"
        values[columns.index("context_id")] = context_id
        placeholders = ",".join("?" for _ in columns)
        cursor = connection.execute(
            f"INSERT INTO learning_rounds ({','.join(columns)}) VALUES ({placeholders})",
            values,
        )
        inserted["rounds"] += int(cursor.rowcount)
        round_ids[imported_id] = imported_id
    for name, collection, table in (
        ("assets", assets, "learning_assets"),
        ("preferences", preferences, "learning_preferences"),
        ("series", series, "learning_series"),
    ):
        for row in collection:
            assert isinstance(row, dict)
            validate_columns(table, row)
            columns = expected_columns[table]
            values = [row[column] for column in columns]
            round_id = round_ids.get(str(row["round_id"]))
            if round_id is None:
                raise LearningCorpusError(f"Imported {table} row references an unknown round")
            values[columns.index("round_id")] = round_id
            if table == "learning_assets":
                try:
                    decoded = base64.b64decode(str(row["feature_base64"]), validate=True)
                except (KeyError, ValueError) as error:
                    raise LearningCorpusError("Imported feature snapshot is corrupt") from error
                if (
                    not decoded
                    or len(decoded) % 4 != 0
                    or not row.get("feature_schema")
                    or not row.get("feature_provenance_json")
                ):
                    raise LearningCorpusError("Imported feature snapshot lacks provenance")
            placeholders = ",".join("?" for _ in columns)
            cursor = connection.execute(
                f"INSERT OR IGNORE INTO {table} ({','.join(columns)}) VALUES ({placeholders})",
                values,
            )
            inserted[name] += int(cursor.rowcount)
    for round_id in sorted(set(round_ids.values())):
        context = connection.execute(
            """
            SELECT lc.album_context_hash, lc.episode_context_hash
            FROM learning_rounds lr JOIN learning_contexts lc ON lc.id=lr.context_id
            WHERE lr.id=?
            """,
            (round_id,),
        ).fetchone()
        if context:
            _sync_taste_examples(
                connection,
                round_id,
                str(context["album_context_hash"]),
                str(context["episode_context_hash"]),
            )
    calibration_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM preference_examples WHERE split='calibration' "
            "AND id NOT IN (SELECT id FROM learning_pair_exclusions)"
        ).fetchone()[0]
    )
    if calibration_count >= MIN_CALIBRATION_PAIRS:
        train_taste_profile(connection)
    return inserted


def reset_all_learning(connection: sqlite3.Connection) -> None:
    connection.execute("DELETE FROM learning_pair_exclusions")
    repository.reset_taste_profile(connection)
    repository.ensure_taste_profile(connection)
