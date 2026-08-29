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
- [ ] Добавить в snapshot и publish revalidation явные media type и modification/edit
  revision из PhotoKit вместо косвенной проверки через versioned render.
- [ ] Сериализовать start/resume/delete/publish per-project lock/state machine; delete и
  publish не могут пересекаться с работающим analysis Future.
- [ ] Зарезервировать уникальный destination album identity до apply и исключить reuse
  существующего одноимённого Photos album.

### R1 acceptance

- [x] Ghost duplicate regression: после исчезновения лидера живая копия не получает Reject,
  publish fail-closed до свежего анализа.
- [x] Source-drift regression: membership/content change после dry-run блокирует apply.
- [ ] Реальный PhotoKit regression: edit revision после dry-run блокирует apply.
- [ ] Concurrency regression: coordinated start/delete и parallel apply не теряют локальные
  данные и не объединяют разные destination albums.
- [ ] Реальный disposable PhotoKit acceptance на edited asset и source membership drift.

## R2 — snapshot, schema и media integrity

- [ ] Ввести immutable `album_snapshot_items`: UUID, source membership, album position,
  fractional creation date, modification date, media type/subtype, edit state, dimensions,
  orientation и revision/render fingerprint.
- [ ] Централизовать invalidation derived data при изменении времени, membership, render,
  media subtype или edit state.
- [ ] Сделать schema migration атомарной: pre-migration backup, обязательные core tables,
  post-schema verifier, fault-injection tests; не повышать `user_version` частичной схеме.
- [ ] Исправить same-process degraded/iCloud preview repair: tiny render обязан повторно
  пройти provider/helper либо остаться честно degraded.
- [ ] Описать и реализовать явную policy для video, Live Photo, animated image, burst,
  hidden, edited и iCloud-only assets.
- [ ] Для обычного PhotoKit album полностью исключать видео до render/model pipeline и
  показывать в UI `N видео пропущено`.
- [ ] Включить все разрешённые burst members в source snapshot, если это не нарушает
  пользовательскую Photos boundary.
- [ ] Временно скрыть Shared Album intake либо завершить immutable local snapshot с safe
  filenames, reference counting и честной Live Photo motion policy.

### R2 acceptance

- [ ] Mixed-media corpus: photo, full video, Live Photo, animated image, burst, hidden,
  edited и iCloud-only; model-call audit содержит только разрешённые media types.
- [ ] Повторный inventory сохраняет исходный album order и инвалидирует затронутые stages.
- [ ] Migration проходит с чистой, старой, частичной и аварийно прерванной SQLite.

## R3 — Engine v3: правильная декомпозиция задачи

Целевая иерархия:

```text
Album -> capture episode -> semantic scene -> moment/near-duplicate stack -> ranked subset
```

- [ ] Exact duplicates выделить в независимый fail-safe слой без эстетической семантики.
- [ ] Capture episodes строить по album order, fractional time, gap/change points и GPS,
  если location доступна; отсутствие timestamp не должно отключать visual matching.
- [ ] Semantic scenes строить внутри/между соседними episodes по validated embeddings и
  change-point detection, а не по жёсткому окну 120 секунд.
- [ ] Near-duplicate stacks формировать только для реально сравнимых поз, моментов и
  ракурсов; исключить star-clustering fragmentation и произвольный hard max=3.
- [ ] Удалить blind temporal demotion. Время является candidate signal, но не доказательством
  визуальной избыточности.
- [ ] Разделить objective defect, technical quality, aesthetic appeal, personal taste,
  leader quality и marginal novelty; не начислять один штраф несколько раз.
- [ ] Сохранить непрерывный rank signal без массового clamp в 0/100.
- [ ] Ввести variable per-scene budget: минимум один representative essential scene,
  дополнительные кадры только за новый момент, человека, выражение, ракурс или coverage.
- [ ] Отдельно объяснять `безопасно оставить`, `лучший в серии`, `альтернатива` и
  `подтверждённый брак`; низкая привлекательность сама по себе не является delete evidence.
- [ ] Запускать Engine v1/V2 и v3 параллельно в immutable shadow snapshots.

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

- [ ] Хранить defect labels, ranking preferences, Top-K и human series отдельно от product
  manual decisions и prediction-conditioned review.
- [ ] Top-K сравнивать в одном universe: human и model ранжируют один и тот же frozen sample.
- [ ] Human series должна иметь source provenance и temporal/semantic coherence; случайный
  набор из двух UUID не закрывает gate.
- [ ] Разрешить разметку scene budget, essential moment, redundant-but-good и причины
  относительного выбора между технически нормальными кадрами.
- [ ] Taste calibration и holdout брать из разных albums/episodes; correlated pairs из
  шести кадров не считать независимым evidence.
- [ ] Structural readiness отделить от evaluator pass и release eligibility.
- [ ] Устранить расхождение CLI/native export источников held-out preference pairs.

## R6 — series-first Gallery и UX

- [ ] Добавить API `series(group_id)` с полным составом независимо от page и
  Pick/Alternative/Reject bucket.
- [ ] Expand/Compare/Survey загружают явный series context и никогда не подставляют
  произвольную фотографию.
- [ ] Устранить задержку single-click: selection выполняется сразу, Details получает
  отдельное действие либо корректно exclusive AppKit recognizer.
- [ ] Разделить монолитный `AppModel` минимум на workflow, gallery paging, selection,
  analysis progress, image pipeline и Quality Lab state.
- [ ] Prefetch декодирует настоящий `reviewPath`, имеет in-flight coalescing, cancellation,
  visible priority и отдельные thumbnail/review caches.
- [ ] Identity image task включает `(path, maxPixelSize)` и очищает stale image при смене.
- [ ] Реализовать auto-pagination near end, переход стрелкой через page boundary,
  scroll-to-selection, pinned toolbar и честный текст размера страницы.
- [ ] Исправить Survey для 5–6 кадров, workspace Alternative action, stale detail/page races,
  empty shared-only picker и неверные progress/warning тексты.
- [ ] Заменить gesture-only controls на `Button`/accessibility actions, добавить selected
  traits и guards для bare-key shortcuts при TextField/Quality Wizard focus.

### R6 performance acceptance

- [ ] Click-to-highlight p95 < 50 ms.
- [ ] Warm selected-photo/Loupe transition p95 < 100 ms, cold < 250 ms.
- [ ] 2k/5k gallery имеет ненулевой scroll range, bounded RSS и не блокирует MainActor
  при decode, worker restart или IPC.
- [ ] Series expand показывает полный состав через границы page/bucket.
- [ ] Retained VoiceOver/keyboard evidence покрывает выбор, series controls и decisions.

## R7 — честный benchmark и behavioral tests

- [ ] Заменить текущий 36-card/no-op-scroll benchmark на production RootView/AppModel harness
  с cold cache на сценарий, decode readiness, append до 2k/5k, RSS, click, Loupe и series.
- [ ] Добавить XCTest/XCUITest либо эквивалентные behavioral tests для single/double click,
  series pagination, Compare/Survey, arrow pagination, stale responses и accessibility.
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
