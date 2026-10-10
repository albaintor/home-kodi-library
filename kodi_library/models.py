"""Typed public API contract; secrets are never included in response models."""

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class Kind(StrEnum):
    MOVIE = "movie"
    TVSHOW = "tvshow"
    EPISODE = "episode"


class WatchStatus(StrEnum):
    WATCHED = "watched"
    UNWATCHED = "unwatched"
    IN_PROGRESS = "in_progress"
    UNKNOWN = "unknown"


class SortField(StrEnum):
    TITLE = "title"
    DATE_ADDED = "date_added"
    YEAR = "year"
    RATING = "rating"
    LAST_PLAYED = "last_played"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApiError(BaseModel):
    code: str
    message: str
    details: list[dict[str, Any]] | None = None


class ErrorResponse(BaseModel):
    error: ApiError


class SourceConfig(StrictModel):
    host: str = Field(default="", max_length=253)
    port: int = Field(default=3306, ge=1, le=65535)
    username: str = Field(default="", max_length=128)
    password: SecretStr | None = None
    database: str = Field(default="", max_length=128)
    tls: bool = False
    ca_file: str | None = None

    @field_validator("database")
    @classmethod
    def safe_database(cls, value: str) -> str:
        if value and not all(c.isascii() and (c.isalnum() or c == "_") for c in value):
            raise ValueError("Use an alphanumeric database name, with optional underscores.")
        return value


class SourceInput(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    type: Literal["kodi", "clz"]
    config: SourceConfig = Field(default_factory=SourceConfig)
    enabled: bool = True


class MariaDBConnectionInput(StrictModel):
    config: SourceConfig
    source_id: str | None = None


class KodiConnectionInput(StrictModel):
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=8080, ge=1, le=65535)
    username: str = Field(default="", max_length=128)
    password: SecretStr | None = None
    tls: bool = False
    player_id: str | None = None


class ConnectionResult(BaseModel):
    success: Literal[True] = True
    kind: Literal["mariadb", "kodi"]
    message: str
    database: str | None = None
    available_media: list[str] = Field(default_factory=list)
    version: str | None = None
    playback_allowed: bool | None = None


class SourceView(BaseModel):
    id: str
    name: str
    type: Literal["kodi", "clz"]
    enabled: bool
    config: dict[str, Any]
    last_sync: str | None = None
    last_error: str | None = None
    item_count: int = 0


class ArtworkLinks(BaseModel):
    poster: str | None = None
    fanart: str | None = None


class MediaSummary(BaseModel):
    id: str
    kind: Kind
    title: str
    original_title: str | None = None
    year: int | None = None
    plot: str | None = None
    runtime_minutes: int | None = None
    rating: float | None = None
    user_rating: float | None = None
    date_added: str | None = None
    last_played: str | None = None
    watch_status: WatchStatus
    play_count: int | None = None
    resume_seconds: float = 0
    total_seconds: float = 0
    parent_id: str | None = None
    season: int | None = None
    episode: int | None = None
    genres: list[str] = Field(default_factory=list)
    formats: list[str] = Field(default_factory=list)
    collections: list[str] = Field(default_factory=list)
    source_types: list[str] = Field(default_factory=list)
    artwork: ArtworkLinks


class EditionView(BaseModel):
    id: str
    source_id: str
    source_name: str
    source_type: str
    external_key: str
    identifiers: dict[str, str]
    metadata: dict[str, Any]
    watched: bool | None = None
    play_count: int | None = None
    resume_seconds: float = 0
    total_seconds: float = 0
    date_added: str | None = None

    last_played: str | None = None


class MediaDetail(MediaSummary):
    identifiers: dict[str, list[str]]
    editions: list[EditionView]


class MediaPage(BaseModel):
    items: list[MediaSummary]
    total: int
    limit: int
    offset: int
    has_more: bool


class Facets(BaseModel):
    genres: list[str]
    formats: list[str]
    collections: list[str]
    years: list[int]
    sources: list[SourceView]


class JobView(BaseModel):
    id: str
    source_id: str
    status: Literal["pending", "running", "completed", "failed"]
    created_at: str
    finished_at: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


class PlayerInput(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    source_id: str
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=8080, ge=1, le=65535)
    username: str = Field(default="", max_length=128)
    password: SecretStr | None = None
    tls: bool = False


class PlayerView(BaseModel):
    id: str
    name: str
    source_id: str
    host: str
    port: int
    username: str
    tls: bool
    has_password: bool


class PlayInput(StrictModel):
    media_id: str
    edition_id: str | None = None
    resume: bool = True


class PlayResult(BaseModel):
    player_id: str
    media_id: str
    edition_id: str
    status: Literal["started"] = "started"


class SessionInput(StrictModel):
    token: SecretStr


class SessionView(BaseModel):
    role: Literal["admin", "read"]
    csrf_token: str | None = None


class CatalogueStatus(BaseModel):
    version: str
    api_version: str
    works: int
    editions: int
    counts: dict[str, int]
    sources: list[SourceView]
