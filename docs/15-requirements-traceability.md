# Requirements Traceability V2

| Requirement | Target area | Validation |
|---|---|---|
| Generic Swipe Score | Vision engine + versioned scorer | held-out pairwise and Top-K report |
| Universal, non-dating framing | product copy and score contract | UX/content review |
| Apple Vision baseline | native analysis module | compatibility + contribution benchmark |
| Semantic enrichment | Core ML adapter | ablation uplift, license and bundle audit |
| Hardware acceleration | Core ML compute policy | Instruments/benchmark evidence on target Macs |
| Scene/series ranking | feature retrieval + pairwise ranker | labelled leader accuracy |
| Personal Taste Profile | local feature/ranking store | held-out per-user uplift + reset/export/delete tests |
| Generic/personal explainability | score record + Inspector | snapshot and usability tests |
| Diverse final Top K | diversity pass | diversity metric + labelled review |
| Native four-step workflow | SwiftUI/AppKit application | E2E Choose → Analyze → Review → Save |
| Large native gallery | SwiftUI grid / AppKit fallback | 2 000/5 000 item performance tests |
| Regular Photos source | PhotoKit adapter | real-library read acceptance |
| Shared source intake | optional service-owned snapshot | resumable copy and cleanup tests |
| Approved result to Photos | PhotoKit publisher | dry-run + disposable real apply |
| No automatic deletion | publisher boundary | API/static review and negative tests |
| No direct Photos DB writes | Photos adapters | static audit and integration gate |
| Source/resolution protection | integrity layer | drift, missing and inversion fixtures |
| Resume and invalidation | coordinator + ProjectStore | restart/model/source change tests |
| One mutable state owner | native store + versioned IPC | architecture and concurrency tests |
| Local-only privacy | app/network boundary | entitlement/network audit |
| Signed native release | build pipeline | verified macOS `.dmg` + codesign + notarization |

## Historical implementation mapping

The current `photo_curator/` web/Python implementation supplies the frozen baseline and
proven safety adapters. It is not traceability evidence for the native UI or new ranking
quality until the corresponding S-stage acceptance passes.
