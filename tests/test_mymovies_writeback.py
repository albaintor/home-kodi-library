"""The licensed My Movies Remote Data API is opt-in and only supports watch flags."""

from pathlib import Path
from unittest.mock import patch

import httpx

from home_media_catalog.connectors.collection_xml import parse_collection


def source_with_export(client, admin, *, enabled=True, api_key="very-private-key"):
    response = client.post(
        "/api/v1/sources",
        headers=admin,
        json={
            "name": "My Movies export",
            "type": "mymovies",
            "config": {
                "remote_host": "mymovies.local",
                "remote_port": 51414,
                "remote_api_key": api_key,
                "remote_enabled": enabled,
            },
        },
    )
    assert response.status_code == 201, response.text
    sid = response.json()["id"]
    xml = (Path(__file__).parent / "fixtures" / "mymovies.xml").read_bytes()
    client.app.state.store.apply_snapshot(sid, parse_collection(xml, "mymovies"))
    media = client.get("/api/v1/media", headers=admin).json()["items"][0]
    return sid, media["id"]


def fake_remote(handler):
    actual = httpx.AsyncClient

    def respond(request):
        return handler(request)

    return patch(
        "home_media_catalog.mymovies_writeback.httpx.AsyncClient",
        side_effect=lambda **kwargs: actual(transport=httpx.MockTransport(respond), **kwargs),
    )


def remote_reply(request, *, matching=True):
    command = request.url.params["command"]
    if command == "GetTitle":
        title = "Le Voyage" if matching else "A Different Movie"
        return httpx.Response(
            200,
            content=(
                '<Response status="ok"><Title><ID>42</ID><LocalTitle>'
                + title
                + "</LocalTitle><Barcode>1234567890123</Barcode></Title></Response>"
            ),
        )
    return httpx.Response(200, content='<Response status="ok"></Response>')


def test_remote_my_movies_watched_and_unwatched(client, admin):
    sid, mid = source_with_export(client, admin)
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.params["apikey"] == "very-private-key"
        assert request.url.params["id"] == "42"
        return remote_reply(request)

    with fake_remote(handler):
        for watched, command in (
            (False, "ClearElementWatched"),
            (True, "ElementWatched"),
        ):
            response = client.patch(
                f"/api/v1/media/{mid}/personal", headers=admin, json={"watched": watched}
            )
            assert response.status_code == 200, response.text
            assert response.json()["writeback"][0]["status"] == "updated"
            assert response.json()["writeback"][0]["source_id"] == sid
            assert requests[-1].url.params["command"] == command
    assert [request.url.params["command"] for request in requests] == [
        "GetTitle",
        "ClearElementWatched",
        "GetTitle",
        "ElementWatched",
    ]


def test_remote_my_movies_is_disabled_by_default_and_rating_is_local_only(client, admin):
    sid, mid = source_with_export(client, admin, enabled=False)
    assert client.app.state.store.source(sid, private=True)["config"]["remote_enabled"] is False
    response = client.patch(
        f"/api/v1/media/{mid}/personal",
        headers=admin,
        json={"watched": False, "user_rating": 9},
    )
    assert response.status_code == 200
    assert response.json()["writeback"] == []
    assert response.json()["user_rating"] == 9

    with fake_remote(lambda req: (_ for _ in ()).throw(AssertionError("Unexpected remote call"))):
        response = client.patch(
            f"/api/v1/media/{mid}/personal", headers=admin, json={"user_rating": 7.5}
        )
    assert response.status_code == 200
    assert response.json()["writeback"] == []


def test_remote_my_movies_secret_is_redacted_and_preserved(client, admin):
    sid, _mid = source_with_export(client, admin)
    response = client.get("/api/v1/sources", headers=admin)
    assert response.status_code == 200
    assert "very-private-key" not in response.text
    assert response.json()[0]["config"]["has_remote_api_key"] is True
    update = client.put(
        f"/api/v1/sources/{sid}",
        headers=admin,
        json={
            "name": "My Movies export",
            "type": "mymovies",
            "config": {
                "remote_host": "nas.local",
                "remote_enabled": True,
                "remote_api_key": None,
            },
        },
    )
    assert update.status_code == 200
    assert "very-private-key" not in update.text
    stored = client.app.state.store.source(sid, private=True)["config"]
    assert stored["remote_api_key"] == "very-private-key"
    assert stored["remote_host"] == "nas.local"


def test_remote_my_movies_never_updates_different_title(client, admin):
    _sid, mid = source_with_export(client, admin)
    requests = []

    def handler(request):
        requests.append(request.url.params["command"])
        return remote_reply(request, matching=False)

    with fake_remote(handler):
        response = client.patch(
            f"/api/v1/media/{mid}/personal", headers=admin, json={"watched": False}
        )
    assert response.status_code == 200
    assert response.json()["writeback"][0]["status"] == "failed"
    assert response.json()["watch_status"] == "unwatched"
    assert requests == ["GetTitle"]


def test_remote_my_movies_api_errors_dont_expose_api_key(client, admin):
    _sid, mid = source_with_export(client, admin)
    with fake_remote(lambda _request: httpx.Response(403, content="private-key")):
        response = client.patch(
            f"/api/v1/media/{mid}/personal", headers=admin, json={"watched": False}
        )
    assert response.status_code == 200
    assert response.json()["writeback"][0]["status"] == "failed"
    assert "very-private-key" not in response.text


def test_xml_export_has_stable_remote_id(client, admin):
    _sid, mid = source_with_export(client, admin)
    edition = client.get(f"/api/v1/media/{mid}", headers=admin).json()["editions"][0]
    assert edition["metadata"]["mymovies_remote_title_id"] == "42"
    assert edition["metadata"]["mymovies_title"] == "Le Voyage"
