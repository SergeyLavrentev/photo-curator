# Photo Curator — пакет проектной документации

Этот архив содержит согласованное техническое задание и комплект инженерной документации для MVP локального приложения **Photo Curator**.

Цель продукта — безопасно анализировать фотографии из **обычного пользовательского альбома** Apple Photos, визуально показывать ход обработки и результаты, помогать человеку выбрать неудачные кадры и публиковать подтверждённые кандидаты в отдельный временный Reject-альбом. Приложение не удаляет фотографии самостоятельно.

## Зафиксированный пользовательский workflow

```text
Apple Shared Album
        ↓ ручной импорт пользователем
Личная медиатека Apple Photos
        ↓
Обычный рабочий альбом «Черногория»
        ↓
Photo Curator
        ↓
локальный анализ + визуальное ревью
        ↓
временный обычный Reject-альбом
        ↓
ручное Command+Delete в Photos.app
```

Shared Albums не являются прямым источником MVP. Пользователь сначала импортирует нужные элементы в личную медиатеку и собирает их в обычный альбом.

## С чего начинать Codex

Передайте агенту целиком файл:

```text
CODEX_PROJECT_SPEC.md
```

Это основной исполняемый документ: scope, ограничения, архитектура, pipeline, UI, данные, тестирование, milestones и Definition of Done.

## Состав архива

| Путь | Назначение |
|---|---|
| `CODEX_PROJECT_SPEC.md` | Единое полное ТЗ, готовое для передачи Codex |
| `docs/01-product-requirements.md` | Пользовательские сценарии, scope и критерии продукта |
| `docs/02-architecture.md` | Компоненты, потоки данных и границы модулей |
| `docs/03-shared-album-intake.md` | Безопасный импорт из Shared Albums |
| `docs/04-safety-and-data-integrity.md` | Неизменяемые safety-инварианты |
| `docs/05-photos-integration.md` | Контракт с Apple Photos и `osxphotos` |
| `docs/06-analysis-pipeline.md` | Previews, метрики, perceptual hashes и решения |
| `docs/07-ui-ux.md` | Web GUI, pipeline visualizer и review workflow |
| `docs/08-data-model.md` | SQLite schema и правила миграции |
| `docs/09-api-and-jobs.md` | HTTP API, background jobs и resume |
| `docs/10-testing-and-quality.md` | Unit, integration и manual tests |
| `docs/11-operations-and-troubleshooting.md` | Doctor, логи, cache и диагностика |
| `docs/12-compatibility.md` | Матрица совместимости и capability gates |
| `docs/13-milestones-and-dod.md` | План реализации и Definition of Done |
| `docs/14-security.md` | Защита локального web UI и subprocesses |
| `docs/15-requirements-traceability.md` | Связь требований, тестов и milestones |
| `decisions/` | Архитектурные решения в формате ADR |
| `templates/PROGRESS.md` | Шаблон статуса для Codex |
| `templates/config.example.toml` | Пример конфигурации |
| `MANIFEST.json` | Состав архива и SHA-256 документов |

## Источник истины

При конфликте документов приоритет следующий:

1. `CODEX_PROJECT_SPEC.md`.
2. ADR в `decisions/`.
3. Тематические документы в `docs/`.
4. Примеры и шаблоны.

## Принципы MVP

- Обычный пользовательский альбом — единственный прямой source.
- Shared Album — только источник ручного импорта.
- Локальное выполнение без cloud vision API.
- Лёгкий стек: Python, `osxphotos`, Pillow, NumPy, FastAPI, Jinja2.
- Никаких Docker, Node.js, Electron, Qt, PyTorch и внешнего ML-сервера.
- Никаких прямых записей в Photos SQLite.
- Никакого автоматического удаления.
- Основной интерфейс — визуальный локальный web GUI, а не набор консольных команд.
- Автоматический `reject` по умолчанию применяется только к уверенным проигравшим в duplicate-группах.
- Все субъективные и неоднозначные случаи идут в `review`.
