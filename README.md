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
`make app SIGN_IDENTITY="Developer ID Application: …"`; значение по умолчанию `-`
создаёт локальную ad-hoc подпись, пригодную для сборки на этом Mac, но не заменяет
Developer ID и notarization для распространения другим пользователям.

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

### Swipe Score v1

Real-project pipeline сохраняет versioned Swipe Score отдельно от review decision. В нём
есть generic Apple Vision aesthetics, relative rich Apple Photos signals, attention,
best-in-series, portrait signal, technical penalty, confidence и model provenance.
Без обученного profile `personal_delta` равен нулю; после calibration он рассчитывается
локальной моделью и остаётся видимым отдельно от generic score.

Техническая резкость/экспозиция больше не является главным weighted score: она может
понизить рекомендацию или защитить решение, но сильный визуальный кадр способен обогнать
технически идеальный слабый кадр. Качество формулы остаётся гипотезой до S0 held-out report.

### Personal Taste Profile

Локальный profile обучается на явных A/B comparisons поверх native Vision feature prints.
Feature vectors копируются в profile storage, поэтому накопленный вкус не пропадает при
удалении старого проекта. Pairwise linear model добавляет ограниченный `personal_delta`
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

Полное ТЗ находится в `CODEX_PROJECT_SPEC.md`, тематические документы — в `docs/`,
архитектурные решения — в `decisions/`. Инструкции для разработчика и текущий статус:
`DEVELOPMENT.md` и `PROGRESS.md`.
