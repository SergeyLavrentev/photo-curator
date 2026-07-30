# Разработка Photo Curator

Источник истины для продукта — `ROADMAP.md` и ADR-0006. Исторический
`CODEX_PROJECT_SPEC.md` остаётся обязательным для safety-инвариантов, которые не
заменены новым roadmap. Проект требует Python 3.12 и управляется через `uv`.

## Локальная среда

```bash
uv sync
uv run photo-curator legacy-web --demo --no-browser
```

Runtime-данные хранятся в стандартных каталогах macOS:

- `~/Library/Application Support/PhotoCurator/` — SQLite state;
- `~/Library/Caches/PhotoCurator/` — review JPEG, thumbnails и publish UUID files;
- `~/Library/Logs/PhotoCurator/photo-curator.log` — локальный log.

Нативное приложение читает поддерживаемые альбомы через публичный PhotoKit helper и
публикует подтверждённый Best-альбом через отдельный PhotoKit helper. Переходный
`legacy-web` использует публичный Python API `osxphotos` только как read-only adapter и
документированный `batch-edit` после dry-run. Прямые записи в Photos DB и UI scripting
запрещены во всех режимах.

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

## Сборка macOS-приложения

`make app` собирает minimal frozen JSONL worker через PyInstaller, компилирует SwiftUI app,
создаёт `build/macos/PhotoCurator.app`, подписывает весь bundle и проверяет подпись.
Полученный `.app` self-contained; сборочная машина должна иметь Xcode Command Line Tools,
Python 3.12, `uv`, `qlmanage`, `iconutil` и `codesign`.

```bash
make app
make verify-app
make install                         # /Applications/PhotoCurator.app
make install INSTALL_DIR="$HOME/Applications" # только для текущего пользователя
make app SIGN_IDENTITY="Developer ID Application: Example (TEAMID)"
```

Native app владеет worker-процессом и обменивается с ним versioned JSONL через
stdin/stdout; browser, HTTP и localhost не используются. При `⌘Q` app посылает protocol
shutdown, затем завершает worker. Native bundle entrypoint не включает FastAPI, Jinja,
web assets и `osxphotos`; legacy web CLI остаётся только в development environment.

Перед релизом дополнительно проверить demo workflow, Quick Look, keyboard/undo,
restart/cancel/resume, отсутствие TCP listener и source paths в JSONL payload, а также
сохранность manual overrides после reanalysis.
