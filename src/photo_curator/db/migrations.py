from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 9

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


def migrate(connection: sqlite3.Connection) -> None:
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version > SCHEMA_VERSION:
        raise RuntimeError(
            f"Database schema {version} is newer than supported schema {SCHEMA_VERSION}"
        )
    if version < 1:
        connection.executescript(MIGRATION_1)
        connection.execute("PRAGMA user_version = 1")
        version = 1
    if version < 2:
        connection.executescript(MIGRATION_2)
        connection.execute("PRAGMA user_version = 2")
        version = 2
    if version < 3:
        connection.executescript(MIGRATION_3)
        connection.execute("PRAGMA user_version = 3")
        version = 3
    if version < 4:
        connection.executescript(MIGRATION_4)
        connection.execute("PRAGMA user_version = 4")
        version = 4
    if version < 5:
        connection.executescript(MIGRATION_5)
        connection.execute("PRAGMA user_version = 5")
        version = 5
    if version < 6:
        connection.executescript(MIGRATION_6)
        connection.execute("PRAGMA user_version = 6")
        version = 6
    if version < 7:
        connection.executescript(MIGRATION_7)
        connection.execute("PRAGMA user_version = 7")
        version = 7
    if version < 8:
        connection.executescript(MIGRATION_8)
        connection.execute("PRAGMA user_version = 8")
        version = 8
    if version < 9:
        connection.executescript(MIGRATION_9)
        connection.execute("PRAGMA user_version = 9")
    connection.commit()
