# Analysis Pipeline

## Принцип

MVP использует лёгкий, локальный и объяснимый анализ. Он не пытается решать все субъективные задачи фотографии. Основной automatic reject — уверенный проигравший в duplicate-группе. Технические и эстетические сомнения направляются в review.

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
- burst key.

## Pair confirmation

- dHash;
- aspect ratio;
- normalized pixel MAE;
- histogram similarity;
- time delta;
- resolution ratio.

## Grouping

Union-find connected components. Проверить similarity каждого member к chosen leader. Chaining с плохим leader similarity → `ambiguous`.

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

## Decisions

```text
keep
review
reject
```

Default automatic reject:

- render-equivalent duplicate loser;
- high-confidence near-duplicate loser при отсутствии protections и warnings.

Default technical defect → review.

## Optional Apple scores

Используются только как relative ranking signal. Низкий aesthetic score сам по себе не создаёт reject.

## Deferred analysis

До optional native Vision helper отложить:

- closed eyes;
- semantic composition;
- pose/occlusion analysis;
- face identity;
- neural aesthetic model;
- advanced crop-invariant similarity.
