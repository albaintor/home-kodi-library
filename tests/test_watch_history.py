from dataclasses import replace

import pytest

from kodi_library.connectors.base import Record
from kodi_library.connectors.clz import parse_clz


def export(history):
    return (
        "<collectorz><data><movieinfo><movielist><movie><hash>watched</hash>"
        "<title>Un film</title><imdbnr>1234567</imdbnr>"
        + history
        + "</movie></movielist></movieinfo></data></collectorz>"
    ).encode()


def test_user_seenhistory_example_and_multiple_dates(store):
    history = (
        "<seenhistory>"
        + "".join(
            f"<seendate><date><year>{y}</year><month>{m}</month><day>{d}</day>"
            "<displaydate>Ignored locale label</displaydate></date></seendate>"
            for y, m, d in [(2026, 10, 10), (2025, 1, 2)]
        )
        + "</seenhistory>"
    )
    record = parse_clz(export(history))[0]
    assert record.watched is True
    assert record.play_count == 2
    assert record.last_played == "2026-10-10"
    assert record.metadata["seen_dates"] == ["2025-01-02", "2026-10-10"]
    clz = store.save_source({"name": "CLZ", "type": "clz"})["id"]
    kodi = store.save_source({"name": "Kodi", "type": "kodi"})["id"]
    store.apply_snapshot(clz, [record])
    store.apply_snapshot(
        kodi,
        [
            Record(
                "movie:1:1",
                "movie",
                "Un film",
                identifiers={"imdb": "tt1234567"},
                watched=False,
                play_count=0,
            )
        ],
    )
    page = store.media(watch_status="watched")
    assert page["total"] == 1
    detail = store.detail(page["items"][0]["id"])
    assert detail["last_played"] == "2026-10-10"
    assert detail["play_count"] == 2
    assert (
        next(e for e in detail["editions"] if e["source_type"] == "clz")["last_played"]
        == "2026-10-10"
    )
    store.apply_snapshot(clz, [replace(record, watched=False, play_count=0, last_played=None)])
    assert store.media(watch_status="unwatched")["total"] == 1


@pytest.mark.parametrize(
    ("history", "watched", "count", "last"),
    [
        ("", None, None, None),
        ("<seenhistory />", False, 0, None),
        (
            "<seenhistory><seendate><date><year>2026</year><month>2</month><day>30</day></date></seendate></seenhistory>",
            True,
            1,
            None,
        ),
        ("<seenhistory><seendate /></seenhistory>", True, 1, None),
    ],
)
def test_unknown_empty_and_invalid_history(history, watched, count, last):
    record = parse_clz(export(history))[0]
    assert record.watched is watched
    assert record.play_count == count
    assert record.last_played == last


def test_non_ascii_login_does_not_crash(client):
    assert client.post("/api/v1/auth/session", json={"token": "échec 🔒"}).status_code == 401
