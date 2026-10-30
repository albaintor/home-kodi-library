"""Bearer clients, private browser sessions and a distinct trusted ingress credential."""

import hashlib
import os
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from ipaddress import ip_address, ip_network
from threading import RLock

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer = HTTPBearer(auto_error=False, scheme_name="BearerToken")
COOKIE = "home_media_catalog_session"


def secure_equal(left: str, right: str) -> bool:
    return secrets.compare_digest(left.encode(), right.encode())


@dataclass
class Principal:
    role: str
    csrf: str | None = None
    via: str = "bearer"


class Auth:
    def __init__(self, settings, store=None):
        self.settings = settings
        self.store = store
        self.devices: dict[str, str] = {}
        self.pairing: tuple[str, float, str] | None = None
        self.pairing_failures = 0
        self.pairing_attempts: dict[str, deque[float]] = defaultdict(deque)
        self.pairing_global: deque[float] = deque()
        self.pairing_lock = RLock()
        self.sessions: dict[str, tuple[float, Principal]] = {}
        self.attempts: dict[str, deque[float]] = defaultdict(deque)
        self.local_networks = [ip_network(n) for n in settings.local_auth_networks]
        self.proxy_networks = [ip_network(n) for n in settings.trusted_proxy_networks]
        self.local_csrf = secrets.token_urlsafe(24)

    def is_local(self, request: Request) -> bool:
        def address(value):
            ip = ip_address(value)
            return getattr(ip, "ipv4_mapped", None) or ip

        def trusted(ip):
            return any(ip in network for network in self.proxy_networks)

        try:
            peer = address(request.client.host) if request.client else None
            if peer is None:
                return False
            # A TLS relay connects to FastAPI from loopback. Do not treat
            # every remote HTTPS client as a locally trusted administrator.
            if os.getenv("HMC_TLS_RELAY_ACTIVE") == "1" and peer.is_loopback:
                return False
            if trusted(peer):
                forwarded = request.headers.getlist("x-forwarded-for")
                # A trusted proxy must append/replace a validated client chain.
                if len(forwarded) != 1 or not forwarded[0] or len(forwarded[0]) > 4096:
                    return False
                chain = [address(v.strip()) for v in forwarded[0].split(",")]
                for candidate in reversed(chain):
                    peer = candidate
                    if not trusted(peer):
                        break
                else:
                    return False
            return any(peer in network for network in self.local_networks)
        except ValueError:
            return False

    def load_devices(self) -> None:
        if self.store is not None:
            with self.pairing_lock:
                self.devices = {
                    row["token_hash"]: row["role"] for row in self.store.device_tokens()
                }

    @staticmethod
    def _digest(secret: str) -> str:
        return hashlib.sha256(secret.encode("utf-8")).hexdigest()

    def token_role(self, token: str) -> str | None:
        if secure_equal(token, self.settings.api_token):
            return "admin"
        if self.settings.read_token and secure_equal(token, self.settings.read_token):
            return "read"
        with self.pairing_lock:
            return self.devices.get(self._digest(token))

    def start_pairing(self, role: str) -> dict[str, str | int]:
        if role not in ("admin", "read"):
            raise ValueError("Invalid pairing role.")
        with self.pairing_lock:
            code = f"{secrets.randbelow(100_000_000):08d}"
            self.pairing = (self._digest(code), time.monotonic() + 300, role)
            self.pairing_failures = 0
            return {"code": code, "expires_in": 300, "role": role}

    def redeem_pairing(self, code: str, name: str, address: str) -> dict[str, str]:
        with self.pairing_lock:
            current = time.monotonic()
            attempts = self.pairing_attempts[address]
            while attempts and attempts[0] < current - 60:
                attempts.popleft()
            while self.pairing_global and self.pairing_global[0] < current - 60:
                self.pairing_global.popleft()
            if len(attempts) >= 5 or len(self.pairing_global) >= 25:
                raise HTTPException(429, "Too many pairing attempts. Try again later.")
            attempts.append(current)
            self.pairing_global.append(current)
            pending = self.pairing
            if not pending or current >= pending[1]:
                self.pairing = None
                raise HTTPException(400, "No active pairing code or code expired.")
            if not secure_equal(self._digest(code), pending[0]):
                self.pairing_failures += 1
                if self.pairing_failures >= 5:
                    self.pairing = None
                raise HTTPException(400, "Invalid pairing code.")
            if self.store is None:
                raise HTTPException(503, "Device pairing unavailable.")
            token = secrets.token_urlsafe(48)
            token_hash = self._digest(token)
            self.store.register_device_token(token_hash, name, pending[2])
            self.devices[token_hash] = pending[2]
            self.pairing = None  # One-use code, consumed only after persistence.
            return {"token": token, "role": pending[2]}

    def revoke_device(self, token_hash: str) -> bool:
        if self.store is None:
            return False
        with self.pairing_lock:
            removed = self.store.revoke_device_token(token_hash)
            self.devices.pop(token_hash, None)
            return removed

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
        if self.is_local(request):
            return Principal("admin", csrf=self.local_csrf, via="local")
        raise HTTPException(401, "Authentication required.", headers={"WWW-Authenticate": "Bearer"})


def authenticated(
    request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)
) -> Principal:
    return request.app.state.auth.principal(request, credentials)


def writable(request: Request, principal: Principal = Depends(authenticated)) -> Principal:
    if principal.role != "admin":
        raise HTTPException(403, "This token has read-only access.")
    if principal.via in ("session", "local") and request.method not in ("GET", "HEAD", "OPTIONS"):
        csrf = request.headers.get("X-CSRF-Token", "")
        if not principal.csrf or not secure_equal(csrf, principal.csrf):
            raise HTTPException(403, "Missing or invalid CSRF token.")
    return principal
