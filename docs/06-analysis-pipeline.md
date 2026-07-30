# Swipe Score analysis pipeline

> Production Apple Vision выполняется только отдельным Swift helper. Старый PyObjC
> compatibility path не запускается coordinator-ом: framework hang не должен блокировать
> проект. Его прямой диагностический вызов изолирован в subprocess с жёстким timeout.

## Product pipeline

```text
source inventory
  → safe render + immutable fingerprint
  → exact duplicate protection
  → Apple Vision native signals
  → optional Core ML / Apple Photos enrichment
  → semantic scene and series grouping
  → generic Swipe Score
  → best-in-series comparison
  → Personal Taste adjustment
  → album-relative selection cutoff
  → diverse Good / Bad buckets
  → approved publish plan
```

Each stage is independently resumable and records processed/total, warnings, failures,
versions and current message. A failed signal degrades confidence; it must not silently turn
an asset into Bad.

## Signal layers

### Integrity and technical protection

- render/source fingerprint, normalized pixel hash, dHash and pHash;
- resolution, orientation, missing state and source drift;
- sharpness, clipping, exposure and contrast defects.

These signals protect correctness and break close ties. They do not define aesthetic appeal.

### Apple-native generic appeal

- `VNCalculateImageAestheticsScoresRequest`;
- Vision feature prints;
- attention/objectness saliency;
- face count, landmarks and face capture quality when relevant;
- optional detailed Apple Photos scores for composition, lighting, framing, subject, colour
  and timing, with explicit missing/zero semantics.

### Validated semantic enrichment

MobileCLIP or another aesthetics model may add content/semantic appeal only after a licensed
Core ML build beats the Vision-only baseline on held-out data within runtime budgets.
Approved Core ML inference sends all cache misses through one model-loading process and stores
only successful raw outputs under a key derived from the registered model SHA-256 and immutable
review-render fingerprint. A changed model, changed render, corrupt entry or prior error is
always a miss; unapproved models never enter the production inference path.

## Scenes, series and duplicates

Exact/render-equivalent copies are handled deterministically. Near frames are candidates only
after bounded temporal, hash and feature-print retrieval. The engine groups coherent scenes,
then compares members relatively to choose a leader. Global Top K is computed after this
step, so twenty near-identical sunset frames cannot dominate the result.

## Swipe Score record

```json
{
  "schema_version": 1,
  "score": 84.2,
  "generic_score": 78.0,
  "personal_delta": 6.2,
  "confidence": 0.81,
  "components": {
    "generic_aesthetics": 0.82,
    "content_appeal": 0.76,
    "composition_and_attention": 0.88,
    "moment_and_subject": 0.74,
    "best_in_series": 0.91,
    "diversity_value": 0.69,
    "technical_penalty": -0.05
  },
  "reasons": ["Сильная композиция", "Лучший кадр серии", "Близко к вашему вкусу"],
  "model_versions": {}
}
```

The score ranks this corpus; it is not a probability or a universal beauty judgement.
Missing components are not treated as zero. Weights and calibration are versioned.

## Binary selection model

The engine always produces two user-facing categories: Good and Bad. Compact, balanced and
broad target approximately 25%, 45% and 65% of the current album, while retaining an absolute
quality floor for each mode. This makes the control describe the future Best album instead of
applying one weak universal cutoff to every corpus.

Decision confidence is calibrated from the distance to that album's cutoff and the reliability
of the available signals. It is not copied from the Swipe Score confidence. Explanations are
category-specific: Good exposes only positive evidence, while Bad exposes only defects,
weaker-duplicate evidence, taste mismatch or the fact that the frame lost to stronger photos in
the same album. Internal score records remain stored for diagnostics but are hidden from the
user-facing explanation.

## Personal Taste Profile

The initial profile is a lightweight local pairwise ranker over stable native/Core ML features.
Onboarding collects three Top-3-of-10 rounds. Each chosen photo is paired against every unchosen
photo in its round, producing 54 calibration and 9 held-out comparisons. The source album may
change between rounds, and the sampled photos are not modified. Later training data can also come
from explicit corrections. Safety and integrity rules are never training targets.

The profile stores feature schema, training examples, model parameters, quality evidence and
updated time. It supports pause, reset, export and deletion. Ranking explanations show the
generic score and personal delta separately.

## Evaluation contract

Use a user-labelled 50–100 photo corpus split into train/calibration and held-out evaluation.
Report at least:

- pairwise preference accuracy;
- best-in-series accuracy;
- Top-K agreement and diversity;
- false exclusion rate;
- generic versus personalized uplift;
- per-stage cold/warm latency, throughput, peak memory and energy.

The full Montenegro album is a scale test only after the bounded quality gate passes.
