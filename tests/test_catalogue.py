from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from kodi_library.connectors.base import Record
from kodi_library.connectors.clz import parse_clz


def source(store, type="clz"):
    return store.save_source({"name": type, "type": type})["id"]


def test_clz_shape_and_normalization(clz_xml):
    entries = parse_clz(clz_xml)
    assert len(entries) == 4
    assert entries[0].identifiers == {"imdb": "tt0123456"}
    assert entries[0].plot == "Une équipe découvre une cité oubliée."
    assert entries[0].watched is None
    assert entries[0].user_rating is None
    assert entries[1].formats == ["Blu-ray"]
    assert entries[-1].kind == "tvshow"
    assert entries[-1].formats == ["Blu-ray 3D"]


def test_multiple_editions_and_remake_are_preserved(store, clz_xml):
    sid = source(store)
    result = store.apply_snapshot(sid, parse_clz(clz_xml))
    assert result["imported"] == 4
    assert store.status()["works"] == 3
    page = store.media(q="voyage", year=2024)
    assert page["total"] == 1
    detail = store.detail(page["items"][0]["id"])
    assert len(detail["editions"]) == 2
    assert detail["formats"] == ["4K UHD", "Blu-ray"]
    assert detail["watch_status"] == "unknown"


def test_kodi_and_clz_merge_by_imdb_and_watch_state(store, clz_xml):
    clz = source(store)
    kodi = source(store, "kodi")
    store.apply_snapshot(clz, parse_clz(clz_xml))
    record = Record(
        "movie:42:12",
        "movie",
        "Le Voyage imaginaire",
        identifiers={"imdb": "tt0123456", "tmdb": "123"},
        year=2024,
        watched=False,
        play_count=0,
        resume_seconds=1800,
        total_seconds=7500,
        date_added="2026-10-01T10:00:00",
    )
    store.apply_snapshot(kodi, [record])
    page = store.media(source_type="kodi", watch_status="in_progress", format="4K UHD")
    assert page["total"] == 1
    work_id = page["items"][0]["id"]
    detail = store.detail(work_id)
    assert detail["source_types"] == ["clz", "kodi"]
    assert len(detail["editions"]) == 3
    assert detail["identifiers"]["tmdb"] == ["123"]
    store.apply_snapshot(kodi, [replace(record, watched=True, play_count=1, resume_seconds=0)])
    assert store.detail(work_id)["watch_status"] == "watched"
    store.delete_source(kodi)
    assert store.detail(work_id)["watch_status"] == "unknown"
    assert len(store.detail(work_id)["editions"]) == 2


def test_import_is_idempotent_and_replaces_only_selected_source(store, clz_xml):
    sid = source(store)
    records = parse_clz(clz_xml)
    store.apply_snapshot(sid, records)
    original = {
        e["external_key"]: e["id"]
        for m in store.media()["items"]
        for e in store.detail(m["id"])["editions"]
    }
    result = store.apply_snapshot(sid, records)
    assert result["created_editions"] == 0
    assert result["updated_editions"] == 4
    current = {
        e["external_key"]: e["id"]
        for m in store.media()["items"]
        for e in store.detail(m["id"])["editions"]
    }
    assert current == original
    result = store.apply_snapshot(sid, records[:1])
    assert result["removed_editions"] == 3
    assert store.status()["works"] == 1


def test_failed_snapshot_is_rolled_back(store):
    sid = source(store)
    initial = Record("one", "movie", "Initial", identifiers={"imdb": "tt1234567"})
    store.apply_snapshot(sid, [initial])
    with pytest.raises(ValueError, match="duplicate"):
        store.apply_snapshot(
            sid, [Record("new", "movie", "New"), Record("new", "movie", "Duplicate")]
        )
    assert store.media()["total"] == 1
    assert store.media()["items"][0]["title"] == "Initial"


def test_free_text_accents_prefixes_and_query_characters(store):
    sid = source(store)
    store.apply_snapshot(
        sid,
        [
            Record(
                "1",
                "movie",
                "Été à Séoul",
                plot="Une équipe découvre une cité",
                genres=["Aventure"],
            ),
            Record("2", "movie", "Une comédie"),
        ],
    )
    assert store.media(q="ete seou")["total"] == 1
    assert store.media(q="équipe cité")["total"] == 1
    assert store.media(q='"OR" NOT *')["total"] == 0
    assert store.media(q="'")["total"] == 2


def test_sort_pagination_combined_filters_and_unknown_watch(store):
    sid = source(store)
    recent = datetime.now(UTC).isoformat(timespec="seconds")
    old = (datetime.now(UTC) - timedelta(days=100)).isoformat(timespec="seconds")
    store.apply_snapshot(
        sid,
        [
            Record(
                "1",
                "movie",
                "Zulu",
                genres=["Action"],
                formats=["4K UHD"],
                rating=7.5,
                date_added=recent,
            ),
            Record(
                "2",
                "movie",
                "Alpha",
                genres=["Action"],
                formats=["DVD"],
                rating=8.5,
                date_added=old,
            ),
            Record("3", "tvshow", "Beta", rating=None),
        ],
    )
    assert store.media(sort="title", order="asc", limit=1)["items"][0]["title"] == "Alpha"
    assert store.media(sort="title", order="asc", limit=1, offset=1)["items"][0]["title"] == "Beta"
    assert store.media(sort="rating", order="desc")["items"][-1]["title"] == "Beta"
    assert (
        store.media(kind="movie", genre="Action", format="4K UHD", source_id=sid, recent_days=30)[
            "total"
        ]
        == 1
    )
    assert store.media(watch_status="unwatched")["total"] == 0
    assert store.media(watch_status="unknown")["total"] == 3
    with pytest.raises(ValueError):
        store.media(sort="title;DROP TABLE works")


def test_conflicting_external_ids_do_not_merge(store):
    sid = source(store)
    store.apply_snapshot(
        sid,
        [
            Record("a", "movie", "Original", identifiers={"imdb": "tt1234567", "tmdb": "1"}),
            Record("b", "movie", "Different", identifiers={"imdb": "tt1234567", "tmdb": "2"}),
        ],
    )
    assert store.status()["works"] == 2


def test_recycled_kodi_id_does_not_replace_physical_collection_work(store):
    clz, kodi = source(store), source(store, "kodi")
    store.apply_snapshot(
        clz, [Record("disc", "movie", "Original", identifiers={"imdb": "tt1234567"})]
    )
    store.apply_snapshot(
        kodi, [Record("movie:1:1", "movie", "Original", identifiers={"imdb": "tt1234567"})]
    )
    old_id = store.media()["items"][0]["id"]
    store.apply_snapshot(
        kodi, [Record("movie:1:1", "movie", "Replacement", identifiers={"imdb": "tt7654321"})]
    )
    assert store.status()["works"] == 2
    assert store.detail(old_id)["title"] == "Original"


def test_parent_links_survive_show_and_episode_import(store):
    sid = source(store, "kodi")
    store.apply_snapshot(
        sid,
        [
            Record(
                "episode:2",
                "episode",
                "Episode",
                parent_key="tvshow:1",
                season=1,
                episode=2,
                watched=False,
            ),
            Record("tvshow:1", "tvshow", "Series", watched=False),
        ],
    )
    parent = store.media(kind="tvshow")["items"][0]["id"]
    assert store.media(parent_id=parent)["total"] == 1
    assert store.media(parent_id=parent)["items"][0]["episode"] == 2
