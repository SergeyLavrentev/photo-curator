# Photo Curator — product roadmap

## Product outcome

Photo Curator turns a large unreviewed trip album into a compact, explainable
gallery of the best photographs. The primary result is a curated selection, not
a deletion list.

```text
Source album
  -> local working cache
  -> technical analysis
  -> exact duplicates
  -> similar scenes and series
  -> ranking inside each series
  -> Selected / Review / Excluded
  -> user corrections
  -> "Best of" album and optional Reject album
```

The application never deletes photos, changes originals, or silently publishes
results. Apple Photos remains the source of truth.

## R0 — clean baseline

- Remove previous application projects, caches, logs, demo data and temporary QA data.
- Confirm that no service-created `PhotoCurator — ...` albums remain in Photos.
- Keep original user albums untouched.
- Replace the old reject-first roadmap and progress claims with this roadmap.

Acceptance: a clean application start contains no projects and no cached media.

## R1 — reliable pipeline

- Normalize provider sentinels such as `burst_key=0` to missing values.
- Generate duplicate candidates through exact hashes, perceptual-hash buckets,
  capture-time windows and valid burst groups for albums of every size.
- Avoid opening image files for unrelated pairs.
- Persist batch progress and expose real candidate/pair counters.
- Resume interrupted work without repeating completed previews and metrics.
- Keep project and job states consistent; stop full-page polling reloads.

Acceptance: a 2,000-photo synthetic inventory completes candidate generation in a
bounded time and interrupted jobs resume with truthful UI state.

## R2 — curated selection model

- Make `selected`, `review` and `excluded` the user-facing result buckets.
- Keep safety-compatible internal dispositions and manual overrides.
- Store a 0–100 overall score and component scores for technical quality,
  series rank and selection confidence.
- Store short human-readable reasons for every automatic recommendation.
- Support selection-density presets: compact, balanced and broad.
- Treat the selected gallery as the primary project screen.

Acceptance: every analyzed asset has a bucket, score and at least one explanation;
manual changes survive reanalysis.

## R3 — series and best-frame selection

- Separate exact duplicates from near-duplicate scenes and temporal series.
- Validate every group member against the chosen leader and split weak chains.
- Rank frames inside a series using resolution, sharpness, exposure, contrast,
  Favorite/edited protections and available Apple scores.
- Add a capability-gated local Vision layer for faces, capture quality and eye
  landmarks; do not claim closed-eye state without a validated classifier and
  never require cloud image APIs.
- Preserve diversity so one scene cannot dominate the final gallery.

Implementation: a 120-second temporal scene keeps at most three ordinary automatic
selections; Favorites, edited frames and validated series leaders remain protected.

Acceptance: a labelled 50–100 photo fixture measures duplicate precision, series
leader accuracy and false exclusions.

Implemented automated baseline: a generated labelled 60-photo fixture covers exact,
recompressed, resized, blurred, dark and separated scenes. Human-labelled travel
album acceptance remains an R7 release gate.

## R4 — clear dark UI

- Use a dark, desktop-first interface with compact typography and controls.
- Replace large stage cards with one horizontal progress rail and one expanded
  active-stage detail row.
- Show only the key outcome counters above the gallery.
- Provide gallery tabs for Selected, Review and Excluded.
- Show score breakdown, reasons and comparison with the series leader on demand.
- Update progress in place without reloading the whole page.

Acceptance: the main workflow fits in one viewport at 1280 px before the gallery,
works at 390 px, and all statuses remain understandable without colour alone.

## R5 — source intake

- Keep existing regular Photos albums as the primary source.
- Detect Shared Albums separately and offer a guided local-working-copy flow only
  when the installed macOS/osxphotos capabilities can export them safely.
- Otherwise present an explicit import-to-library instruction instead of a dead
  disabled selector.
- Record `regular_album` / `manual_shared_copy` provenance and warn when shared
  copies may have reduced resolution or metadata.

Acceptance: the user always understands whether a source is analyzed directly,
copied locally, or requires manual import.

## R6 — outputs

- Review the curated gallery before any external write.
- Create a new regular `PhotoCurator — <source> — Best` album from Selected.
- Optionally create a separate Reject album from confirmed Excluded assets.
- Keep dry-run and explicit apply confirmation for every Photos write.
- Offer a local ZIP of review renders; never label it as an original-file export.

Acceptance: Best and Reject publishing are independently previewed, confirmed and audited.

Verified on macOS: a temporary 20-photo real project produced a successful Best
album `osxphotos` dry-run with 10 selected UUIDs. Apply was intentionally not run.

## R7 — acceptance and release

- Maintain a generated unit fixture for exact, recompressed, resized, cropped,
  blurred, dark, overexposed and unrelated images.
- Maintain a labelled real-world acceptance album of 50–100 expendable assets.
- Run performance acceptance at 100, 2,000 and 5,000 inventory rows.
- Verify tests, lint, dark UI screenshots, restart/resume and macOS publish dry-run.
- Update `PROGRESS.md` only from verified evidence.

## Definition of Done

The new product is done only when a user can start from a supported source, run a
truthful resumable pipeline, understand every recommendation, refine a useful
Selected gallery and explicitly publish a new Best album without any automatic
deletion or mutation of originals.
