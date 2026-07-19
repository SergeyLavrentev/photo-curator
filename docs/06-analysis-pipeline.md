# Analysis Pipeline

## Принцип

Pipeline использует локальный и объяснимый анализ. Его цель — ранжированная подборка, а не только список удаления. Технические сигналы, серии и доступный Apple Vision объединяются в оценку 0–100.

## Preview normalization

- Review JPEG: max 2048 px, quality ~88.
- Thumbnail: max 320 px, quality ~75.
- Correct EXIF orientation.
- Pillow first, `sips` fallback.
- Atomic writes и source fingerprint invalidation.

## Technical metrics

На 1024 px normalized image:

- Laplacian variance;
- gradient energy;
- edge density;
- luminance percentiles;
- black/white clipping ratios;
- contrast standard deviation;
- dynamic range;
- entropy.

Затем album-relative percentiles, median и MAD.

## Hashes

### Render-equivalence hash

SHA-256 normalized 256×256 RGB pixels. Это равенство нормализованного render, не обязательно исходных bytes.

### dHash

9×8 grayscale neighbor comparison.

### pHash

32×32 grayscale, собственная NumPy DCT matrix, 8×8 low frequencies.

### Color histogram

Компактный нормализованный histogram.

## Candidate generation

- equal render-equivalence hash;
- strict pHash distance;
- relaxed pHash + short capture-time window;
- только валидный burst key; `0`, `"0"` и пустые значения считаются отсутствующими;
- band buckets ограничены соседями, temporal bucket — 40 ближайшими кадрами.

## Pair confirmation

- dHash;
- aspect ratio;
- normalized pixel MAE;
- histogram similarity;
- time delta;
- resolution ratio.

## Grouping

Union-find на подтвержденных парах, затем обязательная проверка каждого member непосредственно с chosen leader. Слабые A-B-C chains разбиваются на отдельные группы.

## Leader ranking

1. Manual leader.
2. Favorite.
3. Edited.
4. Burst default pick.
5. Pixel count.
6. Composite quality.
7. UUID tie-break.

## Shared-copy guard

Низкоразрешённая импортированная копия не должна автоматически вытеснять полноразмерный original. `resolution_inversion` блокирует publish до ручного решения.

## Локальный Apple Vision

Capability-gated этап определяет лица, face capture quality и наличие landmarks глаз.
При недоступности framework этап честно помечается warning; cloud fallback отсутствует.

## Decisions

```text
keep
review
reject
```

Пользовательские названия: `Отобрано / Проверить / Исключено`. Оценка хранится в
structured reason вместе с component breakdown, включая series rank и selection
confidence. Пороги зависят от density preset.

Default automatic excluded:

- render-equivalent duplicate loser;
- high-confidence near-duplicate loser при отсутствии protections и warnings.

Clearly weak technical defect может стать Excluded; пограничный или неоднозначный → Review.

После первичного scoring temporal diversity pass оставляет не более трёх обычных
auto-selected кадров в 120-секундной сцене. Favorites, edited assets и подтверждённые
leaders защищены; остальные переходят в Review с причиной `diversity_limit`.

## Optional Apple scores

Используются только как relative ranking signal. Низкий aesthetic score сам по себе не создаёт reject.

## Deferred analysis

- надёжная классификация closed eyes (текущий слой видит landmarks, но не объявляет состояние);
- semantic composition;
- pose/occlusion analysis;
- face identity;
- neural aesthetic model;
- advanced crop-invariant similarity.
