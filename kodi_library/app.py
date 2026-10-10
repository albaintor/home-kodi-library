"""Versioned REST API consumed identically by the web UI and native clients."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import (
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

from . import __version__
from .artwork import Artwork, ArtworkError
from .auth import COOKIE, Auth, Principal, authenticated, secure_equal, writable
from .connectors.base import ConnectorError
from .connectors.kodi import KodiConnector
from .models import (
    CatalogueStatus,
    ConnectionResult,
    ErrorResponse,
    Facets,
    JobView,
    Kind,
    KodiConnectionInput,
    MariaDBConnectionInput,
    MediaDetail,
    MediaPage,
    PlayerInput,
    PlayerView,
    PlayInput,
    PlayResult,
    SessionInput,
    SessionView,
    SortField,
    SourceInput,
    SourceView,
    WatchStatus,
)
from .players import play
from .players import probe as probe_player
from .settings import Settings
from .store import Store
from .sync import BusyError, SyncManager

API = "/api/v1"
STATIC = Path(__file__).parent / "static"


def unsecret(model) -> dict:
    value = model.model_dump()
    config = value.get("config", value)
    if config.get("password") is not None:
        config["password"] = config["password"].get_secret_value()
    return value


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.validate()
    store = Store(settings.data_dir / "catalogue.db")
    sync = SyncManager(store, settings)

    @asynccontextmanager
    async def lifespan(app):
        await asyncio.to_thread(store.initialize)
        sync.start()
        yield
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
    app.state.store, app.state.auth, app.state.sync = store, Auth(settings), sync
    app.state.artwork = Artwork(settings)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "X-CSRF-Token"],
        )

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        # SAMEORIGIN preserves embedding under Home Assistant ingress.
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Cache-Control"] = "private, no-store"
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

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/docs", include_in_schema=False, dependencies=[Depends(authenticated)])
    def docs():
        return get_swagger_ui_html(openapi_url="api/v1/openapi.json", title="Kodi Library API v1")

    @app.get(API + "/openapi.json", include_in_schema=False, dependencies=[Depends(authenticated)])
    def schema():
        return app.openapi()

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
        return {"role": principal.role, "csrf_token": principal.csrf}

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
        return {"version": __version__, "api_version": "v1", **store.status()}

    @app.get(
        API + "/media",
        response_model=MediaPage,
        tags=["Catalogue"],
        dependencies=[Depends(authenticated)],
    )
    def list_media(
        q: str = Query("", max_length=300),
        kind: Kind | None = None,
        watch_status: WatchStatus | None = None,
        genre: str | None = Query(None, max_length=120),
        format: str | None = Query(None, max_length=120),
        collection: str | None = Query(None, max_length=200),
        source_type: Literal["kodi", "clz"] | None = None,
        source_id: str | None = None,
        parent_id: str | None = None,
        year: int | None = Query(None, ge=1800, le=2200),
        recent_days: int | None = Query(None, ge=1, le=3650),
        sort: SortField = SortField.DATE_ADDED,
        order: Literal["asc", "desc"] = "desc",
        limit: int = Query(60, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ):
        return store.media(
            q=q,
            kind=kind,
            watch_status=watch_status,
            genre=genre,
            format=format,
            collection=collection,
            source_type=source_type,
            source_id=source_id,
            parent_id=parent_id,
            year=year,
            recent_days=recent_days,
            sort=sort,
            order=order,
            limit=limit,
            offset=offset,
        )

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
            return value
        raise HTTPException(404, "Unknown media.")

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
        API + "/sources",
        response_model=SourceView,
        status_code=201,
        tags=["Sources"],
        dependencies=[Depends(writable)],
    )
    def create_source(data: SourceInput):
        if data.type == "kodi" and (not data.config.host or not data.config.username):
            raise HTTPException(422, "A Kodi source requires a MariaDB host and username.")
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
        API + "/imports/clz",
        response_model=JobView,
        status_code=202,
        tags=["Imports"],
        dependencies=[Depends(writable)],
    )
    async def import_clz(file: UploadFile = File(...), source_id: str = Form(...)):
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
                422, "Associate this Kodi player with an existing Kodi MariaDB source."
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

    return app
