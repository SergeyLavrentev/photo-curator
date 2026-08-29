# Photo Curator — Swipe Score roadmap

## Product promise

Photo Curator turns a large Apple Photos album into a compact selection of images
that create the strongest first impression for this user.

> Из большого альбома — фотографии, которые хочется свайпнуть вправо.

`Swipe Score` is a universal metaphor, not a dating-only score. It applies to a
portrait, landscape, boat at sunset, street scene, family moment or any other visual
subject. The product evaluates the photograph and its presentation, not the worth or
beauty of a person depicted in it.

The primary outcome is:

1. a personalized ranked gallery;
2. the best frame from every coherent scene or series;
3. a diverse final selection;
4. an explanation of why each image is recommended;
5. a user-confirmed Photos album.

Technical quality is a safety signal and tie-breaker. Sharpness, exposure and contrast
must not dominate aesthetic appeal, meaning, timing or personal taste.

## Product principles

- **First impression first.** Rank visual appeal and emotional pull before technical
  perfection.
- **Personal, not universal.** A generic aesthetic baseline starts the ranking; user
  comparisons and corrections adapt it over time.
- **Relative beats absolute.** Prefer `A or B?` and best-in-series ranking over an
  unsupported claim that an image is objectively beautiful.
- **Apple-native intelligence.** Vision and Core ML are the production baseline;
  compatible models use CPU, GPU and Neural Engine through Core ML.
- **Open-source where it adds value.** `osxphotos`, MobileCLIP and candidate aesthetic
  models are benchmarked, licensed and replaceable adapters rather than hidden truths.
- **Explainable recommendations.** Show a short reason and uncertainty without exposing
  a wall of metrics.
- **Local and reversible.** No automatic deletion or source mutation. Publishing remains
  a dry-run plus explicit approval.

## Target architecture

```text
PhotoCurator.app
  -> SwiftUI/AppKit workflow and review gallery
  -> PhotoKit source and publishing
  -> ProjectStore (single local owner)
  -> AnalysisCoordinator
       -> Apple Vision engine
            aesthetics / feature print / saliency / faces
       -> Core ML engine
            optional MobileCLIP and validated aesthetic models
       -> Python enrichment worker
            osxphotos and temporary experimental algorithms
  -> Personal Taste Profile
```

The released GUI must not require a browser, HTML, JavaScript, FastAPI or a localhost
port. During migration, the existing web application remains a reference implementation
and diagnostic fallback until native feature parity is proven.

Python is allowed behind a narrow local IPC contract. It must not own GUI state and must
be removable without redesigning the application. Production ML inference should move to
Vision/Core ML when the native implementation reaches measured parity.

## Swipe Score contract

Every analyzed image receives a versioned score record:

```text
Swipe Score 0...100
  generic_aesthetics
  content_appeal
  composition_and_attention
  moment_and_subject
  portrait_signal, when relevant
  best_in_series
  personal_taste
  diversity_value
  technical_penalty
  confidence
```

The score is a ranking instrument, not a calibrated probability that every viewer will
like the image. The UI must distinguish generic baseline, personalized adjustment and
technical blockers.

Initial signals:

- `VNCalculateImageAestheticsScoresRequest`;
- Apple Photos scores exposed by `osxphotos`, including composition, lighting, framing,
  subject, colour, timing and overall curation;
- Vision feature prints for semantic similarity and scene/series comparison;
- attention/objectness saliency;
- face capture quality and landmarks when a person is present;
- exact/render hashes for deterministic duplicate protection;
- technical defects only as blockers, penalties and tie-breakers;
- user pairwise choices and manual review corrections.

Future context profiles may include General, Dating Profile, Travel, Portfolio and Family,
but V2 ships one general personal profile before adding specialised claims.

## S0 — truthful baseline and preference dataset

- Preserve the current Python engine as a measurable baseline, not the target product.
- Add human labels for pairwise preference, best-in-series and final Top K, separate from
  duplicate correctness labels.
- Define a reproducible evaluation report: pairwise accuracy, Top-K agreement, series
  leader accuracy, diversity, false exclusion rate, latency and memory.
- Record current heuristic, Apple overall-only and non-personalized baselines.
- Never use the full Montenegro album as a release test before the bounded labelled set
  passes.

Acceptance: a user-labelled 50–100 photo corpus and held-out comparisons can rank two
engine versions without subjective hand-waving.

## S1 — Apple-native intelligence spike

- Build a Swift command-line benchmark independent of the GUI.
- Run Vision aesthetics, feature prints, saliency and face requests on the same corpus.
- Persist model/request revision and unavailable-capability reasons.
- Measure cold/warm latency, throughput, peak memory and energy on the target Mac.
- Compare public Vision aesthetics with detailed stored Apple Photos scores.
- Establish the per-stage runtime budget from measured evidence.

Acceptance: every native signal has a compatibility result, benchmark and observable
contribution to held-out ranking quality.

## S2 — Swipe Score v1

- Replace the technical-first weighted sum with the versioned Swipe Score contract.
- Make Apple Vision aesthetics the mandatory generic baseline on supported macOS.
- Use detailed Apple Photos scores as optional enrichment with missing/zero handling.
- Rank coherent scenes pairwise and select the best frame before global Top K.
- Preserve technical and resolution protections independently from aesthetic taste.
- Store concise reasons and confidence for every recommendation.

Acceptance: Swipe Score v1 outperforms the current heuristic on held-out pairwise and
Top-K evaluation without increasing false exclusions.

## S3 — semantic appeal and hardware acceleration

- This is a private, non-commercial installation. NIMA, MUSIQ and Apple's research-only
  MobileCLIP weights may be enabled for that declared use after their manifests and content
  digests are verified. They must not be redistributed or silently promoted to a public or
  commercial build without a new license review.
- Convert the winning model to Core ML and use `.all` compute units where compatible.
- Cache embeddings by immutable render fingerprint.
- Batch inference and prevent duplicate image decoding across requests.
- Measure CPU/GPU/Neural Engine execution, thermal behaviour and battery impact.
- Keep model download/size optional until its measured product uplift justifies it.

Acceptance: any bundled model provides a predeclared ranking uplift over Vision alone and
stays within the S1 runtime, memory and energy budgets.

## S4 — Personal Taste Profile

- Add a short calibration flow based on pairwise image choices, not abstract sliders only.
- Let the user adjust selection breadth and visible preference dimensions before final
  publishing.
- Learn a lightweight local ranking head over stable image features.
- Treat manual Keep/Review/Exclude and leader changes as preference signals only after an
  explicit product choice.
- Persist the profile with feature schema, model version and training evidence.
- Show generic score versus personal adjustment.
- Allow pause, reset, export and complete deletion of the taste profile.
- Keep safety, duplicate and resolution rules outside the learned profile.

Acceptance: the personal model improves held-out pairwise agreement for the same user and
never changes protected assets or Photos content by itself.

## S5 — Native macOS workflow

- Replace the user-facing web GUI with SwiftUI/AppKit.
- Use a native album picker, four-step workflow, progress, review gallery and settings.
- Use PhotoKit and native thumbnail caching where capability parity is proven.
- Support keyboard navigation, Quick Look, undo, accessibility and state restoration.
- Keep one state owner; a Python worker communicates through versioned JSONL IPC and does
  not write UI/project state independently.
- Keep AppKit `NSCollectionView` as a measured fallback if SwiftUI grids cannot sustain
  large albums.

Acceptance: Choose → Analyze → Review → Save works without a browser or localhost and
remains responsive on a 2,000-photo project.

## S6 — Native Photos integration

- Inventory regular Photos albums through PhotoKit where its metadata is sufficient.
- Keep `osxphotos` as an optional read-only enrichment adapter for scores or sources that
  public APIs do not expose reliably.
- Revalidate regular albums, Shared Albums, edited assets, Live Photos and iCloud-only
  resources through explicit capability tests.
- Publish only the approved result through PhotoKit.
- Preserve disk snapshots only when Shared Album access requires them; remove them with
  the last referencing project.
- Continue to skip video analysis in V2.

Acceptance: the native workflow covers supported source and publish paths without opening
Photos or writing its database directly.

## S7 — personal review and portfolio selection

- Make Swipe Score the default ordering and strongest visible decision aid.
- Present explanations behind a compact disclosure rather than technical card chrome.
- Optimise the final set for both score and visual diversity; prevent one scene or subject
  from dominating.
- Let the user tune the result and immediately preview how the personal profile changes
  the ranking.
- Reserve specialised context profiles without claiming a dating-specific predictor until
  matching labelled evidence exists.

Acceptance: the user can produce a useful, diverse final selection with only local edits,
understand the largest score changes and approve the exact Photos output.

## S8 — native release gate

- Run labelled generic, scene/series and personal-preference evaluations.
- Benchmark 100, 2,000 and 5,000-photo inventories on supported Apple Silicon hardware.
- Verify restart, cancellation, resume, model invalidation and taste-profile reset.
- Verify code signing, notarization plan, permissions and bundle contents.
- [x] Remove FastAPI/Jinja/JavaScript and the browser launcher after native parity.
- Remove bundled Python only when native engines cover all validated product signals; its
  removal is not required for the first native release.

Acceptance: the signed native application proves ranking uplift, personalised uplift,
large-album responsiveness and safe PhotoKit publishing with reproducible evidence.

## Definition of Done

V2 is done only when a user can choose a supported album, receive a materially better
than technical-baseline Swipe Score ranking, tune and retain personal taste, review a
diverse native gallery and explicitly publish the approved result — without a browser,
localhost or automatic source mutation.

## Historical baseline

The previous R0–R6 implementation remains valuable evidence for inventory, previews,
duplicates, resumability, Shared Album snapshots and safe publishing. It does not prove
Swipe Score quality, personal preference quality, Apple-native GUI parity or native ML
hardware utilisation. Previous R7 is superseded by S0–S8 above.
