# Разработка Photo Curator

Требования и архитектурные решения находятся в `CODEX_PROJECT_SPEC.md`, `docs/` и
`decisions/`. Источник истины при конфликте — `CODEX_PROJECT_SPEC.md`.

## Запуск Milestone 0

```bash
uv sync
uv run photo-curator --demo
```

Для запуска без автоматического открытия браузера:

```bash
uv run photo-curator --demo --no-browser
```

Приложение слушает только loopback-интерфейс. URL с одноразовым startup token
выводится в терминал; после первого открытия token заменяется Strict-cookie и
удаляется из адресной строки.

## Проверки

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

