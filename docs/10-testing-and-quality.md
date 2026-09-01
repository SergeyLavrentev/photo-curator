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

`FakePhotosProvider` позволяет выполнять полный pipeline на любой платформе.
Нативный demo запускается с `PHOTO_CURATOR_NATIVE_DEMO=1 make run`; CLI никогда не
поднимает HTTP server. Demo publisher проходит тот же immutable dry-run, explicit confirmation,
source revalidation и SQLite audit, но пишет только synthetic destination identifier и никогда
не обращается к Apple Photos.

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

Проверить read gate, pipeline, review, dry-run, создание и cleanup временного альбома. Исходные
assets не удалять: acceptance удаляет только созданный тестовый album container.

Реальная проверка 2026-08-09 прошла на обычном PhotoKit album `документы`: установленное
приложение проанализировало 11 фото, пользовательский Pick сформировал dry-run ровно из одного
существующего `PHAsset`, а apply создал обычный album без копирования asset. Затем был удалён
только созданный album container; исходный album сохранил все 11 фото. Полные идентификаторы,
хеши сборки и границы проверки записаны в `docs/17-acceptance-2026-08-09.md`. Текущее состояние
движка, iCloud-блокер и свежая сборка зафиксированы в `docs/18-acceptance-2026-08-27.md`.

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

Для scene-aware Engine v3 серия дополнительно получает явный target budget, обязательные
моменты, хорошие, но избыточные кадры и причины относительного выбора лидера. Эти ответы
связаны с immutable source snapshot; старые series labels без scene-budget полей не считаются
полноценным evidence и не закрывают structural readiness.

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

Нативный Settings → «Проверка качества» открывает отдельный мастер. Он выбирает стабильную
для проекта выборку независимо от prediction, скрывает score/reasons/auto decision, проводит
50–100 disposition labels, требует явный тип дефекта для Reject, собирает отдельные held-out
A/B-пары, ordered Top-K и одну человеческую серию с лидером. Lab A/B хранится отдельно от
профиля вкуса и не попадает в его обучение. В schema v18 lab-sampled provenance отделяет
слепой corpus от ручных решений в prediction-conditioned review-галерее.

Мастер экспортирует manifest и отдельный immutable Swipe Score snapshot с schema/model
provenance. Экспорт fail-honest: predicted dispositions, duplicate groups и автоматический
Top-K не копируются в truth labels; серия готова только после lab manual decision для каждого
кадра. Итоговый экран перечисляет недостающие gates, запускает versioned evaluator без CLI и
переключает `release_ready` только при полной структуре corpus.
Quality Lab включается пользователем в Settings и не требует специальной environment variable.
До выполнения этих человеческих действий `release_ready=false` является ожидаемым и обязательным
результатом, а не поводом ослаблять gate.

## Production gallery benchmark

`make gallery-benchmark` компилирует production `PhotoCard`, `CachedThumbnail` и тот же SwiftUI
код приложения с флагом, отключающим только application `@main`. Harness создаёт отдельные
реальные JPEG, отображает production-страницу из 36 карточек и измеряет page-local DTO creation,
initial layout/decode, selection-state-to-highlight p95, scroll p95 и page-swap p95 для каталогов
2k/5k. Selection metric проверяет SwiftUI propagation после уже распознанного действия; он не
заменяет end-to-end mouse/XCUITest click-to-highlight gate. DTO создаются постранично, как в
production UI; отдельный SQLite gate проверяет data path на 50 000 строках. Прямоугольная
surrogate-view больше не является release evidence. JSON schema v3 сохраняется в
`build/evidence/gallery-benchmark.json`.

Native worker regression для `series(group_id)` искусственно разводит членов одной серии по
разным selection buckets и ограничивает gallery page одним элементом. Series payload обязан
вернуть полный typed состав и отдельно исключить `no_longer_exists` и full-video members.

## PhotoKit import benchmark

Реальный album benchmark 2026-07-31: `Карелия: осень 2023`, 103 фотографии,
MacBook Air M1 16 GB, macOS 26.5.2 arm64. Время берётся из persisted `jobs.started_at` /
`finished_at`; cold cache проверяется по пустому `photokit-renders/assets-v2`.

| Вариант | Inventory | Previews | Пользовательский «импорт» |
| --- | ---: | ---: | ---: |
| Baseline, последовательный 2560 px export | 369,11 с | 13,08 с | 382,19 с |
| Optimized, пустой v2 render cache | 0,74 с | 7,60 с | 8,34 с |
| Optimized, общий render + thumbnail cache | 1,60 с | 1,81 с | 3,41 с |

В cold run созданы ровно 103 versioned 2048 px renders. В финальном warm run не создано
ни одного нового cache-файла. Benchmark-проекты после измерения удалены; общий cache
сохранён для проверки реального повторного workflow.

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

## S3 batching, concurrency and energy gate

`local-model-performance-benchmark` прогоняет один и тот же набор preview в режимах
`work_batch_size × max_concurrency`. Для каждого режима проверяются нулевое число ошибок,
структурное равенство output, числовой drift не более `1e-4`, peak RSS, состояния thermal до/после
и throughput. Небезопасный режим не может стать recommended. Production helper ограничивает
очередь 16 объектами и использует подтверждённый режим concurrency 2.

```bash
uv run photo-curator local-model-performance-benchmark \
  --asset-dir /path/to/previews --max-assets 48 --iterations 2 \
  --benchmark-modes 1x1,16x1,16x2 --max-peak-rss-mib 1024 \
  --output build/evidence/local-model-performance.json
```

Energy gate fail-closed: отсутствие `xctrace` или одного из трёх аргументов
`--energy-trace`, `--energy-joules`, `--max-energy-joules` оставляет общий `passed=false`, даже
когда capacity gate прошёл. Наличие `.trace` без измеренного значения и заранее заданного бюджета
также не считается доказательством.

Локальный smoke 2026-08-27 на 12 сохранённых JPEG и всех трёх моделях подтвердил одинаковые
результаты (max drift `5.6e-17`), nominal → nominal и ноль ошибок. Режим `16x2` дал 17.65 фото/с
против 11.83 фото/с у `1x1` при peak RSS 88.5 MB против 76.6 MB. Это capacity evidence, а не
закрытие energy/ANE gate; полный 48-photo run нужно повторить после восстановления preview.

Реальный PhotoKit repair 2026-08-27 подтвердил Full Photos access, но Photos/iCloud вернул
`CloudPhotoLibraryErrorDomain 1005` для полноразмерных кадров и только 48×64 cached previews для
всех 394 фото альбома `Cape Town 2024`. Такие файлы теперь покрыты отдельным resolution gate:
они остаются UI-only, все автоматические сигналы инвалидируются, а 394 решения переводятся в
`Review`. Этот прогон не является 48-photo model/performance или ranking acceptance evidence.

Decision confidence fail-closed: полнота сигналов и cross-model disagreement сохраняются и
показываются отдельно. Расхождение считается по независимым aesthetic outputs на общей
album-percentile шкале и уменьшает raw reliability, но не называется вероятностью. Вероятность
корректности появляется только после валидации Platt calibrator на непересекающихся calibration и
held-out albums; смена raw-confidence contract инвалидирует модели предыдущей версии.
