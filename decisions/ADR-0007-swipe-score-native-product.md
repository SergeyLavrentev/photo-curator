# ADR-0007: Swipe Score and Apple-native product

- Status: Accepted
- Supersedes: ADR-0001 for the target GUI, ADR-0003 for the target analysis strategy
- Amends: ADR-0006; curated output remains primary, but ranking becomes preference-first

## Context

The current product ranks technical quality, exact/near duplicates and a small subset of
Apple signals. It does not yet answer the product question: which photographs create the
strongest first impression for this user?

The shipped macOS bundle still presents HTML/JavaScript through a browser and carries a
Python web server. The desired product is a native local macOS application that uses the
Apple hardware/software stack while retaining proven open-source analysis components.

## Decision

1. `Swipe Score` becomes the primary recommendation and ordering contract.
2. Swipe is a universal preference metaphor for all photographic subjects, not an
   objective rating of a person and not a dating-only claim.
3. Apple Vision aesthetics is the generic native baseline.
4. Vision feature prints, saliency, faces and available Apple Photos scores enrich the
   ranking; technical metrics become blockers, penalties and tie-breakers.
5. A local Personal Taste Profile learns from explicit pairwise choices and approved
   corrections, remains resettable and never overrides safety protections.
6. Core ML is the production runtime for validated custom models and may use CPU, GPU and
   Neural Engine through supported compute units.
7. The target GUI is SwiftUI/AppKit with PhotoKit integration and no browser/localhost.
8. Python may remain as a narrow stateless enrichment worker behind versioned local IPC.
9. Existing web and Python implementations are removed only after measured native parity.

## Consequences

Positive:

- product quality is measured against user preference rather than proxy metrics;
- native macOS UX and hardware acceleration become first-class;
- open-source models can be benchmarked without hard-wiring the product to one runtime;
- personal corrections create durable value across albums.

Costs and risks:

- preference labels and held-out evaluation become mandatory;
- PhotoKit and Shared Album capability differences require real-device tests;
- Swift and Python must not both own mutable project state;
- internal Apple Photos scores are optional, undocumented enrichment and need fallbacks;
- claims about context-specific appeal require matching labelled evidence.

## Safety invariants retained

- no automatic deletion;
- no direct Photos database writes;
- dry-run and explicit confirmation before publishing;
- missing, ambiguous and failed assets remain reviewable;
- personal taste never weakens duplicate, resolution or source-integrity protection.
