from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ApplicationPaths:
    data_dir: Path
    cache_dir: Path
    log_dir: Path

    @property
    def database(self) -> Path:
        return self.data_dir / "photo-curator.sqlite3"

    @property
    def log_file(self) -> Path:
        return self.log_dir / "photo-curator.log"

    @property
    def project_artifacts_dir(self) -> Path:
        """Durable, service-owned project previews used by the UI and analysis."""
        return self.data_dir / "project-artifacts"

    def ensure(self) -> None:
        for directory in (
            self.data_dir,
            self.cache_dir,
            self.log_dir,
            self.project_artifacts_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)


def default_application_paths(home: Path | None = None) -> ApplicationPaths:
    home = (home or Path.home()).expanduser().resolve()
    return ApplicationPaths(
        data_dir=home / "Library" / "Application Support" / "PhotoCurator",
        cache_dir=home / "Library" / "Caches" / "PhotoCurator",
        log_dir=home / "Library" / "Logs" / "PhotoCurator",
    )
