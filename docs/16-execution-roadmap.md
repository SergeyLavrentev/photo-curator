# Photo Curator implementation roadmap

This roadmap turns the product review into an executable plan. It supplements `ROADMAP.md`:
the latter defines the product direction, while this file records implementation order,
dependencies, acceptance evidence and completion status.

Current versioned boundary: `docs/18-acceptance-2026-08-27.md`. The earlier disposable PhotoKit
publish proof remains in `docs/17-acceptance-2026-08-09.md`.

## Product state contract

Safety and selection are independent axes:

| Safety disposition | Meaning |
| --- | --- |
| `keep` | Do not recommend deletion. |
| `review` | Evidence is incomplete or conflicting. |
| `reject` | Safe deletion candidate after explicit user review. |

| Selection state | Meaning |
| --- | --- |
| `pick` | Include in the proposed Best album. |
| `alternative` | Valid photo, but not currently selected. |
| `review` | Selection cannot be made confidently. |
| `reject` | Mirrors a safe reject recommendation. |

`Best` is built from `pick`, never from every non-rejected photo. Personal taste and global
aesthetics rank `pick` candidates but cannot independently make a photo safe to delete.

## Acceptance gates

Quality gates use album/user-separated hold-outs, not pairs made from the same images:

- exact duplicate precision at least 99.9%;
- false automatic rejection of a unique good photo at most 0.5%;
- near-series recall at least 95%;
- best-in-series Top-1 accuracy at least 85%;
- objective-defect reject precision at least 98%;
- calibrated confidence measured with Brier score and ECE;
- vacation-album reduction of 35–60% without losing labelled essential scenes;
- personal taste must improve held-out pairwise accuracy over the generic baseline;
- 50,000-card gallery: cold first page below 700 ms, SQL page p95 below 100 ms,
  no image I/O on the main thread and bounded memory;
- real disposable PhotoKit publish acceptance must prove source revalidation and exact
  destination membership.

## R0 — truthful baseline and fixtures

- [x] Export immutable current, non-personalized and Apple-only score snapshots.
- [ ] Add album-separated pairwise, series-leader, defect and final-pick labels.
- [x] Report confidence calibration and reduction ratio alongside existing acceptance metrics.
- [x] Freeze Apple-only, current ensemble and non-personalized baselines.

Evidence: versioned manifests, reproducible acceptance reports and a documented source split.

## R1 — explicit curation states

- [x] Define independent safety and selection contracts.
- [x] Persist automatic, manual and final selection state.
- [x] Build Best from final `pick` only.
- [x] Map manual Pick/Reject actions without weakening safety protections.
- [x] Expose Pick, Alternatives, Review and Reject counts and filters in native UI.

Evidence: migration tests, pipeline tests, publish UUID contract and migration of a real project.

## R2 — series and duplicate engine v2

- [x] Keep deterministic render hash for exact copies.
- [x] Replace capped bucket neighbours with radius-2 multi-probe pHash candidates.
- [x] Add crop/exposure-aware similarity and Vision feature-print candidates.
- [x] Separate scene, burst and exact-copy grouping.
- [x] Rank leaders only after Vision/Core ML/face signals are available.
- [x] Recompute every member-to-leader evidence row after a leader change.
- [x] Select one or more frames based on pose/moment diversity rather than one fixed leader.

Evidence: duplicate precision/recall, group recall and leader Top-1 on held-out trips.

## R3 — objective defects and calibrated decisions

- [x] Rename album percentiles as relative ranking signals; never call them confirmed defects.
- [x] Add subject/face ROI sharpness and separate motion/defocus evidence.
- [x] Use clipping ratios and subject exposure instead of mean-luma rejection.
- [x] Compare eyes and face capture inside a series; keep unsupported expression/occlusion out.
- [x] Add horizon and blocked-subject evidence where locally supportable.
- [ ] Replace signal-count confidence with calibrated probability and model disagreement.
  - [x] Separate evidence coverage from decision probability and compute robust cross-model
    disagreement on a common album-percentile scale.
  - [x] Reduce uncalibrated reliability when independent models conflict, expose disagreement in
    the native inspector and invalidate the previous calibration contract.
  - [ ] Fit and activate the new calibrator only after album-separated calibration/held-out labels
    pass Brier/ECE gates.
- [x] Preserve exact-copy-only auto reject until the labelled gate passes.

Evidence: defect precision, false reject rate, Brier score and ECE.

## R4 — Swipe Score and Personal Taste v2

- [x] Remove constant saliency-derived composition bonuses.
- [x] Learn non-negative ensemble weights and activate only after album-held-out Apple uplift.
- [x] Treat MUSIQ as technical quality unless held-out attraction uplift is proven.
- [x] Split taste examples by independent assets/albums, not Cartesian pair diagonals.
- [x] Center personal deltas and shrink them until independent evidence is sufficient.
- [x] Keep taste outside duplicate safety and objective-defect decisions.

Evidence: generic and personalized pairwise/Top-K uplift on separate hold-outs.

## R5 — Core ML correctness, provenance and throughput

- [x] Fix MUSIQ multi-scale/aspect-fitted preprocessing.
- [x] Match each model's declared crop/resize/normalization contract.
- [x] Validate finite values, ranges, result schema and per-engine versions.
- [x] Verify model manifests and deterministic content SHA-256 at build and runtime.
- [x] Include model content digest in signal provenance and invalidation.
- [x] Cache compiled models and inference by immutable source fingerprint.
- [ ] Benchmark safe batching/concurrency, memory, thermal behaviour and energy.
  - [x] Add bounded work batches/concurrency with score-parity, RSS and thermal fail-closed gates.
  - [x] Validate `16x2` against `1x1` on a retained local 12-preview smoke set.
  - [x] Repeat capacity/parity on at least 48 restored real review previews.
  - [ ] Retain Instruments energy/ANE evidence with joules and an explicit budget.
- [x] Declare current use private and non-commercial; prevent accidental redistribution claims.

Evidence: conversion fixtures, inference parity, cache invalidation and Apple Silicon benchmarks.

Current Apple Silicon evidence (2026-08-09, 48 real review previews, all three engines): cold
24.189 s including compile/load, warm 3.812 s (12.593 assets/s), zero signal errors, peak RSS
347 MB. Mean warm inference was 12.9 ms NIMA, 8.9 ms MobileCLIP and 47.9 ms MUSIQ. Thermal
state is now recorded before/after every helper run; repeated local validation observed `fair →
fair`. Energy remains explicitly `not_measured`: this host has Command Line Tools without a
working `xctrace`, while `powermetrics` requires interactive superuser authorization.

Additional 2026-08-27 bounded-concurrency smoke evidence (`build/evidence/local-model-performance.json`)
used 12 retained JPEG previews and all three engines. `16x2` was output-equivalent to `1x1`
(maximum numeric drift `5.6e-17`), stayed `nominal → nominal`, raised peak RSS from 76.6 MB to
88.5 MB and improved throughput from 11.83 to 17.65 assets/s. Capacity passed; the report remains
overall failed because energy was not measured. The production helper now defaults to the bounded
`16x2` schedule.

Fresh restored-preview evidence on 2026-08-27 used 48 images from project
`9d612b8e-9a7b-47a0-b4ae-f89901554875`. All three modes completed with zero errors, maximum
numeric drift `1.11e-16`, peak RSS below 273 MB and `nominal → nominal` thermal state. Under a
highly contended host, `16x2` still led at 7.78 assets/s versus 0.66 for `1x1`; the result is valid
capacity/parity evidence but not a clean throughput comparison. Overall `passed=false` remains
correct because `xctrace`, joules and an energy budget were unavailable.

## R6 — PhotoKit publishing

- [x] Add existing `PHAsset` objects to Best for regular PhotoKit projects.
- [x] Keep independent import only for service-owned Shared/disk snapshots.
- [x] Preserve immutable dry-run, source revalidation, explicit approval and audit.
- [x] Preview the exact Pick set, immutable UUID digest and destination behaviour before apply.

Evidence: disposable real-library membership test proving no duplicate asset was created for a
regular source, the exact acceptance album was removed, its source asset survived cleanup and
snapshots still import independent copies.

## R7 — scalable gallery data path

- [x] Replace full-project decode/sort/slice with SQL filter/order/keyset cursor/limit.
- [x] Return a versioned typed lightweight card page and fetch details separately.
- [x] Give cards explicit thumbnail paths and loupe/detail explicit review paths.
- [x] Add mutation generations and ignore stale updates per photo.
- [x] Replace mutable offset pagination with a stable score/UUID cursor.

Evidence: query plans, pagination mutation tests and 50,000-row API benchmarks.

Current release benchmark (2026-08-27, 50,000 rows): cold first page 11.8 ms, SQL page
p95 4.1 ms and mutation refresh 4.1 ms. The score-indexed CTE pages decision views without
sorting the complete album.

Real-project duplicate rerank evidence (2026-08-09): 1,666 assets and 262 groups completed in
2.221 s at 197 MB peak RSS, preserving all 262 groups. The previous full-review JPEG path took
29.399 s and 233 MB; thumbnail descriptors and a shared cache made this pass about 13× faster.

## R8 — Lightroom-style native workspace

- [x] Add source/filter sidebar, lazy Grid, metadata inspector and filmstrip.
- [x] Add Grid, Loupe, Compare and Survey modes.
- [x] Collapse duplicate/series stacks and expose series frames in Compare/Survey.
- [x] Add ratings; `P` Pick, `X` Reject, `U` Unflag, arrows, undo and batch actions.
- [x] Downsample through ImageIO, coalesce requests, cancel off-screen work and prefetch neighbours.
- [x] Remove synchronous image decoding from `MainActor` views.
- [x] Replace the synthetic rectangle benchmark with production `PhotoCard`/ImageIO/JPEG paging
  plus real SQLite/JPEG/mutation coverage.

Evidence: UI contract tests, real render benchmark, accessibility audit and manual workflow run.

Manual packaged-app QA (2026-08-09) exercised Grid, Loupe, Compare, Survey, inspector, filmstrip
and lazy reason details against the 1,666-photo project. It caught and fixed an unbounded Loupe/
Survey canvas; the final build keeps the whole photo, 2×2 survey and filmstrip in one viewport.

Production render evidence was refreshed earlier on 2026-08-27 with the shipped `PhotoCard`,
ImageIO loader and real JPEG pages: 2k/5k initial layout/decode was 374/369 ms, page-swap p95
154/195 ms and scroll p95 6.5/6.4 ms. A later same-day stress rerun under a highly contended host
failed the page-swap budget while initial layout and scrolling still passed. Inspection found that
cancelled off-screen cards left detached decodes queued. The loader now removes cancelled waiters
from the bounded decode queue and clears its immutable-path cache before explicit preview repair.
A fresh current-source run on 2026-08-28 at ordinary interactive load passed both 2k and 5k.
After separating visible `.userInitiated` decode work from `.utility` prefetch, three consecutive
runs passed: initial layout/decode 370–392/409–419 ms, page-swap p95 153–270/161–313 ms and
scroll p95 7.6–7.8/7.7–7.7 ms. A
packaged read-only reopen also exposed a deleted local preview cache; the UI replaces permanent
spinners with an explicit unavailable state and a user-triggered preview-repair action.

A real repair attempt on 2026-08-27 initially found 394 opportunistic PhotoKit renders that were
only 48×64 px. The pipeline marks sub-256×512 renders `degraded`, keeps them UI-visible,
invalidates their automatic signals and routes every affected asset to Review without running
Apple Vision, Core ML or Codex. A later isolated cache probe proved that the shared v5 render cache,
not current album-wide iCloud availability, was preserving those obsolete tiny files: removing
only one service-owned render caused exactly that asset to return at analysis resolution.
The native helper now validates every shared-cache hit and re-requests only undersized files.
A fresh installed local run then obtained 386 analysis-grade renders out of the current 387-photo
album; one HEIC remained a truthful iCloud/degraded warning. All 386 completed technical metrics,
Apple Vision, NIMA, MobileCLIP and MUSIQ; no image was transmitted to Codex. Exact timings and
decision distribution are recorded in `docs/18-acceptance-2026-08-27.md`.

An isolated packaged synthetic E2E on 2026-08-27 completed Choose → Analyze → Review without
Photos access and exercised Grid, Loupe, Compare, Survey, tabs and a manual Reject. It exposed and
fixed two state-language defects: Pick used API value `keep` while final state used `pick`, so the
active control was never highlighted; and positive engine reasons were shown beside a conflicting
manual Reject without attribution. The inspector now separates `Решение пользователя` from
`Рекомендация движка`, labels recommendation reasons explicitly and disables the already-active
decision control.

## R9 — maintainable execution architecture

- [x] Split the remaining 4k-line SwiftUI application/state owner into bounded feature modules.
- [x] Replace untyped `[String: Any]` IPC at the UI boundary with Codable envelopes/DTOs.
- [x] Multiplex worker requests so progress, cancellation and reads do not share one blocking lock.
- [x] Persist stage input fingerprints and invalidate dependent artifacts automatically.
- [x] Preserve one authoritative coordinator state owner and SQLite audit trail.
- [x] Refuse a second worker for the same catalog and create a verified online backup before
  every project cascade delete.

Evidence: dependency invalidation tests, concurrent IPC tests and reduced module boundaries.

Current boundary: requests, responses and progress events use versioned `Codable` envelopes and
named DTOs; the Swift IPC path contains no `[String: Any]` or `JSONSerialization` bridge. The former
4,260-line application file is split into a bounded app shell, AppModel, Quality Lab model/wizard,
root workflow, help and gallery/detail modules. Settings uses a resizable scroll container rather
than a clipped fixed-size form. Contract tests enforce per-file limits and both benchmark and app
compile manifests enumerate the same production sources.

Quality acceptance now has a dedicated six-step native wizard: source analysis, deterministic
blind sample, defect taxonomy, held-out A/B, ordered Top-K, series/leader and export/evaluation.
Lab labels carry explicit sampled provenance, and lab A/B evidence is isolated from taste training.

## R10 — completion audit

- [x] Unit, integration, Ruff, Swift typecheck and packaging verification pass.
- [ ] Labelled generic, personal, defect, duplicate and series gates pass.
- [x] Current-source 2k/5k gallery performance passes again on a non-contended host; the 50k SQL
  data-path evidence remains green.
- [x] Restart, cancel, resume, settings/model invalidation and migration pass.
- [x] Disposable real PhotoKit source/publish acceptance passes.
- [ ] Installed private non-commercial build completes Choose → Analyze → Review → Save.

The project is complete only when every unchecked item has authoritative evidence. Green unit
tests alone do not satisfy quality, real Photos or visual-performance gates.

Packaged synthetic E2E (2026-08-09) passed in an isolated temporary `HOME`: Choose → Analyze
12/12 → Review/Loupe → immutable two-asset dry-run → explicit confirmation → Save/audit. The demo
publisher revalidates the exact set and never touches Photos. This does not replace the unchecked
installed real-PhotoKit gate above.

Real PhotoKit acceptance first passed on 2026-08-09: one existing asset was added to a uniquely
named album and the exact album container was subsequently removed while the 11-photo source
remained unchanged. A later helper-launch experiment exposed a separate catalog-safety defect: both
local project rows were cascade-deleted. The source Photos library was unaffected. SQLite forensic
recovery restored 1,660 of 1,666 old-project assets and the project is explicitly marked
`recovery_status=partial`; the current build adds single-worker locking, explicit delete tokens,
pre-delete online backups and destructive-action audit.

Fresh installed-build acceptance passed again on 2026-08-27 through the authorized native worker:
the source album and source-asset membership were revalidated, one existing PHAsset was added to a
unique disposable Best album without import or duplication, and cleanup removed exactly the
returned album identifier. A new PhotoKit read confirmed both source album and source asset still
present and the disposable album absent. Exact identifiers and installed hashes are recorded in
`docs/18-acceptance-2026-08-27.md`. This refresh closes the disposable PhotoKit gate. The installed
local build has also completed real Choose → Analyze → Review on 386/387 analysis-grade previews,
but Save remains open because no non-disposable Best album was created during this audit.
