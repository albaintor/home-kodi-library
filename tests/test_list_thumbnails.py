"""Stored list cover mosaics and revision-driven regeneration."""

import io
from unittest.mock import patch

import pytest
from PIL import Image

from kodi_library.connectors.base import Record
from kodi_library.list_thumbnails import render_thumbnail


def picture(color):
    image = Image.new("RGB", (200, 300), color)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


@pytest.mark.parametrize(
    ("count", "coordinates"),
    [
        (0, []),
        (1, [(250, 250)]),
        (2, [(100, 250), (400, 250)]),
        (3, [(100, 250), (400, 100), (400, 400)]),
        (4, [(100, 100), (400, 100), (100, 400), (400, 400)]),
    ],
)
def test_poster_mosaic_layout(count, coordinates):
    colors = ["#e00000", "#00e000", "#0000e0", "#e0e000"]
    data = render_thumbnail([picture(color) for color in colors[:count]])
    with Image.open(io.BytesIO(data)) as result:
        assert result.size == (512, 512)
        assert result.format == "JPEG"
        for (x, y), hex_color in zip(coordinates, colors):
            expected = tuple(bytes.fromhex(hex_color.removeprefix("#")))
            actual = result.getpixel((x, y))
            assert all(abs(a - e) < 8 for a, e in zip(actual, expected))


def test_thumbnail_persists_and_refreshes_on_list_changes(client, admin, tmp_path):
    store = client.app.state.store
    source = store.save_source({"name": "Kodi", "type": "kodi"})
    colors = ["red", "green", "blue", "yellow", "purple"]
    store.apply_snapshot(
        source["id"],
        [
            Record(str(index), "movie", f"Film {index}", poster=[f"https://example.org/{index}.jpg"])
            for index in range(5)
        ],
    )
    ids = {row["title"]: row["id"] for row in store.media()["items"]}
    fetched = []

    async def fake_get(candidates):
        fetched.append(candidates[0])
        index = int(candidates[0].rsplit("/", 1)[-1].split(".")[0])
        return picture(colors[index]), "image/png"

    with patch.object(client.app.state.artwork, "get", side_effect=fake_get):
        created = client.post("/api/v1/lists", headers=admin, json={"name": "Favorites"})
        assert created.status_code == 201
        cover = created.json()["thumbnail"]
        assert cover and "?v=0" in cover
        list_id = created.json()["id"]
        cover_path = tmp_path / "list_thumbnails" / f"{list_id}.jpg"
        assert cover_path.is_file()
        empty_image = cover_path.read_bytes()
        for index in range(5):
            saved = client.post(
                f"/api/v1/lists/{list_id}/items",
                headers=admin,
                json={"media_id": ids[f"Film {index}"]},
            )
            assert saved.status_code == 204
        result = client.get("/api/v1/lists", headers=admin).json()[0]
        assert result["item_count"] == 5
        assert "?v=5" in result["thumbnail"]
        assert len(fetched) == 14  # first 1+2+3+4+4, never the fifth poster
        generated = cover_path.read_bytes()
        assert generated != empty_image
        assert client.get(result["thumbnail"], headers=admin).content == generated
        assert len(fetched) == 14  # reads use the stored image
        assert client.get(result["thumbnail"]).status_code == 401
        assert client.get("/api/v1/lists/invalid/thumbnail", headers=admin).status_code == 404

        # Reorder changes which four pictures appear first.
        page = client.get(
            f"/api/v1/lists/{list_id}/items?limit=200",
            headers=admin,
        ).json()
        reordered = [page["items"][-1]["id"], *[item["id"] for item in page["items"][:-1]]]
        moved = client.put(
            f"/api/v1/lists/{list_id}/order",
            headers=admin,
            json={"media_ids": reordered, "revision": page["revision"]},
        )
        assert moved.status_code == 204
        assert cover_path.read_bytes() != generated

        # Simulate a container upgrade with an existing list and no cached thumbnail.
        cover_path.unlink()
        assert client.get(
            client.get("/api/v1/lists", headers=admin).json()[0]["thumbnail"],
            headers=admin,
        ).status_code == 200
        assert cover_path.is_file()
        assert client.delete(f"/api/v1/lists/{list_id}", headers=admin).status_code == 204
        assert not cover_path.exists()
