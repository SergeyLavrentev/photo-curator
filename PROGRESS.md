# Progress

## Current milestone

- Milestone: 8 — Hardening и MVP acceptance
- Status: Implementation complete; manual macOS integration gate pending

## Roadmap

- [x] M0 — package, uv lock, SQLite, loopback UI, startup session, synthetic demo
- [x] M1 — provider protocol, fake/real `osxphotos` adapters, Doctor/read gate
- [x] M2 — projects, inventory, assets, jobs, interrupted/retry state
- [x] M3 — render resolution, oriented atomic previews, cache/retry/invalidation
- [x] M4 — local metrics, hashes, robust album-relative normalization
- [x] M5 — candidate reduction, pair evidence, union-find, leaders/resolution guard
- [x] M6 — conservative decisions, protections, review filters/sort/batch/shortcuts/notes
- [x] M7 — capability gate, revalidation, unique album, dry-run/apply/audit separation
- [x] M8 — resume, source drift, safe cleanup, security audit, docs and tests

## Validation performed — 2026-07-18

```text
uv sync                                     — success
ruff format --check .                       — passed
ruff check .                                — passed
pytest                                      — 37 passed
pytest --cov=photo_curator                   — 74% total coverage
osxphotos batch-edit capability flags       — confirmed
demo full pipeline                          — 12/12 previews, metrics and decisions
cache reuse and source invalidation         — automated test passed
manual override after reanalysis            — automated test passed
publisher dry-run/apply state machine       — mocked integration passed
loopback/session/CSRF/media path isolation   — automated tests passed
```

## External acceptance gate

`photo-curator doctor` проходит platform, Python, `sips`, `osxphotos`, writable dirs
и loopback checks, но macOS отклоняет чтение текущей Photos Library. Требуется вручную
выдать Photos/Full Disk Access для Codex или Terminal, перезапустить приложение и
выполнить manual test на отдельном альбоме. До этого реальный Photos read gate и
создание временного Reject-альбома не отмечены как проверенные.

## Safety invariants verified in code

- bind только `127.0.0.1`, Host allowlist, startup session и CSRF;
- никаких direct Photos DB writes, automatic delete, cloud calls или telemetry;
- subprocess без shell, UUID file sorted one-per-line;
- Favorite/edited/missing/leader/ambiguous cases не получают automatic reject;
- higher-resolution inversion блокирует publish без manual override;
- recursive cleanup разрешён только внутри cache root.
