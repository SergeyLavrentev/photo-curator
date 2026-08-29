# Bundled local models

Photo Curator 0.2 bundles three optional Core ML models. Generated packages live in the
ignored `.model-cache/` directory and are copied into the signed app by `build_app.sh`.

| Engine | Source | Checkpoint | License used by this private build |
| --- | --- | --- | --- |
| NIMA | PyIQA `nima` | Inception-ResNet-v2, AVA | PolyForm Noncommercial 1.0.0 |
| MobileCLIP | `apple/coreml-mobileclip` | MobileCLIP-S0 image encoder | Apple sample/research license |
| MUSIQ | PyIQA `musiq` | KonIQ-10k | PolyForm Noncommercial 1.0.0 |

The NIMA and MUSIQ PyTorch checkpoints were converted to fixed-shape FP16 Core ML packages.
MUSIQ uses 337 fixed 32x32 patches: 49 at 224 px, 144 at 384 px, and 144 from an
aspect-fitted 384 px image. MobileCLIP uses Apple's published Core ML image encoder. Its
text encoder is only used during preparation to create the normalized prompt centroids in
`mobileclip-prompts.json`; the text model is not shipped.

These artifacts are deliberately not committed. Before a reproducible release, populate
`.model-cache/` with the four entries from `models.json` and verify the deterministic content
digests with this command from the repository root:

```bash
(cd .model-cache && find <name> -type f -print0 | LC_ALL=C sort -z \
  | xargs -0 shasum -a 256 | shasum -a 256)
```

This model stack must not be redistributed or used commercially without a fresh license
review. The application UI permits every engine to be disabled independently.
