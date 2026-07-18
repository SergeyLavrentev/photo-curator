# Progress

## Current milestone

- Milestone: 0 — Scaffold и visual shell
- Goal: Запускаемый demo UI с локальной БД и безопасной сессией
- Status: Complete

## Completed

- [x] Структура Python package и CLI entrypoint
- [x] Application paths и SQLite migration
- [x] FastAPI/Jinja shell с локальными assets
- [x] Loopback host gate и startup token session
- [x] Demo dashboard с визуальным pipeline
- [x] Desktop и narrow-width visual QA

## In progress

- [ ] Milestone 1 — Doctor и PhotosProvider

## Validation performed

```text
uv sync — success (Python 3.12.7, uv.lock создан)
uv run photo-curator --demo --no-browser --port 48765 — success
startup token → Strict cookie → clean URL — success
desktop и 390 px UI — success, horizontal overflow отсутствует
uv run pytest — 9 passed
uv run ruff check . — passed
uv run ruff format --check . — passed
```

## Known issues

- Реальный Photos provider и полный doctor относятся к Milestone 1.

## Decisions made

- Demo использует отдельное synthetic представление и не обращается к Apple Photos.
- Default paths точно следуют macOS Application Support/Caches/Logs.

## Next step

1. Реализовать Milestone 1: Doctor, PhotosProvider protocol и fake/real providers.
