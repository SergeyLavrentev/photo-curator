# Модель данных SQLite

## Connection settings

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;
```

Миграции через `PRAGMA user_version`.

## `projects`

```sql
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
```

## `assets`

```sql
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
```

## `metrics`

```sql
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
    face_count INTEGER,
    face_capture_quality REAL,
    eyes_detected INTEGER,
    calculated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, asset_uuid),
    FOREIGN KEY (project_id, asset_uuid)
      REFERENCES assets(project_id, asset_uuid) ON DELETE CASCADE
);
```

## `duplicate_groups`

```sql
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
```

## `duplicate_members`

```sql
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
```

## `decisions`

`reasons_json` содержит запись `selection_score` с overall 0–100 и component
breakdown; отдельные колонки не добавляются, чтобы manual override schema оставалась совместимой.

```sql
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
```

## `jobs`

```sql
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
```

## `publishes`

```sql
CREATE TABLE publishes (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    album_name TEXT NOT NULL,
    asset_count INTEGER NOT NULL,
    uuid_file TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'reject',
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
```

## JSON policy

JSON fields используются для optional/evolving metadata. Core queryable fields должны оставаться отдельными columns.

## Manual override policy

Re-analysis может менять `auto_disposition`, confidence, flags и reasons, но не должен очищать `manual_disposition`, `manual_override` и `manual_note`.
