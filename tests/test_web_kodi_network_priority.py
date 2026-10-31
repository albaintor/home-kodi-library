"""Network-first Kodi web playback: exact source matching and offline indicators."""

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from home_media_catalog import local_stream


def request_with_locations(locations):
    store = SimpleNamespace(playback_locations=lambda: locations)
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(store=store)))


@pytest.mark.parametrize(
    ("scheme", "root", "media", "relative"),
    [
        ("smb", "Movies", "smb://kodi-nas/Movies/4K/film.mkv", "4K/film.mkv"),
        ("nfs", "export/Movies", "nfs://kodi-nas/export/Movies/film.mkv", "film.mkv"),
        ("ftp", "Movies", "ftp://kodi-nas/Movies/Film%20A.mkv", "Film A.mkv"),
        ("sftp", "Movies", "sftp://kodi-nas/Movies/Film%20A.mkv", "Film A.mkv"),
        ("sftp", "", "sftp://kodi-nas/Movies/film.mkv", "Movies/film.mkv"),
    ],
)
def test_network_source_matching_protocols_and_hostname_alias(
    scheme, root, media, relative
):
    defaults = {"smb": 445, "nfs": 2049, "ftp": 21, "sftp": 22}
    location = {
        "id": "configured",
        "name": "NAS direct",
        "kind": scheme,
        "host": "192.168.1.20",
        "hostname": "kodi-nas",
        "target": f"{scheme}://192.168.1.20/{root}",
        "share": root,
        "port": defaults[scheme],
    }
    assert local_stream._registered_media_location(
        request_with_locations([location]), media
    ) == (location, relative)


@pytest.mark.parametrize(
    "file",
    [
        "smb://other-host/Movies/film.mkv",
        "smb://kodi-nas/Movies2/film.mkv",
        "smb://kodi-nas/Movies/%2E%2E/film.mkv",
        "smb://kodi-nas/Movies/film.mkv?token=test",
        "smb://kodi-nas:1445/Movies/film.mkv",
        "ftp://kodi-nas/Movies/film.mkv",
    ],
)
def test_network_match_never_crosses_protocol_host_port_or_root(file):
    location = {
        "id": "configured",
        "kind": "smb",
        "host": "192.168.1.20",
        "hostname": "kodi-nas",
        "share": "Movies",
        "target": "smb://192.168.1.20/Movies",
        "port": 445,
    }
    assert (
        local_stream._registered_media_location(
            request_with_locations([location]), file
        )
        is None
    )


def test_longest_share_root_wins():
    locations = [
        {
            "id": "a",
            "kind": "smb",
            "host": "nas",
            "share": "Movies",
            "target": "smb://nas/Movies",
            "port": 445,
        },
        {
            "id": "b",
            "kind": "smb",
            "host": "nas",
            "share": "Movies/4K",
            "target": "smb://nas/Movies/4K",
            "port": 445,
        },
    ]
    match = local_stream._registered_media_location(
        request_with_locations(locations), "smb://nas/Movies/4K/film.mkv"
    )
    assert match == (locations[1], "film.mkv")


def test_network_probe_failure_falls_back_to_kodi(monkeypatch):
    location = {
        "id": "net",
        "kind": "ftp",
        "host": "nas",
        "target": "ftp://nas/Movies",
        "share": "Movies",
        "port": 21,
    }
    store = SimpleNamespace(
        playback_locations=lambda: [location],
        player=lambda player_id: {
            "config": {"host": "kodi", "port": 8080, "username": "", "password": ""}
        },
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                store=store, playback_oauth=None, playback_nfs_mounts={}
            )
        )
    )
    ticket = "web-network-priority-test"
    local_stream._TICKETS[ticket] = {
        "player_id": "player",
        "file": "ftp://nas/Movies/film.mkv",
        "expires": time.monotonic() + 60,
    }
    monkeypatch.setattr(local_stream, "_credentials", lambda *_: "")
    monkeypatch.setattr(
        local_stream,
        "network_source",
        lambda *_: (_ for _ in ()).throw(HTTPException(502, "Offline")),
    )
    kodi_source_calls = []
    monkeypatch.setattr(
        local_stream,
        "kodi_source",
        lambda *args: kodi_source_calls.append(args) or "Kodi VFS",
    )

    async def fake_probe(source):
        return {"source": source}

    monkeypatch.setattr(local_stream, "probe", fake_probe)
    try:
        source, details = asyncio.run(
            local_stream._ticket_source(request, "player", ticket)
        )
        assert source == "Kodi VFS"
        assert details["source"] == "Kodi VFS"
        assert len(kodi_source_calls) == 1
    finally:
        local_stream._TICKETS.pop(ticket, None)


def test_browser_picker_displays_remote_reachability_not_active_playback():
    js = (
        Path(__file__).resolve().parents[1] / "home_media_catalog/static/app.js"
    ).read_text()
    assert "function playbackKodiOption(player)" in js
    assert "status===true?'🔵':status===false?'🔴':'⚪'" in js
    assert "api('players/'+encodeURIComponent(player.id)+'/remote'" in js
    assert "playbackKodiConnectivity.set(player.id,online)" in js
    assert "startPlaybackKodiStatus(players)" in js
    assert "stopPlaybackKodiStatus" in js
    assert "preferred_transport" in js
