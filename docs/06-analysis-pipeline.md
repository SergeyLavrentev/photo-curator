# Swipe Score analysis pipeline

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
  → diverse Top K
  → human review and approved publish plan
```

Each stage is independently resumable and records processed/total, warnings, failures,
versions and current message. A failed signal degrades confidence; it must not silently turn
an asset into Excluded.

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

## Personal Taste Profile

The initial profile is a lightweight local pairwise ranker over stable native/Core ML
features. Training data comes from explicit A/B choices and, only after consent, review
corrections. Safety and integrity rules are never training targets.

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
