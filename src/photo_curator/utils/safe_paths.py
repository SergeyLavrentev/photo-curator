from __future__ import annotations

import shutil
from pathlib import Path


def ensure_within(path: Path, root: Path) -> Path:
    resolved = path.expanduser().resolve()
    resolved_root = root.expanduser().resolve()
    if resolved == resolved_root or not resolved.is_relative_to(resolved_root):
        raise ValueError(f"Path is outside the allowed root: {resolved}")
    return resolved


def safe_rmtree(path: Path, root: Path) -> None:
    resolved = ensure_within(path, root)
    if resolved.is_symlink():
        raise ValueError("Refusing to recursively delete a symlink")
    if resolved.exists():
        shutil.rmtree(resolved)
