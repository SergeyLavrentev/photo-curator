# Photo Curator

Photo Curator is a native macOS app that helps turn a large Apple Photos album into a
smaller, personal selection. It looks for weak frames, near-duplicate bursts, technical
problems, composition, and your own visual preferences while keeping you in control of
every final decision.

The original library is never edited or deleted. Photo Curator renders review-sized JPEGs
into private service-owned project storage, proposes a selection, shows a dry run, and only
creates a new Photos album after explicit confirmation. Purgeable PhotoKit cache can be
regenerated without leaving the gallery or Quality Lab permanently blank.

> **Private preview status:** this build is for non-commercial use without redistribution.
> Treat recommendations as review assistance until the strict gates pass on diverse,
> human-labelled real-world albums.

## Download

Download the latest Apple Silicon build from
[GitHub Releases](https://github.com/SergeyLavrentev/photo-curator/releases/latest).

Requirements:

- Apple Silicon Mac (`arm64`)
- macOS 13 or later
- access to the Apple Photos library
- ChatGPT or Codex signed in on the Mac only if you choose **Codex Vision**

The current preview DMG is ad-hoc signed and is **not notarized**. On first launch, macOS may
require Control-clicking **Photo Curator.app**, choosing **Open**, and confirming once. Drag
the app from the DMG to `Applications` before launching it.

## What it does

1. Select a regular or shared Apple Photos album.
2. Choose an analysis mode and start the run.
3. Review Pick, Alternatives, Review, and Reject in Grid, Loupe, Compare, or Survey.
4. Use ratings 1–5, `P/U/X`, undo and batch actions; teach the versioned local selection ranker.
5. Preview the exact immutable Pick set and explicitly create a Best album.

The analysis pipeline records:

- independently switchable Apple Vision, NIMA, MobileCLIP S0, and MUSIQ signals;
- local aesthetic, semantic/genre, and multi-scale image-quality signals through Core ML;
- technical quality signals such as blur, exposure, resolution, and horizon;
- exact, burst, and scene grouping with crop/exposure and Vision-feature confirmation;
- diversity protection so one scene does not dominate the result;
- a versioned local Personal Taste/selection ranker trained only from explicit training choices;
- conservative safety rules that protect favourites, edits, originals, and uncertain cases.

## Analysis modes

### Local analysis (default)

The standard mode runs on the Mac. Apple Vision is the production ranking baseline. NIMA and
MobileCLIP remain visible advisory signals until an album-separated held-out evaluation validates
their normalized ensemble weights; MUSIQ contributes only a bounded technical-quality penalty.
No photo review copies are sent to an external AI service.

### Codex Vision (explicit opt-in)

Codex Vision is an experimental, separately selected mode for semantic and visual review.
It can reason about subject visibility, expression, composition, awkward framing, boring
shots, and which image is strongest within a very similar series.

Before a run, the app warns that review copies will be sent to OpenAI and that the run will
consume ChatGPT/Codex subscription limits. It automatically looks for Codex in the signed-in
ChatGPT or Codex app and in `PATH`. No API key is requested or accepted by this mode.

Codex Vision is optional. If it is unavailable or not selected, Photo Curator remains a
fully local application.
Codex scores use an anchored 0–100 contract and scale-collapse guard. They remain advisory for
ranking until `validated_codex_ranking` has explicit human-labelled evidence; this prevents an
experimental model or route-specific scale from replacing the Apple baseline silently.

## Privacy and safety

- Local analysis is the default; Codex Vision requires an explicit choice and warning.
- Every local engine can be enabled or disabled in Settings and is enabled by default.
- The bundled NIMA/MUSIQ and MobileCLIP artifacts are restricted to this private,
  non-commercial build; review their licenses before publication or commercial use.
- The app has no telemetry and does not run a local web server.
- Photos are accessed through public PhotoKit APIs, never by writing to `Photos.sqlite`.
- Originals, edits, favourites, keywords, and existing albums are not modified.
- There is no photo deletion API or automatic deletion workflow.
- Publishing shows the exact UUID-backed Pick set and is separated into dry-run and apply.
- Local review renders and project data can be removed without touching the Photos library.
- Secrets, authentication tokens, and API keys are not stored in this repository.

## Architecture

The SwiftUI application launches a bundled Python coordinator as a child process. They
communicate through versioned JSONL messages over stdin/stdout—there is no browser, HTTP
server, or localhost service in the native workflow.

The packaged app is self-contained and does not require the repository, Python, `uv`, or a
virtual environment at runtime. PhotoKit helper processes handle album discovery, review
renders, and creating the approved destination album. Videos are skipped.

More detailed product and engineering documents are available in:

- [`ROADMAP.md`](ROADMAP.md)
- [`CODEX_PROJECT_SPEC.md`](CODEX_PROJECT_SPEC.md)
- [`docs/`](docs/)
- [`decisions/`](decisions/)

Some internal design documents are currently in Russian.

## Build from source

Development requirements:

- Apple Silicon Mac with macOS 13+
- Xcode Command Line Tools
- Python 3.12
- [`uv`](https://docs.astral.sh/uv/)

```bash
uv sync
make app
```

The build produces:

```text
build/macos/PhotoCurator.app
build/macos/PhotoCurator.dmg
```

Useful commands:

```bash
make test          # Python test suite
make lint          # Ruff formatting and lint checks
make verify-app    # bundle, signature, entitlement, and runtime audit
make verify-dmg    # mounted DMG verification
make install       # build, install to /Applications, and launch
make stop          # stop the installed application and its backend
make uninstall     # move the installed app to Trash
```

By default, `make app` uses an ad-hoc signature and does not read or modify the login
Keychain. A release operator can explicitly provide a Developer ID identity:

```bash
make app SIGN_IDENTITY="Developer ID Application: ..."
```

Notarization is fail-closed and requires explicit App Store Connect credentials supplied at
build time. Private key files (`*.p8`) and environment files are ignored by Git.

## Development checks

```bash
uv run pytest
uv run pytest --cov=photo_curator
uv run ruff format --check .
uv run ruff check .
```

A synthetic native demo exercises the workflow without Photos access:

```bash
PHOTO_CURATOR_NATIVE_DEMO=1 make run
```

The repository also contains reproducible benchmark and human-labelled acceptance tooling.
See the architecture and pipeline documents for native Apple Vision, optional Core ML,
gallery, release-hot-path, and held-out Swipe Score evaluation commands.
Settings → **Проверка качества** opens a dedicated Quality Lab wizard. It guides the user through
a deterministic blind 50–100-photo sample, explicit defect labels, purpose-separated A/B
comparisons, ordered Top-K, one human series/leader, completeness checks and export. Automatic
recommendations are hidden during blind labelling and are never copied into the human truth set.

Quality Lab stores accepted human evidence in a durable local corpus that is independent of an
analysis project. Each album context is locked either to training or to held-out evaluation, so a
single context cannot leak into both. Training rounds update only a small versioned local ranker
over immutable Apple Vision/Core ML feature snapshots; held-out rounds never participate in fit,
and foundation/Core ML models are not retrained. Deleting an analysis preserves the corpus and
active ranker. Restarting a round creates a new attempt; deleting all accumulated learning is a
separate confirmed action with a verified database backup.

The local-model performance gate compares bounded work batches and concurrency while checking
score parity, peak RSS and thermal state. It intentionally exits non-zero until retained energy
evidence is supplied:

```bash
uv run photo-curator local-model-performance-benchmark \
  --asset-dir <preview-directory> --benchmark-modes 1x1,16x1,16x2 \
  --iterations 2 --output local-model-performance.json
```

Export the immutable current, non-personalized and Apple-only score snapshots together with
their provenance and the human-label manifest:

```bash
uv run photo-curator acceptance-evidence-export --project-id <id> --output evidence.json
uv run photo-curator learning-corpus-export --output learning-corpus.json
uv run photo-curator learning-corpus-import --input learning-corpus.json
```

## Known limitations

- The downloadable preview is Apple Silicon only and not notarized.
- Codex Vision depends on a working local ChatGPT/Codex installation and subscription limits.
- AI and heuristic recommendations can be wrong; review is mandatory.
- A broad real-photo, human-labelled acceptance corpus is still required before claiming
  production-level selection quality.

## License and model-use boundary

No open-source license has been selected. This installation is private and non-commercial.
Bundled third-party model weights must not be redistributed or reused commercially without a
separate license review.
