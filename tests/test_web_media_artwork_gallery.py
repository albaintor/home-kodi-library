"""Media details support copying the title and a swipeable existing-image gallery."""

from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "kodi_library" / "static"


def test_media_detail_title_is_a_copy_button():
    js = (STATIC / "app.js").read_text()
    assert 'id="copy-media-title"' in js
    assert "async function copyMediaTitle()" in js
    assert "navigator.clipboard.writeText(title)" in js
    assert "document.execCommand('copy')" in js
    assert "button.id==='copy-media-title'" in js


def test_gallery_uses_both_existing_artwork_fields_and_deduplicates():
    js = (STATIC / "app.js").read_text()
    assert "item.artwork?.poster" in js
    assert "item.artwork?.fanart" in js
    assert "images.findIndex(candidate=>candidate.path===image.path)===index" in js
    assert 'id="open-artwork-gallery"' in js
    assert "showDialog('artwork-gallery-dialog')" in js
    assert "object-fit:contain" in (STATIC / "app.css").read_text()


def test_gallery_is_modal_and_supports_swipe_buttons_and_keyboard():
    html = (STATIC / "index.html").read_text()
    js = (STATIC / "app.js").read_text()
    assert '<dialog id="artwork-gallery-dialog"' in html
    assert 'data-artwork-step="-1"' in html
    assert 'data-artwork-step="1"' in html
    assert "stepArtworkGallery(dx<0?1:-1)" in js
    assert "event.key==='ArrowRight'||event.key==='ArrowLeft'" in js
    assert "if($('artwork-gallery-dialog').open)$('artwork-gallery-dialog').close()" in js
