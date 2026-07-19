# Photo Curator

Локальный macOS-помощник, который превращает большой неразобранный альбом в
объяснимую подборку лучших фотографий. Основной результат — галерея
`Selected / Review / Excluded`, которую пользователь проверяет до создания нового
Best-альбома. Исходный альбом и оригиналы не изменяются; автоматического удаления нет.

Новый продуктовый план и критерии готовности находятся в [`ROADMAP.md`](ROADMAP.md).

## Быстрый запуск

```bash
uv sync
uv run photo-curator --demo
```

Demo использует 12 синтетических изображений и проходит весь workflow без доступа к
Photos Library. Для работы с реальной медиатекой:

```bash
uv run photo-curator doctor
uv run photo-curator
```

Сервер слушает только `127.0.0.1`. Одноразовый startup token заменяется Strict-cookie
и удаляется из URL после первого открытия.

## Workflow

1. Выбрать обычный альбом Photos или создать из Shared Album частичную/полную локальную фотокопию.
2. Выбрать плотность подборки: компактную, сбалансированную или широкую.
3. Запустить previews → метрики → серии → локальный Apple Vision → решения.
4. Просмотреть `Отобрано / Проверить / Исключено`, оценки 0–100 и причины.
5. Исправить спорные решения; ручные изменения переживают повторный анализ.
6. Выполнить dry-run и подтвердить Best-альбом или скачать ZIP review-превью.

Shared Albums отображаются отдельным безопасным intake workflow: первые N, отмеченные
на странице или все доступные фотографии. После плана и явного подтверждения сервис
создаёт новый обычный альбом Photos, не меняя Shared Album. Видео пропускаются, потому
что Photos предоставляет для них только JPEG-превью; Shared renders не называются originals.
Запись выполняет встроенный native PhotoKit helper через публичный macOS API — интерфейс
Photos.app для этого не запускается.
Приложение не пишет напрямую в Photos SQLite, не меняет Favorite/keywords/originals и
не содержит API удаления фото.

## Проверки

```bash
uv run pytest
uv run pytest --cov=photo_curator
uv run ruff check .
uv run ruff format --check .
```

### Human-labelled acceptance

Для R7 используется отдельный manifest, связанный с уже проанализированным проектом.
Команда создаёт шаблон с UUID и именами файлов, но не копирует фотографии:

```bash
uv run photo-curator acceptance-template --project-id PROJECT_ID \
  --output human-labels.json
```

Для каждого фото человек заполняет `expected_disposition` (`keep`, `review` или
`reject`). Кадры одной серии получают одинаковый `duplicate_group`; ровно один из
них отмечается `expected_leader: true`. После этого отчёт строится одной командой:

```bash
uv run photo-curator acceptance-evaluate --project-id PROJECT_ID \
  --labels human-labels.json
```

Release-fixture должен содержать 50–100 фото и хотя бы одну размеченную серию.
Пороговые значения записаны прямо в manifest: duplicate precision ≥ 90%, recall ≥
80%, точность лидера серии ≥ 80%, доля ложных исключений ≤ 5%. Ключ `--json`
выдаёт машинно-читаемый отчёт. Exit code `0` означает PASS, `1` — измеренный FAIL,
`2` — некорректную или неполную разметку.

Полное ТЗ находится в `CODEX_PROJECT_SPEC.md`, тематические документы — в `docs/`,
архитектурные решения — в `decisions/`. Инструкции для разработчика и текущий статус:
`DEVELOPMENT.md` и `PROGRESS.md`.
