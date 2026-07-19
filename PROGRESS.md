# Progress

## Current milestone

- Milestone: R7 — Acceptance and release audit
- Status: Feature stages R0–R6 implemented; external visual and Photos apply gates remain

## Product roadmap

The authoritative roadmap is [`ROADMAP.md`](ROADMAP.md). The product now targets
an explainable curated gallery and a user-confirmed Best album.

- [x] R0 — clean application data baseline and replace the roadmap
- [x] R1 — reliable candidate generation, progress, resume and state handling
- [x] R2 — Selected / Review / Excluded, scores, explanations and density presets
- [x] R3 — coherent series, best-frame ranking and local Apple Vision signals
- [x] R4 — compact dark UI, gallery-first workflow and in-place progress polling
- [x] R5 — guided Shared Album intake with explicit capability limits
- [x] R6 — independent Best/Reject dry-run/apply and local preview ZIP export
- [ ] R7 — labelled acceptance, performance gates and release audit

## Current verified evidence

```text
real subset: 20 photos from Черногория — локально
all local tools including Apple Vision       — 3.55 s
candidate reduction                          — 39 / 190 pairs
confirmed series                             — 2 near groups, 2 members each
Vision                                       — 21 faces, 21 eye landmarks, 18 quality samples
curated result                               — 3 selected / 16 review / 1 excluded
score range                                  — 26–90
2,000 synthetic rows                         — 5,454 / 1,999,000 pairs in 0.013 s
5,000 synthetic rows                         — 19,059 / 12,497,500 pairs in 0.039 s
60-photo labelled generated fixture          — passed; no cross-scene groups
real 20-photo Best publish dry-run            — dry_run_ok, 10 UUIDs, apply not run
```

The in-app browser is currently blocked from `127.0.0.1` by enterprise network
policy. Responsive screenshot acceptance is therefore not checked. A real 50–100
photo human-labelled album remains the last external release-quality gate. The
real macOS Photos Best dry-run passed; apply was intentionally not executed.

## Previous MVP evidence — retained for history only

```text
uv sync                                     — success
ruff format --check .                       — passed
ruff check .                                — passed
pytest                                      — 48 passed
pytest --cov=photo_curator                   — 81% total coverage
osxphotos batch-edit capability flags       — confirmed
demo full pipeline                          — 12/12 previews, metrics and decisions
cache reuse and source invalidation         — automated test passed
manual override after restart/reanalysis    — automated test passed
publisher dry-run/apply state machine       — mocked integration passed
source drift and resolution inversion       — automated tests passed
>3000 asset candidate reduction             — automated test passed
loopback/session/CSRF/media path isolation   — automated tests passed
desktop + 390 px browser workflow            — passed, no horizontal overflow
duplicate manual override via browser        — passed end-to-end
```

The checks below covered the former reject-first implementation. They do not prove
the new roadmap complete and must not be used as release acceptance.

## Current external acceptance gate

The root failure from the old 1,671-photo run is covered by sentinel and bounded
candidate tests. Release acceptance still requires the three R7 gates listed above.

## Safety invariants verified in code

- bind только `127.0.0.1`, Host allowlist, startup session и CSRF;
- никаких direct Photos DB writes, automatic delete, cloud calls или telemetry;
- subprocess без shell, UUID file sorted one-per-line;
- missing/failed/ambiguous cases не получают automatic reject; Favorite/edited/leader
  are selected unless analysis is unavailable;
- higher-resolution inversion блокирует publish без manual override;
- recursive cleanup разрешён только внутри cache root.
