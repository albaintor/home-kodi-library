"""Regression checks for web/iOS playback and Kodi parity."""

from types import SimpleNamespace

import pytest

from home_media_catalog.local_stream import _registered_media_location
from home_media_catalog.models import PlayerInput
from home_media_catalog.remote import wake_on_lan


class _Locations:
    def playback_locations(self):
        return [
            {"id": "smb", "kind": "smb", "host": "192.168.1.20",
             "hostname": "MEDIA-NAS", "share": "Movies"},
            {"id": "nfs", "kind": "nfs", "host": "192.168.1.20",
             "hostname": "MEDIA-NAS", "share": "/volume1/Films"},
        ]


def _request():
    return SimpleNamespace(app=SimpleNamespace(
        state=SimpleNamespace(store=_Locations())
    ))


def test_kodi_smb_alias_resolves_to_configured_ip_share():
    matching = _registered_media_location(_request(), "smb://MEDIA-NAS/Movies/Film.mkv")
    assert matching is not None
    location, relative = matching
    assert location["id"] == "smb"
    assert location["host"] == "192.168.1.20"
    assert relative == "Film.mkv"


def test_kodi_nfs_alias_and_unmatched_paths():
    assert _registered_media_location(
        _request(), "nfs://MEDIA-NAS/volume1/Films/sub/Film.mkv"
    )[1] == "sub/Film.mkv"
    assert _registered_media_location(
        _request(), "smb://OTHER-NAS/Movies/Film.mkv"
    ) is None
    assert _registered_media_location(
        _request(), "smb://MEDIA-NAS/Other/Film.mkv"
    ) is None
    assert _registered_media_location(
        _request(), "smb://MEDIA-NAS/Movies/../Secret.mkv"
    ) is None


def test_mac_normalization_validation_and_legacy_optional_input():
    base = {"name": "Kodi", "source_id": "kodi-1", "host": "192.168.1.15"}
    assert PlayerInput(**base).mac is None
    assert PlayerInput(**base, mac="aabbccddeeff").mac == "aa:bb:cc:dd:ee:ff"
    with pytest.raises(ValueError):
        PlayerInput(**base, mac="invalid-address")


def test_wake_packet_contains_exact_mac_and_broadcast_destination(monkeypatch):
    written = []

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def setsockopt(self, *_args):
            pass

        def settimeout(self, *_args):
            pass

        def sendto(self, payload, target):
            written.append((payload, target))

    monkeypatch.setattr("home_media_catalog.remote.socket.socket",
                        lambda *_args, **_kwargs: FakeSocket())
    wake_on_lan("aa:bb:cc:dd:ee:ff")
    assert len(written) == 1
    payload, destination = written[0]
    assert len(payload) == 102
    assert payload == bytes([255]) * 6 + bytes.fromhex("aabbccddeeff") * 16
    assert destination == ("255.255.255.255", 9)


def test_web_feature_controls_are_wired_to_backend():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "home_media_catalog" / "static"
    app = (root / "app.js").read_text()
    remote = (root / "remote.js").read_text()
    html = (root / "index.html").read_text()
    assert 'id="network-browser-sort"' in html
    assert "sortedNetworkEntries(browser)" in app
    assert "network-browser-download" in app
    assert "kind==='onedrive'" not in app.split("function networkPlay(entry)", 1)[1].split("async function openNetworkBrowser", 1)[0]
    assert 'items/bulk' in app
    assert "aiRefinementHistory.pop()" in app
    assert "networkThumbnailPaused=true" in app
    assert "data-network-field=\"hostname\"" in app
    assert "players/'+encodeURIComponent(this.player.id)+'/wake" in remote
    assert "remote-volume" in remote
    assert "remote-touchpad" in remote
    assert "kodi-catalogue-clean" in app
    assert "lastBufferAhead" in (root / "player.js").read_text()
