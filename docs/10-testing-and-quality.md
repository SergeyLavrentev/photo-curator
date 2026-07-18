# Testing и quality gates

## Unit fixtures

Генерировать через Pillow:

- sharp image;
- Gaussian blur;
- motion-like blur;
- dark;
- overexposed;
- low contrast;
- render-equivalent duplicate;
- JPEG recompressed duplicate;
- resized duplicate;
- slight crop;
- similar but distinct;
- higher-resolution original;
- lower-resolution shared-like copy;
- EXIF-rotated image.

## Unit coverage

- orientation normalization;
- render-equivalence hash;
- dHash/pHash/Hamming;
- technical metrics;
- robust normalization;
- candidate generation;
- pair confirmation;
- union-find;
- ambiguous chaining;
- leader ranking;
- Favorite/edited/missing protections;
- manual override persistence;
- resolution inversion;
- migrations;
- safe cache paths;
- publish validation.

## Fake provider

`FakePhotosProvider` позволяет выполнять полный pipeline на любой платформе. `photo-curator --demo` должен быть демонстрацией и integration test UI.

## Publisher tests

Mock subprocess runner проверяет:

- argv list и `shell=False`;
- one UUID per line;
- empty reject set;
- sanitised unique album name;
- dry-run/apply distinction;
- return code handling;
- persistence stdout/stderr;
- отсутствие automatic retry apply;
- capability-disabled mode.

## Manual macOS test

Отдельный обычный альбом `PhotoCurator Test` из 50–100 неважных assets:

- duplicate pairs;
- imported lower-resolution copies;
- Favorite;
- edited;
- Live Photo;
- missing/iCloud-only при возможности.

Проверить read gate, pipeline, review, dry-run и создание временного альбома. Ничего не удалять приложением.

## Commands

```bash
uv run pytest
uv run pytest --cov=photo_curator
uv run ruff check .
uv run ruff format --check .
```

## Quality gates

- Tests зелёные перед следующим milestone.
- Нет `shell=True`.
- Нет direct Photos DB writes.
- Нет web routes, напрямую вызывающих `osxphotos`.
- Нет необработанных source paths в HTML/JSON.
- Manual overrides не теряются.
