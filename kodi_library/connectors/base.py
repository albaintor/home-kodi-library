"""Stable connector boundary for future collection providers."""

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class Record:
    external_key: str
    kind: str
    title: str
    identifiers: dict[str, str] = field(default_factory=dict)
    original_title: str | None = None
    sort_title: str | None = None
    year: int | None = None
    plot: str | None = None
    runtime_minutes: int | None = None
    rating: float | None = None
    user_rating: float | None = None
    genres: list[str] = field(default_factory=list)
    formats: list[str] = field(default_factory=list)
    collections: list[str] = field(default_factory=list)
    date_added: str | None = None
    last_played: str | None = None
    watched: bool | None = None
    play_count: int | None = None
    resume_seconds: float = 0
    total_seconds: float = 0
    poster: list[str] = field(default_factory=list)
    fanart: list[str] = field(default_factory=list)
    parent_key: str | None = None
    season: int | None = None
    episode: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ConnectorError(Exception):
    """Safe, actionable source error, with no source credentials."""


class Connector(Protocol):
    def fetch(self) -> list[Record]:
        """Return a complete successful snapshot; errors must raise before replacement."""
