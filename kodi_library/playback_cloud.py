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

    def decrypt_secret(self, value: str) -> str:
        return self.fernet.decrypt(value.encode("ascii")).decode("utf-8")

    def provider_options(self, provider: str) -> tuple[dict, str]:
        saved = self.store.playback_oauth_app(provider)
        if saved:
            secret = None
            if saved.get("secret_cipher"):
                try:
                    secret = self.fernet.decrypt(saved["secret_cipher"].encode("ascii")).decode(
                        "utf-8"
                    )
                except Exception:
                    LOG.warning("Cannot decrypt OAuth app secret for %s", provider)
            return {"client_id": saved["client_id"], "client_secret": secret or ""}, saved[
                "redirect_url"
            ]
        return self.clients.get(provider, {}), self.redirect

    def configuration_error(self, provider: str) -> str | None:
        """Return a safe actionable explanation, never an OAuth app secret."""
        from urllib.parse import urlsplit

        if provider not in PROVIDERS:
            return "Fournisseur cloud non pris en charge."
        client, redirect = self.provider_options(provider)
        if not client.get("client_id"):
            return (
                "Aucune application OAuth n’est enregistrée pour ce fournisseur sur le serveur. "
                "La configuration est à effectuer une seule fois par l’administrateur."
            )
        if provider in {"onedrive", "google_drive", "box", "pcloud"} and not client.get("client_secret"):
            return "Le secret de l’application OAuth manque dans la configuration du serveur."
        parsed = urlsplit(redirect or "")
        if (
            parsed.scheme != "https" or not parsed.hostname
            or parsed.path != "/api/v1/playback/cloud/callback"
            or parsed.username or parsed.password or parsed.query or parsed.fragment
        ):
            return (
                "L’URL de retour OAuth doit être une adresse HTTPS publique "
                "se terminant par /api/v1/playback/cloud/callback."
            )
        return None

    def configured(self, provider: str) -> bool:
        return self.configuration_error(provider) is None

    def start(self, location_id: str, provider: str, return_to: str = "web") -> str:
        if return_to not in {"web", "ios"}:
            raise CloudOAuthError("Destination OAuth non reconnue.")
        problem = self.configuration_error(provider)
        if problem:
            raise CloudOAuthError(problem)
        # Expire old one-time CSRF states on each attempt.
        now = time.monotonic()
        self.pending = {k: v for k, v in self.pending.items() if now - v["at"] < 600}
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
            .rstrip(b"=")
            .decode("ascii")
        )
        self.pending[state] = {
            "at": now,
            "location_id": location_id,
            "provider": provider,
            "verifier": verifier,
            "redirect": self.provider_options(provider)[1],
            "return_to": return_to,
        }
        client, redirect = self.provider_options(provider)
        params = {
            "client_id": client["client_id"],
            "redirect_uri": redirect,
            "response_type": "code",
            "state": state,
        }
        if provider != "pcloud":
            params.update(
                {
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                }
            )
        if PROVIDERS[provider][2]:
            params["scope"] = PROVIDERS[provider][2]
        if provider == "google_drive":
            params["access_type"] = "offline"
            params["prompt"] = "consent"
        if provider == "dropbox":
            params["token_access_type"] = "offline"
        return PROVIDERS[provider][0] + "?" + urlencode(params)

    async def complete(self, *, state: str, code: str, error: str, hostname: str = "") -> str:
        info = self.pending.pop(state, None)
        if info is None or time.monotonic() - info["at"] > 600 or error or not code:
            raise CloudOAuthError("Autorisation expirée ou refusée.")
        provider = info["provider"]
        client, _ = self.provider_options(provider)
        payload = {
            "client_id": client["client_id"],
            "code": code,
        }
        if client.get("client_secret"):
            payload["client_secret"] = client["client_secret"]
        if provider != "pcloud":
            payload.update(
                {
                    "grant_type": "authorization_code",
                    "redirect_uri": info["redirect"],
                    "code_verifier": info["verifier"],
                }
            )
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
            LOG.warning(
                "Cloud OAuth token exchange failed for %s (%s)", provider, type(exc).__name__
            )
            raise CloudOAuthError("Impossible de valider le compte auprès du fournisseur.") from exc
        if (
            not isinstance(token, dict)
            or not isinstance(token.get("access_token"), str)
            or not token["access_token"]
            or (provider == "pcloud" and token.get("result") != 0)
        ):
            raise CloudOAuthError("Réponse d'authentification non valide.")
        if provider == "pcloud":
            token["api_hostname"] = hostname or "api.pcloud.com"
        if token.get("expires_in") is not None:
            token["expires_at"] = time.time() + max(0, float(token["expires_in"]))
        location = self.store.playback_location(info["location_id"])
        if not location or location["kind"] != provider:
            raise CloudOAuthError("L'emplacement a été supprimé ou modifié.")
        serialized = json.dumps(token, separators=(",", ":"))
        encrypted = self.fernet.encrypt(serialized.encode("utf-8")).decode("ascii")
        self.store.set_playback_cloud_token(info["location_id"], provider, encrypted)
        return info.get("return_to", "web")


    async def credentials(self, location_id: str, provider: str) -> dict:
        """Return a live provider token server-side; refresh without exposing it."""
        saved = self.store.playback_cloud_credentials(location_id)
        if not saved or saved["provider"] != provider:
            raise CloudOAuthError("Connectez ce compte cloud dans les réglages.")
        try:
            token = json.loads(self.decrypt_secret(saved["token_cipher"]))
        except (ValueError, TypeError, KeyError, UnicodeError) as exc:
            raise CloudOAuthError("Jeton cloud illisible. Reconnectez ce compte.") from exc
        if not isinstance(token, dict) or not token.get("access_token"):
            raise CloudOAuthError("Jeton cloud absent. Reconnectez ce compte.")
        expires_at = float(token.get("expires_at") or 0)
        refresh = token.get("refresh_token")
        if not refresh or provider == "pcloud":
            return token
        if expires_at > time.time() + 90:
            return token
        client, _ = self.provider_options(provider)
        if not client.get("client_id"):
            raise CloudOAuthError("Application OAuth supprimée. Reconfigurez ce fournisseur.")
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": client["client_id"],
        }
        if client.get("client_secret"):
            payload["client_secret"] = client["client_secret"]
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client_http:
                response = await client_http.post(PROVIDERS[provider][1], data=payload)
                response.raise_for_status()
                fresh = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            LOG.warning("Cloud OAuth refresh failed for %s (%s)", provider, type(exc).__name__)
            raise CloudOAuthError(
                "La session cloud a expiré et son renouvellement a échoué. Reconnectez ce compte."
            ) from exc
        if not isinstance(fresh, dict) or not fresh.get("access_token"):
            raise CloudOAuthError("Le fournisseur a refusé le renouvellement du jeton.")
        token.update(fresh)
        if not token.get("refresh_token"):
            token["refresh_token"] = refresh
        # Fall back to a short-lived expiry if a provider omits expires_in.
        token["expires_at"] = time.time() + max(0, float(fresh.get("expires_in") or 3600))
        encrypted = self.encrypt_password(json.dumps(token, separators=(",", ":")))
        self.store.set_playback_cloud_token(location_id, provider, encrypted)
        return token
