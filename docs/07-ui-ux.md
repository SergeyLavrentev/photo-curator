# Native macOS UI and UX

## Product surface

The target interface is SwiftUI with focused AppKit components where measured performance or
platform behaviour requires them. The current web interface is transitional and must not
constrain the native information architecture.

## Main workflow

The home window contains one large, sequential path rather than metric cards:

1. **Настройте вкус** — three rounds of ten photos with exactly three favourites per round.
   The source album can change between rounds and progress survives restart.
2. **Выберите фотографии** — regular Photos album or guided Shared snapshot. This step unlocks
   after the initial taste profile is trained.
3. **Получите подборку** — analysis with one overall progress bar and expanded stage details.
4. **Отбор** — Good / Bad galleries, direct correction and creation of the Best album.

Only the current action is visually dominant. Statistics, Doctor, model versions and stage
telemetry live under `Статус и детали`.

Completed and interrupted analyses are persistent documents in the sidebar. `Новый анализ`
creates only a cancellable UI draft; it never deletes, hides or replaces earlier analyses.
Deletion is an explicit per-analysis destructive action with confirmation and removes only that
analysis and its local cache.

## Analysis progress

Show overall percent, processed/total, current human-readable action and pause/cancel. Stage
details are expanded by default. Stopping preserves completed fingerprints and previews for an
explicit resume; deleting that cache is a separate destructive action. On restart, only incomplete
atomic temporary files are removed. Internal
stages use compact states: pending, running, done, warning, error and interrupted. Never show a
speculative ETA. Restart resumes completed fingerprints and visibly explains invalidation.

## Selection gallery

- default order: personal Swipe Score, then best-in-series and diversity;
- large, fluid native thumbnails with prefetch and bounded cache;
- two explicit tabs, `Хорошие` and `Плохие`, with counts;
- direct `Хорошие` / `Плохие` correction on every card and undo;
- checkbox multi-selection in both tabs with select-visible, clear and batch move actions;
- double-click opens a large photo view together with the same decision evidence as the info
  control; Space keeps the lightweight system Quick Look;
- one compact information control; filenames and a separate `Почему?` button do not consume card
  space;
- category-specific explanations: positive evidence for Good, negative evidence for Bad;
- decision reliability and internal ranking score are different concepts; the score is not shown
  as a universal quality percentage;
- Quick Look, arrow navigation and configurable shortcuts;
- AppKit `NSCollectionView` fallback if SwiftUI grid misses the 5 000-item performance gate.

The UI says «подходит вашему вкусу», not «объективно красиво». It never rates a person's
worth or promises how another person will react.

## Taste calibration and settings

Initial calibration presents ten representative photos and asks for exactly three favourites.
It repeats three times, using different assets and optionally different source albums. Every
selected/non-selected relation becomes an explicit pairwise label: 54 labels train the profile
and 9 remain held out. Initial calibration cannot be skipped; it is local, resumable and remembered
for future albums. Settings show whether personalization is active, number/recency of examples,
current feature/model version and measured profile confidence. The user can pause learning,
recalibrate, reset, export or delete all taste data.
After onboarding succeeds it is application-level state and is not shown as a required step for
each new analysis. Only an explicit reset in Settings makes onboarding required again. A pending
round exposes `Сменить альбом`, which discards only that unfinished round.

Selection breadth is explicitly labelled as the size/strictness of the future Best album; the
source album is always analyzed in full. It is a result-control, not a hidden score threshold.
Future General, Dating,
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
