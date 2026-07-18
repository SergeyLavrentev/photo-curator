from __future__ import annotations

import secrets
from dataclasses import dataclass

from fastapi import Request

SESSION_COOKIE = "photo_curator_session"
CSRF_COOKIE = "photo_curator_csrf"


@dataclass(frozen=True, slots=True)
class SessionSecrets:
    startup_token: str
    session_token: str
    csrf_token: str

    @classmethod
    def generate(cls) -> SessionSecrets:
        return cls(
            startup_token=secrets.token_urlsafe(32),
            session_token=secrets.token_urlsafe(32),
            csrf_token=secrets.token_urlsafe(32),
        )


def has_valid_session(request: Request, secrets_: SessionSecrets) -> bool:
    candidate = request.cookies.get(SESSION_COOKIE, "")
    return secrets.compare_digest(candidate, secrets_.session_token)
