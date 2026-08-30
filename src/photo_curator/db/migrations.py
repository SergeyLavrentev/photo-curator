from __future__ import annotations

import sqlite3
from pathlib import Path

from photo_curator.db.connection import create_database_backup

SCHEMA_VERSION = 21

MIGRATION_1 = """
CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    library_path TEXT NOT NULL,
    database_path TEXT,
    library_fingerprint TEXT NOT NULL,
    album_id TEXT NOT NULL,
    album_name TEXT NOT NULL,
    album_folder_path TEXT,
    album_full_path TEXT NOT NULL,
    album_snapshot_hash TEXT,
    state TEXT NOT NULL,
    settings_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE assets (
    project_id TEXT NOT NULL,
    asset_uuid TEXT NOT NULL,
    original_filename TEXT,
    current_filename TEXT,
    taken_at TEXT,
    date_added TEXT,
    width INTEGER,
    height INTEGER,
    original_width INTEGER,
    original_height INTEGER,
    orientation INTEGER,
    favorite INTEGER NOT NULL DEFAULT 0,
    hidden INTEGER NOT NULL DEFAULT 0,
    has_adjustments INTEGER NOT NULL DEFAULT 0,
    is_live_photo INTEGER NOT NULL DEFAULT 0,
    is_burst INTEGER NOT NULL DEFAULT 0,
    burst_key TEXT,
    burst_default_pick INTEGER NOT NULL DEFAULT 0,
    is_missing INTEGER NOT NULL DEFAULT 0,
    no_longer_exists INTEGER NOT NULL DEFAULT 0,
    source_kind TEXT,
    source_path TEXT,
    source_size INTEGER,
    source_mtime REAL,
    source_fingerprint TEXT,
    review_path TEXT,
    thumbnail_path TEXT,
    cache_state TEXT NOT NULL DEFAULT 'pending',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    apple_scores_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, asset_uuid),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE metrics (
    project_id TEXT NOT NULL,
    asset_uuid TEXT NOT NULL,
    laplacian_variance REAL,
    gradient_energy REAL,
    edge_density REAL,
    luma_mean REAL,
    luma_std REAL,
    luma_p01 REAL,
    luma_p05 REAL,
    luma_p50 REAL,
    luma_p95 REAL,
    luma_p99 REAL,
    black_clipped_ratio REAL,
    white_clipped_ratio REAL,
    contrast_std REAL,
    dynamic_range REAL,
    entropy REAL,
    sharpness_percentile REAL,
    gradient_percentile REAL,
    contrast_percentile REAL,
    apple_overall_percentile REAL,
    dhash TEXT,
    phash TEXT,
    normalized_pixel_hash TEXT,
    histogram_json TEXT,
    technical_quality REAL,
    calculated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, asset_uuid),
    FOREIGN KEY (project_id, asset_uuid)
      REFERENCES assets(project_id, asset_uuid) ON DELETE CASCADE
);

CREATE TABLE duplicate_groups (
    project_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    confidence REAL NOT NULL,
    leader_uuid TEXT,
    leader_is_manual INTEGER NOT NULL DEFAULT 0,
    member_count INTEGER NOT NULL,
    flags_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, group_id),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE duplicate_members (
    project_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    asset_uuid TEXT NOT NULL,
    is_leader INTEGER NOT NULL DEFAULT 0,
    similarity REAL,
    quality_score REAL,
    resolution_ratio REAL,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (project_id, group_id, asset_uuid),
    FOREIGN KEY (project_id, group_id)
      REFERENCES duplicate_groups(project_id, group_id) ON DELETE CASCADE,
    FOREIGN KEY (project_id, asset_uuid)
      REFERENCES assets(project_id, asset_uuid) ON DELETE CASCADE
);

CREATE TABLE decisions (
    project_id TEXT NOT NULL,
    asset_uuid TEXT NOT NULL,
    auto_disposition TEXT NOT NULL,
    manual_disposition TEXT,
    final_disposition TEXT NOT NULL,
    confidence REAL NOT NULL,
    flags_json TEXT NOT NULL DEFAULT '[]',
    reasons_json TEXT NOT NULL DEFAULT '[]',
    manual_override INTEGER NOT NULL DEFAULT 0,
    manual_note TEXT,
    reviewed INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, asset_uuid),
    FOREIGN KEY (project_id, asset_uuid)
      REFERENCES assets(project_id, asset_uuid) ON DELETE CASCADE
);

CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    total_items INTEGER NOT NULL DEFAULT 0,
    processed_items INTEGER NOT NULL DEFAULT 0,
    warning_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    current_message TEXT,
    started_at TEXT,
    finished_at TEXT,
    error_text TEXT,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE publishes (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    album_name TEXT NOT NULL,
    asset_count INTEGER NOT NULL,
    uuid_file TEXT NOT NULL,
    dry_run_stdout TEXT,
    dry_run_stderr TEXT,
    dry_run_return_code INTEGER,
    apply_stdout TEXT,
    apply_stderr TEXT,
    apply_return_code INTEGER,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    applied_at TEXT,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);
"""

MIGRATION_2 = """
ALTER TABLE publishes ADD COLUMN kind TEXT NOT NULL DEFAULT 'reject';
"""

MIGRATION_3 = """
ALTER TABLE metrics ADD COLUMN face_count INTEGER;
ALTER TABLE metrics ADD COLUMN face_capture_quality REAL;
ALTER TABLE metrics ADD COLUMN eyes_detected INTEGER;
"""

MIGRATION_4 = """
CREATE TABLE shared_copy_jobs (
    id TEXT PRIMARY KEY,
    shared_album_id TEXT NOT NULL,
    shared_album_name TEXT NOT NULL,
    destination_album_name TEXT NOT NULL,
    destination_album_id TEXT,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    total_items INTEGER NOT NULL,
    processed_items INTEGER NOT NULL DEFAULT 0,
    imported_items INTEGER NOT NULL DEFAULT 0,
    reused_items INTEGER NOT NULL DEFAULT 0,
    skipped_videos INTEGER NOT NULL DEFAULT 0,
    warning_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    current_message TEXT,
    error_text TEXT,
    asset_uuids_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
"""

MIGRATION_5 = """
ALTER TABLE publishes ADD COLUMN destination_album_id TEXT;
"""

MIGRATION_6 = """
CREATE TABLE analysis_signals (
    project_id TEXT NOT NULL,
    asset_uuid TEXT NOT NULL,
    signal_kind TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    engine_name TEXT NOT NULL,
    engine_version TEXT NOT NULL,
    request_revision INTEGER,
    source_fingerprint TEXT,
    status TEXT NOT NULL,
    value_json TEXT,
    duration_ms REAL,
    error_text TEXT,
    calculated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, asset_uuid, signal_kind),
    FOREIGN KEY (project_id, asset_uuid)
      REFERENCES assets(project_id, asset_uuid) ON DELETE CASCADE
);
CREATE INDEX analysis_signals_project_kind
ON analysis_signals(project_id, signal_kind, status);
"""

MIGRATION_7 = """
CREATE TABLE swipe_scores (
    project_id TEXT NOT NULL,
    asset_uuid TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    score REAL NOT NULL,
    generic_score REAL NOT NULL,
    personal_delta REAL NOT NULL DEFAULT 0,
    confidence REAL NOT NULL,
    components_json TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    model_versions_json TEXT NOT NULL,
    source_fingerprint TEXT,
    calculated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, asset_uuid),
    FOREIGN KEY (project_id, asset_uuid)
      REFERENCES assets(project_id, asset_uuid) ON DELETE CASCADE
);
CREATE INDEX swipe_scores_project_score
ON swipe_scores(project_id, score DESC);
"""

MIGRATION_8 = """
CREATE TABLE taste_profiles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    feature_schema TEXT,
    model_version TEXT,
    weights_base64 TEXT,
    dimension INTEGER,
    training_examples INTEGER NOT NULL DEFAULT 0,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE preference_examples (
    id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    project_id TEXT,
    left_uuid TEXT NOT NULL,
    right_uuid TEXT NOT NULL,
    preferred_uuid TEXT NOT NULL,
    split TEXT NOT NULL,
    feature_schema TEXT NOT NULL,
    left_feature_base64 TEXT NOT NULL,
    right_feature_base64 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (profile_id) REFERENCES taste_profiles(id) ON DELETE CASCADE
);
CREATE INDEX preference_examples_profile_split
ON preference_examples(profile_id, split, created_at);
"""

MIGRATION_9 = """
CREATE TABLE model_registry (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    model_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    license_id TEXT NOT NULL,
    source_url TEXT,
    commercial_use_allowed INTEGER NOT NULL DEFAULT 0,
    compute_policy TEXT NOT NULL,
    status TEXT NOT NULL,
    compatibility_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(name, version)
);
CREATE INDEX model_registry_status ON model_registry(status, name, version);
"""

MIGRATION_10 = """
CREATE TABLE quality_asset_labels (
    project_id TEXT NOT NULL,
    asset_uuid TEXT NOT NULL,
    top_k_rank INTEGER,
    duplicate_group TEXT,
    expected_leader INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, asset_uuid),
    FOREIGN KEY (project_id, asset_uuid)
      REFERENCES assets(project_id, asset_uuid) ON DELETE CASCADE
);
CREATE UNIQUE INDEX quality_asset_labels_top_k
ON quality_asset_labels(project_id, top_k_rank)
WHERE top_k_rank IS NOT NULL;
CREATE INDEX quality_asset_labels_duplicate_group
ON quality_asset_labels(project_id, duplicate_group)
WHERE duplicate_group IS NOT NULL;
"""

MIGRATION_11 = """
CREATE TABLE taste_rounds (
    id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    album_id TEXT NOT NULL,
    album_name TEXT NOT NULL,
    round_index INTEGER NOT NULL,
    status TEXT NOT NULL,
    candidate_json TEXT NOT NULL,
    selected_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (profile_id) REFERENCES taste_profiles(id) ON DELETE CASCADE,
    UNIQUE(profile_id, round_index)
);
CREATE INDEX taste_rounds_profile_status
ON taste_rounds(profile_id, status, round_index);

CREATE TABLE taste_assets (
    profile_id TEXT NOT NULL,
    asset_uuid TEXT NOT NULL,
    album_id TEXT NOT NULL,
    filename TEXT,
    taken_at TEXT,
    review_path TEXT NOT NULL,
    feature_schema TEXT NOT NULL,
    feature_base64 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (profile_id, asset_uuid),
    FOREIGN KEY (profile_id) REFERENCES taste_profiles(id) ON DELETE CASCADE
);
"""

MIGRATION_12 = """
ALTER TABLE taste_rounds ADD COLUMN rejected_json TEXT;
"""

MIGRATION_13 = """
ALTER TABLE decisions
ADD COLUMN auto_selection TEXT NOT NULL DEFAULT 'alternative'
CHECK (auto_selection IN ('pick', 'alternative', 'review', 'reject'));
ALTER TABLE decisions
ADD COLUMN manual_selection TEXT
CHECK (manual_selection IS NULL OR manual_selection IN ('pick', 'alternative', 'review', 'reject'));
ALTER TABLE decisions
ADD COLUMN final_selection TEXT NOT NULL DEFAULT 'alternative'
CHECK (final_selection IN ('pick', 'alternative', 'review', 'reject'));

UPDATE decisions
SET auto_selection = CASE
        WHEN auto_disposition='reject' THEN 'reject'
        WHEN auto_disposition='review' THEN 'review'
        WHEN flags_json LIKE '%"best_candidate"%' THEN 'pick'
        ELSE 'alternative'
    END,
    manual_selection = CASE manual_disposition
        WHEN 'keep' THEN 'pick'
        WHEN 'reject' THEN 'reject'
        WHEN 'review' THEN 'review'
        ELSE NULL
    END,
    final_selection = CASE
        WHEN manual_override=1 AND manual_disposition='keep' THEN 'pick'
        WHEN manual_override=1 AND manual_disposition='reject' THEN 'reject'
        WHEN manual_override=1 AND manual_disposition='review' THEN 'review'
        WHEN final_disposition='reject' THEN 'reject'
        WHEN final_disposition='review' THEN 'review'
        WHEN flags_json LIKE '%"best_candidate"%' THEN 'pick'
        ELSE 'alternative'
    END;

CREATE INDEX decisions_project_selection
ON decisions(project_id, final_selection, asset_uuid);
"""

MIGRATION_14 = """
CREATE TABLE stage_fingerprints (
    project_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    PRIMARY KEY (project_id, stage),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);
"""

MIGRATION_15 = """
ALTER TABLE decisions ADD COLUMN manual_rating INTEGER
CHECK (manual_rating IS NULL OR manual_rating BETWEEN 1 AND 5);
"""

MIGRATION_16 = """
CREATE INDEX IF NOT EXISTS swipe_scores_project_score_uuid
ON swipe_scores(project_id, score DESC, asset_uuid);
CREATE INDEX IF NOT EXISTS decisions_project_selection_disposition
ON decisions(project_id, final_selection, final_disposition, asset_uuid);
"""

MIGRATION_17 = """
ALTER TABLE metrics ADD COLUMN dominant_horizon_degrees REAL;
ALTER TABLE metrics ADD COLUMN horizon_support REAL;
"""

MIGRATION_18 = """
ALTER TABLE quality_asset_labels
ADD COLUMN defect_codes_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE quality_asset_labels ADD COLUMN defect_severity INTEGER
CHECK (defect_severity IS NULL OR defect_severity BETWEEN 1 AND 3);
ALTER TABLE quality_asset_labels ADD COLUMN defect_confidence REAL
CHECK (defect_confidence IS NULL OR defect_confidence BETWEEN 0 AND 1);
ALTER TABLE quality_asset_labels ADD COLUMN quality_note TEXT;
ALTER TABLE quality_asset_labels
ADD COLUMN lab_sampled INTEGER NOT NULL DEFAULT 0 CHECK (lab_sampled IN (0, 1));

CREATE TABLE quality_preference_examples (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    left_uuid TEXT NOT NULL,
    right_uuid TEXT NOT NULL,
    preferred_uuid TEXT NOT NULL,
    split TEXT NOT NULL DEFAULT 'held_out',
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id, left_uuid)
      REFERENCES assets(project_id, asset_uuid) ON DELETE CASCADE,
    FOREIGN KEY (project_id, right_uuid)
      REFERENCES assets(project_id, asset_uuid) ON DELETE CASCADE,
    CHECK (left_uuid <> right_uuid),
    CHECK (preferred_uuid = left_uuid OR preferred_uuid = right_uuid),
    UNIQUE(project_id, left_uuid, right_uuid)
);
CREATE INDEX quality_preference_examples_project
ON quality_preference_examples(project_id, created_at);
"""

MIGRATION_19 = """
ALTER TABLE quality_asset_labels ADD COLUMN expected_disposition TEXT
CHECK (
    expected_disposition IS NULL
    OR expected_disposition IN ('keep', 'review', 'reject')
);
"""

MIGRATION_20 = """
ALTER TABLE assets ADD COLUMN media_type TEXT NOT NULL DEFAULT 'image'
CHECK (media_type IN ('image', 'video', 'audio', 'unknown'));
ALTER TABLE assets ADD COLUMN media_subtypes INTEGER NOT NULL DEFAULT 0;
ALTER TABLE assets ADD COLUMN creation_timestamp REAL;
ALTER TABLE assets ADD COLUMN modification_timestamp REAL;
ALTER TABLE assets ADD COLUMN edit_state TEXT NOT NULL DEFAULT 'original'
CHECK (edit_state IN ('original', 'adjusted', 'unknown'));
ALTER TABLE assets ADD COLUMN source_revision TEXT;

CREATE TABLE album_snapshots (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    source_album_id TEXT NOT NULL,
    membership_hash TEXT NOT NULL,
    item_count INTEGER NOT NULL,
    photo_count INTEGER NOT NULL,
    skipped_video_count INTEGER NOT NULL,
    captured_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE INDEX album_snapshots_project_captured
ON album_snapshots(project_id, captured_at, id);

CREATE TABLE album_snapshot_items (
    snapshot_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    asset_uuid TEXT NOT NULL,
    album_position INTEGER NOT NULL,
    source_membership INTEGER NOT NULL DEFAULT 1 CHECK (source_membership IN (0, 1)),
    creation_date TEXT,
    creation_timestamp REAL,
    modification_timestamp REAL,
    media_type TEXT NOT NULL CHECK (media_type IN ('image', 'video', 'audio', 'unknown')),
    media_subtypes INTEGER NOT NULL DEFAULT 0,
    edit_state TEXT NOT NULL CHECK (edit_state IN ('original', 'adjusted', 'unknown')),
    width INTEGER,
    height INTEGER,
    orientation INTEGER,
    revision_fingerprint TEXT NOT NULL,
    render_fingerprint TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, asset_uuid),
    UNIQUE (snapshot_id, album_position),
    FOREIGN KEY (snapshot_id) REFERENCES album_snapshots(id) ON DELETE CASCADE,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE INDEX album_snapshot_items_project_media
ON album_snapshot_items(project_id, media_type, snapshot_id);
"""

MIGRATION_21 = """
CREATE TABLE engine_shadow_runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    source_snapshot_id TEXT NOT NULL,
    engine_name TEXT NOT NULL,
    engine_version TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL,
    config_json TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (source_snapshot_id) REFERENCES album_snapshots(id) ON DELETE CASCADE,
    UNIQUE (project_id, engine_name, engine_version, input_fingerprint)
);
CREATE INDEX engine_shadow_runs_project_created
ON engine_shadow_runs(project_id, created_at, id);

CREATE TABLE engine_shadow_nodes (
    run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    parent_node_id TEXT,
    kind TEXT NOT NULL CHECK (
        kind IN ('episode', 'scene', 'moment_stack', 'exact_duplicate')
    ),
    node_position INTEGER NOT NULL,
    member_count INTEGER NOT NULL,
    selection_budget INTEGER,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (run_id, node_id),
    FOREIGN KEY (run_id) REFERENCES engine_shadow_runs(id) ON DELETE CASCADE,
    FOREIGN KEY (run_id, parent_node_id)
      REFERENCES engine_shadow_nodes(run_id, node_id) ON DELETE CASCADE
);
CREATE INDEX engine_shadow_nodes_run_kind
ON engine_shadow_nodes(run_id, kind, node_position);

CREATE TABLE engine_shadow_members (
    run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    asset_uuid TEXT NOT NULL,
    album_position INTEGER NOT NULL,
    rank_score REAL,
    novelty_score REAL,
    recommended INTEGER NOT NULL DEFAULT 0 CHECK (recommended IN (0, 1)),
    evidence_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (run_id, node_id, asset_uuid),
    FOREIGN KEY (run_id, node_id)
      REFERENCES engine_shadow_nodes(run_id, node_id) ON DELETE CASCADE,
    FOREIGN KEY (project_id, asset_uuid)
      REFERENCES assets(project_id, asset_uuid) ON DELETE CASCADE
);
CREATE INDEX engine_shadow_members_project_asset
ON engine_shadow_members(project_id, asset_uuid, run_id);
"""

MIGRATION_19_BACKFILL = """
UPDATE quality_asset_labels
SET expected_disposition = (
    SELECT decisions.manual_disposition
    FROM decisions
    WHERE decisions.project_id=quality_asset_labels.project_id
      AND decisions.asset_uuid=quality_asset_labels.asset_uuid
      AND decisions.manual_override=1
)
WHERE lab_sampled=1 AND expected_disposition IS NULL;
"""

MIGRATIONS = (
    MIGRATION_1,
    MIGRATION_2,
    MIGRATION_3,
    MIGRATION_4,
    MIGRATION_5,
    MIGRATION_6,
    MIGRATION_7,
    MIGRATION_8,
    MIGRATION_9,
    MIGRATION_10,
    MIGRATION_11,
    MIGRATION_12,
    MIGRATION_13,
    MIGRATION_14,
    MIGRATION_15,
    MIGRATION_16,
    MIGRATION_17,
    MIGRATION_18,
    MIGRATION_19,
    MIGRATION_20,
    MIGRATION_21,
)

_TABLES_BY_VERSION = {
    1: {
        "projects",
        "assets",
        "metrics",
        "duplicate_groups",
        "duplicate_members",
        "decisions",
        "jobs",
        "publishes",
    },
    4: {"shared_copy_jobs"},
    6: {"analysis_signals"},
    7: {"swipe_scores"},
    8: {"taste_profiles", "preference_examples"},
    9: {"model_registry"},
    10: {"quality_asset_labels"},
    11: {"taste_rounds", "taste_assets"},
    14: {"stage_fingerprints"},
    18: {"quality_preference_examples"},
    20: {"album_snapshots", "album_snapshot_items"},
    21: {"engine_shadow_runs", "engine_shadow_nodes", "engine_shadow_members"},
}

_COLUMNS_BY_VERSION = {
    1: {
        "projects": {"id", "album_id", "state", "settings_json"},
        "assets": {"project_id", "asset_uuid", "source_fingerprint", "cache_state"},
        "metrics": {"project_id", "asset_uuid", "phash", "technical_quality"},
        "decisions": {"project_id", "asset_uuid", "final_disposition"},
        "publishes": {"id", "project_id", "album_name", "status"},
    },
    2: {"publishes": {"kind"}},
    3: {"metrics": {"face_count", "face_capture_quality", "eyes_detected"}},
    5: {"publishes": {"destination_album_id"}},
    12: {"taste_rounds": {"rejected_json"}},
    13: {
        "decisions": {"auto_selection", "manual_selection", "final_selection"},
    },
    15: {"decisions": {"manual_rating"}},
    17: {"metrics": {"dominant_horizon_degrees", "horizon_support"}},
    18: {
        "quality_asset_labels": {
            "defect_codes_json",
            "defect_severity",
            "defect_confidence",
            "quality_note",
            "lab_sampled",
        },
    },
    19: {"quality_asset_labels": {"expected_disposition"}},
    20: {
        "assets": {
            "media_type",
            "media_subtypes",
            "creation_timestamp",
            "modification_timestamp",
            "edit_state",
            "source_revision",
        },
        "album_snapshot_items": {
            "snapshot_id",
            "project_id",
            "asset_uuid",
            "album_position",
            "source_membership",
            "creation_date",
            "creation_timestamp",
            "modification_timestamp",
            "media_type",
            "media_subtypes",
            "edit_state",
            "width",
            "height",
            "orientation",
            "revision_fingerprint",
            "render_fingerprint",
        },
    },
    21: {
        "engine_shadow_runs": {
            "id",
            "project_id",
            "source_snapshot_id",
            "engine_name",
            "engine_version",
            "input_fingerprint",
            "config_json",
            "summary_json",
            "created_at",
        },
        "engine_shadow_nodes": {
            "run_id",
            "node_id",
            "parent_node_id",
            "kind",
            "node_position",
            "member_count",
            "selection_budget",
            "metadata_json",
        },
        "engine_shadow_members": {
            "run_id",
            "node_id",
            "project_id",
            "asset_uuid",
            "album_position",
            "rank_score",
            "novelty_score",
            "recommended",
            "evidence_json",
        },
    },
}


def verify_schema(connection: sqlite3.Connection, expected_version: int) -> None:
    """Fail closed when a versioned database is structurally incomplete."""
    actual_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if actual_version != expected_version:
        raise RuntimeError(
            f"Database schema version mismatch: expected {expected_version}, got {actual_version}"
        )
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    required_tables: set[str] = set()
    for introduced_at, introduced_tables in _TABLES_BY_VERSION.items():
        if introduced_at <= expected_version:
            required_tables.update(introduced_tables)
    missing_tables = sorted(required_tables - tables)
    if missing_tables:
        raise RuntimeError(
            f"Database schema {expected_version} is missing tables: {', '.join(missing_tables)}"
        )
    missing_columns: list[str] = []
    for introduced_at, table_columns in _COLUMNS_BY_VERSION.items():
        if introduced_at > expected_version:
            continue
        for table, expected_columns in table_columns.items():
            if table not in tables:
                continue
            actual_columns = {
                str(row[1])
                for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
            }
            missing_columns.extend(
                f"{table}.{column}" for column in sorted(expected_columns - actual_columns)
            )
    if missing_columns:
        raise RuntimeError(
            f"Database schema {expected_version} is missing columns: {', '.join(missing_columns)}"
        )
    integrity = connection.execute("PRAGMA quick_check").fetchone()
    if not integrity or str(integrity[0]) != "ok":
        raise sqlite3.DatabaseError(f"Database integrity check failed: {integrity!r}")
    foreign_key_violation = connection.execute("PRAGMA foreign_key_check").fetchone()
    if foreign_key_violation is not None:
        raise sqlite3.IntegrityError(
            f"Database foreign key check failed: {tuple(foreign_key_violation)!r}"
        )


def _main_database_path(connection: sqlite3.Connection) -> Path | None:
    row = next(
        (row for row in connection.execute("PRAGMA database_list").fetchall() if row[1] == "main"),
        None,
    )
    if row is None or not row[2]:
        return None
    return Path(str(row[2])).resolve()


def _migration_script(version: int) -> str:
    statements = ["BEGIN IMMEDIATE;"]
    for target_version in range(version + 1, SCHEMA_VERSION + 1):
        statements.append(MIGRATIONS[target_version - 1])
        if target_version == 19:
            statements.append(MIGRATION_19_BACKFILL)
        statements.append(f"PRAGMA user_version = {target_version};")
    return "\n".join(statements)


def migrate(connection: sqlite3.Connection) -> None:
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version > SCHEMA_VERSION:
        raise RuntimeError(
            f"Database schema {version} is newer than supported schema {SCHEMA_VERSION}"
        )
    existing_tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    if version == 0 and existing_tables:
        raise RuntimeError("Unversioned database contains application tables")
    if version > 0:
        database_path = _main_database_path(connection)
        if version < SCHEMA_VERSION and database_path is not None:
            create_database_backup(
                database_path,
                database_path.parent / "backups",
                reason=f"before-schema-v{version}-to-v{SCHEMA_VERSION}",
            )
        verify_schema(connection, version)
    if version == SCHEMA_VERSION:
        return
    try:
        connection.executescript(_migration_script(version))
        verify_schema(connection, SCHEMA_VERSION)
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    connection.commit()
