"""Layout regression: Kodi source actions may never crush its description."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_kodi_source_settings_actions_are_a_full_width_second_row():
    stylesheet = (ROOT / "home_media_catalog/static/app.css").read_text(encoding="utf-8")
    assert ".kodi-source-card{display:grid;grid-template-columns:minmax(0,1fr)" in stylesheet
    assert ".kodi-source-card>.connection-actions{grid-column:1;" in stylesheet
    assert "max-width:none;width:100%;justify-content:flex-start" in stylesheet
    assert ".kodi-source-card>.connection-actions button{flex:0 0 auto}" in stylesheet
    assert ".source-kodi-instances{grid-column:1/-1;" in stylesheet
