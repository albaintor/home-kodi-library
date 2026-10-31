"""Web series navigation, episode action stacking and play-to selector contracts."""

from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "home_media_catalog/static"
JS = (STATIC / "app.js").read_text()
CSS = (STATIC / "app.css").read_text()
HTML = (STATIC / "index.html").read_text()


def test_season_selection_scrolls_episode_list_only_for_explicit_choice():
    handler = JS.split("if(button.hasAttribute('data-season'))", 1)[1]
    assert "loadSeasonEpisodes(Number(button.dataset.season),0,true)" in handler
    loader = JS.split("async function loadSeasonEpisodes(", 1)[1]
    loader = loader.split("function closeEpisodeContextMenu()", 1)[0]
    assert "offset=0,scrollToEpisodes=false" in loader
    assert "scrollToEpisodes&&!offset&&$('detail-dialog').open" in loader
    assert "node.scrollIntoView({behavior:'smooth',block:'start'})" in loader
    assert "state.detail?.id===parent&&node.dataset.season===String(season)" in loader
    assert ".episode-links{" in CSS and "scroll-margin-top:16px" in CSS


def test_episode_menu_uses_top_layer_from_inside_modal():
    menu = JS.split("function closeEpisodeContextMenu() {", 1)[1]
    menu = menu.split("document.addEventListener('pointerdown'", 1)[0]
    assert "if(menu.matches(':popover-open'))menu.hidePopover()" in menu
    assert "menu.setAttribute('popover','manual')" in menu
    assert "$('detail-dialog').append(menu)" in menu
    assert "menu.showPopover()" in menu
    assert "document.body.append(menu)" not in menu
    assert ".episode-context-menu{position:fixed;inset:auto;margin:0;" in CSS
    assert "data-watch-through-episode" in menu


def test_play_to_action_is_available_for_films_series_and_episodes():
    detail = JS.split("function detailActions(item)", 1)[1]
    detail = detail.split("function openTrailers()", 1)[0]
    assert "item.kind==='tvshow'" in detail
    assert "item.editions.some(e=>e.source_type==='kodi')" in detail
    assert 'class="action-button play-target-button"' in detail
    assert "actionIcon('playtarget')" in detail
    assert '<span>Lire sur…</span>' in detail
    assert "Lire sur Kodi" not in detail
    assert 'id="play-title"' in HTML and "Lire sur…</h2>" in HTML
    assert ".play-target-button{" in CSS


def test_series_play_selector_resolves_real_episode_before_play_requests():
    series = JS.split("function playbackSeriesMarkup(", 1)[1]
    series = series.split("function savePlaybackDraft()", 1)[0]
    assert 'id="play-season"' in series
    assert 'id="play-episode"' in series
    assert "while(more&&offset<5000)" in series
    assert "await api('media/'+encodeURIComponent(id))" in series
    assert "episode.editions?.some(e=>e.source_type==='kodi')" in series
    assert "state.playbackItem=episode" in series
    assert "state.playbackMediaId=episode.id" in series
    assert "playbackMarkup(episode)" in series

    assert "const item=state.playbackItem||state.detail" in JS
    assert "const playbackItem=state.playbackItem||state.detail" in JS
    assert "media_id:playbackItem.id" in JS
    assert "subtitle:item.kind==='episode'" in JS
    assert "returnToPlaybackSeries()" in JS
    assert "startPlaybackKodiStatus(players)" in JS
