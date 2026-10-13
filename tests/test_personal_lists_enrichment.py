from unittest.mock import patch

import httpx
import pytest
from conftest import READ

from kodi_library.connectors.base import ConnectorError, Record
from kodi_library.connectors.clz import parse_clz
from kodi_library.enrichment import enrich_tmdb
from kodi_library.people import youtube_url
from kodi_library.store import Store


def seed(store):
    source = store.save_source({"name": "Kodi", "type": "kodi"})
    records = [
        Record(
            str(i), "movie", name, watched=True, user_rating=6, identifiers={"imdb": f"tt123456{i}"}
        )
        for i, name in enumerate(["Zulu", "Alpha", "Bravo"])
    ]
    store.apply_snapshot(source["id"], records)
    ids = {i["title"]: i["id"] for i in store.media()["items"]}
    return source["id"], records, ids


def test_personal_overrides_survive_sync_restart_and_reset(client, admin):
    store = client.app.state.store
    sid, records, ids = seed(store)
    mid = ids["Zulu"]
    path = f"/api/v1/media/{mid}/personal"
    assert (
        client.patch(
            path, headers={"Authorization": f"Bearer {READ}"}, json={"watched": False}
        ).status_code
        == 403
    )
    assert client.patch(path, headers=admin, json={"user_rating": 11}).status_code == 422
    result = client.patch(path, headers=admin, json={"user_rating": 0, "watched": False})
    assert result.status_code == 200
    assert result.json()["watch_status"] == "unwatched" and result.json()["user_rating"] == 0
    assert result.json()["editions"][0]["watched"] is True
    assert client.get("/api/v1/media?watch_status=unwatched", headers=admin).json()["total"] == 1
    store.apply_snapshot(sid, records)
    reopened = Store(store.path)
    reopened.initialize()
    assert reopened.detail(mid)["user_rating"] == 0
    assert reopened.detail(mid)["watch_status"] == "unwatched"
    assert client.patch(path, headers=admin, json={"watched": None}).json()["user_rating"] == 0
    result = client.patch(path, headers=admin, json={"user_rating": None, "watched": None}).json()
    assert result["watch_status"] == "watched" and result["user_rating"] == 6
    assert (
        client.patch(
            "/api/v1/media/absent/personal", headers=admin, json={"watched": True}
        ).status_code
        == 404
    )


def test_list_api_order_sort_concurrency_and_persistence(client, admin):
    store = client.app.state.store
    sid, records, ids = seed(store)
    created = client.post("/api/v1/lists", headers=admin, json={"name": " À voir "})
    assert created.status_code == 201 and created.json()["name"] == "À voir"
    lid = created.json()["id"]
    base = f"/api/v1/lists/{lid}"
    for name in ["Zulu", "Alpha", "Bravo"]:
        assert (
            client.post(base + "/items", headers=admin, json={"media_id": ids[name]}).status_code
            == 204
        )
    client.post(base + "/items", headers=admin, json={"media_id": ids["Zulu"]})
    assert client.get("/api/v1/lists", headers=admin).json()[0]["item_count"] == 3
    page = client.get(base + "/items?limit=2", headers=admin).json()
    assert [m["title"] for m in page["items"]] == ["Zulu", "Alpha"] and page["has_more"]
    revision = page["revision"]
    order = [ids["Bravo"], ids["Zulu"], ids["Alpha"]]
    assert (
        client.put(
            base + "/order", headers=admin, json={"media_ids": order, "revision": revision}
        ).status_code
        == 204
    )
    assert (
        client.put(
            base + "/order", headers=admin, json={"media_ids": order, "revision": revision}
        ).status_code
        == 409
    )
    assert (
        client.put(
            base + "/order", headers=admin, json={"media_ids": order[:-1], "revision": revision + 1}
        ).status_code
        == 422
    )
    assert (
        client.get(base + "/items?sort=title", headers=admin).json()["items"][0]["title"] == "Alpha"
    )
    assert client.get(base + "/items", headers=admin).json()["items"][0]["title"] == "Bravo"
    assert client.get(f"/api/v1/media/{ids['Zulu']}", headers=admin).json()["lists"][0]["id"] == lid
    store.apply_snapshot(sid, records)
    store.initialize()
    assert store.list_page(lid)["items"][0]["title"] == "Bravo"
    assert (
        client.put(
            base, headers=admin, json={"name": "Mes films", "description": "Priorités"}
        ).status_code
        == 200
    )
    assert (
        client.post(base + "/items", headers=admin, json={"media_id": "absent"}).status_code == 404
    )
    assert client.post("/api/v1/lists", headers=admin, json={"name": "   "}).status_code == 422
    client.delete(base + f"/items/{ids['Alpha']}", headers=admin)
    assert store.list_page(lid)["total"] == 2
    # Removed catalogue members cascade out of lists and change the revision.
    prior_revision = store.list_page(lid)["revision"]
    store.apply_snapshot(sid, [records[0]])
    assert store.list_page(lid)["total"] == 1
    assert store.list_page(lid)["revision"] > prior_revision
    assert client.delete(base, headers=admin).status_code == 204
    assert store.media()["total"] == 1
    assert client.get(base + "/items", headers=admin).status_code == 404


def test_page_and_detail_include_real_sources_without_repeated_editions(client, admin):
    store = client.app.state.store
    kodi_a = store.save_source({"name": "Kodi Films", "type": "kodi"})
    kodi_b = store.save_source({"name": "Kodi Archives", "type": "kodi"})
    physical = store.save_source({"name": "Ma collection", "type": "clz"})
    sources = [kodi_a, kodi_b, physical]
    for i, source in enumerate(sources):
        store.apply_snapshot(
            source["id"],
            [
                Record(
                    f"movie:{i}:1",
                    "movie",
                    "Film commun",
                    identifiers={"imdb": "tt7777777"},
                ),
                Record(
                    f"movie:{i}:2",
                    "movie",
                    "Film commun",
                    identifiers={"imdb": "tt7777777"},
                ),
            ],
        )
    page = client.get("/api/v1/media", headers=admin).json()
    assert page["total"] == 1
    item = page["items"][0]
    expected = {
        ("Kodi Films", "kodi"),
        ("Kodi Archives", "kodi"),
        ("Ma collection", "clz"),
    }
    assert {(s["name"], s["type"]) for s in item["media_sources"]} == expected
    assert len(item["media_sources"]) == 3
    assert {s["id"] for s in item["media_sources"]} == {s["id"] for s in sources}

    media_id = item["id"]
    detail = client.get(f"/api/v1/media/{media_id}", headers=admin).json()
    assert detail["media_sources"] == item["media_sources"]

    created = client.post("/api/v1/lists", headers=admin, json={"name": "À revoir"}).json()
    client.post(
        f"/api/v1/lists/{created['id']}/items",
        headers=admin,
        json={"media_id": media_id},
    )
    listed = client.get(f"/api/v1/lists/{created['id']}/items", headers=admin).json()
    assert listed["items"][0]["media_sources"] == item["media_sources"]

    store.delete_source(kodi_a["id"])
    after = client.get(f"/api/v1/lists/{created['id']}/items", headers=admin).json()
    assert {s["name"] for s in after["items"][0]["media_sources"]} == {
        "Kodi Archives",
        "Ma collection",
    }


def test_search_mode_api_and_configured_source_counts(client, admin):
    store = client.app.state.store
    first = store.save_source({"name": "Kodi NAS", "type": "kodi"})
    second = store.save_source({"name": "Collection Blu-ray", "type": "clz"})
    store.apply_snapshot(
        first["id"],
        [Record("film-1", "movie", "Film recherché", plot="Un voyage sur Mars")],
    )
    store.apply_snapshot(
        second["id"],
        [Record("disc-1", "movie", "Autre film", plot="Voyage lunaire")],
    )
    response = client.get("/api/v1/media?q=Mars", headers=admin).json()
    assert response["total"] == 0
    response = client.get("/api/v1/media?q=Mars&deep_search=true", headers=admin).json()
    assert response["total"] == 1
    assert response["items"][0]["media_sources"][0]["type_count"] == 1
    second_kodi = store.save_source({"name": "Kodi secondaire", "type": "kodi"})
    response = client.get("/api/v1/media?q=recherché", headers=admin).json()
    assert response["items"][0]["media_sources"][0]["type_count"] == 2
    store.delete_source(second_kodi["id"])


def test_schema_v1_is_migrated_without_losing_catalogue(store):
    _, _, ids = seed(store)
    with store.connect() as db:
        for table in ["list_items", "lists", "personal", "enrichment"]:
            db.execute("DROP TABLE " + table)
        db.execute("PRAGMA user_version=1")
    store.initialize()
    assert store.detail(ids["Zulu"])["title"] == "Zulu"
    assert store.lists() == []
    with store.connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2


def test_clz_credits_and_trailers_are_normalized():
    xml = b"""<collectorz><data><movieinfo><movielist><movie><hash>x</hash><title>Example</title><cast><actor><displayname>Alex Example</displayname><role>Pilot</role><imdbnr>nm1234567</imdbnr><thumbnail>https://example.org/portrait.jpg</thumbnail></actor></cast><crew><member><name>Camille Example</name><job>Original Music Composer</job></member></crew><links><link><url>http://www.youtube.com/watch?v=C6PbWhWGUrY</url></link><link><url>javascript:alert(1)</url></link></links></movie></movielist></movieinfo></data></collectorz>"""
    record = parse_clz(xml)[0]
    assert record.metadata["people"][0]["photos"] == ["https://example.org/portrait.jpg"]
    assert record.metadata["people"][0]["imdb_id"] == "nm1234567"
    assert record.metadata["people"][1]["category"] == "composer"
    assert record.metadata["trailers"] == ["https://www.youtube.com/watch?v=C6PbWhWGUrY"]


@pytest.mark.parametrize(
    "value",
    [
        "javascript:alert(1)",
        "https://youtube.com.evil/watch?v=C6PbWhWGUrY",
        "https://youtube.com/watch?v=bad",
        "https://user:pass@youtube.com/watch?v=C6PbWhWGUrY",
    ],
)
def test_unsafe_trailer_urls_are_rejected(value):
    assert youtube_url(value) is None


def test_kodi_youtube_trailer():
    assert (
        youtube_url("plugin://plugin.video.youtube/play/?video_id=C6PbWhWGUrY")
        == "https://www.youtube.com/watch?v=C6PbWhWGUrY"
    )


def test_tmdb_uses_imdb_identity_and_caches_credits(client, admin):
    store = client.app.state.store
    _, _, ids = seed(store)
    mid = ids["Zulu"]
    calls = []

    def upstream(request):
        calls.append(request)
        if "/find/" in request.url.path:
            return httpx.Response(200, json={"movie_results": [{"id": 42}]})
        return httpx.Response(
            200,
            json={
                "credits": {
                    "cast": [
                        {
                            "id": 7,
                            "name": "Alex Example",
                            "character": "Pilot",
                            "order": 0,
                            "profile_path": "/portrait.jpg",
                        }
                    ],
                    "crew": [
                        {
                            "id": 8,
                            "name": "Camille Example",
                            "job": "Original Music Composer",
                            "profile_path": None,
                        }
                    ],
                },
                "videos": {
                    "results": [
                        {
                            "site": "YouTube",
                            "type": "Trailer",
                            "key": "C6PbWhWGUrY",
                            "iso_639_1": "fr",
                            "official": True,
                        }
                    ]
                },
            },
        )

    original = httpx.AsyncClient

    def session(**kwargs):
        return original(**kwargs, transport=httpx.MockTransport(upstream))

    import asyncio

    with patch("kodi_library.enrichment.httpx.AsyncClient", side_effect=session):
        payload = asyncio.run(enrich_tmdb(store.detail(mid), "test-tmdb-token"))
    assert calls[0].url.path.endswith("/find/tt1234560")
    assert calls[0].headers["authorization"] == "Bearer test-tmdb-token"
    assert payload["people"][1]["category"] == "composer"
    store.save_enrichment(mid, payload)
    result = client.get(f"/api/v1/media/{mid}", headers=admin).json()
    assert result["cast"][0]["links"]["tmdb"] == "https://www.themoviedb.org/person/7"
    assert result["cast"][0]["thumbnail"].endswith("/people/0/artwork")
    assert result["enriched_at"] and len(result["trailers"]) == 1
    assert client.get("/api/v1/media?q=Camille", headers=admin).json()["total"] == 1
    with patch("kodi_library.app.enrich_tmdb") as fetch:
        assert client.post(f"/api/v1/media/{mid}/enrich", headers=admin).status_code == 200
        fetch.assert_not_called()
    store.initialize()
    assert store.detail(mid)["people"][0]["photos"] == [
        "https://image.tmdb.org/t/p/w185/portrait.jpg"
    ]

    async def fetch_art(candidates):
        assert candidates == ["https://image.tmdb.org/t/p/w185/portrait.jpg"]
        return b"fake", "image/png"

    with patch.object(client.app.state.artwork, "get", side_effect=fetch_art):
        assert client.get(result["cast"][0]["thumbnail"]).status_code == 401
        assert client.get(result["cast"][0]["thumbnail"], headers=admin).status_code == 200

    async def refreshed(*args):
        return {"people": [{"name": "New Person", "category": "director"}], "trailers": []}

    with patch("kodi_library.app.enrich_tmdb", side_effect=refreshed):
        updated = client.post(f"/api/v1/media/{mid}/enrich?refresh=true", headers=admin).json()
        assert updated["people"][0]["name"] == "New Person"
    assert (
        client.post(
            f"/api/v1/media/{mid}/enrich?refresh=true", headers={"Authorization": f"Bearer {READ}"}
        ).status_code
        == 403
    )


def test_failed_enrichment_retains_cache(client, admin):
    store = client.app.state.store
    _, _, ids = seed(store)
    mid = ids["Zulu"]
    store.save_enrichment(mid, {"people": [{"name": "Cached Person"}]})

    async def fail(*args):
        raise ConnectorError("Unavailable")

    with patch("kodi_library.app.enrich_tmdb", side_effect=fail):
        assert (
            client.post(f"/api/v1/media/{mid}/enrich?refresh=true", headers=admin).status_code
            == 502
        )
    assert store.detail(mid)["people"][0]["name"] == "Cached Person"
