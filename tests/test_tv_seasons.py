"""TV seasons are browsed inside their series, never in the main catalogue."""

from kodi_library.connectors.base import Record


def test_main_search_excludes_episodes_and_seasons_are_paged(client, admin):
    response = client.post(
        "/api/v1/sources",
        headers=admin,
        json={"name": "Kodi TV", "type": "kodi", "config": {"host": "nas", "username": "reader"}},
    )
    assert response.status_code == 201
    sid = response.json()["id"]
    episodes = [
        Record(
            external_key=f"episode:{1000 + episode}",
            kind="episode",
            title=f"Chapter {episode}",
            parent_key="tvshow:500",
            season=1 if episode <= 3 else 2,
            episode=episode if episode <= 3 else episode - 3,
            plot=f"The story of chapter {episode}.",
            runtime_minutes=42,
            watched=episode in (1, 2, 5),
        )
        for episode in range(1, 6)
    ]
    episodes.reverse()
    client.app.state.store.apply_snapshot(
        sid,
        [
            Record("movie:100", "movie", "A Movie"),
            Record("tvshow:500", "tvshow", "A Series"),
            Record(
                "episode:999", "episode", "A Special", parent_key="tvshow:500", season=0, episode=1
            ),
            *episodes,
        ],
    )
    main = client.get("/api/v1/media?sort=title&order=asc", headers=admin)
    assert main.status_code == 200, main.text
    assert main.json()["total"] == 2
    assert [i["kind"] for i in main.json()["items"]] == ["movie", "tvshow"]
    series_summary = next(item for item in main.json()["items"] if item["kind"] == "tvshow")
    assert series_summary["episode_count"] == 6
    assert series_summary["watched_episode_count"] == 3
    assert (
        client.get(f"/api/v1/media/{series_summary['id']}", headers=admin).json()[
            "watched_episode_count"
        ]
        == 3
    )

    searched = client.get("/api/v1/media?q=Chapter", headers=admin).json()
    assert searched["total"] == 0
    explicit = client.get("/api/v1/media?kind=episode", headers=admin).json()
    assert explicit["total"] == 6

    series_id = next(i["id"] for i in main.json()["items"] if i["kind"] == "tvshow")
    seasons = client.get(f"/api/v1/media/{series_id}/seasons", headers=admin)
    assert seasons.status_code == 200
    assert seasons.json() == [
        {"season": 0, "episode_count": 1, "watched_count": 0, "poster": None},
        {"season": 1, "episode_count": 3, "watched_count": 2, "poster": None},
        {"season": 2, "episode_count": 2, "watched_count": 1, "poster": None},
    ]

    first = client.get(f"/api/v1/media/{series_id}/seasons/1/episodes?limit=2", headers=admin)
    assert first.status_code == 200, first.text
    assert first.json()["total"] == 3
    assert first.json()["has_more"] is True
    assert [r["episode"] for r in first.json()["items"]] == [1, 2]
    final = client.get(
        f"/api/v1/media/{series_id}/seasons/1/episodes?limit=2&offset=2",
        headers=admin,
    )
    assert final.status_code == 200
    assert [r["episode"] for r in final.json()["items"]] == [3]
    assert final.json()["has_more"] is False
    eid = first.json()["items"][0]["id"]
    detail = client.get(f"/api/v1/media/{eid}", headers=admin)
    assert detail.status_code == 200
    assert detail.json()["plot"] == "The story of chapter 1."
    assert detail.json()["runtime_minutes"] == 42
    assert detail.json()["parent_id"] == series_id

    assert client.get(f"/api/v1/media/{eid}/seasons", headers=admin).status_code == 404
    assert (
        client.get(f"/api/v1/media/{series_id}/seasons/-1/episodes", headers=admin).status_code
        == 422
    )
    assert (
        client.get(
            f"/api/v1/media/{series_id}/seasons/1/episodes?limit=201", headers=admin
        ).status_code
        == 422
    )
    # The aggregated series indicator must track episode edits without a
    # complete remote rescan or changes to the series' own saved watch flag.
    first_episode = first.json()["items"][0]["id"]
    changed = client.patch(
        f"/api/v1/media/{first_episode}/personal",
        headers=admin,
        json={"watched": False},
    )
    assert changed.status_code == 200
    assert (
        client.get(f"/api/v1/media/{series_id}", headers=admin).json()["watched_episode_count"] == 2
    )
    assert (
        client.get(f"/api/v1/media/{series_id}/seasons", headers=admin).json()[1]["watched_count"]
        == 1
    )

    assert client.get(f"/api/v1/media/{series_id}/seasons").status_code == 401

    # Bulk edits affect all imported episodes (including the specials), not
    # the parent series' independent personal watch flag.
    endpoint = f"/api/v1/media/{series_id}/episodes/watch"
    parent_status = client.get(f"/api/v1/media/{series_id}", headers=admin).json()["watch_status"]
    bulk_season = client.patch(endpoint, headers=admin, json={"watched": True, "season": 1})
    assert bulk_season.status_code == 200, bulk_season.text
    assert bulk_season.json() == {
        "total": 3,
        "updated": 2,
        "watched": True,
        "season": 1,
        "up_to_episode": None,
    }
    assert [
        item["watched_count"]
        for item in client.get(f"/api/v1/media/{series_id}/seasons", headers=admin).json()
    ] == [0, 3, 1]
    assert (
        client.patch(endpoint, headers=admin, json={"watched": True, "season": 1}).json()["updated"]
        == 0
    )

    all_unwatched = client.patch(endpoint, headers=admin, json={"watched": False})
    assert all_unwatched.status_code == 200
    assert all_unwatched.json() == {
        "total": 6,
        "updated": 4,
        "watched": False,
        "season": None,
        "up_to_episode": None,
    }
    assert (
        client.get(f"/api/v1/media/{series_id}", headers=admin).json()["watched_episode_count"] == 0
    )

    all_watched = client.patch(endpoint, headers=admin, json={"watched": True})
    assert all_watched.status_code == 200
    assert all_watched.json()["updated"] == 6
    seasons_after = client.get(f"/api/v1/media/{series_id}/seasons", headers=admin).json()
    assert [item["watched_count"] for item in seasons_after] == [1, 3, 2]
    full_series = client.get(f"/api/v1/media/{series_id}", headers=admin).json()
    assert full_series["watched_episode_count"] == 6
    assert full_series["watch_status"] == parent_status

    assert client.patch(endpoint, json={"watched": True}).status_code == 401
    from conftest import READ

    read = {"Authorization": f"Bearer {READ}"}
    assert client.patch(endpoint, headers=read, json={"watched": True}).status_code == 403
    assert (
        client.patch(endpoint, headers=admin, json={"watched": True, "season": -1}).status_code
        == 422
    )
    assert (
        client.patch(
            f"/api/v1/media/{eid}/episodes/watch", headers=admin, json={"watched": True}
        ).status_code
        == 404
    )


def test_watch_up_to_selected_episode_scopes_to_season(client, admin):
    from kodi_library.connectors.base import Record

    response = client.post(
        "/api/v1/sources",
        headers=admin,
        json={"name": "TV", "type": "kodi", "config": {"host": "nas", "username": "reader"}},
    )
    assert response.status_code == 201
    source_id = response.json()["id"]
    client.app.state.store.apply_snapshot(
        source_id,
        [
            Record("show:100", "tvshow", "Show"),
            Record("special:1", "episode", "Special", parent_key="show:100", season=0, episode=1),
            Record("ep:1", "episode", "One", parent_key="show:100", season=1, episode=1),
            Record("ep:2", "episode", "Two", parent_key="show:100", season=1, episode=2),
            Record("ep:3", "episode", "Three", parent_key="show:100", season=1, episode=3),
            Record("other:1", "episode", "Other", parent_key="show:100", season=2, episode=1),
        ],
    )
    series_id = client.app.state.store.media(kind="tvshow")["items"][0]["id"]
    path = f"/api/v1/media/{series_id}/episodes/watch"
    assert (
        client.patch(path, headers=admin, json={"watched": True, "up_to_episode": 2}).status_code
        == 422
    )
    assert (
        client.patch(
            path, headers=admin, json={"watched": True, "season": 1, "up_to_episode": -1}
        ).status_code
        == 422
    )
    response = client.patch(
        path, headers=admin, json={"watched": True, "season": 1, "up_to_episode": 2}
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "total": 2,
        "updated": 2,
        "watched": True,
        "season": 1,
        "up_to_episode": 2,
    }
    assert [
        row["watched_count"]
        for row in client.get(f"/api/v1/media/{series_id}/seasons", headers=admin).json()
    ] == [0, 2, 0]

    again = client.patch(
        path, headers=admin, json={"watched": True, "season": 1, "up_to_episode": 2}
    )
    assert again.status_code == 200
    assert again.json()["updated"] == 0
    assert (
        client.patch(
            path, headers=admin, json={"watched": False, "season": 1, "up_to_episode": 1}
        ).json()["updated"]
        == 1
    )
    assert [
        row["watched_count"]
        for row in client.get(f"/api/v1/media/{series_id}/seasons", headers=admin).json()
    ] == [0, 1, 0]
