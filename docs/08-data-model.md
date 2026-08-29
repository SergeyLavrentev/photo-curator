# Модель данных SQLite

> Это схема переходного baseline плюс V2 migrations. `analysis_signals` хранит native
> Vision provenance, `swipe_scores` — versioned ranking, schema v8 — локальные pairwise
> examples и Personal Taste Profile, а schema v10 — независимую human quality-разметку.
> Реальный personalization uplift ещё не принят.

## V2 schema direction

Новые queryable сущности должны разделять:

- immutable `analysis_signals` с render fingerprint, capability, model/schema version;
- `swipe_scores` с generic score, personal delta, confidence, components и reasons;
- `preference_examples` с явными A/B choices и provenance;
- `taste_profiles` с feature schema, parameters, training/evaluation evidence и status;
- `taste_rounds` с тремя сохраняемыми Top-3-of-10 раундами onboarding;
- `taste_assets` с локальными review-render и Vision feature vectors выбранного bounded sample;
- `model_registry` с immutable name/version, streaming SHA-256, license/source,
  explicit commercial-use flag, Core ML compute policy, lifecycle status и versioned
  compatibility/runtime/held-out uplift evidence. Изменившаяся модель становится invalid;
  research-only или не доказавшая uplift модель не может стать approved.
- `quality_asset_labels` (schema v10) с явным ordered Top-K и human duplicate-group/leader
  labels. Они не имеют foreign key к predicted `duplicate_groups`, поэтому не меняются и не
  исчезают после повторного запуска алгоритма.
- `album_snapshots` и append-only `album_snapshot_items` (schema v20) фиксируют точный
  source order/membership, fractional timestamps, media/edit state и revision fingerprints.

Manual decisions и safety protections остаются отдельными от learned preference state.

## `analysis_signals` (schema v6)

Каждый native result хранится отдельно по `(project_id, asset_uuid, signal_kind)`:
`aesthetics`, `feature_print`, `attention_saliency` или `faces`. Запись содержит
render fingerprint, schema/engine/request revision, status, JSON value, duration и
явный error/unavailable reason. Изменение source render удаляет зависимые signals.

Legacy face columns в `metrics` временно остаются projection для совместимости старого
decision engine; источником provenance является `analysis_signals`.

## `swipe_scores` (schema v7)

Отдельная строка на asset хранит schema version, итоговый score, generic score,
personal delta, confidence, components, короткие reasons, model/request provenance и
source fingerprint. Review disposition остаётся в `decisions`: пользовательское решение
не смешивается с ranking model и не теряется при пересчёте score.

## `taste_profiles` и `preference_examples` (schema v8)

Profile хранит status, feature schema, model version, Float32 weights, training count и
quality evidence. Каждая явная A/B пара копирует оба feature-print payload: preference
переживает удаление исходного project/assets. `split` разделяет calibration и held-out;
held-out examples не участвуют в обучении.

Pause не удаляет данные, reset удаляет profile и все examples каскадно. Safety state,
manual decisions и Photos metadata не входят в learned profile.

## Connection settings

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;
```

Миграции через `PRAGMA user_version`.

`projects.settings_json` stores `selection_density`, source photo/video counts and
`source_provenance` (`regular_album`, `manual_shared_copy` or `service_shared_copy`).

Service-owned albums находятся вне SQLite в
`Application Support/PhotoCurator/local_albums/local-<job-id>/`: каталог `assets/`
содержит независимые renders, а атомарный `manifest.json` — album metadata и snapshot
каждого asset. Каталог без manifest считается незавершённой resumable-копией и не
показывается в album browser.

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
    media_type TEXT NOT NULL DEFAULT 'image',
    media_subtypes INTEGER NOT NULL DEFAULT 0,
    creation_timestamp REAL,
    modification_timestamp REAL,
    edit_state TEXT NOT NULL DEFAULT 'original',
    source_revision TEXT,
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

`assets` содержит только поддерживаемые still images активного analysis working set. Полное
содержимое исходного альбома, включая исключённые видео, хранится в immutable snapshot:

```sql
CREATE TABLE album_snapshot_items (
    snapshot_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    asset_uuid TEXT NOT NULL,
    album_position INTEGER NOT NULL,
    source_membership INTEGER NOT NULL,
    creation_date TEXT,
    creation_timestamp REAL,
    modification_timestamp REAL,
    media_type TEXT NOT NULL,
    media_subtypes INTEGER NOT NULL,
    edit_state TEXT NOT NULL,
    width INTEGER,
    height INTEGER,
    orientation INTEGER,
    revision_fingerprint TEXT NOT NULL,
    render_fingerprint TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, asset_uuid)
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
    destination_album_id TEXT,
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
