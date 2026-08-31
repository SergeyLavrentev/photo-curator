# Roadmap: analysis engine, gallery and safety refactor

Дата начала: 2026-08-30.

Этот roadmap переводит результаты глубокого аудита ядра, PhotoKit/publish boundary,
Quality Lab и нативной галереи в последовательную программу изменений. Он дополняет
`docs/16-execution-roadmap.md`: старый документ описывает становление продукта, этот —
исправление выявленных дефектов и переход к scene-aware Engine v3.

## Правила статуса

- `[ ]` — работа не завершена либо не имеет требуемого evidence.
- `[x]` — реализовано в указанном commit и прошли перечисленные проверки.
- Code, tests, app build, installed runtime, human acceptance и release считаются разными
  уровнями готовности.
- Один логический пункт закрывается отдельным scoped commit. Commit не включает unrelated
  изменения рабочего дерева.
- Ни одна галочка не разрешает удалять Photos assets, менять originals/metadata, выполнять
  `make install`, publish, notarization или release без отдельной явной задачи.
- Engine v3 до human acceptance работает только в shadow/advisory режиме; существующие
  manual decisions сохраняются.

## Целевой пользовательский результат

Для альбома из 1 500–2 000 отпускных фотографий приложение должно:

1. полностью игнорировать видео и явно показывать их число;
2. разделять поездку на эпизоды, сцены и сравнимые серии;
3. находить exact duplicates отдельно от похожих, но содержательно разных кадров;
4. для серии из десятков технически нормальных кадров предлагать небольшой набор лучших,
   сохраняя важные моменты, людей, ракурсы и разнообразие;
5. объяснять брак отдельно от эстетического выбора;
6. показывать всю серию в Gallery/Compare/Survey независимо от страницы и bucket;
7. оставаться отзывчивым при 2 000–5 000 фото;
8. никогда не превращать устаревший анализ, лабораторную разметку или отсутствующий лидер
   duplicate-группы в опасную рекомендацию.

## R0 — зафиксировать baseline и границы доказательств

- [x] Провести read-only аудит кода, текущей SQLite, UI и acceptance evidence.
  Evidence 2026-08-30: проект `Синий жук и бобр`, 1 734 фото, 372 видео,
  291 duplicate/scene group, 998 ungrouped assets, 653 temporal demotions.
- [x] Выполнить baseline `make lint` и полный `uv run pytest -q`.
  Evidence 2026-08-30: lint passed, 220 tests passed.
- [ ] Сохранить versioned machine-readable baseline Engine v1/V2 для нескольких альбомов,
  не изменяя truth labels и product decisions.
- [ ] Добавить reproducible audit fixtures для каждого P0/P1 дефекта ниже.

## R1 — немедленная изоляция опасных контуров

- [x] Отделить Quality Lab disposition/defect truth от `decisions`: лабораторная разметка
  не меняет `manual_disposition`, `manual_selection`, Best или Reject.
  Evidence: schema v19 хранит `expected_disposition` в `quality_asset_labels`; native
  regression проверяет неизменность product disposition/selection после lab label.
- [ ] Добавить безопасную миграцию уже созданных Quality Lab overrides. Миграция должна
  отличать lab provenance от настоящих пользовательских решений и не переписывать их
  без доказуемого происхождения и отдельного backup.
- [x] Исключить `no_longer_exists`, missing/degraded assets из duplicate grouping и leader
  selection; удалить затронутые группы при inventory/preview invalidation и не показывать
  исчезнувшие assets в активных gallery counts.
  Evidence: unavailable-asset и signal-rerank regressions, повторный inventory удалённого
  asset очищает metrics/group membership и уменьшает gallery total.
- [x] Исключить stale-revision assets из publish safety evidence и любых сохранённых leaders.
  Evidence: source fingerprint v2 включает SHA-256 содержимого, а publish revalidation
  сравнивает свежий provider render каждого кандидата и duplicate leader.
- [x] Запретить near/exact Reject, если fresh active snapshot не содержит проверенного
  сохранённого лидера или эквивалентной full-resolution копии.
  Evidence: publish всегда сбрасывает provider caches, заново читает assets/membership и
  исключает duplicate loser при исчезнувшем retained leader.
- [x] Перед каждым dry-run и apply заново читать exact source membership и сравнивать
  render/content fingerprint; любое изменение требует нового analysis/dry-run.
- [x] Добавить в snapshot и publish revalidation явные media type и modification/edit
  revision из PhotoKit вместо косвенной проверки через versioned render.
  Evidence: source helper v2 возвращает media type/subtypes, fractional creation/modification,
  adjustment state и source revision; apply сравнивает fresh revision и content fingerprint.
- [x] Сериализовать start/resume/delete per-project lock/state machine; delete не может
  пересекаться с зарегистрированным analysis Future, даже до входа worker в `run`.
  Evidence: coordinator фиксирует `running` до submit, а start/delete проходят через один
  project operation lock; coordinated regression удерживает Future до DB work.
- [x] Провести publish/dry-run через тот же per-project lock и запретить их пересечение с
  analysis Future и другим apply.
  Evidence: native worker проверяет live Future под project operation lock; concurrent
  regression удерживает первый apply и подтверждает, что второй не входит в publisher.
- [x] Зарезервировать уникальный destination album identity до content apply и исключить
  reuse существующего одноимённого Photos album.
  Evidence: dry-run name содержит reservation token; confirmed apply сначала создаёт пустой
  album, сохраняет PhotoKit local identifier и добавляет assets только по нему. Retry использует
  тот же identifier, а helper отклоняет существующее имя и identity/name mismatch.

### R1 acceptance

- [x] Ghost duplicate regression: после исчезновения лидера живая копия не получает Reject,
  publish fail-closed до свежего анализа.
- [x] Source-drift regression: membership/content change после dry-run блокирует apply.
- [ ] Реальный PhotoKit regression: edit revision после dry-run блокирует apply.
- [x] Concurrency regression: coordinated start/delete не удаляет проект между submit и
  первым DB write pipeline.
- [x] Concurrency regression: parallel apply одного проекта выполняются последовательно.
- [x] Regression: независимые publish plans получают разные имена и PhotoKit identities;
  повторный apply одного failed plan не создаёт новый destination.
- [ ] Реальный disposable PhotoKit acceptance на edited asset и source membership drift.

## R2 — snapshot, schema и media integrity

- [x] Ввести immutable `album_snapshot_items`: UUID, source membership, album position,
  fractional creation date, modification date, media type/subtype, edit state, dimensions,
  orientation и revision/render fingerprint.
- [x] Централизовать invalidation derived data при изменении времени, membership, render,
  media subtype или edit state.
  Evidence: каждый inventory создаёт append-only snapshot; upsert сравнивает revision fields и
  удаляет metrics/signals/auto decisions и затронутую duplicate group только изменённого asset.
- [x] Сделать schema migration атомарной: pre-migration backup, обязательные core tables,
  post-schema verifier, fault-injection tests; не повышать `user_version` частичной схеме.
  Evidence: все pending DDL и `user_version` выполняются в одной `BEGIN IMMEDIATE`
  transaction; verifier проверяет version/tables/columns/integrity/FK до commit, а injected
  post-verifier failure возвращает v18 без колонки v19.
- [x] Исправить same-process degraded/iCloud preview repair: tiny render обязан повторно
  пройти provider/helper либо остаться честно degraded.
  Evidence: preview stage адресно вызывает `repair_assets` только для degraded/missing UUID;
  PhotoKit source v3 удаляет tiny cache и повторяет request с network access. Same-process
  regression восстанавливает analysis-grade renders и запускает ранее пропущенные models.
- [x] Описать и реализовать явную policy для video, Live Photo, animated image, burst,
  hidden, edited и iCloud-only assets.
  Evidence: `docs/05-photos-integration.md` разделяет provenance и model eligibility; motion/
  animation не выдаются за проанализированные, hidden не является reject evidence, degraded
  iCloud fail-closed исключается из моделей, edited revision инвалидирует analysis.
- [x] Для обычного PhotoKit album полностью исключать видео до render/model pipeline и
  показывать в UI `N видео пропущено`.
  Evidence: metadata snapshot сохраняет video provenance/count, но source helper фильтрует
  `.image` до render, а coordinator повторно фильтрует до active assets; mixed synthetic
  regression подтверждает отсутствие video UUID в assets/signals.
- [x] Включить все разрешённые burst members в source snapshot, если это не нарушает
  пользовательскую Photos boundary.
  Evidence: read-only PhotoKit fetch задаёт `includeAllBurstAssets=true`; каждый member остаётся
  исходным `PHAsset` с собственным UUID и попадает в общий immutable snapshot path.
- [ ] Временно скрыть Shared Album intake либо завершить immutable local snapshot с safe
  filenames, reference counting и честной Live Photo motion policy.

### R2 acceptance

- [x] Synthetic mixed-media regression: full video остаётся только в snapshot и не попадает
  в analysis signals/gallery candidates.
- [ ] Mixed-media corpus: photo, full video, Live Photo, animated image, burst, hidden,
  edited и iCloud-only; model-call audit содержит только разрешённые media types.
- [x] Повторный inventory сохраняет исходный album order и инвалидирует затронутые stages.
  Evidence: повторный immutable snapshot сохраняет точный изменённый provider order;
  revision drift одного asset удаляет только его metrics/signals/swipe score, сохраняя
  derived evidence неизменившегося соседа.
- [x] Migration проходит с чистой, старой, частичной и аварийно прерванной SQLite.
  Evidence: clean/idempotent schema, upgrades v9/v18/v21/v22, partial-schema fail-closed,
  pre-migration backup и injected post-verifier rollback сохраняют старый `user_version`
  и не оставляют частично добавленные columns.

## R3 — Engine v3: правильная декомпозиция задачи

Целевая иерархия:

```text
Album -> capture episode -> semantic scene -> moment/near-duplicate stack -> ranked subset
```

- [x] Exact duplicates выделить в независимый fail-safe слой без эстетической семантики.
  Evidence: Engine v3 shadow создаёт album-wide `exact_duplicate` nodes только по
  render-equivalence hash; эстетика не участвует в membership, а protected/quality выбирают
  лишь advisory leader.
- [x] Capture episodes строить по album order, fractional time, gap/change points и GPS,
  если location доступна; отсутствие timestamp не должно отключать visual matching.
  Evidence: Engine v3.1 сохраняет PhotoKit coordinates в versioned immutable snapshot,
  включает location drift в revision/invalidation, использует расстояние только как episode
  change point и продолжает visual scene/stack matching при отсутствующих timestamp/GPS.
- [x] Semantic scenes строить внутри/между соседними episodes по validated embeddings и
  change-point detection, а не по жёсткому окну 120 секунд.
  Evidence: Engine v3 строит scene boundaries по adaptive median/MAD change point соседних
  visual feature prints с fallback на image metrics; фиксированного scene window нет.
- [x] Near-duplicate stacks формировать только для реально сравнимых поз, моментов и
  ракурсов; исключить star-clustering fragmentation и произвольный hard max=3.
  Evidence: stack membership требует complete-link совместимости с каждым участником,
  проверяет aspect/embedding/hash/histogram/burst и не имеет member limit; A-B-C regression
  не объединяет визуально несовместимые края через общий B.
- [x] Удалить blind temporal demotion. Время является candidate signal, но не доказательством
  визуальной избыточности.
  Evidence: diversity v2 больше не применяет fallback `3 кадра / 120 секунд`; при отсутствии
  валидного feature print все технически хорошие кадры остаются без demotion. Время продолжает
  только расширять candidate retrieval для последующей визуальной проверки.
- [x] Разделить objective defect, technical quality, aesthetic appeal, personal taste,
  leader quality и marginal novelty; не начислять один штраф несколько раз.
  Evidence: каждый scene member хранит эти каналы отдельно; personal taste остаётся `null`,
  пока не валидирован, objective defect — `not_confirmed`, а общий rank не используется как
  safety disposition.
- [x] Сохранить непрерывный rank signal без массового clamp в 0/100.
  Evidence: shadow members хранят непрерывный weighted rank и отдельный marginal novelty.
- [x] Ввести variable per-scene budget: минимум один representative essential scene,
  дополнительные кадры только за новый момент, человека, выражение, ракурс или coverage.
  Evidence: budget зависит от `sqrt(scene size)` и density, protected assets сохраняются,
  остальные выбираются greedy quality+novelty; synthetic 100-frame scene даёт 10 picks.
- [x] Отдельно объяснять `безопасно оставить`, `лучший в серии`, `альтернатива` и
  `подтверждённый брак`; низкая привлекательность сама по себе не является delete evidence.
  Evidence: immutable member evidence содержит `safety_disposition=keep` и независимый
  `series_role` (`best_in_stack`, `alternative`, `redundant_but_good`, `protected`);
  неподтверждённый defect никогда не превращается в Reject в shadow.
- [x] Запускать Engine v1/V2 и v3 параллельно в immutable shadow snapshots.
  Evidence: отдельный pipeline stage сохраняет versioned `engine_shadow_runs/nodes/members`,
  повторный идентичный input переиспользует run, изменённый создаёт новый; regression
  подтверждает неизменность product decisions и старого snapshot.

### R3 acceptance

- [ ] Near-series recall >= 95% на album-separated human corpus.
- [ ] Best-in-series Top-1 >= 85%; дополнительно считать per-scene NDCG/Top-K.
- [ ] Essential-scene coverage проходит согласованный порог, включая события, людей и
  короткие уникальные сцены.
- [ ] Reduction 35–60% достигается adaptive budget, а не глобальным percentile cutoff.
- [ ] False automatic Reject <= 0.5%; objective-defect precision >= 98%.
- [ ] Сценарий 50–100 технически хороших кадров одной локации сокращается до осмысленного
  набора без потери ключевых моментов.

## R4 — лица, объективный брак и model policy

- [ ] Заменить `eyesDetected` на реальную оценку eyes-open/blink/occlusion и сопоставлять
  качество каждому важному лицу, а не брать максимум по группе.
- [ ] Для shortlisted/ambiguous stacks выполнять high-resolution ROI pass: лица, глаза,
  subject sharpness и motion blur; полный альбом не декодировать повторно без необходимости.
- [ ] Перед допуском NIMA, MobileCLIP, MUSIQ, taste или Codex к leader/final ranking требовать
  album-separated held-out uplift именно по leader/Top-K/coverage.
- [ ] MobileCLIP/совместимые embeddings оценить прежде всего для scene segmentation и
  novelty, а не как абсолютную эстетику.
- [ ] Исправить horizon detector: отличать реальный горизонт от архитектурных/контрастных
  линий и измерить false-positive rate.
- [ ] Сделать ensemble/calibrator реальным versioned product pipeline с provenance,
  уникальными парами и независимыми calibration/held-out albums.
- [ ] Codex использовать как budgeted sparse teacher/auditor на disagreement/uncertainty.
- [ ] Для групп больше лимита запроса использовать tournament с общими anchors; результаты
  chunks не могут напрямую перезаписывать global leader.
- [ ] Distill подтверждённые teacher labels в локальный ранкер; offline работа остаётся
  полноценным baseline.

## R5 — независимый Quality Lab и human truth

- [x] Хранить defect labels, ranking preferences, Top-K и human series отдельно от product
  manual decisions и prediction-conditioned review.
  Evidence: Quality Lab использует только `quality_asset_labels` и
  `quality_preference_examples`; product decisions и taste onboarding pairs не входят в
  human quality export.
- [ ] Top-K сравнивать в одном universe: human и model ранжируют один и тот же frozen sample.
- [ ] Human series должна иметь source provenance и temporal/semantic coherence; случайный
  набор из двух UUID не закрывает gate.
- [ ] Разрешить разметку scene budget, essential moment, redundant-but-good и причины
  относительного выбора между технически нормальными кадрами.
- [ ] Taste calibration и holdout брать из разных albums/episodes; correlated pairs из
  шести кадров не считать независимым evidence.
- [ ] Structural readiness отделить от evaluator pass и release eligibility.
- [x] Устранить расхождение CLI/native export источников held-out preference pairs.
  Evidence: CLI и native worker вызывают один database-backed exporter; оба читают только
  project-scoped `quality_preference_examples`, никогда global taste preferences.

## R6 — series-first Gallery и UX

- [x] Добавить API `series(group_id)` с полным составом независимо от page и
  Pick/Alternative/Reject bucket.
  Evidence: typed schema v1 endpoint читает active image membership напрямую из
  `duplicate_members`, а regression разводит одну серию по Pick/Alternative при page limit 1 и
  всё равно получает полный состав; inactive/video rows исключаются.
- [x] Expand/Compare/Survey загружают явный series context и никогда не подставляют
  произвольную фотографию.
  Evidence: series cache живёт отдельно от gallery page; expanded stack инъецирует полный
  context, Compare/Survey до загрузки показывает только выбранный кадр, не случайного соседа.
- [x] Устранить задержку single-click: selection выполняется сразу, Details получает
  отдельное действие либо корректно exclusive AppKit recognizer.
  Evidence: основной image surface — native `Button`, competing single/double tap recognizers
  удалены, Details вынесен в отдельную info-кнопку и context menu.
- [ ] Разделить монолитный `AppModel` минимум на workflow, gallery paging, selection,
  analysis progress, image pipeline и Quality Lab state.
  - [x] Series cache, selection, rating, detail generation guards и neighbour prefetch вынесены
    в отдельный `PhotoCuratorSeriesModel.swift`; основной state owner сохранён.
- [x] Prefetch декодирует настоящий `reviewPath`, имеет in-flight coalescing, cancellation,
  visible priority и отдельные thumbnail/review caches.
  Evidence: selection prefetch использует `reviewPath`, пять соседей отменяются при смене окна;
  shared decode учитывает consumers и повышает utility request до visible priority; два bounded
  `NSCache` разделяют card и review pixels.
- [x] Identity image task включает `(path, maxPixelSize)` и очищает stale image при смене.
  Evidence: SwiftUI task identity также включает cache epoch; loader очищает старый image до
  cache/decode lookup, а repair атомарно инвалидирует cache, in-flight work и active views.
- [x] Реализовать бесконечную автоподгрузку, переход стрелкой через внутреннюю batch boundary,
  scroll-to-selection, pinned toolbar и честный loaded/total.
  - [x] Нижний sentinel автоматически загружает следующий 36-item keyset batch без кнопки
    пагинации; стрелка вправо на последнем загруженном кадре выбирает первый новый кадр,
    selection прокручивается в видимую область, а footer показывает loaded/total. При ошибке
    отдельная кнопка повторяет именно неудавшийся запрос и не служит обычной пагинацией.
  - [x] Toolbar режимов и размера карточек находится вне gallery `ScrollView`, сохраняет
    положение при длинной прокрутке и отделён material background/divider.
- [ ] Исправить Survey для 5–6 кадров, workspace Alternative action, stale detail/page races,
  empty shared-only picker и неверные progress/warning тексты.
  - [x] Detail response имеет generation/project/selection guards и не может открыть ранее
    выбранную фотографию поверх более нового запроса.
  - [x] Decision response изменяет только исходный bucket/generation; при смене вкладки или
    reload текущая page перечитывается и не принимает optimistic rollback/response старого
    page context.
  - [x] Survey использует 3×2 для пяти-шести кадров; workspace имеет явный Alternative,
    который меняет selection независимо от safety disposition. Schema v22 разносит manual
    safety/selection overrides и отклоняет запоздалую mutation generation после restart.
  - [x] Album picker показывает Shared-only источник даже при пустом regular list; stage detail
    выводит реальные warning/error counts вместо безусловно нейтрального processed/total.
- [x] Заменить gesture-only controls на `Button`/accessibility actions, добавить selected
  traits и guards для bare-key shortcuts при TextField/Quality Wizard focus.
  - [x] Gallery card, workspace photo и filmstrip selection используют `Button`; selected state
    опубликован как accessibility trait.
  - [x] Gallery commands проверяют first responder и не выполняют bare-key/Command-Z action,
    пока фокус находится в `NSTextField`/`NSTextView` либо открыт Quality Wizard.

### R6 performance acceptance

- [ ] Click-to-highlight p95 < 50 ms.
- [ ] Warm selected-photo/Loupe transition p95 < 100 ms, cold < 250 ms.
- [ ] 2k/5k gallery имеет ненулевой scroll range, bounded RSS и не блокирует MainActor
  при decode, worker restart или IPC.
- [ ] Series expand показывает полный состав через границы page/bucket.
- [ ] Retained VoiceOver/keyboard evidence покрывает выбор, series controls и decisions.

Synthetic evidence 2026-08-30: production `PhotoCard`/ImageIO benchmark schema v3 прошёл для
2k/5k; selection-state-to-highlight p95 5.1/9.1 ms, initial layout/decode 390/386 ms,
page-swap p95 159/185 ms, scroll p95 7.6/7.7 ms. Это подтверждает быстрый SwiftUI update после
распознанного действия, но не закрывает end-to-end click, Loupe, RSS или human visual gates.

## R7 — честный benchmark и behavioral tests

- [ ] Заменить текущий 36-card/no-op-scroll benchmark на production RootView/AppModel harness
  с cold cache на сценарий, decode readiness, append до 2k/5k, RSS, click, Loupe и series.
- [ ] Добавить XCTest/XCUITest либо эквивалентные behavioral tests для single/double click,
  series pagination, Compare/Survey, arrow pagination, stale responses и accessibility.
  - [x] Native worker behavioral regression покрывает полный series payload через page/bucket
    boundary и fail-closed фильтрацию inactive/video members.
- [ ] Добавить image pipeline tests для coalescing, cancellation, priority inversion и
  cache separation.
- [ ] Source-string contract tests оставить только как smoke и не использовать как UX proof.

## R8 — staged rollout и финальная приемка

- [ ] Выполнить blind human A/B Engine v1/V2 против v3 на нескольких независимых альбомах.
- [ ] Зафиксировать machine-readable evaluator report, immutable snapshots и model versions.
- [ ] Выполнить `make lint`, полный `make test`, `make app` и `make verify-app` на точном commit.
- [ ] После отдельного разрешения выполнить installed Choose -> Analyze -> Review -> Save
  на disposable PhotoKit album и проверить exact app version/build/signature/worker.
- [ ] Отдельно подтвердить affected-user path на большом реальном альбоме без удаления
  source assets и без автоматизации `Command+Delete`.
- [ ] Обновить требования/архитектуру/operations docs и закрыть только реально принятые gates.
- [ ] Release/publish/notarization остаются отдельной явной задачей.

## Предлагаемая последовательность атомарных commits

1. `docs: add analysis engine and gallery refactor roadmap`
2. `fix: isolate quality lab labels from product decisions`
3. `fix: exclude inactive assets from duplicate and publish safety`
4. `fix: revalidate source revisions before publish`
5. `fix: serialize project lifecycle mutations`
6. `fix: make schema migrations fail closed`
7. `feat: persist immutable album snapshot metadata`
8. `feat: add scene-aware Engine v3 in shadow mode`
9. `feat: add validated relative series ranking`
10. `feat: make gallery series-first across pages`
11. `perf: rebuild gallery image pipeline and benchmark`
12. `test: add human-labelled Engine v3 acceptance`

Порядок может дробиться дальше, если один пункт затрагивает несколько независимых safety
инвариантов. Объединять несвязанные исправления ради меньшего числа commits запрещено.
