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
    USER_RATING = "user_rating"
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
    # Optional, licensed My Movies Remote Server API (not XML or cloud access).
    remote_host: str = Field(default="", max_length=253)
    remote_port: int = Field(default=51414, ge=1, le=65535)
    remote_api_key: SecretStr | None = None
    remote_enabled: bool = False

    @field_validator("database")
    @classmethod
    def safe_database(cls, value: str) -> str:
        if value and not all(c.isascii() and (c.isalnum() or c == "_") for c in value):
            raise ValueError("Use an alphanumeric database name, with optional underscores.")
        return value


class SourceInput(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    type: Literal["kodi", "clz", "mymovies", "filmotech"]
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
    kind: Literal["mariadb", "kodi", "tmdb"]
    message: str
    database: str | None = None
    available_media: list[str] = Field(default_factory=list)
    version: str | None = None
    playback_allowed: bool | None = None


class SourceView(BaseModel):
    id: str
    name: str
    type: Literal["kodi", "clz", "mymovies", "filmotech"]
    enabled: bool
    config: dict[str, Any]
    last_sync: str | None = None
    last_error: str | None = None
    item_count: int = 0


class ArtworkLinks(BaseModel):
    poster: str | None = None
    fanart: str | None = None


class MediaSource(BaseModel):
    id: str
    name: str
    type: Literal["kodi", "clz", "mymovies", "filmotech"]
    type_count: int = 1


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
    media_sources: list[MediaSource] = Field(default_factory=list)
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


class CastMember(BaseModel):
    name: str
    role: str | None = None
    order: int | None = None
    category: str = "cast"
    imdb_id: str | None = None
    tmdb_id: str | None = None
    links: dict[str, str] = Field(default_factory=dict)
    thumbnail: str | None = None


class PersonalInput(StrictModel):
    user_rating: float | None = Field(default=None, ge=0, le=10)
    watched: bool | None = None


class PersonalView(BaseModel):
    user_rating: float | None = None
    watched: bool | None = None


class WritebackResult(BaseModel):
    source_id: str
    source_name: str
    source_type: str
    status: Literal["updated", "unavailable", "failed"]
    message: str | None = None


class ListInput(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)

    @field_validator("name")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("List name must not be blank.")
        return value.strip()


class ListView(BaseModel):
    id: str
    name: str
    description: str
    revision: int
    item_count: int
    created_at: str


class ListItemInput(StrictModel):
    media_id: str


class ListOrderInput(StrictModel):
    media_ids: list[str] = Field(max_length=10000)
    revision: int = Field(ge=0)


class MediaDetail(MediaSummary):
    identifiers: dict[str, list[str]]
    editions: list[EditionView]
    cast: list[CastMember] = Field(default_factory=list)
    people: list[CastMember] = Field(default_factory=list)
    trailers: list[str] = Field(default_factory=list)
    personal: PersonalView = Field(default_factory=PersonalView)
    writeback: list[WritebackResult] = Field(default_factory=list)
    lists: list[ListView] = Field(default_factory=list)
    tmdb_available: bool = False
    enriched_at: str | None = None


class MediaPage(BaseModel):
    items: list[MediaSummary]
    total: int
    limit: int
    offset: int
    has_more: bool


class ListPage(MediaPage):
    revision: int


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
    progress_phase: str | None = None
    progress_current: int = 0
    progress_total: int = 0
    overall_percent: int = Field(default=0, ge=0, le=100)


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


class PairingRequest(StrictModel):
    role: Literal["admin", "read"] = "admin"


class PairingRedeem(StrictModel):
    code: str = Field(pattern=r"^[0-9]{8}$")
    device_name: str = Field(min_length=1, max_length=80)


class PairingResult(BaseModel):
    code: str
    expires_in: int
    role: Literal["admin", "read"]


class SessionInput(StrictModel):
    token: SecretStr


class SessionView(BaseModel):
    role: Literal["admin", "read"]
    csrf_token: str | None = None
    access_method: Literal["session", "bearer", "ingress", "local"] = "session"


class CatalogueStatus(BaseModel):
    version: str
    api_version: str
    works: int
    editions: int
    counts: dict[str, int]
    sources: list[SourceView]
    tmdb_available: bool = False
