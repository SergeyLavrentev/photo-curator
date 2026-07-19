from __future__ import annotations

import importlib.metadata
import platform
import socket
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from photo_curator.analysis.vision import vision_available
from photo_curator.paths import ApplicationPaths
from photo_curator.photos.provider import PhotosProvider
from photo_curator.photos.render_resolver import resolve_source_render
from photo_curator.utils.subprocesses import find_executable


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    code: str
    label: str
    status: str
    detail: str

    def json(self) -> dict[str, str]:
        return asdict(self)


def run_doctor(provider: PhotosProvider | None, paths: ApplicationPaths) -> list[DoctorCheck]:
    checks = [
        DoctorCheck(
            "platform",
            "macOS",
            "OK" if sys.platform == "darwin" else "ERROR",
            platform.mac_ver()[0],
        ),
        DoctorCheck("architecture", "Архитектура", "OK", platform.machine()),
        DoctorCheck(
            "python",
            "Python",
            "OK" if sys.version_info >= (3, 12) else "ERROR",
            platform.python_version(),
        ),
        _command_check("sips", "System image converter", "/usr/bin/sips"),
        _command_check("osxphotos_cli", "osxphotos CLI", find_executable("osxphotos")),
        _directory_check("data_dir", "Application Support", paths.data_dir),
        _directory_check("cache_dir", "Cache", paths.cache_dir),
        _directory_check("log_dir", "Logs", paths.log_dir),
        _loopback_check(),
        _photos_process_check(),
        DoctorCheck(
            "apple_vision",
            "Apple Vision (лица и глаза)",
            "OK" if vision_available() else "WARNING",
            "локально доступен" if vision_available() else "недоступен; этап будет пропущен",
        ),
    ]
    try:
        version = importlib.metadata.version("osxphotos")
        checks.append(DoctorCheck("osxphotos_import", "osxphotos Python API", "OK", version))
    except importlib.metadata.PackageNotFoundError:
        checks.append(
            DoctorCheck("osxphotos_import", "osxphotos Python API", "ERROR", "не установлен")
        )
    if provider is not None:
        try:
            library = provider.get_current_library()
            albums = provider.list_regular_albums()
            shared = provider.list_shared_albums()
            checks.extend(
                [
                    DoctorCheck("library", "Photos Library", "OK", library.library_path),
                    DoctorCheck(
                        "albums", "Обычные альбомы", "OK" if albums else "WARNING", str(len(albums))
                    ),
                    DoctorCheck(
                        "shared_albums",
                        "Shared Albums",
                        "WARNING" if shared else "OK",
                        f"{len(shared)} доступны через отдельную дисковую копию Photo Curator",
                    ),
                ]
            )
            checks.extend(_read_gate_checks(provider, albums))
        except Exception as error:  # provider must turn environment failures into diagnostics
            checks.append(
                DoctorCheck("library", "Photos Library", "ERROR", _provider_error_detail(error))
            )
    return checks


def _command_check(code: str, label: str, executable: str | None) -> DoctorCheck:
    available = bool(executable and Path(executable).is_file())
    return DoctorCheck(code, label, "OK" if available else "ERROR", executable or "не найден")


def _directory_check(code: str, label: str, path: Path) -> DoctorCheck:
    try:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        probe = path / ".photo-curator-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return DoctorCheck(code, label, "OK", "доступен для записи")
    except OSError as error:
        return DoctorCheck(code, label, "ERROR", str(error))


def _loopback_check() -> DoctorCheck:
    try:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
        return DoctorCheck("loopback", "Loopback port", "OK", "свободный порт доступен")
    except OSError as error:
        return DoctorCheck("loopback", "Loopback port", "ERROR", str(error))


def _photos_process_check() -> DoctorCheck:
    result = subprocess.run(
        ["/usr/bin/pgrep", "-x", "Photos"],
        shell=False,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return DoctorCheck(
        "photos_app",
        "Photos.app",
        "WARNING" if result.returncode == 0 else "OK",
        "запущено; чтение может быть медленнее" if result.returncode == 0 else "не запущено",
    )


def _read_gate_checks(provider: PhotosProvider, albums: list) -> list[DoctorCheck]:
    selected = None
    assets = []
    for album in sorted(albums, key=lambda item: item.photo_count, reverse=True)[:10]:
        candidate = [asset for asset in provider.list_assets(album.id) if asset.is_photo]
        if len(candidate) >= 10:
            selected, assets = album, candidate
            break
    if not selected:
        return [
            DoctorCheck(
                "read_gate_assets",
                "Read gate: test album",
                "WARNING",
                "не найден обычный альбом минимум с 10 фото",
            )
        ]
    render_available = any(resolve_source_render(asset).path for asset in assets[:50])
    return [
        DoctorCheck(
            "read_gate_assets",
            "Read gate: metadata",
            "OK",
            f"{selected.full_path}: {len(assets)} photo assets с UUID",
        ),
        DoctorCheck(
            "read_gate_render",
            "Read gate: local render",
            "OK" if render_available else "ERROR",
            "доступен"
            if render_available
            else "ни одного локального render среди первых 50 assets",
        ),
    ]


def _provider_error_detail(error: Exception) -> str:
    detail = str(error)
    if (
        "Error copying" in detail
        or "not authorized" in detail
        or "Operation not permitted" in detail
    ):
        return (
            "macOS запретила чтение Photos Library. Разрешите доступ к Фото и "
            "Full Disk Access для Codex/Terminal, затем перезапустите приложение."
        )
    return detail
