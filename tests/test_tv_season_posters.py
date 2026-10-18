"""TV season art is cached on the server, never fetched with a mobile TMDB key."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from kodi_library.connectors.base import Record
from kodi_library.enrichment import enrich_tmdb


def test_tv_season_poster_paths_are_cached_and_auth_protected(client, admin):
    store = client.app.state.store
    source = store.save_source({"name": "Kodi", "type": "kodi"})
    store.apply_snapshot(
        source["id"],
        [
            Record("tv:42", "tvshow", "Example Show", identifiers={"tmdb": "42"}),
            Record("ep:1", "episode", "Pilot", parent_key="tv:42", season=1, episode=1),
            Record("ep:2", "episode", "Special", parent_key="tv:42", season=0, episode=1),
        ],
    )
    mid = store.media(kind="tvshow")["items"][0]["id"]
    store.save_enrichment(
        mid,
        {
            "season_posters": {
                "1": "https://image.tmdb.org/t/p/w342/one.jpg",
                "0": "https://image.tmdb.org/t/p/w342/special.jpg",
            },
            "people": [],
        },
    )
    response = client.get(f"/api/v1/media/{mid}/seasons", headers=admin)
    assert response.status_code == 200
    assert response.json()[0]["poster"].endswith("/seasons/0/poster")
    assert response.json()[1]["poster"].endswith("/seasons/1/poster")
    assert client.get(f"/api/v1/media/{mid}/seasons/1/poster").status_code == 401
    with patch.object(client.app.state.artwork, "get", new_callable=AsyncMock) as getter:
        getter.return_value = (b"fake-jpeg-data", "image/jpeg")
        image = client.get(f"/api/v1/media/{mid}/seasons/1/poster", headers=admin)
        assert image.status_code == 200
        getter.assert_awaited_once_with(["https://image.tmdb.org/t/p/w342/one.jpg"])
    store.save_enrichment(mid, {"season_posters": {"1": "https://evil.example/poster.jpg"}})
    assert client.get(f"/api/v1/media/{mid}/seasons/1/poster", headers=admin).status_code == 404


def test_tmdb_tv_enrichment_reads_season_posters_from_series_details():
    @asynccontextmanager
    async def fake_connection(_token):
        async def get(path, **_params):
            assert path == "tv/42"
            return {
                "credits": {},
                "videos": {"results": []},
                "seasons": [
                    {"season_number": 0, "poster_path": "/special.jpg"},
                    {"season_number": 1, "poster_path": "/first.jpg"},
                    {"season_number": 2, "poster_path": None},
                    {"season_number": 3, "poster_path": "javascript:bad"},
                ],
            }

        yield get

    with patch("kodi_library.enrichment.tmdb_connection", fake_connection):
        enriched = asyncio.run(
            enrich_tmdb({"kind": "tvshow", "identifiers": {"tmdb": ["42"]}}, "token")
        )
    assert enriched["season_posters"] == {
        "0": "https://image.tmdb.org/t/p/w342/special.jpg",
        "1": "https://image.tmdb.org/t/p/w342/first.jpg",
    }
