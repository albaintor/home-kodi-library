"""Versioned REST API consumed identically by the web UI and native clients."""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.cors import CORSMiddleware

from . import DATA_MODEL_VERSION, __version__
from .artwork import Artwork, ArtworkError
from .auth import COOKIE, Auth, Principal, authenticated, secure_equal, writable
from .backup import MAX_BACKUP_BYTES, export_backup, import_backup
from .compatibility import mobile_compatibility
from .connectors.base import ConnectorError
from .connectors.kodi import KodiConnector
from .enrichment import enrich_tmdb, probe_tmdb
from .list_thumbnails import ListThumbnails
from .models import (
    CatalogueStatus,
    ConnectionResult,
    EpisodeWatchInput,
    EpisodeWatchResult,
    ErrorResponse,
    Facets,
    JobView,
    Kind,
    KodiConnectionInput,
    ListInput,
    ListItemInput,
    ListOrderInput,
    ListPage,
    ListView,
    MariaDBConnectionInput,
    MariaDBDatabasesResult,
    MediaDetail,
    MediaPage,
    PairingRedeem,
    PairingRequest,
    PairingResult,
    PersonalInput,
    PlayerInput,
    PlayerView,
    PlayInput,
    PlayResult,
    ReplayResponse,
    SeasonSummary,
    SessionInput,
    SessionView,
    SortField,
    SourceInput,
    SourceView,
    WatchStatus,
    WritebackStatus,
)
from .players import play
from .players import probe as probe_player
from .settings import Settings
from .store import Store
from .sync import BusyError, SyncManager
from .writeback import propagate_personal

_LOG = logging.getLogger(__name__)

API = "/api/v1"
STATIC = Path(__file__).parent / "static"


def unsecret(model) -> dict:
    value = model.model_dump()
    config = value.get("config", value)
    for secret in ("password", "remote_api_key"):
        if config.get(secret) is not None:
            config[secret] = config[secret].get_secret_value()
    return value


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.validate()
    store = Store(settings.data_dir / "catalogue.db")
    sync = SyncManager(store, settings)
    auth = Auth(settings, store)
    personal_edit_lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(app):
        await asyncio.to_thread(store.initialize)
        await asyncio.to_thread(auth.load_devices)
        await sync.bootstrap_empty_catalogue()
        discovery = None
        if settings.discovery_enabled:
            from .discovery import BonjourPublisher

            discovery = BonjourPublisher(settings.discovery_name, settings.discovery_port)
            await asyncio.to_thread(discovery.start)
        try:
            yield
        finally:
            if discovery:
                await asyncio.to_thread(discovery.stop)
            await sync.close()

    app = FastAPI(
        title="Kodi Library API",
        version=__version__,
        lifespan=lifespan,
        description="API v1 for Kodi MariaDB and physical collections. Bearer authentication for native clients; the web interface uses the same endpoints. Watch status unknown is distinct from unwatched.",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        responses={
            code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 413, 422, 429, 502)
        },
    )
    app.state.store, app.state.auth, app.state.sync = store, auth, sync
    app.state.artwork = Artwork(settings)
    thumbnails = ListThumbnails(store, app.state.artwork, settings.data_dir)
    app.state.list_thumbnails = thumbnails
    enrichment_lock = asyncio.Lock()
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "X-CSRF-Token"],
        )

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        # SAMEORIGIN preserves embedding under Home Assistant ingress.
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Cache-Control"] = (
            "private, max-age=31536000, immutable"
            if "/lists/" in request.url.path
            and "/thumbnails/" in request.url.path
            and response.status_code == 200
            else "private, no-store"
        )
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        codes = {
            401: "unauthorized",
            403: "forbidden",
            404: "not_found",
            409: "conflict",
            413: "payload_too_large",
            422: "invalid_request",
            429: "rate_limited",
            502: "upstream_error",
        }
        return JSONResponse(
            {
                "error": {
                    "code": codes.get(exc.status_code, "request_error"),
                    "message": str(exc.detail),
                }
            },
            status_code=exc.status_code,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Do not echo input values: a malformed request may include passwords.
        details = [
            {"location": list(e["loc"]), "message": e["msg"], "type": e["type"]}
            for e in exc.errors()
        ]
        return JSONResponse(
            {
                "error": {
                    "code": "invalid_request",
                    "message": "Invalid request parameters.",
                    "details": details,
                }
            },
            status_code=422,
        )

    @app.get("/healthz", include_in_schema=False)
    def health():
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/service-worker.js", include_in_schema=False)
    def service_worker():
        # Serve from the site root so it can recover the application shell
        # after a reload while the server is temporarily offline.
        return FileResponse(STATIC / "service-worker.js", media_type="text/javascript")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/docs", include_in_schema=False, dependencies=[Depends(authenticated)])
    def docs():
        return get_swagger_ui_html(openapi_url="api/v1/openapi.json", title="Kodi Library API v1")

    @app.get(API + "/openapi.json", include_in_schema=False, dependencies=[Depends(authenticated)])
    def schema():
        return app.openapi()

    @app.get(API + "/discovery", include_in_schema=False)
    def discovery_info():
        # Deliberately public, with no credentials, file paths or catalogue data.
        return {
            "service": "home-kodi-library",
            "api_version": "v1",
            "server_version": __version__,
            "data_model_version": DATA_MODEL_VERSION,
            "pairing": True,
            "mobile_compatibility": mobile_compatibility(),
        }

    @app.post(
        API + "/pairing/start",
        response_model=PairingResult,
        dependencies=[Depends(writable)],
        tags=["Authentication"],
    )
    def start_pairing(request: PairingRequest):
        return auth.start_pairing(request.role)

    @app.post(API + "/pairing/redeem", tags=["Authentication"])
    def redeem_pairing(request: PairingRedeem, http_request: Request):
        # The sole unauthenticated mutation; bounded lifetime, one use,
        # per-peer and global throttles, with five failures per issued code.
        peer = http_request.client.host if http_request.client else "unknown"
        return auth.redeem_pairing(request.code, request.device_name, peer)

    @app.get(
        API + "/pairing/devices",
        dependencies=[Depends(writable)],
        tags=["Authentication"],
    )
    def paired_devices():
        return store.device_tokens()

    @app.delete(
        API + "/pairing/devices/{token_hash}",
        status_code=204,
        dependencies=[Depends(writable)],
        tags=["Authentication"],
    )
    def revoke_paired_device(token_hash: str):
        if len(token_hash) != 64 or any(char not in "0123456789abcdef" for char in token_hash):
            raise HTTPException(404, "Unknown paired device.")
        if not auth.revoke_device(token_hash):
            raise HTTPException(404, "Unknown paired device.")

    @app.post(API + "/auth/session", response_model=SessionView, tags=["Authentication"])
    def login(data: SessionInput, request: Request, response: Response):
        address = request.client.host if request.client else "unknown"
        session_id, principal = app.state.auth.login(data.token.get_secret_value(), address)
        response.set_cookie(
            COOKIE,
            session_id,
            max_age=12 * 3600,
            httponly=True,
            secure=settings.secure_cookie or request.url.scheme == "https",
            samesite="lax",
            path="/",
        )
        return {"role": principal.role, "csrf_token": principal.csrf}

    @app.get(API + "/auth/session", response_model=SessionView, tags=["Authentication"])
    def session(principal: Principal = Depends(authenticated)):
        return {
            "role": principal.role,
            "csrf_token": principal.csrf,
            "access_method": principal.via,
        }

    @app.delete(API + "/auth/session", status_code=204, tags=["Authentication"])
    def logout(request: Request, response: Response, principal: Principal = Depends(authenticated)):
        if principal.via == "session" and not secure_equal(
            request.headers.get("X-CSRF-Token", ""), principal.csrf or ""
        ):
            raise HTTPException(403, "Invalid CSRF token.")
        app.state.auth.sessions.pop(request.cookies.get(COOKIE, ""), None)
        response.delete_cookie(COOKIE, path="/")

    @app.get(
        API + "/status",
        response_model=CatalogueStatus,
        tags=["Catalogue"],
        dependencies=[Depends(authenticated)],
    )
    def status():
        return {
            "version": __version__,
            "data_model_version": DATA_MODEL_VERSION,
            "api_version": "v1",
            "mobile_compatibility": mobile_compatibility(),
            **store.status(),
            "tmdb_available": bool(settings.tmdb_api_token.strip()),
        }

    @app.get(
        API + "/media",
        response_model=MediaPage,
        tags=["Catalogue"],
        dependencies=[Depends(authenticated)],
    )
    def list_media(
        q: str = Query("", max_length=300),
        deep_search: bool = False,
        person_tmdb_id: str | None = Query(None, pattern=r"^[0-9]+$", max_length=24),
        person_imdb_id: str | None = Query(None, pattern=r"^nm[0-9]+$", max_length=24),
        person_name: str | None = Query(None, max_length=200),
        kind: Kind | None = None,
        watch_status: WatchStatus | None = None,
        genre: str | None = Query(None, max_length=120),
        format: list[str] | None = Query(None),
        collection: str | None = Query(None, max_length=200),
        source_type: Literal["kodi", "clz", "mymovies", "filmotech"] | None = None,
        source_id: str | None = None,
        parent_id: str | None = None,
        season: int | None = Query(None, ge=0, le=10000),
        year: int | None = Query(None, ge=1800, le=2200),
        recent_days: int | None = Query(None, ge=1, le=3650),
        sort: SortField = SortField.DATE_ADDED,
        order: Literal["asc", "desc"] = "desc",
        limit: int = Query(60, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ):
        return store.media(
            q=q,
            deep_search=deep_search,
            person_tmdb_id=person_tmdb_id,
            person_imdb_id=person_imdb_id,
            person_name=person_name,
            kind=kind,
            watch_status=watch_status,
            genre=genre,
            format=format,
            collection=collection,
            source_type=source_type,
            source_id=source_id,
            parent_id=parent_id,
            season=season,
            year=year,
            recent_days=recent_days,
            sort=sort,
            order=order,
            limit=limit,
            offset=offset,
        )

    @app.get(
        API + "/media/{media_id}/seasons",
        response_model=list[SeasonSummary],
        tags=["Catalogue"],
        dependencies=[Depends(authenticated)],
    )
    def media_seasons(media_id: str):
        """Navigable season list; empty for shows without imported episodes."""
        result = store.seasons(media_id)
        if result is None:
            raise HTTPException(404, "Unknown TV show.")
        return result

    @app.get(
        API + "/media/{media_id}/seasons/{season}/episodes",
        response_model=MediaPage,
        tags=["Catalogue"],
        dependencies=[Depends(authenticated)],
    )
    def season_episodes(
        media_id: str,
        season: int,
        limit: int = Query(100, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ):
        if season < 0 or season > 10000:
            raise HTTPException(422, "Invalid season.")
        if store.seasons(media_id) is None:
            raise HTTPException(404, "Unknown TV show.")
        return store.media(
            kind="episode",
            parent_id=media_id,
            season=season,
            sort="episode",
            order="asc",
            limit=limit,
            offset=offset,
        )

    async def finish_episode_watch_writebacks(episode_ids: list[str], watched: bool):
        # Changes and their pending writebacks are saved before responding.
        # Subsequent contradictory edits supersede older queued values.
        async with personal_edit_lock:
            players = [
                await asyncio.to_thread(store.player, player["id"])
                for player in await asyncio.to_thread(store.players)
            ]
            source_cache = {}
            for episode_id in episode_ids:
                try:
                    detail = await asyncio.to_thread(store.detail, episode_id)
                    if not detail or detail.get("personal", {}).get("watched") != watched:
                        continue
                    sources = []
                    for source_id in {e["source_id"] for e in detail["editions"]}:
                        if source_id not in source_cache:
                            source_cache[source_id] = await asyncio.to_thread(
                                store.source, source_id, private=True
                            )
                        if source_cache[source_id]:
                            sources.append(source_cache[source_id])
                    changes = {"watched": watched}
                    results = await propagate_personal(detail, changes, sources, players)
                    if results:
                        await asyncio.to_thread(
                            store.record_writebacks, episode_id, changes, results
                        )
                except Exception:
                    # The per-episode retry record remains durable.
                    _LOG.exception("Bulk watch writeback failed for %s", episode_id)

    @app.patch(
        API + "/media/{media_id}/episodes/watch",
        response_model=EpisodeWatchResult,
        tags=["Personal"],
        dependencies=[Depends(writable)],
    )
    async def edit_episode_watches(
        media_id: str, data: EpisodeWatchInput, background_tasks: BackgroundTasks
    ):
        """Mark a season, all episodes, or those through an inclusive cutoff."""
        if data.up_to_episode is not None and data.season is None:
            raise HTTPException(422, "up_to_episode requires a season.")
        async with personal_edit_lock:
            result = await asyncio.to_thread(
                store.set_episode_watch_status,
                media_id,
                data.watched,
                data.season,
                data.up_to_episode,
            )
            if result is None:
                raise HTTPException(404, "Unknown TV show.")
            # Persist retryable per-episode upstream writes before acknowledging.
            source_cache = {}
            for episode_id in result["ids"]:
                detail = await asyncio.to_thread(store.detail, episode_id)
                sources = []
                for source_id in {e["source_id"] for e in detail["editions"]}:
                    if source_id not in source_cache:
                        source_cache[source_id] = await asyncio.to_thread(
                            store.source, source_id, private=True
                        )
                    if source_cache[source_id]:
                        sources.append(source_cache[source_id])
                await asyncio.to_thread(
                    store.stage_writebacks, episode_id, detail, {"watched": data.watched}, sources
                )
            if result["ids"]:
                background_tasks.add_task(
                    finish_episode_watch_writebacks, result["ids"], data.watched
                )
            return {key: value for key, value in result.items() if key != "ids"}

    @app.get(
        API + "/facets",
        response_model=Facets,
        tags=["Catalogue"],
        dependencies=[Depends(authenticated)],
    )
    def facets():
        return store.facets()

    @app.get(
        API + "/media/{media_id}",
        response_model=MediaDetail,
        tags=["Catalogue"],
        dependencies=[Depends(authenticated)],
    )
    def media_detail(media_id: str):
        if value := store.detail(media_id):
            return {
                **value,
                "tmdb_available": bool(settings.tmdb_api_token.strip()),
                "writeback_state": store.writeback_state(media_id),
            }
        raise HTTPException(404, "Unknown media.")

    @app.get(
        API + "/media/{media_id}/seasons/{season}/poster",
        tags=["Catalogue"],
        dependencies=[Depends(authenticated)],
        responses={200: {"content": {"image/jpeg": {}, "image/png": {}, "image/webp": {}}}},
    )
    async def tv_season_poster(media_id: str, season: int):
        candidates = await asyncio.to_thread(store.season_poster, media_id, season)
        if not candidates:
            raise HTTPException(404, "No season-specific poster.")
        try:
            data, content_type = await app.state.artwork.get(candidates)
            return Response(data, media_type=content_type)
        except ArtworkError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get(
        API + "/media/{media_id}/diagnostics",
        tags=["Catalogue"],
        dependencies=[Depends(writable)],
    )
    async def diagnose_media(media_id: str):
        """Inspect Kodi's movie-navigation SQL using MariaDB only."""
        detail = await asyncio.to_thread(store.detail, media_id)
        if not detail:
            raise HTTPException(404, "Unknown media.")
        by_source = {}
        for edition in detail["editions"]:
            sid = edition["source_id"]
            report = by_source.setdefault(
                sid,
                {
                    "source_id": sid,
                    "source_name": edition["source_name"],
                    "source_type": edition["source_type"],
                    "editions": [],
                },
            )
            metadata = edition["metadata"]
            report["editions"].append(
                {
                    "external_key": edition["external_key"],
                    "kodi_id": metadata.get("kodi_id"),
                    "file_id": metadata.get("file_id"),
                    "file": metadata.get("file"),
                    "database_at_import": metadata.get("database"),
                    "exists_in_movie": None,
                    "in_library": None,
                }
            )

        reports = []
        for sid, report in by_source.items():
            if report["source_type"] == "kodi":
                source = await asyncio.to_thread(store.source, sid, private=True)
                if not source:
                    report["error"] = "Source no longer exists."
                else:
                    report["configured_database"] = source["config"].get("database") or None
                    movie_ids = [
                        edition["kodi_id"]
                        for edition in report["editions"]
                        if isinstance(edition["kodi_id"], int)
                    ]
                    if detail["kind"] == "movie" and movie_ids:
                        try:
                            inspected = await asyncio.to_thread(
                                KodiConnector(source["config"]).inspect_movie_links, movie_ids
                            )
                            report["queried_database"] = inspected["database"]
                            report["library_rule"] = inspected["library_rule"]
                            report["library_verified"] = inspected["library_verified"]
                            listed = set(inspected["library_visible_movie_ids"])
                            movie_rows = {int(k): v for k, v in inspected["movies"].items()}
                            for edition in report["editions"]:
                                movie_id = edition["kodi_id"]
                                if isinstance(movie_id, int):
                                    edition["exists_in_movie"] = movie_id in movie_rows
                                    edition["sql_links"] = movie_rows.get(movie_id)
                                    edition["in_library"] = movie_id in listed
                        except ConnectorError as exc:
                            report["error"] = str(exc)
                    else:
                        report["note"] = "Movie navigation applies to films only."
            reports.append(report)
        return {
            "media_id": detail["id"],
            "title": detail["title"],
            "sources": reports,
            "has_clz": any(report["source_type"] == "clz" for report in reports),
        }

    @app.patch(
        API + "/media/{media_id}/personal",
        response_model=MediaDetail,
        tags=["Personal"],
        dependencies=[Depends(writable)],
    )
    async def edit_personal(media_id: str, data: PersonalInput):
        # Serialize edits so two concurrent PATCH requests cannot reach Kodi out
        # of order. The user's local value remains saved if Kodi is offline;
        # the response explicitly reports each upstream outcome.
        async with personal_edit_lock:
            detail = await asyncio.to_thread(store.detail, media_id)
            if detail is None:
                raise HTTPException(404, "Unknown media.")
            # PATCH is the sole outbound-write trigger. Unchanged values must
            # not retry a failed propagation or overwrite a third-party edit.
            changes = {
                key: value
                for key, value in data.model_dump(exclude_unset=True).items()
                if detail.get("personal", {}).get(key) != value
            }
            if changes:
                await asyncio.to_thread(store.update_personal, media_id, changes)
            relevant_source_ids = {edition["source_id"] for edition in detail["editions"]}
            sources = [
                await asyncio.to_thread(store.source, source_id, private=True)
                for source_id in relevant_source_ids
            ]
            await asyncio.to_thread(store.stage_writebacks, media_id, detail, changes, sources)
            public_players = await asyncio.to_thread(store.players)
            players = [
                await asyncio.to_thread(store.player, player["id"])
                for player in public_players
                if any(
                    edition["source_id"] == player["source_id"] and edition["source_type"] == "kodi"
                    for edition in detail["editions"]
                )
            ]
            writeback = await propagate_personal(detail, changes, sources, players)
            if writeback:
                await asyncio.to_thread(store.record_writebacks, media_id, changes, writeback)
            updated = await asyncio.to_thread(store.detail, media_id)
            updated["writeback"] = writeback
            updated["writeback_state"] = await asyncio.to_thread(store.writeback_state, media_id)
            return updated

    @app.get(
        API + "/media/{media_id}/writeback",
        tags=["Personal"],
        dependencies=[Depends(authenticated)],
    )
    def media_writeback_history(media_id: str, limit: int = Query(default=50, ge=1, le=200)):
        if not store.detail(media_id):
            raise HTTPException(404, "Unknown media.")
        return store.writeback_history(work_id=media_id, limit=limit)

    @app.get(
        API + "/writebacks/status",
        response_model=WritebackStatus,
        tags=["Personal"],
        dependencies=[Depends(authenticated)],
    )
    def writeback_status():
        """Current outgoing synchronization indicators for web and iOS."""
        return {
            "pending_count": store.writeback_pending_count(),
            "pending": store.pending_writebacks(),
            "recent": store.writeback_state(limit=30),
        }

    @app.get(
        API + "/writebacks/pending",
        tags=["Personal"],
        dependencies=[Depends(authenticated)],
    )
    def pending_writebacks():
        return store.pending_writebacks()

    async def replay_writeback_item(pending_id: str) -> dict:
        row = await asyncio.to_thread(store.pending_writebacks, pending_id=pending_id)
        if not row:
            raise HTTPException(404, "Pending synchronization not found.")
        pending = row[0]
        media_id = pending["media_id"]
        detail = await asyncio.to_thread(store.detail, media_id)
        if detail is None:
            raise HTTPException(409, "The media no longer exists.")
        if not any(e["source_id"] == pending["source_id"] for e in detail["editions"]):
            raise HTTPException(409, "The original source edition is no longer associated.")
        changes = pending["changes"]
        # Reject stale data: never overwrite a third-party service with values
        # which are no longer the user's explicit local preferences.
        if any(detail.get("personal", {}).get(k) != v for k, v in changes.items()):
            raise HTTPException(
                409, "Pending values do not match the current personal preferences."
            )
        source = await asyncio.to_thread(store.source, pending["source_id"], private=True)
        if not source:
            raise HTTPException(409, "The original source no longer exists.")
        if source["type"] == "mymovies" and not source["config"].get("remote_enabled"):
            raise HTTPException(409, "My Movies writeback is not enabled.")
        players = (
            [
                await asyncio.to_thread(store.player, player["id"])
                for player in await asyncio.to_thread(store.players)
                if player["source_id"] == pending["source_id"]
            ]
            if source["type"] == "kodi"
            else []
        )
        scoped = {
            **detail,
            "editions": [
                edition
                for edition in detail["editions"]
                if edition["source_id"] == pending["source_id"]
            ],
        }
        result = await propagate_personal(scoped, changes, [source], players)
        if not result:
            raise HTTPException(409, "The source no longer supports these fields.")
        await asyncio.to_thread(store.record_writebacks, media_id, changes, result, replay=True)
        return {"pending_id": pending_id, "media_id": media_id, "result": result[0]}

    @app.post(
        API + "/writebacks/pending/{pending_id}/retry",
        response_model=ReplayResponse,
        tags=["Personal"],
        dependencies=[Depends(writable)],
    )
    async def retry_writeback(pending_id: str):
        async with personal_edit_lock:
            result = await replay_writeback_item(pending_id)
            return {
                "results": [result],
                "pending_count": await asyncio.to_thread(store.writeback_pending_count),
            }

    @app.post(
        API + "/writebacks/retry",
        response_model=ReplayResponse,
        tags=["Personal"],
        dependencies=[Depends(writable)],
    )
    async def retry_all_writebacks():
        """User-initiated only; partial failures stay queued for another replay."""
        async with personal_edit_lock:
            pending = await asyncio.to_thread(store.pending_writebacks)
            results = []
            for item in pending:
                try:
                    result = await replay_writeback_item(item["id"])
                    results.append(result)
                except HTTPException as exc:
                    # A missing/obsolete entry stays cached; do not replay or
                    # delete it silently, and continue with independent items.
                    results.append(
                        {
                            "pending_id": item["id"],
                            "media_id": item["media_id"],
                            "result": {
                                "source_id": item["source_id"],
                                "source_name": item["source_name"],
                                "source_type": item["source_type"],
                                "status": "failed",
                                "message": str(exc.detail),
                            },
                        }
                    )
            return {
                "results": results,
                "pending_count": await asyncio.to_thread(store.writeback_pending_count),
            }

    @app.post(
        API + "/media/{media_id}/enrich",
        response_model=MediaDetail,
        tags=["Enrichment"],
        dependencies=[Depends(writable)],
    )
    async def enrich_media(media_id: str, refresh: bool = False):
        async with enrichment_lock:
            detail = await asyncio.to_thread(store.detail, media_id)
            if not detail:
                raise HTTPException(404, "Unknown media.")
            updated = detail.get("enriched_at")
            if (
                not refresh
                and updated
                and datetime.fromisoformat(updated) > datetime.now(UTC) - timedelta(days=30)
                and (detail["kind"] != "tvshow" or detail.get("season_posters_cached"))
            ):
                return {**detail, "tmdb_available": bool(settings.tmdb_api_token.strip())}
            try:
                payload = await enrich_tmdb(detail, settings.tmdb_api_token)
            except ConnectorError as exc:
                raise HTTPException(502, str(exc)) from exc
            if not await asyncio.to_thread(store.save_enrichment, media_id, payload):
                raise HTTPException(404, "Media was removed during enrichment.")
            if refresh:
                photos = [
                    url
                    for member in [*detail["people"], *payload.get("people", [])]
                    for url in member.get("photos") or []
                ]
                await asyncio.to_thread(app.state.artwork.invalidate, photos)
            return media_detail(media_id)

    @app.get(
        API + "/media/{media_id}/people/{index}/artwork",
        tags=["Enrichment"],
        dependencies=[Depends(authenticated)],
    )
    async def person_artwork(media_id: str, index: int):
        detail = await asyncio.to_thread(store.detail, media_id)
        if not detail or not 0 <= index < len(detail["people"]):
            raise HTTPException(404, "Unknown credit.")
        try:
            data, content_type = await app.state.artwork.get(
                detail["people"][index].get("photos") or []
            )
            return Response(data, media_type=content_type)
        except ArtworkError as exc:
            raise HTTPException(404, str(exc)) from exc

    def list_error(operation):
        try:
            return operation()
        except KeyError as exc:
            raise HTTPException(404, str(exc.args[0])) from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get(
        API + "/backup",
        tags=["Backup"],
        dependencies=[Depends(authenticated)],
    )
    def download_backup():
        return export_backup(store)

    @app.post(
        API + "/backup",
        tags=["Backup"],
        dependencies=[Depends(writable)],
    )
    async def restore_backup(request: Request, background_tasks: BackgroundTasks):
        if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
            raise HTTPException(422, "A JSON backup is required.")
        if (
            request.headers.get("content-length", "").isdigit()
            and int(request.headers["content-length"]) > MAX_BACKUP_BYTES
        ):
            raise HTTPException(413, "Backup exceeds 100 MiB.")
        payload = await request.body()
        if len(payload) > MAX_BACKUP_BYTES:
            raise HTTPException(413, "Backup exceeds 100 MiB.")
        try:
            document = json.loads(payload)
            async with personal_edit_lock:
                previous, restored = await asyncio.to_thread(import_backup, store, document)
        except (ValueError, UnicodeError) as exc:
            raise HTTPException(422, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

        async def rebuild_thumbnails():
            # Purge cached mosaics even if a restored list reuses the same revision.
            ids = set(previous) | set(restored)
            await asyncio.gather(*(thumbnails.delete(item) for item in ids))
            await asyncio.gather(*(thumbnails.warm(item) for item in restored))

        background_tasks.add_task(rebuild_thumbnails)
        return {
            "status": "restored",
            "lists": len(restored),
            "media": len(document["tables"]["works"]),
        }

    @app.get(
        API + "/lists",
        response_model=list[ListView],
        tags=["Lists"],
        dependencies=[Depends(authenticated)],
    )
    def lists():
        return store.lists()

    @app.post(
        API + "/lists",
        response_model=ListView,
        status_code=201,
        tags=["Lists"],
        dependencies=[Depends(writable)],
    )
    def create_list(data: ListInput, background_tasks: BackgroundTasks):
        result = store.save_list(data.model_dump())
        background_tasks.add_task(thumbnails.warm, result["id"])
        return result

    @app.put(
        API + "/lists/{list_id}",
        response_model=ListView,
        tags=["Lists"],
        dependencies=[Depends(writable)],
    )
    def update_list(list_id: str, data: ListInput, background_tasks: BackgroundTasks):
        result = list_error(lambda: store.save_list(data.model_dump(), list_id))
        background_tasks.add_task(thumbnails.warm, list_id)
        return result

    @app.delete(
        API + "/lists/{list_id}", status_code=204, tags=["Lists"], dependencies=[Depends(writable)]
    )
    def delete_list(list_id: str, background_tasks: BackgroundTasks):
        list_error(lambda: store.delete_list(list_id))
        background_tasks.add_task(thumbnails.delete, list_id)

    @app.get(
        API + "/lists/{list_id}/items",
        response_model=ListPage,
        tags=["Lists"],
        dependencies=[Depends(authenticated)],
    )
    def list_items(
        list_id: str,
        sort: Literal[
            "position", "title", "year", "rating", "user_rating", "date_added"
        ] = "position",
        order: Literal["asc", "desc"] = "asc",
        limit: int = Query(60, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ):
        return list_error(
            lambda: store.list_page(list_id, sort=sort, order=order, limit=limit, offset=offset)
        )

    @app.post(
        API + "/lists/{list_id}/items",
        status_code=204,
        tags=["Lists"],
        dependencies=[Depends(writable)],
    )
    def add_list_item(list_id: str, data: ListItemInput, background_tasks: BackgroundTasks):
        list_error(lambda: store.change_list_item(list_id, data.media_id))
        background_tasks.add_task(thumbnails.warm, list_id)

    @app.delete(
        API + "/lists/{list_id}/items/{media_id}",
        status_code=204,
        tags=["Lists"],
        dependencies=[Depends(writable)],
    )
    def remove_list_item(list_id: str, media_id: str, background_tasks: BackgroundTasks):
        list_error(lambda: store.change_list_item(list_id, media_id, remove=True))
        background_tasks.add_task(thumbnails.warm, list_id)

    @app.put(
        API + "/lists/{list_id}/order",
        status_code=204,
        tags=["Lists"],
        dependencies=[Depends(writable)],
    )
    def order_list(list_id: str, data: ListOrderInput, background_tasks: BackgroundTasks):
        list_error(lambda: store.reorder_list(list_id, data.media_ids, data.revision))
        background_tasks.add_task(thumbnails.warm, list_id)

    @app.get(
        API + "/lists/{list_id}/thumbnails/{revision}.jpg",
        tags=["Lists"],
        dependencies=[Depends(authenticated)],
        responses={200: {"content": {"image/jpeg": {}}}},
    )
    async def list_thumbnail(list_id: str, revision: int):
        # Revisioned immutable URL prevents obsolete covers in browsers and
        # iOS NSCache. Old URLs are not recomputed after a list changes.
        source = await asyncio.to_thread(store.list_thumbnail_input, list_id)
        if source is None or source["revision"] != revision:
            raise HTTPException(404, "Unknown list thumbnail revision.")
        data = await thumbnails.ensure(list_id, revision)
        if data is None:
            raise HTTPException(404, "List thumbnail changed while loading.")
        return Response(data, media_type="image/jpeg")

    @app.get(
        API + "/media/{media_id}/artwork/{aspect}",
        tags=["Catalogue"],
        dependencies=[Depends(authenticated)],
        responses={200: {"content": {"image/jpeg": {}, "image/png": {}, "image/webp": {}}}},
    )
    async def media_artwork(media_id: str, aspect: Literal["poster", "fanart"]):
        candidates = await asyncio.to_thread(store.artwork, media_id, aspect)
        try:
            data, content_type = await app.state.artwork.get(candidates)
            return Response(data, media_type=content_type)
        except ArtworkError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post(
        API + "/connections/mariadb/databases",
        response_model=MariaDBDatabasesResult,
        tags=["Connections"],
        dependencies=[Depends(writable)],
    )
    async def discover_mariadb_databases(data: MariaDBConnectionInput):
        """List only visible Kodi video schemas; never persist entered credentials."""
        config = unsecret(data)["config"]
        if data.source_id:
            existing = await asyncio.to_thread(store.source, data.source_id, private=True)
            if not existing or existing["type"] != "kodi":
                raise HTTPException(404, "Unknown Kodi MariaDB source.")
            if config.get("password") is None:
                config["password"] = existing["config"].get("password", "")
        config["password"] = config.get("password") or ""
        try:
            return await asyncio.to_thread(KodiConnector(config).discover_databases)
        except ConnectorError as exc:
            raise HTTPException(502, str(exc)) from exc

    @app.post(
        API + "/connections/mariadb/test",
        response_model=ConnectionResult,
        tags=["Connections"],
        dependencies=[Depends(writable)],
    )
    async def test_mariadb(data: MariaDBConnectionInput):
        config = unsecret(data)["config"]
        if data.source_id:
            existing = await asyncio.to_thread(store.source, data.source_id, private=True)
            if not existing or existing["type"] != "kodi":
                raise HTTPException(404, "Unknown Kodi MariaDB source.")
            if config.get("password") is None:
                config["password"] = existing["config"].get("password", "")
        config["password"] = config.get("password") or ""
        try:
            result = await asyncio.to_thread(KodiConnector(config).probe)
        except ConnectorError as exc:
            raise HTTPException(502, str(exc)) from exc
        return {
            "kind": "mariadb",
            "message": f"Connexion MariaDB réussie : {result['database']}, accès en lecture vérifié.",
            **result,
        }

    @app.post(
        API + "/connections/kodi/test",
        response_model=ConnectionResult,
        tags=["Connections"],
        dependencies=[Depends(writable)],
    )
    async def test_kodi(data: KodiConnectionInput):
        config = unsecret(data)
        if data.player_id:
            existing = await asyncio.to_thread(store.player, data.player_id)
            if not existing:
                raise HTTPException(404, "Unknown Kodi player.")
            if config.get("password") is None:
                config["password"] = existing["config"].get("password", "")
        config["password"] = config.get("password") or ""
        try:
            result = await probe_player(config)
        except ConnectorError as exc:
            raise HTTPException(502, str(exc)) from exc
        return {
            "kind": "kodi",
            "message": "Connexion Kodi réussie : API HTTP et contrôle de lecture vérifiés.",
            **result,
        }

    @app.get(
        API + "/sources",
        response_model=list[SourceView],
        tags=["Sources"],
        dependencies=[Depends(authenticated)],
    )
    def sources():
        return store.sources()

    @app.post(
        API + "/connections/tmdb/test",
        response_model=ConnectionResult,
        tags=["Connections"],
        dependencies=[Depends(writable)],
    )
    async def test_tmdb():
        if not settings.tmdb_api_token.strip():
            raise HTTPException(
                422, "Renseignez TMDB_API_TOKEN dans .env puis recréez le conteneur."
            )
        try:
            await probe_tmdb(settings.tmdb_api_token)
        except ConnectorError as exc:
            raise HTTPException(502, str(exc)) from exc
        return {
            "kind": "tmdb",
            "message": "Connexion TMDB réussie : accès à l'API vérifié. Ouvrez une fiche pour récupérer le casting et les bandes-annonces disponibles.",
        }

    @app.post(
        API + "/sources",
        response_model=SourceView,
        status_code=201,
        tags=["Sources"],
        dependencies=[Depends(writable)],
    )
    def create_source(data: SourceInput):
        # The primary source can be Kodi JSON-RPC only. MariaDB is optional.
        if data.type == "kodi" and bool(data.config.host) != bool(data.config.username):
            raise HTTPException(422, "MariaDB fallback requires both host and username.")
        return store.save_source(unsecret(data))

    @app.put(
        API + "/sources/{source_id}",
        response_model=SourceView,
        tags=["Sources"],
        dependencies=[Depends(writable)],
    )
    def update_source(source_id: str, data: SourceInput):
        if source_id in sync.active:
            raise HTTPException(409, "Wait for this source's current job before editing it.")
        if not store.source(source_id):
            raise HTTPException(404, "Unknown source.")
        if data.type == "kodi" and bool(data.config.host) != bool(data.config.username):
            raise HTTPException(422, "MariaDB fallback requires both host and username.")
        try:
            return store.save_source(unsecret(data), source_id)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.delete(
        API + "/sources/{source_id}",
        status_code=204,
        tags=["Sources"],
        dependencies=[Depends(writable)],
    )
    def delete_source(source_id: str):
        if source_id in sync.active:
            raise HTTPException(409, "Wait for this source's current job before removing it.")
        if not store.source(source_id):
            raise HTTPException(404, "Unknown source.")
        store.delete_source(source_id)

    @app.post(
        API + "/sources/sync-stale",
        response_model=list[JobView],
        status_code=202,
        tags=["Sources"],
        dependencies=[Depends(authenticated)],
    )
    async def synchronize_stale_on_open():
        """Refresh MariaDB snapshots on client open if older than 24 hours.

        This endpoint never invokes Kodi's video scan; the server alone
        decides whether any enabled SQL source is stale.
        """
        return await sync.sync_stale_on_open()

    @app.post(
        API + "/sources/{source_id}/sync",
        response_model=JobView,
        status_code=202,
        tags=["Sources"],
        dependencies=[Depends(writable)],
    )
    async def synchronize(source_id: str):
        if not await asyncio.to_thread(store.source, source_id):
            raise HTTPException(404, "Unknown source.")
        try:
            return await sync.submit(source_id)
        except BusyError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post(
        API + "/sources/scan-all",
        response_model=list[JobView],
        status_code=202,
        tags=["Sources"],
        dependencies=[Depends(writable)],
    )
    async def scan_all_kodi_players():
        """Scan every configured remote Kodi instance, once per SQL source."""
        players = await asyncio.to_thread(store.players)
        sources = await asyncio.to_thread(store.sources)
        available = {s["id"] for s in sources if s["type"] == "kodi" and s["enabled"]}
        grouped: dict[str, list[str]] = {}
        for player in players:
            if player["source_id"] in available:
                grouped.setdefault(player["source_id"], []).append(player["id"])
        if not grouped:
            raise HTTPException(422, "Configure at least one Kodi instance on an enabled source.")
        if any(sid in sync.active for sid in grouped):
            raise HTTPException(409, "A Kodi source is already synchronizing. Retry later.")
        jobs = []
        for source_id, ids in grouped.items():
            private_players = [
                await asyncio.to_thread(store.player, player_id) for player_id in ids
            ]
            try:
                jobs.append(await sync.submit_scans(source_id, private_players))
            except BusyError as exc:
                raise HTTPException(409, str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
        return jobs

    @app.post(
        API + "/sources/{source_id}/scan",
        response_model=JobView,
        status_code=202,
        tags=["Sources"],
        dependencies=[Depends(writable)],
    )
    async def scan_kodi_source(source_id: str, player_id: str | None = None):
        source = await asyncio.to_thread(store.source, source_id)
        if not source:
            raise HTTPException(404, "Unknown source.")
        if source["type"] != "kodi":
            raise HTTPException(422, "Only Kodi sources can be scanned.")
        if player_id:
            player = await asyncio.to_thread(store.player, player_id)
            if not player or player["source_id"] != source_id:
                raise HTTPException(422, "Kodi player is not associated with the selected source.")
        else:
            all_players = await asyncio.to_thread(store.players)
            chosen = next((p for p in all_players if p["source_id"] == source_id), None)
            if not chosen:
                raise HTTPException(422, "Configure a Kodi JSON-RPC player for this source.")
            player = await asyncio.to_thread(store.player, chosen["id"])
        try:
            return await sync.submit_scan(source_id, player)
        except BusyError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get(
        API + "/sources/{source_id}/writeback",
        tags=["Sources"],
        dependencies=[Depends(authenticated)],
    )
    def source_writeback_history(source_id: str, limit: int = Query(default=50, ge=1, le=200)):
        if not store.source(source_id):
            raise HTTPException(404, "Unknown source.")
        return store.writeback_history(source_id=source_id, limit=limit)

    @app.post(
        API + "/imports/clz",
        response_model=JobView,
        status_code=202,
        tags=["Imports"],
        dependencies=[Depends(writable)],
    )
    @app.post(
        API + "/imports/mymovies",
        response_model=JobView,
        status_code=202,
        tags=["Imports"],
        dependencies=[Depends(writable)],
    )
    @app.post(
        API + "/imports/filmotech",
        response_model=JobView,
        status_code=202,
        tags=["Imports"],
        dependencies=[Depends(writable)],
    )
    async def import_collection(
        request: Request,
        file: UploadFile = File(...),
        source_id: str = Form(...),
    ):
        collection_type = request.url.path.rsplit("/", 1)[-1]
        source = await asyncio.to_thread(store.source, source_id)
        if not source:
            raise HTTPException(404, "Unknown source.")
        if source["type"] != collection_type:
            raise HTTPException(422, "The export type must match the selected collection source.")
        chunks, length = [], 0
        while chunk := await file.read(65536):
            length += len(chunk)
            if length > settings.max_import_bytes:
                await file.close()
                raise HTTPException(413, "The XML export exceeds the 20 MiB import limit.")
            chunks.append(chunk)
        await file.close()
        if not length:
            raise HTTPException(422, "Upload a non-empty XML file.")
        try:
            return await sync.submit(source_id, b"".join(chunks))
        except BusyError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get(
        API + "/jobs",
        response_model=list[JobView],
        tags=["Imports"],
        dependencies=[Depends(authenticated)],
    )
    def recent_jobs():
        """Expose background and manual jobs to native/web progress indicators."""
        return store.jobs()

    @app.get(
        API + "/jobs/{job_id}",
        response_model=JobView,
        tags=["Imports"],
        dependencies=[Depends(authenticated)],
    )
    def job(job_id: str):
        if value := store.job(job_id):
            return value
        raise HTTPException(404, "Unknown import job.")

    @app.get(
        API + "/players",
        response_model=list[PlayerView],
        tags=["Players"],
        dependencies=[Depends(authenticated)],
    )
    def players():
        return store.players()

    def validate_player(data: PlayerInput):
        source = store.source(data.source_id)
        if not source or source["type"] != "kodi":
            raise HTTPException(
                422, "Associate this Kodi instance with an existing Kodi library source."
            )
        if any(c in data.host for c in ("/", "@", "?", "#")):
            raise HTTPException(422, "Enter a Kodi hostname, not a URL.")

    @app.post(
        API + "/players",
        response_model=PlayerView,
        status_code=201,
        tags=["Players"],
        dependencies=[Depends(writable)],
    )
    def create_player(data: PlayerInput):
        validate_player(data)
        return store.save_player(unsecret(data))

    @app.put(
        API + "/players/{player_id}",
        response_model=PlayerView,
        tags=["Players"],
        dependencies=[Depends(writable)],
    )
    def update_player(player_id: str, data: PlayerInput):
        if not store.player(player_id):
            raise HTTPException(404, "Unknown player.")
        validate_player(data)
        return store.save_player(unsecret(data), player_id)

    @app.delete(
        API + "/players/{player_id}",
        status_code=204,
        tags=["Players"],
        dependencies=[Depends(writable)],
    )
    def delete_player(player_id: str):
        if not store.player(player_id):
            raise HTTPException(404, "Unknown player.")
        store.delete_player(player_id)

    @app.post(
        API + "/players/{player_id}/play",
        response_model=PlayResult,
        tags=["Players"],
        dependencies=[Depends(writable)],
    )
    async def play_media(player_id: str, data: PlayInput):
        player = await asyncio.to_thread(store.player, player_id)
        detail = await asyncio.to_thread(store.detail, data.media_id)
        if not player or not detail:
            raise HTTPException(404, "Unknown player or media.")
        try:
            return await play(player, detail, data.edition_id, data.resume)
        except ConnectorError as exc:
            raise HTTPException(502, str(exc)) from exc

    # Remote-control API and authenticated Kodi notification relay.
    from .remote import router as remote_router

    app.include_router(remote_router)

    return app
