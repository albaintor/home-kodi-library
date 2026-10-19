"""Kodi Remote: URI encoding, opaque credential handling, and safe commands."""
from urllib.parse import quote_plus

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from kodi_library.remote import RemoteAction, _kodi_artwork_path, _remember_artwork, _resolve_artwork, seconds


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
