from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from photo_curator.photos.provider import PhotoAsset


@dataclass(frozen=True, slots=True)
class SourceRender:
    path: Path | None
    kind: str
    warning: str | None = None


def resolve_source_render(asset: PhotoAsset) -> SourceRender:
    if asset.has_adjustments and _valid(asset.edited_path):
        return SourceRender(asset.edited_path, "edited")
    derivatives = [path for path in asset.derivative_paths if _valid(path)]
    if asset.has_adjustments and derivatives:
        return SourceRender(max(derivatives, key=lambda path: path.stat().st_size), "derivative")
    if not asset.has_adjustments and _valid(asset.source_path):
        return SourceRender(asset.source_path, "original")
    if derivatives:
        warning = "edited_render_missing" if asset.has_adjustments else None
        return SourceRender(
            max(derivatives, key=lambda path: path.stat().st_size),
            "derivative",
            warning,
        )
    if _valid(asset.source_path):
        return SourceRender(asset.source_path, "original", "edited_render_missing")
    return SourceRender(None, "missing", "missing_preview")


def _valid(path: Path | None) -> bool:
    return bool(path and path.is_file())
