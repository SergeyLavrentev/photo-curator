# Blind A/B: comparing frames of one scene

The earlier short taste session mixed temporal neighbours with random album-wide pairs.
The legacy taste selector even excluded members of the same automatic group. Both could
teach a preference for one subject or visited place over another.

All three pair selectors now use `analysis/blind_pairs.py`, policy `same-scene-v2`:

- Candidates must have ready image renders, compatible usable features and capture times
  within 120 seconds. At most 12 temporal neighbours per asset bound the candidate search.
- Every displayed pair requires direct Vision-feature similarity plus perceptual hashes,
  colour histogram, aspect ratio and available pixel evidence. A category, timestamp,
  burst ID or transitive membership alone cannot authorize a pair.
- Identical normalized renders are omitted. Missing/uncertain evidence produces fewer
  questions, possibly none; there is no unrelated-photo fallback.
- Scores, auto dispositions, favourites and predicted series leaders do not choose the
  questions. Short sessions balance coverage between connected scenes, but every actual
  question is a directly verified edge, never an inferred transitive edge.
- The short session hides scores and offers left/right, equal/skip and different-scene.
  Only left/right choices train the ranker. No choice changes gallery decisions or Photos.
- Old unversioned sessions cannot resume or accept an answer. Schema 31 retains identifiable
  answered legacy blind pairs and their feature evidence, but excludes their IDs from fitting
  and learning export. Affected coefficients are invalidated. This exclusion survives project
  deletion and reprojection. Unattributed historical Quality Lab/onboarding evidence is not
  silently reclassified: an old pair without session provenance cannot reliably be attributed
  to this sampler. No source snapshot is invented.

These rules conservatively narrow comparisons; they do not prove subject identity or location
for all albums. For example, indistinguishable subjects in nearly identical compositions can
still require “Разные сцены”. Similar scenes with missing timestamps or a large reframing may
be omitted. This change is not an aesthetic-model accuracy claim.

## Verification

Synthetic tests cover different locations with identical embeddings, temporal/category-only
false matches, exact copies, undated/distant frames, score/group independence, held-out
separation, stale sessions, skips, immutable feature drift, and migration/reprojection retention.

The current saved analysis of “Синий жук и бобр” was read through a read-only SQLite connection
and copied to memory. No answers were submitted. Runtime inspection scripts, exact pairs,
visual contact sheets and timings are in `build/evidence/blind-scenes-2026-09-05`.

The built `.app` and DMG passed verification. The native app opened the current album's
first same-scene question with both images and all answer controls; no answer was submitted.
The 12 selected pairs were all visually consistent with one scene in this inspection.

The initial full test run passed 309 cases and exposed two obsolete release-benchmark
fixtures. Benchmark schema 3 now supplies actual temporary frames and valid feature vectors
for its comparison path. Its two tests passed after the fixture correction. The final 49
scoped comparison/learning/worker regressions and lint also passed. The complete suite was
not repeated after this CLI-only benchmark change; that module is absent from the native
bundle's PyInstaller dependency list. Exact logs and boundaries are in `verification.json`.

The running app is the local `build/macos/PhotoCurator.app`; `/Applications` was not updated.
This is implementation and local runtime evidence, not human quality or release acceptance.
