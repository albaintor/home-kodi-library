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


def test_nfs_stream_suffix_offset_and_isolated_ranges(tmp_path):
    from kodi_library.playback_browser import _sync_stream

    payload = bytes(range(256)) * 8
    (tmp_path / "sample.mkv").write_bytes(payload)
    location = {"id": "nfs-test", "kind": "nfs"}
    mounts = {"nfs-test": str(tmp_path)}

    size, full = _sync_stream(location, "sample.mkv", "", mounts, 0, None)
    assert size == len(payload)
    assert b"".join(full()) == payload

    size, suffix = _sync_stream(location, "sample.mkv", "", mounts, -121, None)
    assert size == len(payload)
    assert b"".join(suffix()) == payload[-121:]

    size, middle = _sync_stream(location, "sample.mkv", "", mounts, 260, 513)
    assert size == len(payload)
    assert b"".join(middle()) == payload[260:514]


@pytest.mark.parametrize(
    ("requested", "expected_start", "expected_end", "expected_status"),
    [
        ("bytes=0-99", 0, 99, 206),
        ("bytes=100-", 100, None, 206),
        ("bytes=-120", -120, None, 206),
        (None, 0, None, 200),
    ],
)
def test_http_range_relay_for_all_network_backends(
    monkeypatch, requested, expected_start, expected_end, expected_status
):
    import asyncio
    from kodi_library import playback_browser

    observed = []
    monkeypatch.setattr(playback_browser, "_credentials", lambda *_: "")
    def fake_stream(location, path, secret, mounts, start, end):
        observed.append((location["kind"], start, end))
        return 1024, lambda: iter([b"x"])

    monkeypatch.setattr(playback_browser, "_sync_stream", fake_stream)
    for kind in ("smb", "nfs", "ftp", "sftp"):
        response = asyncio.run(
            playback_browser.stream_location(
                {"id": "test", "kind": kind}, "movie.mkv", requested,
                None, None, {},
            )
        )
        assert response.status_code == expected_status
        assert response.headers["accept-ranges"] == "bytes"
        assert response.headers["content-length"] == str(
            1024 - (max(0, 1024 + expected_start) if expected_start < 0 else expected_start)
            if expected_end is None
            else expected_end - expected_start + 1
        )
    assert observed == [(kind, expected_start, expected_end)
                        for kind in ("smb", "nfs", "ftp", "sftp")]


@pytest.mark.parametrize("requested", ["bytes=-0", "bytes=4-2", "bytes=3-4,10-11"])
def test_reject_invalid_or_unsatisfiable_ranges(monkeypatch, requested):
    import asyncio
    from kodi_library import playback_browser

    monkeypatch.setattr(playback_browser, "_credentials", lambda *_: "")
    monkeypatch.setattr(
        playback_browser, "_sync_stream",
        lambda *_: (1024, lambda: iter(())),
    )
    with pytest.raises(HTTPException) as error:
        asyncio.run(
            playback_browser.stream_location(
                {"id": "test", "kind": "smb"}, "movie.mkv", requested,
                None, None, {},
            )
        )
    assert error.value.status_code == 416
