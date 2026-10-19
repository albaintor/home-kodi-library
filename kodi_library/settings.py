"""Configuration shared by standalone Docker and Home Assistant ingress."""

import json
import os
from dataclasses import dataclass, field
from ipaddress import ip_network
from pathlib import Path


@dataclass
class Settings:
    data_dir: Path = Path("/data")
    api_token: str = ""
    read_token: str = ""
    ingress_token: str = ""
    cors_origins: list[str] = field(default_factory=list)
    artwork_mounts: dict[str, str] = field(default_factory=dict)
    max_import_bytes: int = 20 * 1024 * 1024
    max_artwork_bytes: int = 8 * 1024 * 1024
    secure_cookie: bool = False
    sync_interval: int = 300
    scheduler_enabled: bool = False
    local_auth_networks: list[str] = field(default_factory=list)
    trusted_proxy_networks: list[str] = field(default_factory=list)
    tmdb_api_token: str = ""
    discovery_enabled: bool = False
    discovery_name: str = "Home Kodi Library"
    discovery_port: int = 8099

    def validate(self) -> None:
        if len(self.api_token) < 24:
            raise ValueError(
                "API_TOKEN must contain at least 24 characters; generate a random token."
            )
        for name, token in (
            ("API_READ_TOKEN", self.read_token),
            ("INGRESS_TOKEN", self.ingress_token),
        ):
            if token and (len(token) < 24 or token == self.api_token):
                raise ValueError(
                    f"{name} must be a distinct random token of at least 24 characters."
                )
        if self.read_token and self.read_token == self.ingress_token:
            raise ValueError("API_READ_TOKEN and INGRESS_TOKEN must be distinct.")
        if "*" in self.cors_origins:
            raise ValueError("CORS_ORIGINS must list exact origins, not a wildcard.")
        if not (1 <= self.discovery_port <= 65535) or not self.discovery_name.strip():
            raise ValueError("Invalid Bonjour discovery name or port.")
        if self.sync_interval < 60:
            raise ValueError("SYNC_INTERVAL must be at least 60 seconds.")
        for name, networks in (
            ("LOCAL_AUTH_NETWORKS", self.local_auth_networks),
            ("TRUSTED_PROXY_NETWORKS", self.trusted_proxy_networks),
        ):
            if not isinstance(networks, list):
                raise ValueError(f"{name} must be a JSON array of CIDR networks.")
            for value in networks:
                if not isinstance(value, str):
                    raise ValueError(f"{name} must contain CIDR strings.")
                network = ip_network(value, strict=True)
                if network.prefixlen == 0:
                    raise ValueError(f"{name} cannot allow every IP address.")

    @classmethod
    def from_env(cls) -> "Settings":
        value = cls(
            data_dir=Path(os.environ.get("DATA_DIR", "/data")),
            api_token=os.environ.get("API_TOKEN", ""),
            read_token=os.environ.get("API_READ_TOKEN", ""),
            ingress_token=os.environ.get("INGRESS_TOKEN", ""),
            cors_origins=json.loads(os.environ.get("CORS_ORIGINS", "[]")),
            artwork_mounts=json.loads(os.environ.get("ARTWORK_MOUNTS", "{}")),
            secure_cookie=os.environ.get("COOKIE_SECURE", "false").lower() == "true",
            sync_interval=int(os.environ.get("SYNC_INTERVAL", "300")),
            scheduler_enabled=os.environ.get("SCHEDULER_ENABLED", "false").lower() == "true",
            local_auth_networks=json.loads(os.environ.get("LOCAL_AUTH_NETWORKS", "[]")),
            trusted_proxy_networks=json.loads(os.environ.get("TRUSTED_PROXY_NETWORKS", "[]")),
            tmdb_api_token=os.environ.get("TMDB_API_TOKEN", ""),
            discovery_enabled=os.environ.get("DISCOVERY_ENABLED", "false").lower() == "true",
            discovery_name=os.environ.get("DISCOVERY_NAME", "Home Kodi Library"),
            discovery_port=int(os.environ.get("DISCOVERY_PORT", os.environ.get("PORT", "8099"))),
        )
        value.validate()
        return value
