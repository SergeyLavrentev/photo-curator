# Native macOS UI and UX

## Product surface

The target interface is SwiftUI with focused AppKit components where measured performance or
platform behaviour requires them. The current web interface is transitional and must not
constrain the native information architecture.

## Main workflow

The home window contains one large, sequential path rather than metric cards:

1. **Выберите фотографии** — regular Photos album or guided Shared snapshot.
2. **Настройте вкус** — optional short A/B calibration, remembered for future albums.
3. **Получите подборку** — analysis with one overall progress bar and expandable stages.
4. **Проверьте и сохраните** — personalized gallery, dry-run and approved Photos album.

Only the current action is visually dominant. Statistics, Doctor, model versions and stage
telemetry live under `Статус и детали`.

## Analysis progress

Show overall percent, processed/total, current human-readable action and pause/cancel. Internal
stages use compact states: pending, running, done, warning, error and interrupted. Never show a
speculative ETA. Restart resumes completed fingerprints and visibly explains invalidation.

## Review gallery

- default order: personal Swipe Score, then best-in-series and diversity;
- large, fluid native thumbnails with prefetch and bounded cache;
- compact score and one primary reason; full component evidence behind `?`/Inspector;
- generic baseline and personal adjustment shown separately;
- filters for Selected, Review, Excluded, series and uncertainty;
- multi-select, batch disposition, leader change and undo;
- Quick Look, arrow navigation and configurable shortcuts;
- AppKit `NSCollectionView` fallback if SwiftUI grid misses the 5 000-item performance gate.

The UI says «подходит вашему вкусу», not «объективно красиво». It never rates a person's
worth or promises how another person will react.

## Taste calibration and settings

Calibration presents representative A/B choices and can be skipped. Settings show whether
personalization is active, number/recency of examples, current feature/model version and
measured profile confidence. The user can pause learning, recalibrate, reset, export or delete
all taste data.

Selection breadth is a result-control, not a hidden score threshold. Future General, Dating,
Travel, Portfolio and Family profiles remain disabled until separately evaluated.

## Publish UX

Show the exact selected count, destination name, source-drift blockers and immutable dry-run
summary. Apply requires explicit confirmation and creates/updates only the approved destination
through PhotoKit. Reject is never published or deleted automatically.

## macOS quality bar

- native menu commands, Dock lifecycle, settings and state restoration;
- accessibility labels, VoiceOver order, keyboard focus and reduced motion;
- responsive resize, dark/light appearance and high-quality colour management;
- no browser tab, localhost error page or backend controls in the released GUI.
