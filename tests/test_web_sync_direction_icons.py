"""Directional icons must identify the two distinct Kodi data flows."""

from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "kodi_library" / "static"


def test_incoming_refresh_button_uses_download_arrow():
    html = (STATIC / "index.html").read_text()
    button = html.split('id="scan-all-kodi"', 1)[1].split("</button>", 1)[0]
    label = "Analyser les vidéothèques Kodi puis synchroniser MariaDB"
    assert f'aria-label="{label}"' in button
    assert '<rect x="3" y="3" width="18" height="18" rx="2"/>' in button
    assert 'd="M12 8v8m-4-4 4 4 4-4"' in button
    assert "M20 7v5h-5" not in button


def test_outgoing_indicator_uses_upload_arrow():
    js = (STATIC / "app.js").read_text()
    assert "function transferIcon(direction)" in js
    assert "'M12 16V8m-4 4 4-4 4 4'" in js
    assert "transferIcon('up')" in js
    assert "icon.innerHTML=transferIcon('up')" in js
    assert "entry.status==='pending'?transferIcon('up')" in js
    assert "icon.textContent=status==='saved'?'✓':'!'" in js
    assert "api('sources/scan-all',{method:'POST'})" in js


def test_outgoing_icons_keep_responsive_sizing():
    css = (STATIC / "app.css").read_text()
    assert ".personal-update-indicator #personal-update-icon svg" in css
    assert ".writeback-chip .writeback-mark svg" in css
