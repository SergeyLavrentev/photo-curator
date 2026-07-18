from pathlib import Path

import pytest

from photo_curator.utils.safe_paths import ensure_within, safe_rmtree


def test_ensure_within_rejects_root_and_traversal(tmp_path: Path) -> None:
    root = tmp_path / "cache"
    root.mkdir()
    assert ensure_within(root / "project", root) == root / "project"
    with pytest.raises(ValueError):
        ensure_within(root, root)
    with pytest.raises(ValueError):
        ensure_within(root / ".." / "outside", root)


def test_safe_rmtree_only_deletes_project_cache(tmp_path: Path) -> None:
    root = tmp_path / "cache"
    project = root / "project"
    project.mkdir(parents=True)
    (project / "preview.jpg").write_text("preview")
    safe_rmtree(project, root)
    assert not project.exists()


def test_safe_rmtree_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "cache"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    link = root / "linked-project"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        safe_rmtree(link, root)
    assert outside.exists()
