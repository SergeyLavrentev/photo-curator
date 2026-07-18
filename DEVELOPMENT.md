# Разработка Photo Curator

Источник истины — `CODEX_PROJECT_SPEC.md`, затем ADR и тематические файлы `docs/`.
Проект требует Python 3.12 и управляется через `uv`.

## Локальная среда

```bash
uv sync
uv run photo-curator --demo --no-browser
```

Runtime-данные хранятся в стандартных каталогах macOS:

- `~/Library/Application Support/PhotoCurator/` — SQLite state;
- `~/Library/Caches/PhotoCurator/` — review JPEG, thumbnails и publish UUID files;
- `~/Library/Logs/PhotoCurator/photo-curator.log` — локальный log.

Оригиналы Photos открываются только на чтение через публичный Python API `osxphotos`.
Publisher вызывает документированный `osxphotos batch-edit` списком argv, сначала с
`--dry-run`; прямые записи в Photos DB и UI scripting запрещены.

## Разрешения macOS

Если doctor сообщает, что Photos Library недоступна, откройте System Settings →
Privacy & Security и разрешите доступ к Photos и Full Disk Access приложению, из
которого запущен Photo Curator (Codex или Terminal). Полностью перезапустите это
приложение и снова выполните:

```bash
uv run photo-curator doctor
```

Ручной acceptance выполняется на отдельном обычном альбоме `PhotoCurator Test` из
50–100 неважных assets. Проверяются Favorite, edited, Live Photo, точные/похожие
дубликаты, low-resolution copy, missing preview, dry-run и создание временного
Reject-альбома. Приложением ничего не удалять.

## Quality gates

```bash
uv run ruff format --check .
uv run ruff check .
uv run pytest
uv run pytest --cov=photo_curator
```

Перед релизом дополнительно проверить demo в desktop и узком viewport, отсутствие
`shell=True`, bind только на `127.0.0.1`, отсутствие source paths в HTML/JSON и
сохранность manual overrides после reanalysis.
