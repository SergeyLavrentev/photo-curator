# Milestones and Definition of Done V2

[`ROADMAP.md`](../ROADMAP.md) is authoritative. S0–S8 replace the former implementation
milestones as the active product plan.

| Stage | Outcome | Acceptance evidence |
|---|---|---|
| S0 | Truthful baseline and preference dataset | 50–100 labelled photos, held-out split, reproducible report |
| S1 | Apple-native intelligence spike | Vision compatibility and quality/performance benchmark |
| S2 | Swipe Score v1 | uplift over technical-first baseline without more false exclusions |
| S3 | Semantic/Core ML enrichment | declared uplift plus CPU/GPU/ANE, memory and energy evidence |
| S4 | Personal Taste Profile | held-out per-user uplift, reset/export/delete verified |
| S5 | Native macOS workflow | Choose → Analyze → Review → Save without browser/localhost |
| S6 | Native Photos integration | capability-tested PhotoKit source and safe publish paths |
| S7 | Personal review quality | understandable, diverse Top K with exact approval preview |
| S8 | Native release gate | signed build, large-album benchmark, recovery and publish acceptance |

## Definition of Done

V2 is complete only when all are true:

1. Generic Swipe Score materially beats the frozen current heuristic on held-out data.
2. Personal Taste improves held-out agreement for the same user and can be fully reset.
3. Best-in-series and final diversity meet predeclared quality thresholds.
4. Native Vision/Core ML stages publish their versions, capabilities and confidence.
5. Hardware claims are backed by Apple Silicon measurements, not runtime assumptions.
6. A 5 000-photo inventory and 2 000-photo analysis satisfy declared responsiveness,
   memory, cancellation and resume budgets.
7. The complete user workflow runs in signed SwiftUI/AppKit without browser or localhost.
8. Source selection and final apply use supported PhotoKit paths where capability permits.
9. Shared snapshots are explicit service-owned renders and cleaned safely when unreferenced.
10. Missing, failed, ambiguous, edited, favourite and higher-resolution assets preserve their
    protections independently of personal taste.
11. Publish performs immutable dry-run, source revalidation, explicit approval and audit.
12. No automatic deletion, direct Photos database write, cloud call or telemetry exists.
13. Tests, static checks, labelled evaluation and real disposable Photos acceptance pass.

## Historical implementation

Former M0–M8 and R0–R6 produced the working Python/web baseline, duplicate protections,
Shared snapshots and safe publisher. That evidence remains useful, but it does not satisfy
Swipe Score, personalization, native GUI or native hardware acceptance.
