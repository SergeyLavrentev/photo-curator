# Milestones и Definition of Done

## Milestone 0 — Scaffold и visual shell

Deliverables:

- package/entrypoint;
- `pyproject.toml`, `.python-version`, `uv.lock`;
- FastAPI/Jinja/local assets;
- application paths и DB migration;
- startup token;
- empty dashboard pipeline;
- `--demo`.

Acceptance:

```bash
uv sync
uv run photo-curator --demo
```

открывает работающий визуальный UI.

## Milestone 1 — Doctor и provider

- `PhotosProvider`, real/fake implementations;
- current library;
- regular album browser;
- separate Shared Albums with capability-gated partial/full local copy;
- Unicode/folder paths;
- read gate.

## Milestone 2 — Inventory

- projects/assets;
- optional score JSON;
- job progress;
- restart/interrupted state;
- per-asset failure containment.

## Milestone 3 — Preview cache

- render resolver;
- Pillow/sips;
- atomic cache;
- orientation;
- thumbnails/gallery skeleton;
- missing retry.

## Milestone 4 — Technical analysis

- metrics;
- robust percentiles;
- flags;
- dashboard distributions.

## Milestone 5 — Duplicates

- hashes;
- candidates/confirmation;
- union-find;
- ambiguity;
- leaders;
- resolution guard;
- comparison page.

## Milestone 6 — Decisions/review

- dispositions/reasons/confidence;
- protections;
- manual overrides/leaders;
- filters, shortcuts, batch actions;
- best candidates.

## Milestone 7 — Publisher

- capability gate;
- revalidation;
- dry-run;
- unique album;
- apply/audit;
- final Photos instructions.

## Milestone 8 — Hardening

- resume/invalidation;
- source drift;
- cache cleanup;
- documentation;
- tests and `ruff`.

## Definition of Done

MVP готов, если:

1. Устанавливается через `uv` и содержит lockfile.
2. Demo показывает полный workflow.
3. Doctor диагностирует среду.
4. Regular albums выбираются; для Shared Albums доступен честный plan/confirm workflow
   частичной или полной фотокопии с явным пропуском видео и manual fallback.
5. Inventory, previews, metrics, groups, локальный Vision и decisions работают.
6. Higher-resolution original защищён от shared-like copy.
7. Review UI и duplicate comparison работают.
8. Manual overrides сохраняются после restart/reanalysis.
9. Favorite, edited, missing и leader не получают automatic reject.
10. Dry-run и apply разделены.
11. Capability-gated publish создаёт новый regular Best album; Reject опционален.
12. Приложение не удаляет, не меняет originals, Favorite и keywords.
13. Source album не меняется до ручного удаления.
14. Cache безопасно очищается.
15. Tests и `ruff` проходят.
16. Нет обязательных Node.js, Docker, Qt или ML-моделей.
