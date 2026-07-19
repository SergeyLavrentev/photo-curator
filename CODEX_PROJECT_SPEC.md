# Photo Curator для Apple Photos — историческое техническое задание MVP

> Reject-first продуктовая модель этого документа заменена [`ROADMAP.md`](ROADMAP.md)
> и ADR-0006. Ограничения безопасности остаются обязательными.

## 0. Инструкция агенту Codex

Нужно реализовать рабочий проект, а не ограничиваться планом, псевдокодом или заготовками. Работать последовательно по milestones из этого документа. После каждого milestone запускать тесты и `ruff`, проверять acceptance criteria и обновлять `PROGRESS.md`.

При неоднозначности выбирать решение, которое:

1. быстрее приводит к рабочему MVP;
2. имеет меньше runtime-зависимостей;
3. не требует Docker, Node.js, Electron, Qt, PyTorch, TensorFlow, OpenCV, SciPy или отдельного ML-сервера;
4. не использует cloud image APIs;
5. не пишет напрямую в базу Apple Photos;
6. не меняет оригинальные изображения;
7. не удаляет фотографии;
8. сохраняет промежуточные результаты и поддерживает resume;
9. даёт пользователю визуальный, а не только консольный workflow.

Перед началом проверить окружение:

```bash
sw_vers
uname -m
command -v uv || true
command -v sips
python3 --version || true
```

Не реализовывать Future Extensions до выполнения Definition of Done.

---

## 1. Цель продукта

Разработать локальное macOS-приложение для assisted culling фотографий из обычного пользовательского альбома Apple Photos.

Пример:

```text
Apple Photos
└── Черногория
    └── 1000 фотографий
```

Среди фотографий могут быть:

- точные или визуально эквивалентные дубликаты;
- уменьшенные копии фотографий, импортированные из Shared Album;
- near-duplicates и серии;
- burst-фотографии;
- смазанные, тёмные, пересвеченные или низкоконтрастные кадры;
- хорошие и лучшие кадры;
- неоднозначные фотографии, требующие решения человека.

Приложение должно:

1. определить текущую Photos Library;
2. показать обычные пользовательские альбомы;
3. создать проект анализа выбранного альбома;
4. получить metadata и локально доступные изображения;
5. создать временные нормализованные previews;
6. выполнить лёгкий локальный анализ;
7. найти duplicate-группы и выбрать лидеров;
8. сформировать рекомендации `keep`, `review`, `reject`;
9. визуально показать весь pipeline и результат;
10. позволить вручную исправить решения;
11. выполнить dry-run публикации;
12. создать новый временный обычный Reject-альбом в Apple Photos;
13. добавить туда ссылки на подтверждённые reject-assets;
14. показать инструкцию по ручному удалению в Photos.app.

Приложение никогда не удаляет фотографии самостоятельно.

---

## 2. Зафиксированный workflow

```text
Apple Shared Album
        ↓ план + подтверждённая локальная фотокопия
Личная медиатека Apple Photos
        ↓
Обычный рабочий альбом «Черногория»
        ↓
Photo Curator
        ↓
инвентаризация
        ↓
временные preview
        ↓
технические метрики + perceptual hashes
        ↓
duplicate groups + decisions
        ↓
визуальное ревью
        ↓
временный Reject-альбом
        ↓
ручное Command+Delete в Photos.app
```

Shared Albums не поддерживаются как прямой источник анализа. Photo Curator может
создать из локально доступных photo renders отдельный обычный альбом после явного
подтверждения пользователя.

---

## 3. Scope источника

### 3.1. Поддерживается

- текущая/default Photos Library;
- обычный пользовательский альбом личной медиатеки;
- JPEG, HEIC, PNG и форматы, конвертируемые системным `sips`;
- edited photos;
- still-компонент Live Photo;
- burst metadata, если доступно через публичный API `osxphotos`.

### 3.2. Не поддерживается напрямую

- Apple Shared Albums;
- iCloud Shared Photo Library как отдельный специальный workflow;
- Smart Albums;
- системные коллекции;
- Memories, Projects, Recently Deleted;
- standalone videos;
- RAW development;
- video-компонент Live Photo.

### 3.3. Intake из Shared Album

Пользователь выбирает первые N, отмеченные на странице или все доступные фотографии.
Приложение проверяет локальные renders, сохраняет план и только после отдельного
подтверждения вызывает встроенный capability-gated Swift/PhotoKit helper. Через публичный
macOS API создаётся новый обычный альбом без запуска или UI scripting Photos.app;
исходный Shared Album остаётся read-only. Видео явно пропускаются, потому что доступный
derivative может быть только JPEG-preview, а не исходным видео. При отсутствии capability
показывается ручной fallback.

В UI показать предупреждение:

```text
Фотографии, импортированные из Shared Album, могут иметь уменьшенное
разрешение и неполный набор metadata. Photo Curator оценивает доступную
импортированную версию, а не гарантированно исходный оригинал.
```

Приложение не меняет Shared Album, участников, комментарии, likes и публикации.

---

## 4. Safety-инварианты

### 4.1. Никогда не удалять автоматически

Запрещено:

- удалять assets из Photos Library;
- перемещать их в Recently Deleted;
- очищать Recently Deleted;
- удалять или менять source album;
- удалять assets из других альбомов.

### 4.2. Никогда не писать напрямую в Photos SQLite

Не выполнять `INSERT`, `UPDATE`, `DELETE`, `REPLACE` и schema changes в Photos database. Чтение допускается только через публичный API `osxphotos`.

### 4.3. Не менять originals и metadata

Нельзя менять:

- original files;
- EXIF;
- title;
- description;
- date;
- location;
- Favorite;
- keywords;
- persons/faces.

### 4.4. Единственное разрешённое изменение Photos

MVP может только добавить выбранные существующие assets в **новый обычный временный альбом**.

### 4.5. Dry-run обязателен

До реального publish выполнить `osxphotos batch-edit ... --dry-run`. Реальная кнопка недоступна при неуспешном dry-run.

### 4.6. Каждый publish создаёт новый альбом

Не переиспользовать старый publish album. Не рассчитывать на `--undo` для отката album membership.

### 4.7. Финальное удаление выполняет пользователь

В UI явно объяснить:

```text
Delete — убирает объект только из текущего альбома.
Command + Delete — удаляет объект из личной медиатеки и всех обычных альбомов.
```

---

## 5. Технологический стек

### 5.1. Runtime

```text
macOS 13+
Python 3.12
uv
```

Файлы:

```text
.python-version
pyproject.toml
uv.lock
```

### 5.2. UI/backend

```text
FastAPI
Uvicorn
Jinja2
vanilla JavaScript
vanilla CSS
```

Без npm, CDN и отдельного frontend build.

### 5.3. Photos integration

```text
osxphotos Python API — read-only inventory
osxphotos CLI — capability-gated add-to-album publish
```

Все обращения к `osxphotos` изолировать в `photo_curator/photos/`.

### 5.4. Image processing

```text
Pillow
NumPy
/usr/bin/sips
hashlib
```

### 5.5. Persistence

```text
sqlite3 из стандартной библиотеки
PRAGMA user_version для миграций
```

### 5.6. Минимальные зависимости

```toml
[project]
dependencies = [
  "osxphotos",
  "fastapi",
  "uvicorn",
  "jinja2",
  "python-multipart",
  "pillow",
  "numpy",
]

[dependency-groups]
dev = [
  "pytest",
  "pytest-cov",
  "httpx",
  "ruff",
]
```

Не добавлять тяжёлые библиотеки без отдельного ADR.

---

## 6. Основной интерфейс

Основной workflow выполняется в локальном web GUI. Пользователь запускает:

```bash
uv run photo-curator
```

Приложение:

1. выбирает свободный порт;
2. bind только на `127.0.0.1`;
3. запускает один Uvicorn worker;
4. генерирует session token;
5. открывает браузер.

Поддержать:

```bash
photo-curator
photo-curator doctor
photo-curator version
photo-curator --no-browser
photo-curator --port 8765
photo-curator --demo
```

`--demo` использует `FakePhotosProvider` и synthetic fixtures.

---

## 7. Структура проекта

```text
photo-curator/
├── README.md
├── PROGRESS.md
├── pyproject.toml
├── uv.lock
├── .python-version
├── src/photo_curator/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py
│   ├── app.py
│   ├── config.py
│   ├── paths.py
│   ├── logging_setup.py
│   ├── db/
│   │   ├── connection.py
│   │   ├── migrations.py
│   │   └── repository.py
│   ├── photos/
│   │   ├── provider.py
│   │   ├── osxphotos_provider.py
│   │   ├── fake_provider.py
│   │   ├── library_resolver.py
│   │   ├── render_resolver.py
│   │   └── publisher.py
│   ├── pipeline/
│   │   ├── coordinator.py
│   │   ├── jobs.py
│   │   ├── inventory.py
│   │   ├── previews.py
│   │   ├── metrics.py
│   │   ├── duplicates.py
│   │   └── decisions.py
│   ├── analysis/
│   │   ├── image_loader.py
│   │   ├── technical.py
│   │   ├── hashes.py
│   │   ├── similarity.py
│   │   ├── normalization.py
│   │   └── decision_engine.py
│   ├── web/
│   │   ├── routes.py
│   │   ├── api.py
│   │   ├── security.py
│   │   ├── view_models.py
│   │   ├── templates/
│   │   └── static/
│   └── utils/
│       ├── subprocesses.py
│       ├── timestamps.py
│       ├── identifiers.py
│       └── safe_paths.py
├── tests/
└── docs/
```

Не дробить проект сильнее до появления реальной потребности.

---

## 8. Application paths

```text
~/Library/Application Support/PhotoCurator/photo-curator.sqlite3
~/Library/Caches/PhotoCurator/<project-id>/
~/Library/Logs/PhotoCurator/photo-curator.log
```

Project cache:

```text
<Project Cache>/
├── review/<asset-uuid>.jpg
├── thumbnails/<asset-uuid>.jpg
├── publish/reject-uuids.txt
├── temp/
└── debug/
```

Не хранить originals.

---

## 9. PhotosProvider

```python
class PhotosProvider(Protocol):
    def get_current_library(self) -> PhotoLibrary: ...
    def list_regular_albums(self) -> list[PhotoAlbum]: ...
    def list_assets(self, album_id: str) -> list[PhotoAsset]: ...
    def refresh_assets(self, asset_uuids: list[str]) -> list[PhotoAsset]: ...
    def asset_still_in_album(self, album_id: str, asset_uuid: str) -> bool: ...
```

Реализации:

```text
OSXPhotosProvider
FakePhotosProvider
```

### 9.1. Library identity

Хранить:

```text
library_path
database_path
database_version
photos_app_version
library_fingerprint
```

Fingerprint строить из path, DB size, mtime и schema/version. Это drift detector, а не security hash.

### 9.2. Album identity

Album name не уникален. Хранить:

```text
album_id
album_name
album_folder_path
album_full_path
```

В UI показывать полный путь папок.

### 9.3. Asset identity

```text
library_fingerprint + asset_uuid
```

Filename и timestamp не являются identity.

### 9.4. Shared albums

Shared collections отображать только как disabled с сообщением о необходимости ручного импорта.

---

## 10. Doctor и capability gates

Команда и страница `/doctor` проверяют:

- macOS и architecture;
- Python;
- import/version/CLI path `osxphotos`;
- обнаружение текущей Photos Library;
- читаемость database;
- количество regular albums;
- наличие `/usr/bin/sips`;
- запись в app/cache/log directories;
- свободный loopback port;
- состояние Photos.app.

Статусы:

```text
OK
WARNING
ERROR
```

### 10.1. Read gate

До полного pipeline должны успешно пройти:

1. открыть current library;
2. получить regular albums;
3. выбрать тестовый альбом;
4. получить не менее 10 assets;
5. получить UUID и metadata;
6. обнаружить локальный source render хотя бы для одного asset.

При провале не переходить на прямой SQLite parsing вне `osxphotos`.

### 10.2. Publish gate

До включения реального publish:

1. выбрать один неважный asset;
2. создать UUID file;
3. выполнить dry-run;
4. показать stdout/stderr;
5. опционально создать уникальный capability-test album после отдельного подтверждения.

Если capability gate не проходит, publish отключается, но read-only анализ и review продолжают работать.

---

## 11. Pipeline

Визуальные stages:

```text
1. Проверка среды
2. Инвентаризация
3. Подготовка preview
4. Анализ
5. Ручное ревью
6. Публикация Reject-альбома
```

Внутри Analysis:

```text
Технические метрики
Perceptual hashes
Поиск duplicate groups
Выбор leaders
Decisions
```

Каждый stage показывает:

- status;
- processed/total;
- progress bar;
- warnings/errors;
- current message;
- elapsed time;
- throughput;
- retry/details.

Не показывать выдуманный ETA.

---

## 12. Inventory

Для каждого photo asset сохранить:

```text
UUID
filename/original filename
taken_at/date_added
dimensions/original dimensions
orientation
Favorite
Hidden
has adjustments
Live Photo
burst metadata
local path availability
edited path availability
derivative paths
optional Apple scores
album membership snapshot
```

Видео пропускать и показывать в summary. Live Photo анализировать по still-component как единый Photos asset.

Ошибка одного asset не останавливает stage; asset получает `analysis_error` и `review`.

Apple score использовать только как optional relative ranking. Не предполагать стабильность полей и диапазонов.

---

## 13. Source render и preview cache

### 13.1. Приоритет render

Для edited photo:

1. `path_edited`;
2. крупнейший валидный derivative;
3. original с flag `edited_render_missing`.

Для unedited photo:

1. local original;
2. крупнейший derivative;
3. RAW render через `sips`, если это единственный доступный вариант.

Если ничего нет:

```text
cache_state = missing
final disposition = review
flag = missing_preview
```

### 13.2. Cache images

```text
review JPEG: max dimension 2048, quality около 88
thumbnail JPEG: max dimension 320, quality около 75
```

Pillow path:

1. open;
2. `ImageOps.exif_transpose`;
3. RGB;
4. resize;
5. atomic save.

Fallback для HEIC/ошибок Pillow — `/usr/bin/sips`.

Никогда не использовать `shell=True`.

### 13.3. Cache invalidation

Fingerprint:

```text
source path + size + mtime + source kind + preview settings version
```

Не перегенерировать валидный cache. Missing assets можно повторить отдельной кнопкой.

Source filesystem paths не передавать браузеру.

---

## 14. Technical metrics

Анализировать review JPEG, уменьшенный в памяти до max dimension 1024.

### 14.1. Sharpness

```text
laplacian_variance
gradient_energy
edge_density
```

Не принимать решение по одной метрике.

### 14.2. Exposure

```text
luma_mean
luma_std
luma_p01/p05/p50/p95/p99
black_clipped_ratio
white_clipped_ratio
```

### 14.3. Contrast/information

```text
contrast_std
dynamic_range
entropy
```

### 14.4. Relative normalization

Считать album-relative percentiles и robust statistics:

```text
median
MAD
percentiles
```

Technical defects по умолчанию отправлять в `review`, а не `reject`.

---

## 15. Perceptual hashes и similarity

Реализовать без OpenCV/SciPy.

### 15.1. Render-equivalence hash

Нормализовать orientation, RGB, resize 256×256 и вычислить SHA-256 raw pixels. Это означает эквивалентность нормализованного render, а не обязательно byte-identical original.

### 15.2. dHash

Grayscale 9×8, сравнение соседей, 64-bit hex.

### 15.3. pHash

Grayscale 32×32, NumPy DCT matrix, low-frequency 8×8, без DC coefficient, median threshold, 64-bit hex.

### 15.4. Histogram

Компактный нормализованный color histogram.

---

## 16. Duplicate detection

Цель — до 5000 assets, типично 500–2000.

Candidate pair, если:

- render-equivalence hash одинаков;
- pHash distance ниже strict threshold;
- либо небольшой time delta и relaxed pHash threshold;
- либо одинаковый burst key.

Начальные настройки:

```text
strict_phash_distance = 4
relaxed_phash_distance = 10
time_window_seconds = 120
aspect_ratio_tolerance = 0.08
```

Confirmation использует:

```text
dHash distance
aspect ratio
normalized pixel MAE
histogram similarity
time distance
resolution ratio
```

Pairs объединять union-find. После группировки проверить similarity каждого member к leader. Chaining с плохим сходством к leader превращает группу в `ambiguous`; automatic rejects в ней запрещены.

---

## 17. Leader selection и shared-copy guard

Приоритет:

1. manual leader;
2. Favorite;
3. edited version;
4. burst default pick;
5. higher resolution;
6. composite quality;
7. deterministic UUID tie-break.

Стартовые weights:

```text
resolution             0.30
sharpness percentile   0.25
exposure quality       0.15
contrast percentile    0.10
Apple overall          0.10
technical quality      0.10
```

Недоступные signals исключить и перенормировать weights.

### 17.1. Lower-resolution shared copy

Если proposed leader имеет менее 75% pixel count максимального member, поставить `leader_lower_resolution`, запретить automatic rejects и отправить группу в review.

Запрещено автоматически публиковать:

```text
higher-resolution asset = reject
lower-resolution near-duplicate = keep
```

Такой `resolution_inversion` блокирует publish до исправления или explicit manual confirmation.

---

## 18. Decision model

Dispositions:

```text
keep
review
reject
```

Flags независимы:

```text
exact_duplicate
near_duplicate
ambiguous_duplicate
burst_member
duplicate_leader
duplicate_loser
possible_blur
severe_blur
underexposed
overexposed
low_contrast
favorite_protected
edited_protected
missing_preview
analysis_error
lower_resolution_copy
leader_lower_resolution
resolution_inversion
apple_low_overall
best_candidate
manual_override
```

Automatic `reject` по умолчанию разрешён только для:

- уверенного render-equivalent duplicate loser;
- high-confidence near-duplicate loser без Favorite, edits, ambiguity и resolution warning, при достаточном quality margin.

Все technical defects по умолчанию — `review`.

Favorite и edited photo не могут получить automatic reject. Missing preview всегда `review`. Duplicate leader не может получить automatic reject.

Хранить:

```text
auto_disposition
manual_disposition
final_disposition
manual_override
manual_note
reviewed
confidence
flags
structured reasons
```

Manual override сохраняется после re-analysis.

`best_candidate` — flag, не отдельный disposition.

---

## 19. SQLite

При connection:

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;
```

Обязательные таблицы:

```text
projects
assets
metrics
duplicate_groups
duplicate_members
decisions
jobs
publishes
```

Полная schema описана в `docs/08-data-model.md`. Миграции — через `PRAGMA user_version`.

---

## 20. Background jobs

Использовать in-process coordinator и bounded `ThreadPoolExecutor`.

```python
max_workers = min(4, os.cpu_count() or 2)
```

Для `sips` — не более 2–3 concurrent processes.

Worker возвращает result; SQLite batch writes выполняет coordinator. Batch size 16–32.

На startup:

```text
running → interrupted
```

Stages idempotent. Resume пропускает валидные previews/metrics и сохраняет manual overrides.

Dependency invalidation:

- decision thresholds → decisions only;
- duplicate thresholds → duplicates + decisions;
- preview settings → previews + all downstream.

---

## 21. Web GUI

Язык MVP — русский. Все строки вынести для будущей локализации.

### 21.1. Home

Показывает library, versions, compatibility, projects, кнопку нового проекта, doctor
и ссылки на частичную/полную локальную копию найденных Shared Albums.

### 21.2. Project dashboard

- visual pipeline stepper;
- summary cards;
- current job;
- warnings;
- throughput;
- cache size;
- inline SVG distributions без chart library.

### 21.3. Review gallery

Категории:

```text
Все
Оставить
На проверку
Удалить
Дубликаты
Лучшие
Favorite
Missing preview
Ошибки
Resolution warnings
```

Функции:

- pagination 60;
- lazy thumbnails;
- filters/sorting;
- multi-select/range-select;
- batch decisions;
- large preview;
- reasons/metrics;
- manual note;
- clear override;
- open in Photos.

Shortcuts:

```text
K keep
R review
X reject
C clear override
←/→ navigation
Space preview
Esc close
```

Не перехватывать shortcuts в input/textarea/select/contenteditable.

### 21.4. Duplicate comparison

Показывать members рядом, dimensions, pixel count, resolution ratio, Favorite, edited, sharpness, quality и disposition. Позволить выбрать leader и decisions.

### 21.5. Publish page

Показать counts automatic/manual reject, duplicate losers, Favorites, edited, resolution inversion, missing preview и unreviewed rejects.

---

## 22. HTTP API

HTML:

```text
GET /
GET /doctor
GET /projects/<id>
GET /projects/<id>/review
GET /projects/<id>/duplicates
GET /projects/<id>/publish
```

JSON:

```text
GET    /api/status
GET    /api/albums
POST   /api/projects
GET    /api/projects/<id>
DELETE /api/projects/<id>
POST   /api/projects/<id>/pipeline/start
POST   /api/projects/<id>/pipeline/resume
POST   /api/projects/<id>/stages/<stage>/retry
POST   /api/projects/<id>/retry-missing
GET    /api/jobs/<job-id>
GET    /api/projects/<id>/assets
GET    /api/projects/<id>/assets/<uuid>
PATCH  /api/projects/<id>/assets/<uuid>/decision
PATCH  /api/projects/<id>/assets/batch-decision
PATCH  /api/projects/<id>/duplicate-groups/<group-id>/leader
POST   /api/projects/<id>/decisions/recalculate
POST   /api/projects/<id>/publish/dry-run
POST   /api/projects/<id>/publish/apply
POST   /api/projects/<id>/cache/clean
```

Media routes строят path самостоятельно из project + UUID и не принимают filesystem path.

---

## 23. Local web security

- bind только `127.0.0.1`;
- startup token → SameSite=Strict cookie → redirect без token;
- CSRF для mutating requests;
- CORS disabled;
- Host allowlist `127.0.0.1`, `localhost`;
- `shell=False` для subprocesses;
- album/path — отдельные argv elements;
- recursive delete только внутри cache root после `is_relative_to` проверки;
- source paths не отдавать в HTML/JSON.

---

## 24. Publishing

### 24.1. Validation

Перед dry-run:

1. перечитать final rejects;
2. проверить UUID и membership;
3. исключить deleted assets;
4. проверить Favorite, edited, missing и resolution inversion;
5. сформировать sorted UUID file.

Source drift показать пользователю. Не публиковать UUID, которых больше нет.

### 24.2. Album name

```text
PhotoCurator — <Source Album> — Reject — <YYYYMMDD-HHMMSS>
```

Имя уникально, sanitised и ограничено по длине.

### 24.3. Dry-run

Концептуально:

```bash
osxphotos batch-edit \
  --uuid-from-file "/path/reject-uuids.txt" \
  --add-to-album "PhotoCurator — Черногория — Reject — 20260717-153000" \
  --dry-run \
  --verbose
```

Использовать argv list, сохранить stdout/stderr/return code. Success только при return code 0.

### 24.4. Apply

Отдельное подтверждение. Та же команда без `--dry-run`. При успехе сохранить audit record, показать имя альбома и открыть Photos.

Если capability gate или apply не поддерживается текущей средой, приложение остаётся полноценным read-only analyzer/reviewer и не применяет обходы через UI scripting или direct DB writes.

### 24.5. Финальная инструкция

```text
1. Откройте созданный Reject-альбом.
2. Ещё раз просмотрите фотографии.
3. Выберите действительно ненужные.
4. Нажмите Command + Delete.
5. Подтвердите удаление из личной медиатеки.
6. При ошибке восстановите из Recently Deleted.
7. Удалите пустой временный альбом.
```

---

## 25. Cache, logging и errors

Cache UI показывает размер, preview counts, missing и last access. Очистка cache не меняет Photos и не удаляет project decisions.

Log:

```text
~/Library/Logs/PhotoCurator/photo-curator.log
```

Не логировать GPS, person names, image contents, full metadata и source paths в UI.

Ошибка отдельного asset → `analysis_error`, `review`, pipeline continues. Stage error сохраняет completed results и предоставляет retry. Running jobs после restart становятся interrupted.

---

## 26. Performance targets

- до 5000 photo assets;
- типично 500–2000;
- memory желательно <1 GB;
- batch processing;
- lazy UI;
- compact hashes;
- без network и neural inference;
- до 3000 допустим pairwise hash scan, выше — candidate reduction;
- не сравнивать full-resolution arrays pairwise.

Не обещать фиксированное время; показывать фактический progress и throughput.

---

## 27. Testing

Synthetic fixtures:

```text
sharp
blurred
dark
overexposed
low contrast
render-equivalent duplicate
JPEG recompressed/resized duplicate
slightly cropped duplicate
similar distinct image
higher-resolution original
lower-resolution shared-like copy
rotated EXIF image
```

Покрыть hashes, metrics, normalization, candidate generation, grouping, ambiguous chaining, leader selection, protections, manual override, resolution inversion, migrations, resume, safe paths и publisher.

`FakePhotosProvider` обеспечивает полный demo/CI workflow без настоящей Photos Library.

Publisher тестируется mock subprocess runner: `shell=False`, UUID file, dry-run/apply distinction, empty set, unique name, return codes, audit.

Manual macOS integration test выполняется на отдельном обычном альбоме `PhotoCurator Test` из 50–100 неважных фотографий.

---

## 28. Milestones

### Milestone 0 — Scaffold и visual shell

FastAPI/Jinja, DB, app paths, startup security, dashboard pipeline, `--demo`.

Acceptance:

```bash
uv sync
uv run photo-curator --demo
```

### Milestone 1 — Doctor и Photos provider

Current library, regular albums, отдельный Shared Album copy workflow, Unicode, read gate.

### Milestone 2 — Inventory

Project snapshot, assets, progress, optional scores, interrupted state.

### Milestone 3 — Preview cache

Render resolver, Pillow/sips, thumbnails, missing retry, gallery skeleton.

### Milestone 4 — Technical analysis

Metrics, percentiles, flags, distributions.

### Milestone 5 — Duplicate detection

Hashes, candidates, confirmation, groups, leaders, resolution guard, comparison page.

### Milestone 6 — Decision engine и review UI

Dispositions, reasons, shortcuts, batch changes, manual overrides/leaders, best candidates.

### Milestone 7 — Publisher

Capability gate, source validation, dry-run, apply, audit, Photos instructions.

### Milestone 8 — Hardening

Resume, stale cache, drift, cleanup, docs, tests, `ruff`.

Полные acceptance criteria — `docs/13-milestones-and-dod.md`.

---

## 29. Definition of Done

MVP готов, если:

1. устанавливается через `uv` и содержит `uv.lock`;
2. `--demo` показывает полный визуальный workflow;
3. doctor диагностирует среду;
4. regular albums видны; Shared Albums нельзя анализировать напрямую, но можно после
   плана и подтверждения скопировать в новый обычный photo-only album;
5. project inventory, previews, metrics, duplicate groups и decisions работают;
6. higher-resolution original защищён от shared-like copy;
7. review gallery и duplicate comparison позволяют manual overrides;
8. overrides переживают restart/reanalysis;
9. Favorite, edited, missing и leader не получают automatic reject;
10. publish dry-run отделён от apply;
11. capability-gated apply создаёт новый обычный Reject-альбом только с final rejects;
12. приложение не удаляет фотографии и не меняет originals/keywords/Favorite;
13. source album не меняется до ручного удаления;
14. cache безопасно очищается;
15. unit tests и `ruff` проходят;
16. основной workflow не требует Node.js, Docker, Qt или ML-моделей.

---

## 30. Future Extensions — не входят в MVP

После стабильного MVP можно добавить optional native Swift/Vision helper:

- Vision FeaturePrint;
- aesthetics score;
- face capture quality;
- более сильное visual similarity;
- portrait-series assistance.

Helper должен быть локальным, optional и без downloadable custom model.

Также позже возможны packaged `.app`, Best/Review temporary albums и персонализация ranking по manual overrides.

---

## 31. Главный архитектурный принцип

```text
Shared Album
        ↓ native PhotoKit copy after confirmation
Personal Photos Library
        ↓
Regular working album
        ↓
Read-only inventory
        ↓
Temporary normalized JPEG cache
        ↓
Lightweight local analysis
        ↓
Visual human review
        ↓
Temporary Reject album
        ↓
Manual Command+Delete
```

Главная ценность MVP — не «идеальная нейросеть», а быстрый, локальный, объяснимый и безопасный workflow, который реально работает поверх Apple Photos и оставляет окончательное решение человеку.
