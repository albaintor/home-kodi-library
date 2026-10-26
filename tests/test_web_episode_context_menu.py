"""Episode watch-through actions use a dedicated button, preserving browser menus."""

from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "home_media_catalog" / "static"


def test_episode_action_button_uses_inclusive_cutoff_without_contextmenu_override():
    source = (APP / "app.js").read_text()
    css = (APP / "app.css").read_text()

    assert 'data-episode-season="${season}"' in source
    assert 'data-episode-number="${Number(e.episode??0)}"' in source
    assert "data-episode-action" in source
    assert "openEpisodeContextMenu(button,rect.right,rect.bottom+6)" in source
    assert "data-watch-through-episode" in source
    assert "up_to_episode:upToEpisode" in source
    assert "saveEpisodeWatches(true,season,episode)" in source
    assert "document.addEventListener('contextmenu'" not in source
    assert ".season-episode-entry{" in css
    assert ".episode-context-menu{position:fixed;" in css
