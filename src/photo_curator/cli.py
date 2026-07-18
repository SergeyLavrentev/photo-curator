from __future__ import annotations

import argparse
import platform
import socket
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn

from photo_curator import __version__
from photo_curator.app import create_app
from photo_curator.logging_setup import configure_logging
from photo_curator.paths import default_application_paths
from photo_curator.web.security import SessionSecrets


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="photo-curator",
        description="Безопасный локальный помощник для ревью Apple Photos",
    )
    parser.add_argument("command", nargs="?", choices=["doctor", "version"])
    parser.add_argument("--demo", action="store_true", help="Запустить synthetic demo")
    parser.add_argument("--no-browser", action="store_true", help="Не открывать браузер")
    parser.add_argument("--port", type=valid_port, default=0, help="Loopback port; 0 — выбрать")
    return parser


def valid_port(value: str) -> int:
    port = int(value)
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError("port должен быть от 0 до 65535")
    return port


def choose_port(requested_port: int) -> int:
    if requested_port:
        return requested_port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def print_doctor() -> int:
    paths = default_application_paths()
    paths.ensure()
    checks = [
        ("Python", platform.python_version()),
        ("macOS", platform.mac_ver()[0] or "not macOS"),
        ("Architecture", platform.machine()),
        ("Database", str(paths.database)),
        ("Cache", str(paths.cache_dir)),
        ("sips", "OK" if Path("/usr/bin/sips").is_file() else "ERROR"),
    ]
    for label, value in checks:
        print(f"{label}: {value}")
    return 0


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command == "version":
        print(__version__)
        return
    if args.command == "doctor":
        raise SystemExit(print_doctor())

    paths = default_application_paths()
    paths.ensure()
    configure_logging(paths.log_file)
    port = choose_port(args.port)
    secrets_ = SessionSecrets.generate()
    app = create_app(demo=args.demo, paths=paths, session_secrets=secrets_)
    url = f"http://127.0.0.1:{port}/?token={secrets_.startup_token}"
    print(f"Photo Curator запущен: {url}", flush=True)
    if not args.no_browser:
        threading.Timer(0.7, webbrowser.open, args=(url,)).start()
    uvicorn.run(app, host="127.0.0.1", port=port, workers=1, log_level="info")


if __name__ == "__main__":
    main(sys.argv[1:])
