from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 0
    open_browser: bool = True


@dataclass(frozen=True, slots=True)
class AppConfig:
    server: ServerConfig = ServerConfig()
    demo: bool = False
