# Native macOS UI and UX

## Product surface

The target interface is SwiftUI with focused AppKit components where measured performance or
platform behaviour requires them. The current web interface is transitional and must not
constrain the native information architecture.

## Main workflow

The home window contains one large, sequential path rather than metric cards:

1. **Настройте вкус** — three rounds of ten photos with exactly three favourites and three
   explicitly disliked photos per round. The source album can change between rounds and progress
   survives restart.
2. **Выберите фотографии** — regular Photos album or guided Shared snapshot. This step unlocks
   after the initial taste profile is trained.
3. **Получите подборку** — analysis with one overall progress bar and expanded stage details.
4. **Отбор** — Pick / Alternatives / Review / Reject and creation of the Best album.

Only the current action is visually dominant. Statistics, Doctor, model versions and stage
telemetry live under `Статус и детали`.

Completed and interrupted analyses are persistent documents in the sidebar. The sidebar contains
only this list; compact window-toolbar actions contain `Новый анализ` and `Настроить вкус`.
`Новый анализ` creates only a cancellable UI draft; it never deletes, hides or replaces earlier
analyses. Deletion is an explicit per-analysis destructive action with confirmation and removes
only that analysis and its local cache.

## Analysis progress

Show overall percent, processed/total, current human-readable action and pause/cancel. Stage
details are expanded by default. Stopping preserves completed fingerprints and previews for an
explicit resume; deleting that cache is a separate destructive action. On restart, only incomplete
atomic temporary files are removed. Internal
stages use compact states: pending, running, done, warning, error and interrupted. Never show a
speculative ETA. Restart resumes completed fingerprints and visibly explains invalidation.

## Selection gallery

- default order: personal Swipe Score, then best-in-series and diversity;
- fluid, compact native thumbnails with prefetch and bounded cache; the default gallery shows at
  least twenty-four cards in a typical desktop window, with a size slider and +/- zoom controls;
- four explicit Pick / Alternatives / Review / Reject filters with counts;
- Lightroom-style Grid, Loupe, Compare and Survey modes with filmstrip and inspector;
- direct `P` Pick, `U` Unflag, `X` Reject, ratings 1–5, arrows, Space and undo;
- collapsed series stacks whose explicit expansion and Compare/Survey context loads every active
  member across page and category boundaries; an unavailable context never falls back to an
  unrelated photo;
- unobtrusive checkbox multi-selection; clear and batch move actions appear only
  after photos are selected;
- a single click selects immediately; a separate info button or context-menu action opens the
  large detail view without making selection wait for a double-click recognizer; Space keeps the
  lightweight system Quick Look;
- cards keep only selection, series, rating and category badges; detailed decisions live in the
  context menu, keyboard workflow and large detail view instead of per-card popovers;
- category-specific explanations: positive evidence for Good, negative evidence for Bad;
- manual decisions and engine recommendations are shown as separate fields; recommendation
  reasons never masquerade as justification for a conflicting user override;
- the active Pick/Reject control is visibly selected and disabled, so repeated no-op actions are
  not presented as available commands;
- decision reliability and internal ranking score are different concepts; the score is not shown
  as a universal quality percentage;
- Quick Look, arrow navigation and configurable shortcuts;
- 36-card keyset-paged SQLite DTOs, ImageIO downsampling with four concurrent decoders, separate
  bounded thumbnail/review caches keyed by path and requested pixel size, in-flight coalescing,
  visible-over-prefetch priority and stale-response generation/epoch guards;
- explicit unavailable thumbnails when a database `ready` row points to a deleted cache file,
  plus a user-triggered **Restore previews** action; restoring a project never silently starts a
  full re-analysis;
- AppKit `NSCollectionView` fallback if SwiftUI misses a measured performance gate.

The UI says «подходит вашему вкусу», not «объективно красиво». It never rates a person's
worth or promises how another person will react.

## Taste calibration and settings

Initial calibration presents ten representative photos and asks for exactly three favourites and
three explicitly disliked photos. Only favourite/disliked relations become pairwise labels; the
four unmarked photos are neutral, never implicit negative examples. It repeats three times, using
different assets and optionally different source albums: 18 labels train the profile and 9 remain
held out. Initial calibration cannot be skipped; it is local, resumable and remembered for future
albums. `Настроить вкус` opens the same editor later, where an extra round adds explicit
preferences and immediately re-ranks completed analyses from the decisions stage. Settings show
whether personalization is active, number/recency of examples, current feature/model version and
measured profile confidence. The user can pause learning, recalibrate, reset, export or delete all
taste data.
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
