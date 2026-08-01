# Photo Curator

Photo Curator is a native macOS app that helps turn a large Apple Photos album into a
smaller, personal selection. It looks for weak frames, near-duplicate bursts, technical
problems, composition, and your own visual preferences while keeping you in control of
every final decision.

The original library is never edited or deleted. Photo Curator renders review-sized JPEGs
into its private cache, proposes a selection, shows a dry run, and only creates a new Photos
album after explicit confirmation.

> **Preview status:** this is an early public build. Treat its recommendations as review
> assistance, not as an automatic verdict. Quality still needs validation on diverse,
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
3. Review ranked Keep, Review, and Reject suggestions.
4. Correct uncertain decisions and teach the local Personal Taste profile.
5. Inspect a dry run and explicitly create a new album containing the approved selection.

The scoring pipeline combines:

- Apple Vision aesthetics, saliency, face, and feature-print signals;
- technical quality signals such as blur, exposure, resolution, and horizon;
- within-series comparison for near-duplicate bursts;
- diversity protection so one scene does not dominate the result;
- a local Personal Taste model trained from explicit A/B choices;
- conservative safety rules that protect favourites, edits, originals, and uncertain cases.

## Analysis modes

### Local analysis (default)

The standard mode runs on the Mac. It uses Apple Vision and the local scoring pipeline; no
photo review copies are sent to an external AI service.

### Codex Vision (explicit opt-in)

Codex Vision is an experimental, separately selected mode for semantic and visual review.
It can reason about subject visibility, expression, composition, awkward framing, boring
shots, and which image is strongest within a very similar series.

Before a run, the app warns that review copies will be sent to OpenAI and that the run will
consume ChatGPT/Codex subscription limits. It automatically looks for Codex in the signed-in
ChatGPT or Codex app and in `PATH`. No API key is requested or accepted by this mode.

Codex Vision is optional. If it is unavailable or not selected, Photo Curator remains a
fully local application.

## Privacy and safety

- Local analysis is the default; Codex Vision requires an explicit choice and warning.
- The app has no telemetry and does not run a local web server.
- Photos are accessed through public PhotoKit APIs, never by writing to `Photos.sqlite`.
- Originals, edits, favourites, keywords, and existing albums are not modified.
- There is no photo deletion API or automatic deletion workflow.
- Publishing is separated into dry-run and confirmed apply steps.
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

## Known limitations

- The downloadable preview is Apple Silicon only and not notarized.
- Codex Vision depends on a working local ChatGPT/Codex installation and subscription limits.
- AI and heuristic recommendations can be wrong; review is mandatory.
- A broad real-photo, human-labelled acceptance corpus is still required before claiming
  production-level selection quality.

## License

No open-source license has been selected yet. The repository and downloadable preview are
public for evaluation, but no additional rights are granted by default.
