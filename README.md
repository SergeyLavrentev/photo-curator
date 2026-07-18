# Photo Curator

Локальный macOS-помощник для безопасного ревью Apple Photos. Он анализирует только
обычные пользовательские альбомы, строит preview, технические метрики и группы
дубликатов, сохраняет ручные решения и может создать отдельный Reject-альбом через
`osxphotos`. Исходный альбом и оригиналы не изменяются; удаление остаётся ручным в
Photos.app.

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

1. Импортировать нужные кадры из Shared Album в личную медиатеку вручную.
2. Собрать их в обычный альбом Photos.
3. Создать проект и запустить inventory → previews → metrics → duplicates → decisions.
4. Просмотреть рекомендации, сравнить дубликаты и сохранить manual overrides.
5. Выполнить publish dry-run и отдельно подтвердить создание нового Reject-альбома.
6. Проверить Reject-альбом в Photos и удалить действительно ненужные кадры вручную.

Shared Albums отображаются, но недоступны для выбора. Приложение не пишет напрямую в
Photos SQLite, не меняет Favorite/keywords/originals и не содержит API удаления фото.

## Проверки

```bash
uv run pytest
uv run pytest --cov=photo_curator
uv run ruff check .
uv run ruff format --check .
```

Полное ТЗ находится в `CODEX_PROJECT_SPEC.md`, тематические документы — в `docs/`,
архитектурные решения — в `decisions/`. Инструкции для разработчика и текущий статус:
`DEVELOPMENT.md` и `PROGRESS.md`.
