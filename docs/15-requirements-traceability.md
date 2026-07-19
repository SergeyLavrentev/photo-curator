# Requirements Traceability

| Requirement | Implementation area | Validation |
|---|---|---|
| Regular albums only | `photos/provider.py`, album UI | Provider tests + manual browser test |
| Shared intake is guided | source capability UI and intake docs | provider/UI tests |
| No automatic deletion | Publisher boundary | Code review + no delete API tests |
| No direct Photos DB writes | `photos/` contract | Static review, integration gate |
| Visual pipeline | dashboard/Jinja/JS | Demo acceptance |
| Truthful compact pipeline | server stage view + dashboard polling | aggregated stale/running job regression test |
| Resume and visible retry | jobs/coordinator + dashboard actions | interrupted project UI/API test |
| Preview cache | render resolver | orientation/cache tests |
| Missing preview retry | preview resume endpoint + dashboard action | missing asset UI test |
| Cache telemetry | project summary + compact safety row | dashboard UI test |
| Lightweight analysis | Pillow/NumPy modules | metrics/hash tests |
| Duplicate grouping | similarity/union-find | duplicate fixtures |
| Protect high-res original | leader/validation | shared-like copy fixture |
| Manual override persistence | decisions/repository | restart/reanalysis test |
| Dry-run before apply | publisher state machine | publisher mocks |
| Approved disk Best to Photos | native PhotoKit importer | compile check + integration fake |
| Unique Best/Reject albums | publisher kind and naming | publisher tests |
| Score 0–100 and reasons | decision engine and gallery | decision/UI tests |
| Bounded candidate generation | hash bands, time windows, valid bursts | scale tests |
| Resolution warning gallery | real resolution flags + filter | category regression test |
| Human-labelled release metrics | `acceptance.py`, CLI manifest | evaluator unit + DB integration tests |
| Manual Reject deletion semantics | conditional publish instructions | Best/Reject UI test |
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
- R7: external human-labelled album and exact responsive visual evidence.
