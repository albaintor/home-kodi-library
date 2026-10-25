"""Server-side cloud OAuth registration for playback locations.

OAuth secrets and access/refresh tokens never enter the iOS configuration cache.
This component registers accounts; cloud directory browsing is a separate layer.
"""
import base64
import hashlib
import json
import logging
import secrets
import time
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet

LOG = logging.getLogger(__name__)


class CloudOAuthError(Exception):
    pass


PROVIDERS = {
    "onedrive": (
        "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "offline_access Files.Read",
    ),
    "google_drive": (
        "https://accounts.google.com/o/oauth2/v2/auth",
        "https://oauth2.googleapis.com/token",
        "https://www.googleapis.com/auth/drive.readonly",
    ),
    "dropbox": (
        "https://www.dropbox.com/oauth2/authorize",
        "https://api.dropboxapi.com/oauth2/token",
        "",
    ),
    "box": (
        "https://account.box.com/api/oauth2/authorize",
        "https://api.box.com/oauth2/token",
        "",
    ),
    "pcloud": (
        "https://my.pcloud.com/oauth2/authorize",
        "https://api.pcloud.com/oauth2_token",
        "",
    ),
}


class CloudOAuth:
    supported = frozenset(PROVIDERS)

    def __init__(self, settings, store):
        self.store = store
        self.clients = settings.cloud_oauth_clients
        self.redirect = settings.cloud_oauth_redirect_url
        # Dedicated key is preferred. The API token fallback preserves existing
        # installations; changing API_TOKEN then requires reconnecting accounts.
        key = settings.playback_encryption_key
        if key:
            try:
                self.fernet = Fernet(key.encode("ascii"))
            except (ValueError, TypeError) as exc:
                raise ValueError("PLAYBACK_ENCRYPTION_KEY must be a Fernet key.") from exc
        else:
            derived = base64.urlsafe_b64encode(
                hashlib.sha256(settings.api_token.encode("utf-8")).digest()
            )
            self.fernet = Fernet(derived)
        self.pending = {}

    def encrypt_password(self, value: str) -> str:
        return self.fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def configured(self, provider: str) -> bool:
        item = self.clients.get(provider)
        return (provider in PROVIDERS and bool(self.redirect)
                and isinstance(item, dict) and bool(item.get("client_id"))
                and (provider not in {"google_drive", "box", "pcloud"} or
                     bool(item.get("client_secret"))))

    def start(self, location_id: str, provider: str) -> str:
        if not self.configured(provider):
            raise CloudOAuthError("OAuth non configuré sur le serveur pour ce fournisseur.")
        # Expire old one-time CSRF states on each attempt.
        now = time.monotonic()
        self.pending = {k: v for k, v in self.pending.items() if now - v["at"] < 600}
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        self.pending[state] = {
            "at": now, "location_id": location_id,
            "provider": provider, "verifier": verifier,
        }
        client = self.clients[provider]
        params = {
            "client_id": client["client_id"],
            "redirect_uri": self.redirect,
            "response_type": "code",
            "state": state,
        }
        if provider != "pcloud":
            params.update({
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            })
        if PROVIDERS[provider][2]:
            params["scope"] = PROVIDERS[provider][2]
        if provider == "google_drive":
            params["access_type"] = "offline"
            params["prompt"] = "consent"
        if provider == "dropbox":
            params["token_access_type"] = "offline"
        return PROVIDERS[provider][0] + "?" + urlencode(params)

    async def complete(self, *, state: str, code: str, error: str,
                       hostname: str = "") -> None:
        info = self.pending.pop(state, None)
        if info is None or time.monotonic() - info["at"] > 600 or error or not code:
            raise CloudOAuthError("Autorisation expirée ou refusée.")
        provider = info["provider"]
        client = self.clients[provider]
        payload = {
            "client_id": client["client_id"],
            "code": code,
        }
        if client.get("client_secret"):
            payload["client_secret"] = client["client_secret"]
        if provider != "pcloud":
            payload.update({
                "grant_type": "authorization_code",
                "redirect_uri": self.redirect,
                "code_verifier": info["verifier"],
            })
        token_url = PROVIDERS[provider][1]
        # pCloud returns the trusted data-center hostname from the callback;
        # accept only documented hosts, never arbitrary attacker URLs.
        if provider == "pcloud":
            if hostname not in ("", "api.pcloud.com", "eapi.pcloud.com"):
                raise CloudOAuthError("Centre de données pCloud non reconnu.")
            if hostname:
                token_url = "https://" + hostname + "/oauth2_token"
        try:
            async with httpx.AsyncClient(timeout=12, follow_redirects=False) as client_http:
                response = await client_http.post(token_url, data=payload)
                response.raise_for_status()
                token = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            LOG.warning("Cloud OAuth token exchange failed for %s (%s)",
                        provider, type(exc).__name__)
            raise CloudOAuthError("Impossible de valider le compte auprès du fournisseur.") from exc
        if (not isinstance(token, dict) or
                not isinstance(token.get("access_token"), str) or
                not token["access_token"] or
                (provider == "pcloud" and token.get("result") != 0)):
            raise CloudOAuthError("Réponse d'authentification non valide.")
        if provider == "pcloud":
            token["api_hostname"] = hostname or "api.pcloud.com"
        location = self.store.playback_location(info["location_id"])
        if not location or location["kind"] != provider:
            raise CloudOAuthError("L'emplacement a été supprimé ou modifié.")
        serialized = json.dumps(token, separators=(",", ":"))
        encrypted = self.fernet.encrypt(serialized.encode("utf-8")).decode("ascii")
        self.store.set_playback_cloud_token(info["location_id"], provider, encrypted)
