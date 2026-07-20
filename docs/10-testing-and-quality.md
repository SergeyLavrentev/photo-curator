# Testing и quality gates

## Unit fixtures

Генерировать через Pillow:

- sharp image;
- Gaussian blur;
- motion-like blur;
- dark;
- overexposed;
- low contrast;
- render-equivalent duplicate;
- JPEG recompressed duplicate;
- resized duplicate;
- slight crop;
- similar but distinct;
- higher-resolution original;
- lower-resolution shared-like copy;
- EXIF-rotated image.

## Unit coverage

- orientation normalization;
- render-equivalence hash;
- dHash/pHash/Hamming;
- technical metrics;
- robust normalization;
- candidate generation;
- pair confirmation;
- union-find;
- ambiguous chaining;
- leader ranking;
- Favorite/edited/missing protections;
- manual override persistence;
- resolution inversion;
- migrations;
- safe cache paths;
- publish validation.

## Fake provider

`FakePhotosProvider` позволяет выполнять полный pipeline на любой платформе. `photo-curator --demo` должен быть демонстрацией и integration test UI.

## Publisher tests

Mock subprocess runner проверяет:

- argv list и `shell=False`;
- one UUID per line;
- empty reject set;
- sanitised unique album name;
- dry-run/apply distinction;
- return code handling;
- persistence stdout/stderr;
- отсутствие automatic retry apply;
- capability-disabled mode.

## Manual macOS test

Отдельный обычный альбом `PhotoCurator Test` из 50–100 неважных assets:

- duplicate pairs;
- imported lower-resolution copies;
- Favorite;
- edited;
- Live Photo;
- missing/iCloud-only при возможности.

Проверить read gate, pipeline, review, dry-run и создание временного альбома. Ничего не удалять приложением.

## Commands

```bash
uv run pytest
uv run pytest --cov=photo_curator
uv run ruff check .
uv run ruff format --check .
```

## Quality gates

- Tests зелёные перед следующим milestone.
- Нет `shell=True`.
- Нет direct Photos DB writes.
- Нет web routes, напрямую вызывающих `osxphotos`.
- Нет необработанных source paths в HTML/JSON.
- Manual overrides не теряются.

## S0 preference acceptance

Schema v2 human manifest содержит 50–100 реальных фотографий, disposition labels,
как минимум одну серию с лидером, минимум 10 held-out A/B preferences и минимум 5
ожидаемых Top-K UUID. Calibration pairs не участвуют в held-out метрике.

Каждый scorer экспортируется в immutable JSON snapshot с `project_id`, engine name,
engine version и числовым score для каждого размеченного asset. Один manifest оценивает
несколько snapshots без изменения labels. Отчёт включает duplicate precision/recall,
series leader accuracy, false exclusion rate, pairwise accuracy и Top-K overlap.

```bash
uv run photo-curator acceptance-template --project-id PROJECT_ID --output labels.json
uv run photo-curator acceptance-score-export --project-id PROJECT_ID \
  --engine-name technical-first-selection-score --engine-version legacy-v1 \
  --output baseline.json
uv run photo-curator acceptance-evaluate --project-id PROJECT_ID \
  --labels labels.json --scores baseline.json --json
```

Нативный Settings → «Проверка качества» экспортирует manifest из явных ручных решений
и project-scoped A/B-пар, а также отдельный immutable Swipe Score snapshot с schema/model
provenance. Экспорт fail-honest: predicted dispositions, duplicate groups и текущий Top-K
не копируются в truth labels. Поэтому summary остаётся `release_ready: false`, пока человек
не закончит разметку серий и ожидаемого Top-K и не прогонит evaluator.

## S1 native Vision benchmark

Swift helper должен собираться с deployment target macOS 13, а aesthetics capability
включаться только на macOS 15+. Integration test запускает helper на синтетическом image
и проверяет versioned JSON, aesthetics range, feature-print payload, saliency heatmap,
request durations и отсутствие per-signal errors.

Реальный benchmark выполняется с warmup и несколькими measured iterations. Отчёт хранит
OS/architecture, capability map, mean/p95 stage durations и request revisions. Отдельный
aesthetics score snapshot нормализует публичный диапазон Apple `-1...1` в `0...100` только
для ranking/evaluation; исходное значение сохраняется в benchmark report.
Helper также сохраняет собственный process peak RSS через `getrusage`; report без
положительного `peak_rss_bytes` считается недостоверным.

## S3 optional Core ML benchmark

Swift helper компилируется независимо от конкретной модели и сообщает capability
`coreml-image-benchmark-v1`. Integration tests подменяют runner и проверяют schema,
точное соответствие asset IDs, `MLComputeUnits.all`, warmup/measured iterations и удаление
временных request/result JSON. Реальная модель допускается в продукт только с коммерчески
совместимой лицензией, checksum и доказанным held-out uplift. Значение `.all` разрешает
Core ML выбирать CPU/GPU/Neural Engine, но само по себе не доказывает использование ANE;
hardware attribution и energy требуют отдельного Instruments acceptance run.
