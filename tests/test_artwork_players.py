import asyncio
import json
from unittest.mock import patch

import httpx
import pytest

from home_media_catalog.artwork import Artwork, ArtworkError
from home_media_catalog.connectors.base import ConnectorError
from home_media_catalog.players import play
from home_media_catalog.settings import Settings


def test_artwork_mount_and_path_containment(tmp_path):
    root = tmp_path / "posters"
    root.mkdir()
    data = b"\x89PNG\r\n\x1a\n" + b"content"
    (root / "cover.png").write_bytes(data)
    (tmp_path / "private.png").write_bytes(data)
    art = Artwork(
        Settings(data_dir=tmp_path / "data", artwork_mounts={"smb://nas/posters/": str(root)})
    )
    assert asyncio.run(art.get(["smb://nas/posters/cover.png"])) == (data, "image/png")
    with pytest.raises(ArtworkError):
        asyncio.run(art.get(["smb://nas/posters/%2e%2e/private.png"]))
    with pytest.raises(ArtworkError):
        art._image_type(b"<svg>active content</svg>")


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/a.jpg",
        "http://192.168.1.1/a.jpg",
        "http://[::1]/a.jpg",
        "file:///etc/passwd",
        "https://user:password@example.org/a.jpg",
    ],
)
def test_artwork_rejects_private_and_credential_urls(tmp_path, url):
    art = Artwork(Settings(data_dir=tmp_path))
    with patch("home_media_catalog.artwork.aiohttp.ClientSession") as session:
        with pytest.raises(ArtworkError):
            asyncio.run(art.get([url]))
        session.assert_not_called()


def test_play_uses_selected_file_and_rejects_wrong_library():
    requests = []
    remote = {"imdb": "tt1234567"}

    def handle(request):
        value = json.loads(request.content)
        requests.append(value)
        result = (
            {"moviedetails": {"uniqueid": remote}}
            if value["method"] == "VideoLibrary.GetMovieDetails"
            else "OK"
        )
        return httpx.Response(200, json={"result": result})

    client_class = httpx.AsyncClient

    def client(**kwargs):
        return client_class(transport=httpx.MockTransport(handle), **kwargs)

    player = {"id": "player", "source_id": "source", "config": {"host": "kodi", "port": 8080}}
    detail = {
        "id": "film",
        "kind": "movie",
        "editions": [
            {
                "id": "version",
                "source_id": "source",
                "source_type": "kodi",
                "identifiers": {"imdb": "tt1234567"},
                "metadata": {
                    "kodi_id": 42,
                    "is_default": False,
                    "file": "smb://nas/films/version.mkv",
                },
            }
        ],
    }
    with patch("home_media_catalog.players.httpx.AsyncClient", side_effect=client):
        assert asyncio.run(play(player, detail, "version", True))["status"] == "started"
        assert requests[-1]["params"]["item"] == {"file": "smb://nas/films/version.mkv"}
        assert requests[-1]["params"]["options"]["resume"] is True
        remote["imdb"] = "tt9876543"
        with pytest.raises(ConnectorError, match="different library"):
            asyncio.run(play(player, detail, None, False))
        assert requests[-1]["method"] == "VideoLibrary.GetMovieDetails"


@pytest.mark.parametrize("kind,id_name", [("movie", "movieid"), ("episode", "episodeid")])
def test_explicit_default_file_plays_without_a_version_picker(kind, id_name):
    calls = []

    def handle(request):
        value = json.loads(request.content)
        calls.append(value)
        result = (
            {
                "moviedetails" if kind == "movie" else "episodedetails": {
                    "uniqueid": {"imdb": "tt1234567"}
                }
            }
            if value["method"].startswith("VideoLibrary.")
            else "OK"
        )
        return httpx.Response(200, json={"result": result})

    original = httpx.AsyncClient
    player = {"id": "p", "source_id": "s", "config": {"host": "kodi", "port": 8080}}
    detail = {
        "id": "m",
        "kind": kind,
        "editions": [
            {
                "id": "e",
                "source_id": "s",
                "source_type": "kodi",
                "identifiers": {"imdb": "tt1234567"},
                "metadata": {
                    "kodi_id": 42,
                    "is_default": True,
                    "file": "smb://nas/media/selected.mkv",
                },
            }
        ],
    }
    with patch(
        "home_media_catalog.players.httpx.AsyncClient",
        side_effect=lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs),
    ):
        asyncio.run(play(player, detail, "e", False))
        assert calls[-1]["params"] == {
            "item": {"file": "smb://nas/media/selected.mkv"},
            "options": {"resume": False},
        }
        asyncio.run(play(player, detail, None, True))
        assert calls[-1]["params"]["item"] == {id_name: 42}
        detail["editions"][0]["metadata"].pop("kodi_id")
        with pytest.raises(ConnectorError, match="Synchronisez"):
            asyncio.run(play(player, detail, "e", True))
