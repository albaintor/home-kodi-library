"""Bearer clients, private browser sessions and a distinct trusted ingress credential."""

import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer = HTTPBearer(auto_error=False, scheme_name="BearerToken")
COOKIE = "kodi_library_session"


def secure_equal(left: str, right: str) -> bool:
    return secrets.compare_digest(left.encode(), right.encode())


@dataclass
class Principal:
    role: str
    csrf: str | None = None
    via: str = "bearer"


class Auth:
    def __init__(self, settings):
        self.settings = settings
        self.sessions: dict[str, tuple[float, Principal]] = {}
        self.attempts: dict[str, deque[float]] = defaultdict(deque)

    def token_role(self, token: str) -> str | None:
        if secure_equal(token, self.settings.api_token):
            return "admin"
        if self.settings.read_token and secure_equal(token, self.settings.read_token):
            return "read"
        return None

    def login(self, token: str, address: str) -> tuple[str, Principal]:
        current = time.monotonic()
        attempts = self.attempts[address]
        while attempts and attempts[0] < current - 60:
            attempts.popleft()
        if len(attempts) >= 10:
            raise HTTPException(429, "Too many login attempts. Retry in one minute.")
        role = self.token_role(token)
        if not role:
            attempts.append(current)
            raise HTTPException(
                401, "Invalid access token.", headers={"WWW-Authenticate": "Bearer"}
            )
        attempts.clear()
        session_id = secrets.token_urlsafe(32)
        principal = Principal(role, csrf=secrets.token_urlsafe(24), via="session")
        self.sessions = {k: v for k, v in self.sessions.items() if v[0] > current}
        self.sessions[session_id] = (current + 12 * 3600, principal)
        return session_id, principal

    def principal(
        self, request: Request, credentials: HTTPAuthorizationCredentials | None
    ) -> Principal:
        if credentials:
            if role := self.token_role(credentials.credentials):
                return Principal(role)
            # An invalid explicit Bearer header must not silently fall back to a cookie.
            raise HTTPException(
                401, "Invalid access token.", headers={"WWW-Authenticate": "Bearer"}
            )
        ingress = request.headers.get("X-Kodi-Library-Ingress-Token", "")
        if self.settings.ingress_token and secure_equal(ingress, self.settings.ingress_token):
            return Principal("admin", via="ingress")
        if cookie := request.cookies.get(COOKIE):
            session = self.sessions.get(cookie)
            if session and session[0] > time.monotonic():
                return session[1]
            self.sessions.pop(cookie, None)
        raise HTTPException(401, "Authentication required.", headers={"WWW-Authenticate": "Bearer"})


def authenticated(
    request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)
) -> Principal:
    return request.app.state.auth.principal(request, credentials)


def writable(request: Request, principal: Principal = Depends(authenticated)) -> Principal:
    if principal.role != "admin":
        raise HTTPException(403, "This token has read-only access.")
    if principal.via == "session" and request.method not in ("GET", "HEAD", "OPTIONS"):
        csrf = request.headers.get("X-CSRF-Token", "")
        if not principal.csrf or not secure_equal(csrf, principal.csrf):
            raise HTTPException(403, "Missing or invalid CSRF token.")
    return principal
