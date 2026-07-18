# Requirements Traceability

| Requirement | Implementation area | Validation |
|---|---|---|
| Regular albums only | `photos/provider.py`, album UI | Provider tests + manual browser test |
| Shared intake is manual | UI warning, intake docs | UX acceptance |
| No automatic deletion | Publisher boundary | Code review + no delete API tests |
| No direct Photos DB writes | `photos/` contract | Static review, integration gate |
| Visual pipeline | dashboard/Jinja/JS | Demo acceptance |
| Resume | jobs/repository/coordinator | `test_pipeline_resume.py` |
| Preview cache | render resolver | orientation/cache tests |
| Lightweight analysis | Pillow/NumPy modules | metrics/hash tests |
| Duplicate grouping | similarity/union-find | duplicate fixtures |
| Protect high-res original | leader/validation | shared-like copy fixture |
| Manual override persistence | decisions/repository | restart/reanalysis test |
| Dry-run before apply | publisher state machine | publisher mocks |
| Unique Reject album | publisher naming | unit test |
| Local web security | `web/security.py` | API/security tests |
| Safe cache cleanup | `safe_paths.py` | traversal/symlink tests |
| No heavy dependencies | `pyproject.toml` | dependency review |

## Milestone mapping

- M0: UI shell/security/demo.
- M1: source/provider/doctor.
- M2: inventory/state.
- M3: previews/cache.
- M4: metrics.
- M5: duplicates/resolution protection.
- M6: decisions/review.
- M7: publish safety.
- M8: hardening/documentation.
