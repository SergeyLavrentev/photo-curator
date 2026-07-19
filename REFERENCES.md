# Внешние ориентиры

Перед реализацией Codex должен сверять актуальную документацию используемых API и команд.

## Apple

- [Vision image aesthetics request](https://developer.apple.com/documentation/vision/vncalculateimageaestheticsscoresrequest).
- [Vision image feature print request](https://developer.apple.com/documentation/vision/vngenerateimagefeatureprintrequest).
- [Vision attention-based saliency request](https://developer.apple.com/documentation/vision/vngenerateattentionbasedsaliencyimagerequest).
- [Core ML compute units](https://developer.apple.com/documentation/coreml/mlcomputeunits).
- [Apple MobileCLIP](https://github.com/apple/ml-mobileclip) and
  [research note](https://machinelearning.apple.com/research/mobileclip).
- Apple Photos User Guide: albums, Shared Albums, import and delete semantics.
- macOS `sips` manual.

## osxphotos

- [Project documentation and CLI reference](https://github.com/RhetTbull/osxphotos).
- Python API documentation.
- `batch-edit`, `--uuid-from-file`, `--add-to-album`, `--dry-run` capability.

## Aesthetic and personal ranking research

- [NIMA: Neural Image Assessment](https://arxiv.org/abs/1709.05424) — candidate
  generic aesthetic baseline, not an automatic shipping dependency.
- [Personalized Image Aesthetics Assessment with Rich Attributes](https://arxiv.org/abs/2203.16754).
- [Personalized Image Aesthetics Assessment via Multi-attribute Interactive Reasoning](https://arxiv.org/abs/2106.07488).

Research and open-source models are hypotheses. Before bundling any model, record its exact
license, weights provenance, Core ML conversion, held-out uplift, bundle size, latency,
memory, energy and compute-unit behaviour.

## Правило актуальности

Не считать этот пакет доказательством поддержки конкретной версии macOS, Vision,
PhotoKit, Photos или `osxphotos`. Обязательны capability probes, versioned fallbacks,
read/publish gates и измерения на целевом Apple Silicon Mac.
