import pytest
from fastapi import HTTPException

from home_media_catalog.playback_browser import _full_path, safe_path


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

    from home_media_catalog import playback_browser

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

    from home_media_catalog import playback_browser
    from home_media_catalog.playback_cloud import CloudOAuthError

    class Crypto:
        async def credentials(self, _location_id, _provider):
            raise CloudOAuthError("Jeton cloud illisible.")

    with pytest.raises(HTTPException) as error:
        asyncio.run(
            playback_browser.cloud_browse(
                {"kind": "onedrive", "id": "test"},
                "",
                None,
                Crypto(),
            )
        )
    assert error.value.status_code == 409


def test_nfs_stream_suffix_offset_and_isolated_ranges(tmp_path):
    from home_media_catalog.playback_browser import _sync_stream

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

    from home_media_catalog import playback_browser

    observed = []
    monkeypatch.setattr(playback_browser, "_credentials", lambda *_: "")

    def fake_stream(location, path, secret, mounts, start, end):
        observed.append((location["kind"], start, end))
        return 1024, lambda: iter([b"x"])

    monkeypatch.setattr(playback_browser, "_sync_stream", fake_stream)
    for kind in ("smb", "nfs", "ftp", "sftp"):
        response = asyncio.run(
            playback_browser.stream_location(
                {"id": "test", "kind": kind},
                "movie.mkv",
                requested,
                None,
                None,
                {},
            )
        )
        assert response.status_code == expected_status
        assert response.headers["accept-ranges"] == "bytes"
        assert response.headers["content-length"] == str(
            1024 - (max(0, 1024 + expected_start) if expected_start < 0 else expected_start)
            if expected_end is None
            else expected_end - expected_start + 1
        )
    assert observed == [
        (kind, expected_start, expected_end) for kind in ("smb", "nfs", "ftp", "sftp")
    ]


@pytest.mark.parametrize("requested", ["bytes=-0", "bytes=4-2", "bytes=3-4,10-11"])
def test_reject_invalid_or_unsatisfiable_ranges(monkeypatch, requested):
    import asyncio

    from home_media_catalog import playback_browser

    monkeypatch.setattr(playback_browser, "_credentials", lambda *_: "")
    monkeypatch.setattr(
        playback_browser,
        "_sync_stream",
        lambda *_: (1024, lambda: iter(())),
    )
    with pytest.raises(HTTPException) as error:
        asyncio.run(
            playback_browser.stream_location(
                {"id": "test", "kind": "smb"},
                "movie.mkv",
                requested,
                None,
                None,
                {},
            )
        )
    assert error.value.status_code == 416


def test_onedrive_cloud_range_proxy_keeps_bearer_token_off_cdn(monkeypatch):
    import asyncio

    import httpx

    from home_media_catalog import playback_browser

    seen = []

    def handle(request):
        seen.append(request)
        if request.url.host == "graph.microsoft.com":
            assert request.headers.get("Authorization") == "Bearer private-access"
            return httpx.Response(
                200, json={"@microsoft.graph.downloadUrl": "https://cdn.example.org/video.mkv"}
            )
        assert request.url.host == "cdn.example.org"
        assert request.headers.get("Authorization") is None
        assert request.headers.get("Range") == "bytes=4-7"
        return httpx.Response(
            206,
            headers={
                "Content-Range": "bytes 4-7/100",
                "Content-Length": "4",
                "Content-Type": "video/x-matroska",
            },
            content=b"abcd",
        )

    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(
        playback_browser.httpx,
        "AsyncClient",
        lambda **options: original_client(transport=transport, **options),
    )

    class OAuth:
        async def credentials(self, location_id, provider):
            assert location_id == "cloud-1"
            assert provider == "onedrive"
            return {"access_token": "private-access"}

    async def read():
        response = await playback_browser.cloud_stream_location(
            {"id": "cloud-1", "kind": "onedrive"},
            "opaque-file-id",
            "bytes=4-7",
            OAuth(),
        )
        assert response.status_code == 206
        assert response.headers["content-range"] == "bytes 4-7/100"
        return b"".join([part async for part in response.body_iterator])

    assert asyncio.run(read()) == b"abcd"
    assert len(seen) == 2


@pytest.mark.parametrize("provider", ["google_drive", "dropbox"])
def test_cloud_browse_follows_provider_pagination(monkeypatch, provider):
    import asyncio

    from home_media_catalog import playback_browser

    class Crypto:
        async def credentials(self, location_id, kind):
            assert location_id == "account-2" and kind == provider
            return {"access_token": "private-bearer"}

    calls = []

    class Response:
        def __init__(self, data):
            self.data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self.data

    class HTTP:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def get(self, url, *, headers, params):
            assert provider == "google_drive"
            assert headers["Authorization"] == "Bearer private-bearer"
            calls.append((url, params))
            if len(calls) == 1:
                assert params["pageSize"] == 200
                assert "pageToken" not in params
                return Response(
                    {"files": [{"id": "movie1", "name": "First.mkv"}], "nextPageToken": "page-two"}
                )
            assert params["pageToken"] == "page-two"
            return Response({"files": [{"id": "movie2", "name": "Second.mp4"}]})

        async def post(self, url, *, headers, json):
            assert provider == "dropbox"
            assert headers["Authorization"] == "Bearer private-bearer"
            calls.append((url, json))
            if len(calls) == 1:
                assert url.endswith("/files/list_folder")
                assert json["limit"] == 200
                return Response(
                    {
                        "entries": [
                            {".tag": "file", "name": "First.mkv", "path_display": "/First.mkv"}
                        ],
                        "has_more": True,
                        "cursor": "cursor-two",
                    }
                )
            assert url.endswith("/files/list_folder/continue")
            assert json == {"cursor": "cursor-two"}
            return Response(
                {
                    "entries": [
                        {".tag": "file", "name": "Second.mp4", "path_display": "/Second.mp4"}
                    ],
                    "has_more": False,
                }
            )

    monkeypatch.setattr(playback_browser.httpx, "AsyncClient", lambda **_: HTTP())
    result = asyncio.run(
        playback_browser.cloud_browse({"kind": provider, "id": "account-2"}, "", None, Crypto())
    )
    assert [file["name"] for file in result] == ["First.mkv", "Second.mp4"]
    assert len(calls) == 2
