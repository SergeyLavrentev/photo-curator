# Photo Curator

Локальный macOS-помощник, который превращает большой неразобранный альбом в
персональную подборку фотографий, которые хочется «свайпнуть вправо». Будущий
`Swipe Score` соединяет Apple Vision, сравнение лучших кадров серии и запоминаемый
вкус пользователя. Исходный альбом и оригиналы не изменяются; автоматического
удаления нет.

Новый продуктовый план и критерии готовности находятся в [`ROADMAP.md`](ROADMAP.md).

> Python/web-реализация остаётся переходным диагностическим baseline. Product logic уже
> доступна полноценному SwiftUI-клиенту через локальный stdin/stdout JSONL worker без
> браузера, HTTP и localhost.

## Быстрый запуск

```bash
uv sync
uv run photo-curator --demo
```

## Нативное приложение для macOS

Photo Curator собирается как обычный self-contained `.app`: SwiftUI отвечает за весь
основной workflow, а встроенный локальный движок не зависит от репозитория, `uv` или
`.venv` во время запуска. Приложение появляется в Dock, Spotlight и списке программ.
Браузер, HTTP-сервер и localhost для нативного workflow не используются.

```bash
make app       # build/macos/PhotoCurator.app + локальная подпись
make install   # установить в /Applications и запустить
make stop      # завершить приложение и backend
make uninstall # переместить установленный .app в Корзину
```

`⌘Q` и завершение приложения из Dock сначала останавливают дочерний движок; из системных
настроек приложения его можно перезапустить. Для подписи сертификатом разработчика используйте
`make app SIGN_IDENTITY="Developer ID Application: …"`. Для стабильной локальной
подписи, не меняющей designated requirement между сборками, один раз выполните:

```bash
make local-signing-identity
```

macOS попросит подтвердить trust settings в login Keychain. После этого `make app`
автоматически выберет `Photo Curator Local Development`. Если identity ещё нет,
используется ad-hoc `-`. Локальный certificate не заменяет Developer ID и notarization
для распространения другим пользователям.

Release notarization не принимает пароль через Makefile или environment. Один раз создайте
защищённый профиль стандартной командой Apple, затем подпишите и отправьте сборку:

```bash
xcrun notarytool store-credentials "PhotoCurator Notary"
make app SIGN_IDENTITY="Developer ID Application: …"
make notarize NOTARY_PROFILE="PhotoCurator Notary"
```

`make notarize` fail-closed проверяет Developer ID authority и TeamIdentifier, выполняет
bundle audit, отправляет временный ZIP через `notarytool --wait`, stapling/validation и
Gatekeeper assessment. Финальный stapled архив появляется в `build/dist/`.

Сборка подписывает GUI и оба PhotoKit helper с Hardened Runtime и entitlement
`com.apple.security.personal-information.photos-library`. `make verify-app` проверяет
не только внешнюю подпись `.app`, но и identities/entitlements вложенных executables,
отсутствие legacy web/osxphotos runtime и размер bundle. При первом запуске macOS
попросит доступ к Фото; если системный prompt скрыт, приложение покажет кнопку перехода
непосредственно в Privacy & Security → Photos.

Переходный web baseline всё ещё можно запустить из репозитория для диагностики. Demo
использует 12 синтетических изображений и проходит workflow без доступа к Photos Library:

```bash
uv run photo-curator doctor
uv run photo-curator
```

Этот server mode не запускается установленным SwiftUI-приложением.

Для воспроизводимой проверки native GUI без Photos Library сборку можно запустить из
терминала с `PHOTO_CURATOR_NATIVE_DEMO=1`; это test-only режим с 12 generated images.
Реальная SwiftUI-сборка читает обычные и общие альбомы через встроенный публичный
PhotoKit helper. Она не открывает `Photos.sqlite` и не требует Full Disk Access.

## Workflow

Основной интерфейс показывает четыре пользовательских шага:

1. Выбрать обычный альбом Photos или общий альбом.
2. Запустить анализ и видеть общий процент выполнения; внутренние стадии скрыты в деталях.
3. Проверить предложенную галерею и исправить только спорные решения.
4. Выполнить dry-run и явно подтвердить новый Best-альбом в Photos.

Плотность подборки, технические стадии, оценки 0–100 и причины доступны по запросу,
но не перегружают основной экран.

Shared Albums отображаются прямо в нативном селекторе. Для анализа PhotoKit создаёт
только локальные JPEG review-renders в cache приложения; Photos Library и Shared Album
не меняются. После проверки сервис добавляет local identifiers одобренного Best-набора
в новый обычный альбом Photos; это отдельная операция после dry-run и подтверждения.
Видео пропускаются, а Shared renders не называются originals.
Приложение не пишет напрямую в Photos SQLite, не меняет Favorite/keywords/originals и
не содержит API удаления фото.

## Проверки

```bash
uv run pytest
uv run pytest --cov=photo_curator
uv run ruff check .
uv run ruff format --check .
```

### Native worker transport

SwiftUI-клиент запускает встроенный coordinator как дочерний процесс и общается с ним
только по versioned JSONL через stdin/stdout:

```bash
uv run photo-curator native-worker
```

Каждый запрос содержит `schema_version`, correlation `id`, `method` и `params`.
Worker предоставляет albums/projects, запуск и progress анализа, ranked assets, ручные
decisions, Personal Taste и безопасный publish plan/apply. Это доверенная локальная
граница приложения, а не сетевой API; stdout зарезервирован только под protocol frames.

### Native Apple Vision benchmark

S1 helper собирается как отдельный Swift CLI и выполняет публичные Vision requests:
aesthetics, feature print, attention saliency и face signals. Результат содержит request
revisions, per-stage latency, platform capabilities и feature-print data.

```bash
make vision-helper
uv run photo-curator vision-benchmark --project-id PROJECT_ID \
  --warmup 1 --iterations 3 \
  --output vision-benchmark.json --score-output vision-scores.json
```

`vision-scores.json` совместим с `acceptance-evaluate --scores`, поэтому Vision baseline
сравнивается с technical-first scorer на неизменной held-out разметке.

### Optional Core ML model benchmark

S3 adapter принимает внешнюю `.mlmodel`, `.mlpackage` или `.mlmodelc`, но сначала
регистрирует immutable checksum и лицензионный контракт. Затем он запускает модель
через Vision/Core ML с `MLComputeUnits.all` и сохраняет versioned outputs и median
latency по готовым preview проекта:

```bash
make coreml-helper
uv run photo-curator model-register \
  --model /path/to/model.mlpackage --model-name NAME --model-version VERSION \
  --license-id Apache-2.0 --source-url https://example/model \
  --commercial-use-allowed
uv run photo-curator model-list
uv run photo-curator coreml-benchmark --project-id PROJECT_ID \
  --model-id MODEL_ID --warmup 1 --iterations 3 \
  --output coreml-benchmark.json
```

Модель не скачивается и не входит в bundle автоматически. Перед продуктовым включением
обязательны совместимая коммерческая лицензия, checksum, held-out uplift над Vision-only
baseline и отдельное измерение CPU/GPU/Neural Engine через Instruments. Команда
`model-approve --model-id MODEL_ID --evidence evidence.json` fail-closed требует
`compatible`, `runtime_passed`, название held-out metric и положительный uplift не меньше
заранее указанного порога. Публичные веса
Apple MobileCLIP не являются продуктовым кандидатом: их model license разрешает только
некоммерческое исследовательское использование.

Release hot paths для inventories 100/2 000/5 000 воспроизводимо проверяются
отдельно от Photos Library:

```bash
uv run photo-curator release-benchmark --iterations 3 \
  --counts 100,2000,5000 --output build/evidence/release-benchmark.json
```

Отчёт измеряет candidate reduction, Swipe Score, подбор A/B-пары и JSONL
payload serialization. Energy честно помечается `not_measured` до отдельного
Instruments/MetricKit release run.

### Swipe Score v1

Real-project pipeline сохраняет versioned Swipe Score отдельно от review decision. В нём
есть generic Apple Vision aesthetics, relative rich Apple Photos signals, attention,
best-in-series, portrait signal, technical penalty, confidence и model provenance.
Без обученного profile `personal_delta` равен нулю; после calibration он рассчитывается
локальной моделью и остаётся видимым отдельно от generic score.

Техническая резкость/экспозиция больше не является главным weighted score: она может
понизить рекомендацию или защитить решение, но сильный визуальный кадр способен обогнать
технически идеальный слабый кадр. Качество формулы остаётся гипотезой до S0 held-out report.

Финальный Keep-набор дополнительно проверяется на разнообразие по Apple Vision feature
prints. Для визуально близкой сцены автоматически остаются до трёх сильнейших кадров,
остальные переходят в Review с причиной `similar_scene`; Favorites, edited assets и
лидеры серий этим правилом не понижаются.

### Personal Taste Profile

Локальный profile обучается на явных A/B comparisons поверх native Vision feature prints.
Feature vectors копируются в profile storage, поэтому накопленный вкус не пропадает при
удалении проекта. В native UI три выбора между похожими по силе кадрами запускают
первую калибровку; Swipe Score и review order обновляются сразу, без повтора тяжёлого
Vision pipeline. Pairwise linear model добавляет ограниченный `personal_delta`
от −20 до +20, а generic score всегда остаётся видимым отдельно.

Profile API поддерживает capture, training, pause/resume, export и полное удаление:

```text
GET    /api/taste-profile
POST   /api/taste-profile/preferences
POST   /api/taste-profile/train
PATCH  /api/taste-profile/status
GET    /api/taste-profile/export
DELETE /api/taste-profile
```

В нативном приложении pause/resume, экспорт JSON и подтверждённое удаление доступны
в Settings. После изменения статуса или удаления текущая подборка пересчитывается с
этапа decisions без повторного декодирования изображений и Apple Vision.

Минимум три calibration comparisons нужны для локального обучения; продуктовый quality
gate требует больше примеров и отдельные held-out comparisons. Profile никогда не меняет
duplicate/source/resolution protections.

### Human-labelled baseline acceptance

Acceptance schema v2 измеряет safety/duplicates, pairwise preference, best-in-series и
Top-K agreement на одном воспроизводимом наборе.
Команда создаёт шаблон с UUID и именами файлов, но не копирует фотографии:

```bash
uv run photo-curator acceptance-template --project-id PROJECT_ID \
  --output human-labels.json
```

Для каждого фото человек заполняет `expected_disposition` (`keep`, `review` или
`reject`). Кадры одной серии получают одинаковый `duplicate_group`; ровно один из
них отмечается `expected_leader: true`. Дополнительно заполняются минимум 10
`preference_pairs` со `split: held_out` и минимум 5 UUID в `expected_top_k`.

Текущий technical-first scorer можно заморозить в отдельный versioned snapshot:

```bash
uv run photo-curator acceptance-score-export --project-id PROJECT_ID \
  --engine-name technical-first-selection-score --engine-version legacy-v1 \
  --output baseline-scores.json
```

После этого отчёт строится одной командой:

```bash
uv run photo-curator acceptance-evaluate --project-id PROJECT_ID \
  --labels human-labels.json --scores baseline-scores.json
```

Release-fixture должен содержать 50–100 фото и хотя бы одну размеченную серию.
Пороговые значения записаны прямо в manifest: duplicate precision ≥ 90%, recall ≥
80%, точность лидера серии ≥ 80%, доля ложных исключений ≤ 5%, pairwise accuracy ≥
65%, Top-K overlap ≥ 60%. Один manifest можно прогнать против нескольких score snapshots.
Ключ `--json` выдаёт машинно-читаемый отчёт. Exit code `0` означает PASS, `1` —
измеренный FAIL, `2` — некорректную или неполную разметку.

В нативном приложении Settings → «Проверка качества» сохраняет совместимые
`photo-curator-labels.json` и `photo-curator-swipe-scores.json`. Экспорт включает только
явные ручные decisions и A/B-предпочтения этого проекта. Автоматическая выборка, найденные
сервисом группы дублей и автоматический Top-K не превращаются в human ground truth.
В review-галерее пользователь сам отмечает звёздами упорядоченный Top-K и назначает лучший
кадр найденной серии. Любые кадры можно собрать в ручную серию, поэтому пропущенные
алгоритмом группы тоже входят в duplicate recall; серия экспортируется только после ручного
решения для всех её кадров.
Кнопка «Оценить заполненный набор…» принимает эти два JSON и показывает PASS/FAIL либо
честно сообщает, что release fixture ещё неполон.
Settings постоянно показывает прогресс по четырём независимым gates: 50–100 manual labels,
10+ held-out A/B, 5+ Top‑K и хотя бы одна полностью размеченная серия.

Полное ТЗ находится в `CODEX_PROJECT_SPEC.md`, тематические документы — в `docs/`,
архитектурные решения — в `decisions/`. Инструкции для разработчика и текущий статус:
`DEVELOPMENT.md` и `PROGRESS.md`.
