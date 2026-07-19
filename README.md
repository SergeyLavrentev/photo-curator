# Photo Curator

Локальный macOS-помощник, который превращает большой неразобранный альбом в
персональную подборку фотографий, которые хочется «свайпнуть вправо». Будущий
`Swipe Score` соединяет Apple Vision, сравнение лучших кадров серии и запоминаемый
вкус пользователя. Исходный альбом и оригиналы не изменяются; автоматического
удаления нет.

Новый продуктовый план и критерии готовности находятся в [`ROADMAP.md`](ROADMAP.md).

> Текущая Python/web-реализация — работающий переходный baseline для inventory,
> previews, duplicates, review и safe publish. Целевая V2 — нативное SwiftUI/AppKit
> приложение с Vision/Core ML и без браузера/localhost. Новый ranking и native GUI
> ещё не считаются реализованными.

## Быстрый запуск

```bash
uv sync
uv run photo-curator --demo
```

## Текущее переходное приложение для macOS

Photo Curator собирается как обычный self-contained `.app`: встроенный backend не
зависит от репозитория, `uv` или `.venv` во время запуска. Приложение появляется в
Dock, Spotlight и списке программ, запускает backend только на `127.0.0.1` и открывает
защищённую страницу в браузере по умолчанию.

```bash
make app       # build/macos/PhotoCurator.app + локальная подпись
make install   # установить в /Applications и запустить
make stop      # завершить приложение и backend
make uninstall # переместить установленный .app в Корзину
```

Backend также можно остановить из значка Photo Curator в строке меню macOS или из
веб-раздела с шестерёнкой. `⌘Q` и завершение приложения из Dock сначала останавливают
дочерний backend. Для подписи сертификатом разработчика используйте
`make app SIGN_IDENTITY="Developer ID Application: …"`; значение по умолчанию `-`
создаёт локальную ad-hoc подпись, пригодную для сборки на этом Mac, но не заменяет
Developer ID и notarization для распространения другим пользователям.

Demo использует 12 синтетических изображений и проходит весь workflow без доступа к
Photos Library. Для работы с реальной медиатекой:

```bash
uv run photo-curator doctor
uv run photo-curator
```

Сервер слушает только `127.0.0.1`. Одноразовый startup token заменяется Strict-cookie
и удаляется из URL после первого открытия.

## Workflow

Основной интерфейс показывает четыре пользовательских шага:

1. Выбрать обычный альбом Photos или общий альбом.
2. Запустить анализ и видеть общий процент выполнения; внутренние стадии скрыты в деталях.
3. Проверить предложенную галерею и исправить только спорные решения.
4. Выполнить dry-run и явно подтвердить новый Best-альбом в Photos.

Плотность подборки, технические стадии, оценки 0–100 и причины доступны по запросу,
но не перегружают основной экран.

Shared Albums отображаются отдельным безопасным intake workflow: первые N, отмеченные
на странице или все доступные фотографии. После плана и явного подтверждения сервис
копирует доступные JPEG/PNG/HEIC renders в собственное persistent-хранилище и показывает
их как локальный альбом в обычном селекторе проекта. Photos Library и Shared Album не
меняются. После проверки результата сервис может нативно импортировать только принятый
Best-набор в новый обычный альбом Photos; это отдельная операция после dry-run и
подтверждения. Видео пропускаются, а Shared renders не называются originals.
При удалении единственного отбора, использующего Shared snapshot, сервис удаляет также
его локальные копии и manifest. Фотографии в Photos при этом не затрагиваются.
Приложение не пишет напрямую в Photos SQLite, не меняет Favorite/keywords/originals и
не содержит API удаления фото.

## Проверки

```bash
uv run pytest
uv run pytest --cov=photo_curator
uv run ruff check .
uv run ruff format --check .
```

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
