# Progress

- 2026-07-20: удалён небезопасный in-process PyObjC Vision fallback: на macOS 26.5.2
  `VNDetectFaceLandmarksRequest.performRequests` мог зависнуть навсегда и остановить весь
  pipeline. Production coordinator теперь требует native Swift helper и честно даёт warning
  без него; legacy compatibility-вызов изолирован subprocess с трёхсекундным timeout и
  нейтральным fail-safe. Native Swift Vision остаётся единственным production path.
- 2026-07-20: нативный Settings экспортирует воспроизводимый S0 quality corpus:
  только явные manual decisions и project-scoped A/B choices попадают в human manifest,
  а текущий Swipe Score сохраняется отдельно с schema/model provenance. Predicted
  dispositions, duplicate groups и автоматический Top-K не маскируются под truth. Schema v10
  и review-галерея сохраняют явный ordered Top-K и выбранного человеком лидера серии отдельно
  от prediction state. Произвольная multi-selection размечает и пропущенные алгоритмом серии,
  не завышая recall; серия готова только после manual decisions всех её кадров. Заполненные
  labels и frozen score snapshot можно импортировать обратно и получить PASS/FAIL без CLI.
  Settings в реальном времени показывает полноту каждого структурного quality gate.
- 2026-07-20: native Personal Taste больше не обучается и оценивается на одних данных:
  первые три выбора server-assigned calibration, затем blind held-out/calibration
  чередуются независимо от client params. Held-out выбор не делает готовую модель stale;
  profile/UI отдельно показывают training и verification counts, а retrain evidence
  рассчитывает held-out accuracy. Это создаёт честный путь к per-user uplift gate.
- 2026-07-20: real packaged demo QA поймал Hardened Runtime failure: ad-hoc backend не
  мог загрузить embedded Python framework из-за library validation/CDHash mismatch.
  Local/ad-hoc backend теперь получает узкий `disable-library-validation` entitlement;
  Developer ID path его не получает. Bundle verifier запускает реальный frozen JSONL
  worker, получает albums response и проверяет clean shutdown, а не доверяет только codesign.
- 2026-07-20: S8 packaging получил воспроизводимый notarization flow: только Developer
  ID Application + TeamIdentifier, credentials только в заранее созданном Keychain profile,
  pre-submit bundle audit, `notarytool --wait`, stapling, validation и Gatekeeper assess.
  Скрипт не содержит Apple ID/password; реальная отправка требует developer credentials.
- 2026-07-20: native Vision и Core ML benchmark reports теперь содержат измеренный
  process peak RSS (`getrusage.ru_maxrss`) и Python boundary fail-closed отклоняет отчёт
  без memory evidence. Это закрывает peak-memory instrumentation, но не подменяет
  Instruments energy/thermal/ANE attribution.
- 2026-07-20: S3 production inference foundation добавляет batch-only miss execution и
  content-addressed Core ML output cache по approved model SHA-256 + immutable render
  fingerprint. Cache write атомарен, corrupt/stale/error entries не принимаются, порядок
  caller сохраняется; смена render/model автоматически даёт miss. Неподтверждённая,
  изменившаяся или не `.all` модель fail-closed не запускается.
- 2026-07-20: schema v9 реализовала отсутствовавший `model_registry`: Core ML package
  получает детерминированный streaming SHA-256, license/source/commercial-use contract,
  `.all` compute policy и compatibility evidence. Research-only модель или модель без
  compatibility pass не может стать approved; изменение/исчезновение файлов переводит
  уже зарегистрированную модель в invalid. CLI register/list/benchmark/approve не даёт
  обойти registry; approval дополнительно требует runtime pass и положительный held-out
  uplift над baseline не ниже predeclared threshold. Реальная лицензированная модель и
  Instruments attribution остаются открытыми gates.
- 2026-07-20: добавлен model-agnostic S3 Core ML benchmark adapter: Swift helper
  принимает внешние `.mlmodel`/`.mlpackage`/`.mlmodelc`, явно использует
  `MLComputeUnits.all`, возвращает versioned classification/vector outputs и median
  latency, а Python/CLI слой проверяет полноту результата и удаляет временные requests.
  Helper реально компилируется на этом Mac; модель и hardware attribution не заявлены.
  Публичные Apple MobileCLIP weights исключены из продуктового кандидата из-за
  research-only non-commercial model license.
- 2026-07-20: native settings теперь замыкают весь Personal Taste lifecycle:
  pause/resume, локальный JSON export и подтверждённое полное удаление preferences.
  После pause/resume/reset текущий ready-project пересчитывает только decisions,
  поэтому generic/personal ranking в галерее не остаётся устаревшим.
- 2026-07-20: исправлен TCC-контракт native Photos доступа: GUI запрашивает
  разрешение до запуска worker, все PhotoKit executables получают официальный
  `com.apple.security.personal-information.photos-library` entitlement и Hardened
  Runtime, а bundle audit проверяет identities, signed embedded Info.plist,
  entitlement, отсутствие web/osxphotos runtime и лимит размера. Для зависшего
  системного prompt UI через 5 секунд показывает понятную причину и кнопку ровно
  в Privacy & Security → Photos. Stable local certificate trust ждёт ручного
  подтверждения macOS; реальный inventory после этой сборки ещё не перепроверен.
- 2026-07-20: native bundle перешёл на отдельный minimal worker entrypoint:
  FastAPI/Jinja/web assets и `osxphotos` больше не пакуются в `.app`; размер уменьшился
  с 103 MB до 50 MB. Frozen production status/shutdown и demo albums JSONL проверены
  на собранном bundle; web CLI остаётся только в development install.
- 2026-07-22: небезопасный local-signing workflow удалён. `make app` всегда использует
  ad-hoc подпись по умолчанию и не обращается к login Keychain; стабильная release-подпись
  возможна только через явно переданный Apple Developer identity. Регрессионный contract
  запрещает возвращать `security import`, `add-trusted-cert` и путь login Keychain в
  build scripts.
- 2026-07-20: packaging получил stable local signing identity через login Keychain.
  Этот экспериментальный путь признан небезопасным и полностью удалён 2026-07-22.
- 2026-07-20: native pipeline lifecycle стал восстанавливаемым: worker startup
  переводит оставшиеся running jobs в `interrupted`, resume начинает с того же
  stage, а cooperative cancel проверяется между stages и в per-asset loops. SwiftUI
  показывает явные кнопки Stop/Continue; cancel во время одного blocking
  PhotoKit/Vision helper по-прежнему завершается после возврата helper.
- 2026-07-20: S8 получил versioned `release-benchmark` для coordinator hot paths:
  на этом arm64 Mac median из трёх запусков составил 0.002/0.045/0.132 s для
  100/2 000/5 000 rows. Измеряются duplicate candidate reduction, Swipe Score,
  taste-pair selection и JSONL serialization; это не подменяет будущий full-image,
  SwiftUI scrolling и Instruments energy acceptance.
- 2026-07-20: runtime QA нашёл и устранил frozen-path defect native Vision:
  backend искал helper в `Contents/native` вместо `Contents/Resources/native`,
  из-за чего aesthetics/faces/feature prints молча становились `unavailable`.
  Swift launcher теперь передаёт явный bundled path, а Python fallback исправлен и покрыт тестом.
- 2026-07-20: native review получил клавиатурный workflow: стрелки меняют кадр,
  1/2/3 задают Keep/Review/Reject, Space открывает native Quick Look, Cmd+Z
  точно возвращает предыдущий manual override. Выбор альбома, density,
  последний ready/running project и review восстанавливаются после перезапуска;
  карточки получили VoiceOver labels/hints и явный focus outline.
- 2026-07-20: S4/S7 native A/B calibration замкнула Personal Taste loop:
  worker выбирает неоценённые близкие по Swipe Score пары вне duplicate group,
  SwiftUI собирает три явных выбора, локально обучает profile и сразу
  пересчитывает decisions без повторного Vision analysis.
- 2026-07-20: native worker теперь fail-closed требует bundled PhotoKit helper:
  неполная сборка больше не откатывается на `osxphotos` и не пытается
  копировать `Photos.sqlite`.
- 2026-07-20: S7 diversity vertical slice заменил placeholder `diversity_value=50`:
  финальный Keep-набор теперь получает versioned evidence из Apple Vision feature prints,
  семантически близкие четвёртые кадры переводятся в Review, а temporal fallback ограничивает
  одну сцену тремя кадрами. Favorites, edited assets и лидеры серий остаются защищёнными.
- 2026-07-20: S6 native PhotoKit source vertical slice заменил filesystem-доступ
  установленного приложения к `Photos.sqlite`: обычные и Shared albums читаются через
  публичный PhotoKit, review-renders сохраняются только в service cache, source assets
  revalidate по local identifiers, а одобренный Best добавляется в Photos тем же публичным
  API. Реальная установленная сборка показала `Cape Town 2024 · 394 фото`, не открыла
  TCP listener и завершила GUI+worker по Cmd+Q. Реальный publish apply намеренно не выполнен.
- 2026-07-20: первый S5 native workflow заменил browser launcher: SwiftUI
  NavigationSplitView, четыре явных шага, native progress, LazyVGrid review, компактные
  score/reasons, ручные decisions и подтверждение publish. Runtime не открывает порт;
  packaged demo provider добавлен для воспроизводимого GUI acceptance. Реальный source
  всё ещё идёт через osxphotos и остаётся migration gap до S6 PhotoKit intake.
- 2026-07-20: добавлена первая часть S5 boundary: versioned correlated JSONL worker
  через stdin/stdout без HTTP/localhost. Он предоставляет нативному клиенту albums,
  projects, progress, ranked assets, decisions, Personal Taste и approval-gated publish.
- 2026-07-20: реализован Personal Taste vertical slice и schema v8: явные A/B examples,
  calibration/held-out split, local pairwise Float32 model, persistent personal delta,
  pause/export/reset и API end-to-end. Реальный held-out per-user uplift ещё не доказан.
- 2026-07-20: реализован Swipe Score v1 и schema v7: Vision aesthetics — generic
  baseline, rich Apple/attention/series/portrait signals — enrichment, technical quality —
  penalty/safety layer. Score, confidence, reasons и model provenance сохраняются отдельно
  от decisions; human-labelled uplift над legacy heuristic ещё не доказан.
- 2026-07-20: native Vision встроен в real-project pipeline: schema v6 хранит каждый
  signal с source fingerprint, engine/request revision, duration и unavailable reason;
  packaged `.app` получает заранее собранный helper и не требует Swift toolchain runtime.
- 2026-07-20: реализован первый S1 vertical slice: независимый Swift Apple Vision CLI,
  публичные aesthetics/feature-print/saliency/face requests, capability и request revision
  provenance, warmup/measured latency, Python runner, CLI export и acceptance score snapshot.
  Synthetic real-framework smoke прошёл; corpus benchmark и DB/pipeline integration впереди.
- 2026-07-20: S0 quality harness переведён на acceptance schema v2: held-out A/B
  preference accuracy, Top-K agreement, series leaders, safety metrics и versioned
  score snapshots для честного сравнения engine versions. Реальная human-разметка
  остаётся обязательной до завершения S0.
- 2026-07-20: принят новый продуктовый контракт V2: универсальный Swipe Score,
  Personal Taste Profile, Apple Vision/Core ML baseline и целевой native SwiftUI/AppKit
  workflow. Roadmap и ADR готовы; реализация S0–S8 ещё не завершена.
- 2026-07-20: добавлен self-contained macOS `.app`: AppKit launcher, управление backend
  из Dock/menu bar и веб-настроек, остановка при `⌘Q`, сборка/установка через Makefile,
  локальная code signing и проверка bundle.

## Current milestone

- Milestone: S0 — Truthful baseline and preference dataset
- Status: S0 harness, S1 native runtime, S2 score and S4 taste implementation exist;
  real labels and measured generic/personal uplift remain acceptance gates

## Product roadmap

The authoritative roadmap is [`ROADMAP.md`](ROADMAP.md). The product now targets a
personalized Swipe Score, Apple-native analysis and a native user-confirmed Best album.

- [ ] S0 — labelled preference dataset and frozen baseline
- [ ] S1 — Apple-native Vision benchmark
- [ ] S2 — versioned Swipe Score v1
- [ ] S3 — validated Core ML enrichment and hardware evidence
- [ ] S4 — local Personal Taste Profile
- [ ] S5 — native SwiftUI/AppKit workflow
- [ ] S6 — native PhotoKit source and publish integration (код готов; real apply gate открыт)
- [ ] S7 — personal, explainable and diverse review experience
- [ ] S8 — signed native release gate

Historical R0–R6 are implemented and retained as migration assets: inventory, previews,
duplicate protections, resumability, Shared snapshots, review and safe publish.

The implemented transition UX follows choose, analyze, review and save. The target native
workflow adds explicit optional taste calibration; deleting the last project backed by a
Shared snapshot continues to remove that service-owned disk copy.

## Current verified evidence

```text
real subset: 20 photos from Черногория — локально
all local tools including Apple Vision       — 3.55 s
candidate reduction                          — 39 / 190 pairs
confirmed series                             — 2 near groups, 2 members each
Vision                                       — 21 faces, 21 eye landmarks, 18 quality samples
curated result                               — 3 selected / 16 review / 1 excluded
score range                                  — 26–90
2,000 synthetic rows                         — 5,454 / 1,999,000 pairs in 0.013 s
5,000 synthetic rows                         — 19,059 / 12,497,500 pairs in 0.039 s
80-photo labelled generated fixture          — exact/resized/cropped/blur/dark/overexposed passed
real 20-photo Best publish dry-run            — dry_run_ok, 10 UUIDs, apply not run
real Chrome desktop + exact 390 px workflow   — passed; no overflow or console errors
demo pipeline rerun from browser              — 5 selected / 3 review / 4 excluded / 2 series
fast rerun terminal-state refresh              — passed; no stale "Анализ запущен" status
responsive navigation regression contract     — passed
390 px overflow contract                       — scroll width 390 at a 390 px viewport
home workflow                                  — album → density → analysis; technical details collapsed
human-labelled manifest + metric evaluator     — implemented; real labels still pending
schema v2 pairwise + Top-K score comparison    — implemented; real labels still pending
native Vision synthetic smoke                  — aesthetics 0.663; feature print 768; all signals OK
native signal provenance                       — schema v6; 4 signals per analyzed asset
Swipe Score v1 persistence                     — schema v7; technical-first weighting replaced
Personal Taste model                           — schema v8; pairwise train/pause/export/reset
native JSONL coordinator transport             — schema v1; HTTP/localhost не используется
spec traceability audit                        — polling/resume/missing/resolution/cache safety fixed
ruff format / check                            — passed
Shared Album disk snapshot + manifest           — implemented; Photos Library writes removed
local album provider → project API               — covered end-to-end in tests
disk Best → Photos native PhotoKit publish       — implemented; dry-run + explicit approval gated
native SwiftUI app + Vision/PhotoKit helpers      — signed bundle verified; real apply not run
real native PhotoKit album inventory              — Cape Town 2024 · 394; no Photos.sqlite access
native app network surface                        — no TCP listener; Cmd+Q stopped GUI + worker
temporary Montenegro snapshot/project/cache     — removed; recoverable copies moved to Trash
legacy 39-photo Photos test album                — removed; library assets left untouched
Core ML generic helper                           — compiled; capability v1; no model bundled
pytest excluding native Vision file + face smoke — 145 passed; macOS smoke waits for Photos TCC
```

The browser opened `127.0.0.1` directly and verified the compact workflow
at an exact 390 x 844 px viewport. The document client and scroll widths were both
390 px, the console contained no warnings or errors, and a fast full-pipeline rerun
returned to a clean `ready` screen without a stale launch message. A real 50–100
photo human-labelled preference corpus is now the S0 quality gate. The real macOS Photos
Best dry-run passed; native disk-snapshot apply was intentionally not executed against the
real library and remains an S6/S8 acceptance item.

## Previous MVP evidence — retained for history only

```text
uv sync                                     — success
ruff format --check .                       — passed
ruff check .                                — passed
pytest                                      — 48 passed
pytest --cov=photo_curator                   — 81% total coverage
osxphotos batch-edit capability flags       — confirmed
demo full pipeline                          — 12/12 previews, metrics and decisions
cache reuse and source invalidation         — automated test passed
manual override after restart/reanalysis    — automated test passed
publisher dry-run/apply state machine       — mocked integration passed
source drift and resolution inversion       — automated tests passed
>3000 asset candidate reduction             — automated test passed
loopback/session/CSRF/media path isolation   — automated tests passed
desktop + 390 px browser workflow            — passed, no horizontal overflow
duplicate manual override via browser        — passed end-to-end
```

The checks below covered the former reject-first implementation. They do not prove
the new roadmap complete and must not be used as release acceptance.

## Current quality gate

The root state failure from the old 1,671-photo run is covered by sentinel and bounded
candidate tests, but ranking quality is not proven. S0 requires a real 50–100-photo corpus
with pairwise preference, best-in-series and Top-K labels before a full-album scale run.

## Safety invariants verified in code

- bind только `127.0.0.1`, Host allowlist, startup session и CSRF;
- никаких direct Photos DB writes, automatic delete, cloud calls или telemetry;
- subprocess без shell, UUID file sorted one-per-line;
- missing/failed/ambiguous cases не получают automatic reject; Favorite/edited/leader
  are selected unless analysis is unavailable;
- higher-resolution inversion блокирует publish без manual override;
- recursive cleanup разрешён только внутри cache root.
