"""Execute snapshot SQL against representative old/new view shapes."""

import sqlite3

import pytest

from kodi_library.connectors.base import Record
from kodi_library.connectors.clz import parse_clz
from kodi_library.connectors.kodi import KodiConnector
from kodi_library.media_formats import stream_metadata, technical_formats


class SnapshotCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []

    def execute(self, query):
        if query == "SHOW FULL TABLES":
            self.rows = [
                {"Tables": row[0]}
                for row in self.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            ]
        elif query.startswith("SHOW COLUMNS FROM"):
            table = query.split("`")[1]
            self.rows = [
                {"Field": row[1]}
                for row in self.connection.execute(f"PRAGMA table_info(`{table}`)")
            ]
        else:
            self.rows = [dict(row) for row in self.connection.execute(query)]

    def fetchall(self):
        return self.rows


@pytest.mark.parametrize("version_type", [None, 0, 1])
def test_movies_versions_cast_and_streams_survive_each_schema(version_type, store):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    extra_columns = (
        ",videoVersionIdFile INTEGER,isDefaultVersion INTEGER,videoVersionTypeItemType INTEGER"
        if version_type is not None
        else ""
    )
    db.execute(
        f"CREATE TABLE movie_view (idMovie INTEGER,idFile INTEGER,c00 TEXT,strFileName TEXT{extra_columns})"
    )
    db.execute("CREATE TABLE movie(idMovie INTEGER PRIMARY KEY)")
    db.execute("INSERT INTO movie VALUES(1)")
    db.execute("INSERT INTO movie VALUES(2)")
    if version_type is None:
        db.execute("INSERT INTO movie_view VALUES(1,10,'Un film','film.mkv')")
    else:
        db.executemany(
            "INSERT INTO movie_view VALUES(?,?,?,?,?,?,?)",
            [
                (1, 10, "Un film", "film.mkv", 10, 1, version_type),
                (1, 10, "Un film", "longue.3D.SBS.mkv", 11, 0, version_type),
                (1, 10, "Un film", "bonus.mkv", 12, 0, version_type + 1),
                (2, 20, "Film sans version", "ancien.mkv", None, 1, None),
            ],
        )
    db.execute("CREATE TABLE tvshow_view(idShow INTEGER,c00 TEXT)")
    db.execute("INSERT INTO tvshow_view VALUES(3,'Une série')")
    db.execute("CREATE TABLE uniqueid(media_id INTEGER,media_type TEXT,type TEXT,value TEXT)")
    db.execute("INSERT INTO uniqueid VALUES(1,'movie','imdb','tt0123456')")
    db.execute(
        "CREATE TABLE streamdetails(idFile INTEGER,iStreamType INTEGER,strVideoCodec TEXT,iVideoWidth INTEGER,iVideoHeight INTEGER,strHdrType TEXT,strStereoMode TEXT,strAudioCodec TEXT,iAudioChannels INTEGER,strAudioLanguage TEXT)"
    )
    db.executemany(
        "INSERT INTO streamdetails VALUES(?,?,?,?,?,?,?,?,?,?)",
        [
            (10, 0, "hevc", 3840, 1608, "dolbyvision", "mono", None, None, None),
            (10, 1, None, None, None, None, None, "truehd", 8, "fra"),
            (11, 0, "h264", 1920, 800, "hdr10plus", "left_right", None, None, None),
            (12, 1, None, None, None, None, None, "eac3_atmos", 6, "eng"),
        ],
    )
    db.execute("CREATE TABLE actor(actor_id INTEGER,name TEXT)")
    db.execute(
        "CREATE TABLE actor_link(actor_id INTEGER,media_id INTEGER,media_type TEXT,role TEXT,cast_order INTEGER)"
    )
    db.executemany(
        "INSERT INTO actor VALUES(?,?)", [(4, "Camille Exemple"), (5, "Alex Démonstration")]
    )
    db.executemany(
        "INSERT INTO actor_link VALUES(?,?,?,?,?)",
        [(5, 1, "movie", "Le pilote", 1), (4, 1, "movie", "La capitaine", 0)],
    )
    records = KodiConnector({})._snapshot(SnapshotCursor(db))
    movies = [r for r in records if r.kind == "movie"]
    assert len(movies) == (1 if version_type is None else 3)
    assert any(r.kind == "tvshow" for r in records)
    assert not any(r.metadata["file"].endswith("bonus.mkv") for r in records)
    default = next(r for r in movies if r.external_key == "movie:1:10")
    assert default.metadata["file"] == "film.mkv" and default.metadata["is_default"]
    assert set(default.formats) == {"4K", "Dolby Vision", "Dolby TrueHD"}
    assert "Dolby Atmos" not in default.formats
    assert default.metadata["cast"][0] == {
        "name": "Camille Exemple",
        "role": "La capitaine",
        "order": 0,
        "category": "cast",
        "photos": [],
    }
    if version_type is not None:
        alternative = next(r for r in movies if r.external_key == "movie:1:11")
        assert set(alternative.formats) == {"1080p", "HDR10+", "3D"}
    source = store.save_source({"name": "Kodi", "type": "kodi"})
    store.apply_snapshot(source["id"], records)
    assert store.media(kind="movie", source_type="kodi")["total"] == (
        1 if version_type is None else 2
    )
    found = store.media(q="Camille")["items"][0]
    assert store.detail(found["id"])["cast"][0]["name"] == "Camille Exemple"
    db.close()


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Dolby Atmos [Français]", ["Dolby Atmos"]),
        ("hdr10plus", ["HDR10+"]),
        ("hdr10", ["HDR10"]),
        ("truehd", ["Dolby TrueHD"]),
        ("Blu-ray", []),
    ],
)
def test_technical_labels_are_explicit(label, expected):
    assert technical_formats([label]) == expected


def test_sparse_streams_do_not_invent_formats():
    assert (
        stream_metadata([{"idFile": 1, "iStreamType": 0, "strVideoCodec": "hevc"}])["formats"] == []
    )


@pytest.mark.parametrize(
    "stereo", [None, "", "mono", " Mono ", "monoscopic", "2D", "none", "0", "unknown", "bad"]
)
def test_2d_and_unknown_stereo_values_do_not_advertise_3d(stereo):
    metadata = stream_metadata([{"iStreamType": 0, "iVideoWidth": 1920, "strStereoMode": stereo}])
    assert metadata["formats"] == ["1080p"]


@pytest.mark.parametrize("stereo", ["left_right", " TOP_BOTTOM ", "block_lr", "block_rl"])
def test_stereoscopic_layout_requires_filename_confirmation(stereo):
    rows = [{"iStreamType": 0, "strStereoMode": stereo}]
    assert stream_metadata(rows)["formats"] == []
    assert stream_metadata(rows, filename="/video/ordinary.mkv")["formats"] == []
    assert stream_metadata(rows, filename="/video/Movie.3D.SBS.mkv")["formats"] == ["3D"]


def test_hdr_detail_cannot_misclassify_kodi_video_as_3d():
    rows = [{"iStreamType": 0, "strHdrDetail": "3D", "strStereoMode": "mono"}]
    assert "3D" not in stream_metadata(rows, filename="movie.mkv")["formats"]


def test_clz_audio_and_explicit_hdr_tags(clz_xml):
    data = clz_xml.replace(
        b"<nrdiscs>2</nrdiscs>",
        "<audiotracks><audiotrack><displayname>Dolby Atmos [Français]</displayname></audiotrack></audiotracks><tags><tag><displayname>Dolby Vision</displayname></tag></tags><nrdiscs>2</nrdiscs>".encode(),
    )
    assert set(parse_clz(data)[0].formats) == {"4K UHD", "Dolby Atmos", "Dolby Vision"}


def test_cast_is_available_in_api(client, admin):
    store = client.app.state.store
    source = store.save_source({"name": "Kodi", "type": "kodi"})
    store.apply_snapshot(
        source["id"],
        [
            Record(
                "movie:1",
                "movie",
                "Un film",
                metadata={"cast": [{"name": "Camille Exemple", "role": "Pilote", "order": 0}]},
                formats=["4K", "HDR10+"],
            )
        ],
    )
    item = client.get("/api/v1/media?q=Camille", headers=admin).json()["items"][0]
    response = client.get(f"/api/v1/media/{item['id']}", headers=admin)
    assert response.status_code == 200
    assert response.json()["cast"][0]["name"] == "Camille Exemple"
    assert response.json()["cast"][0]["role"] == "Pilote"
    assert "imdb_search" in response.json()["cast"][0]["links"]
    assert set(response.json()["formats"]) == {"4K", "HDR10+"}


def test_kodi_portraits_directors_writers_and_youtube_trailer():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
    CREATE TABLE movie(idMovie INTEGER PRIMARY KEY);
    INSERT INTO movie VALUES(1);
    CREATE TABLE movie_view(idMovie INTEGER,idFile INTEGER,c00 TEXT,c19 TEXT);
    INSERT INTO movie_view VALUES(1,10,'Un film','plugin://plugin.video.youtube/play/?video_id=C6PbWhWGUrY');
    CREATE TABLE actor(actor_id INTEGER,name TEXT);
    INSERT INTO actor VALUES(4,'Camille Exemple');
    CREATE TABLE actor_link(actor_id INTEGER,media_id INTEGER,media_type TEXT,role TEXT,cast_order INTEGER);
    INSERT INTO actor_link VALUES(4,1,'movie','Pilote',0);
    CREATE TABLE director_link(actor_id INTEGER,media_id INTEGER,media_type TEXT);
    INSERT INTO director_link VALUES(4,1,'movie');
    CREATE TABLE writer_link(actor_id INTEGER,media_id INTEGER,media_type TEXT);
    INSERT INTO writer_link VALUES(4,1,'movie');
    CREATE TABLE art(media_id INTEGER,media_type TEXT,type TEXT,url TEXT);
    INSERT INTO art VALUES(4,'actor','thumb','image://https%3A%2F%2Fexample.org%2Fportrait.jpg/');
    """)
    record = KodiConnector({})._snapshot(SnapshotCursor(db))[0]
    assert [p["category"] for p in record.metadata["people"]] == ["cast", "director", "writer"]
    assert all(
        p["photos"] == ["https://example.org/portrait.jpg"] for p in record.metadata["people"]
    )
    assert record.metadata["trailers"] == ["https://www.youtube.com/watch?v=C6PbWhWGUrY"]
    db.close()
