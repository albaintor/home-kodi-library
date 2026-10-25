import pytest
from fastapi import HTTPException

from kodi_library.playback_browser import _full_path, safe_path


@pytest.mark.parametrize("path", ["../etc", "/etc/passwd", "folder/../private", "a\\b", "x\x00y"])
def test_reject_remote_traversal(path):
    with pytest.raises(HTTPException):
        safe_path(path)


def test_allow_folder_and_filename():
    assert safe_path("Films/Star Wars.mkv") == "Films/Star Wars.mkv"


def test_reject_symlink_escape(tmp_path):
    (tmp_path / "escape").symlink_to(tmp_path.parent)
    with pytest.raises(HTTPException):
        _full_path(tmp_path.resolve(), "escape")


def test_protocol_errors_are_json_http_exceptions(monkeypatch):
    """SMB/SSH protocol exceptions must not become plain-text HTTP 500."""
    import asyncio

    from kodi_library import playback_browser

    monkeypatch.setattr(playback_browser, "_credentials", lambda *_args: "")
    monkeypatch.setattr(
        playback_browser,
        "_sync_browse",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("transport closed")),
    )
    location = {"id": "test", "kind": "smb"}
    with pytest.raises(HTTPException) as error:
        asyncio.run(playback_browser.browse_location(location, "", None, None, {}))
    assert error.value.status_code == 502
    assert "transport closed" not in str(error.value.detail)


def test_expired_or_unreadable_cloud_credential_returns_409():
    import asyncio

    from cryptography.fernet import InvalidToken

    from kodi_library import playback_browser

    class Store:
        def playback_cloud_credentials(self, _location_id):
            return {"provider": "onedrive", "token_cipher": "broken"}

    class Crypto:
        def decrypt_secret(self, _cipher):
            raise InvalidToken

    with pytest.raises(HTTPException) as error:
        asyncio.run(
            playback_browser.cloud_browse(
                {"kind": "onedrive", "id": "test"},
                "",
                Store(),
                Crypto(),
            )
        )
    assert error.value.status_code == 409
