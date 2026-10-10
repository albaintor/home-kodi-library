import asyncio
import json
from unittest.mock import patch

import httpx
import pytest

from kodi_library.artwork import Artwork, ArtworkError
from kodi_library.connectors.base import ConnectorError
from kodi_library.players import play
from kodi_library.settings import Settings


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
    with patch("kodi_library.artwork.aiohttp.ClientSession") as session:
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
    with patch("kodi_library.players.httpx.AsyncClient", side_effect=client):
        assert asyncio.run(play(player, detail, "version", True))["status"] == "started"
        assert requests[-1]["params"]["item"] == {"file": "smb://nas/films/version.mkv"}
        assert requests[-1]["params"]["options"]["resume"] is True
        remote["imdb"] = "tt9876543"
        with pytest.raises(ConnectorError, match="different library"):
            asyncio.run(play(player, detail, None, False))
        assert requests[-1]["method"] == "VideoLibrary.GetMovieDetails"
