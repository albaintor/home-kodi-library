"""Regression guard: the web network editor must preserve Kodi's SMB/NFS hostname alias."""

from pathlib import Path

APP_JS = (
    Path(__file__).resolve().parents[1] / "home_media_catalog" / "static" / "app.js"
)


def test_web_network_editor_exposes_hostname_and_preserves_it_on_edit():
    text = APP_JS.read_text(encoding="utf-8")
    editor = text.split("async function renderPlaybackNetworkSettings()", 1)[1].split(
        "let selectedConnectionTab=", 1
    )[0]

    assert 'data-network-field="hostname"' in editor
    assert 'name="hostname"' in editor
    assert "Nom d’hôte" in editor
    assert "field==='hostname'?!['smb','nfs'].includes(kind)" in editor
    assert "loc.hostname?' · Nom d’hôte Kodi :" in editor
    assert "'kind','host','hostname','port'" in editor
    assert "hostname:['smb','nfs'].includes(kind)?fields.hostname.value.trim():''" in editor
