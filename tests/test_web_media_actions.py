"""Web catalogue media actions must preserve the native browser context menu."""

from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "home_media_catalog" / "static"


def test_explicit_media_action_buttons_in_both_catalogue_layouts():
    js = (STATIC / "app.js").read_text()
    css = (STATIC / "app.css").read_text()
    html = (STATIC / "index.html").read_text()

    assert 'class="media-card-entry"' in js
    assert 'class="media-actions-trigger"' in js
    assert 'data-media-actions="${esc(item.id)}"' in js
    assert 'data-media="${esc(item.id)}"' in js
    assert 'popover="auto"' in html
    assert 'id="media-actions-popover"' in html
    assert ".media-actions-trigger{" in css
    assert ".list-results .media-card-entry" in css
    assert "document.addEventListener('contextmenu'" not in js
    assert (
        "event.preventDefault()"
        not in js[
            js.index("function openMediaActions(trigger)") : js.index(
                "function attachImageFallbacks(root)"
            )
        ]
    )


def test_quick_actions_share_existing_backend_api_routes():
    js = (STATIC / "app.js").read_text()
    assert 'data-quick-action="details"' in js
    assert 'data-quick-action="watch"' in js
    assert 'data-quick-action="play"' in js
    assert 'data-quick-action="trailer"' in js
    assert 'data-quick-action="new-list"' in js
    assert 'data-quick-list="' in js
    assert "function mediaWatchActions(item)" in js
    assert "progress.complete||progress.partial" in js
    assert "async function quickWatch(id,watched)" in js
    assert "/episodes/watch" in js
    assert "async function quickAddToList(id,listId)" in js
    assert "async function quickCreateList(id)" in js
    assert "await refreshHomeListThumbnails()" in js
