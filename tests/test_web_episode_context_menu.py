"""Web season episode contextual watch menu uses the inclusive API cutoff."""

from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "kodi_library" / "static"


def test_episode_context_menu_uses_cutoff_and_right_click():
    source = (APP / "app.js").read_text()
    css = (APP / "app.css").read_text()
    assert 'data-episode-season="${season}"' in source
    assert 'data-episode-number="${Number(e.episode??0)}"' in source
    assert "document.addEventListener('contextmenu'" in source
    assert "data-watch-through-episode" in source
    assert "up_to_episode:upToEpisode" in source
    assert "saveEpisodeWatches(true,season,episode)" in source
    assert ".episode-context-menu{position:fixed;" in css
