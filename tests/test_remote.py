"""Kodi Remote: URI encoding, opaque credential handling, and safe commands."""
import asyncio
from urllib.parse import quote, quote_plus

import httpx

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from kodi_library import remote
from kodi_library.remote import (RemoteAction, _artwork_sources, _kodi_artwork_path,
                                _kodi_artwork_url, _remember_artwork, _resolve_artwork,
                                _resolve_artwork_candidates, seconds)


def test_image_scheme_encoding_matches_existing_integration():
    image = "image://smb%3a%2f%2fguest%3apass%40nas%2fFilm%20Name.jpg/"
    assert _kodi_artwork_path(image) == quote_plus(image)


def test_raw_file_url_uses_nested_kodi_image_uri():
    uri = _kodi_artwork_path("smb://nas/Film Name.jpg")
    assert uri.startswith("image%3A%2F%2F")
    assert "%253A" in uri  # Nested percent-encoding for the original colon.
    assert "Film%2520Name.jpg" in uri


def test_artwork_reference_never_exposes_embedded_credentials():
    raw = "image://smb%3a%2f%2fusername%3asecret%40nas%2fmovie.jpg/"
    link = _remember_artwork("one", raw)
    assert "username" not in link
    assert "secret" not in link
    ref = link.split("ref=", 1)[1]
    assert len(ref) == 64
    assert _resolve_artwork("one", ref) == raw
    with pytest.raises(HTTPException) as exc:
        _resolve_artwork("another", ref)
    assert exc.value.status_code == 404


def test_seek_and_stream_actions_are_validated():
    assert RemoteAction(action="seek", percentage=31).percentage == 31
    assert RemoteAction(action="audio_select", index=2).index == 2
    assert RemoteAction(action="subtitle_select", index=0).index == 0
    for value in ("JSONRPC.SetConfiguration", "Input.ExecuteAction", ""):
        with pytest.raises(ValidationError):
            RemoteAction(action=value)


def test_position_translation():
    assert seconds({"hours": 1, "minutes": 2, "seconds": 3, "milliseconds": 500}) == 3723.5
    assert seconds(None) == 0


def test_credentialed_artwork_url_matches_integration_kodi_exactly():
    config = {
        "host": "kodi.local", "port": 8080, "tls": False,
        "username": "user@home", "password": "sp ace:%!/",
    }
    image = "image://smb%3a%2f%2fhost%2fMy%20film%20%26%20show.jpg/"
    expected = (
        "http://user%40home:sp%20ace%3A%25%21%2F@kodi.local:8080/image/"
        + quote_plus(image)
    )
    assert _kodi_artwork_url(config, image) == expected
    assert _kodi_artwork_path("smb://host/My film.jpg") == quote(
        "image://" + quote("smb://host/My film.jpg", safe="") + "/", safe=""
    )


def test_kodi_art_prefers_art_over_thumbnail_and_keeps_fallbacks():
    movie = {"art": {"poster": "image://poster/", "thumb": "image://thumb/"},
             "thumbnail": "image://small/"}
    sources = _artwork_sources(movie)
    assert sources == ["image://poster/", "image://thumb/", "image://small/"]
    link = _remember_artwork("example", sources)
    assert _resolve_artwork_candidates("example", link.split("ref=")[1]) == tuple(sources)


def test_kodi_artwork_retries_secondary_image_and_supports_octet_stream(monkeypatch):
    config = {
        "host": "kodi.local", "port": 8080, "tls": False,
        "username": "user@home", "password": "test:secret",
    }
    images = ["image://invalid-first/", "image://working-second/"]
    ref = _remember_artwork("remote-player", images).split("ref=", 1)[1]
    monkeypatch.setattr(remote, "lookup",
                        lambda request, player_id: {"config": config})
    seen = []
    def handler(request):
        seen.append((request.url.raw_path.decode(), request.headers.get("Authorization")))
        if "invalid-first" in request.url.raw_path.decode():
            return httpx.Response(404)
        return httpx.Response(200, content=b"\\x89PNG\\r\\n\\x1a\\n" + b"art",
                              headers={"Content-Type": "application/octet-stream"})
    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient
    monkeypatch.setattr(remote.httpx, "AsyncClient",
                        lambda **kwargs: original_client(transport=transport, **kwargs))
    result = asyncio.run(remote.remote_artwork("remote-player", ref, object()))
    assert result.status_code == 200
    assert result.media_type == "image/png"
    assert len(seen) == 2
    assert all(auth and auth.startswith("Basic ") for _, auth in seen)
    assert seen[0][0].endswith(quote_plus(images[0]))
    assert seen[1][0].endswith(quote_plus(images[1]))
