"""Regression guard for the nested Kodi source/player responsive layout."""

from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "kodi_library" / "static"


def test_kodi_instance_cards_span_the_entire_source_card():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "app.css").read_text(encoding="utf-8")

    # The source header occupies the first grid row (metadata + buttons).
    # Instances must be a sibling after the actions, not nested inside the
    # narrow metadata column, or names/addresses wrap one letter per line.
    source_template = next(
        line for line in js.splitlines()
        if 'data-kodi-instances-for="${esc(s.id)}"' in line
    )
    start = source_template.index('<div class="connection-card${s.type')
    actions = source_template.index('<div class="connection-actions">', start)
    instances = source_template.index('<section class="source-kodi-instances"', start)
    assert start < actions < instances
    assert '</button></div>${s.type===\'kodi\'?\`<section class="source-kodi-instances"' not in source_template
    assert '.source-kodi-instances{grid-column:1/-1;width:100%;' in css
    assert '.kodi-instance-row>div:first-child{flex:1 1 180px;' in css
    assert '.kodi-instance-row .connection-actions{flex-shrink:0;max-width:none}' in css
