"""Regression tests for recursive search and remote write boundaries."""
import asyncio

import pytest
from fastapi import HTTPException

from home_media_catalog import playback_operations as operations


@pytest.mark.parametrize(
    ("operation", "source", "target"),
    [
        ("delete", "", None),
        ("delete", "../x", None),
        ("rename", "a/video.mkv", "b/video.mkv"),
        ("copy", "movies", "movies/child"),
        ("move", "movies", "movies/child"),
        ("copy", "a", "a"),
        ("overwrite", "a", "b"),
        ("copy", "a", "/tmp/b"),
    ],
)
def test_reject_unsafe_mutations(operation, source, target):
    with pytest.raises(HTTPException):
        operations._validate_paths(operation, source, target)


def test_mounted_nfs_copy_move_and_delete(tmp_path):
    location = {"kind": "nfs", "id": "unit"}
    mounts = {"unit": str(tmp_path)}
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "sample.mkv").write_bytes(b"test")

    operations._mutate_mounted_nfs(location, mounts, "copy", "media", "media-copy")
    assert (tmp_path / "media-copy" / "sample.mkv").read_bytes() == b"test"
    operations._mutate_mounted_nfs(location, mounts, "rename", "media-copy", "media-new")
    assert (tmp_path / "media-new" / "sample.mkv").exists()
    operations._mutate_mounted_nfs(location, mounts, "delete", "media-new", None)
    assert not (tmp_path / "media-new").exists()


def test_mounted_nfs_rejects_symlink(tmp_path):
    location = {"kind": "nfs", "id": "unit"}
    outside = tmp_path.parent / "outside"
    outside.mkdir(exist_ok=True)
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(HTTPException) as error:
        operations._mutate_mounted_nfs(
            location, {"unit": str(tmp_path)}, "delete", "link", None,
        )
    assert error.value.status_code == 403


def test_search_pages_are_bounded_and_include_original_path(monkeypatch):
    listing = {
        "": [{"path": "Movies", "name": "Movies", "is_dir": True}],
        "Movies": [
            {"path": "Movies/Alien.mkv", "name": "Alien.mkv", "is_dir": False},
            {"path": "Movies/Alien 2.mkv", "name": "Alien 2.mkv", "is_dir": False},
            {"path": "Movies/Others", "name": "Others", "is_dir": True},
        ],
        "Movies/Others": [
            {"path": "Movies/Others/Alien 3.mkv", "name": "Alien 3.mkv", "is_dir": False},
        ],
    }

    async def browse(_location, folder, *_):
        return listing.get(folder, [])

    monkeypatch.setattr(operations, "browse_location", browse)

    async def search(offset):
        return await operations.search_location(
            {"kind": "smb"}, "", "alien", offset, 2, None, None, {},
        )

    first = asyncio.run(search(0))
    assert [x["path"] for x in first["items"]] == [
        "Movies/Alien.mkv", "Movies/Alien 2.mkv",
    ]
    assert first["next_offset"] == 2
    second = asyncio.run(search(2))
    assert [x["path"] for x in second["items"]] == [
        "Movies/Others/Alien 3.mkv",
    ]
    assert second["next_offset"] is None


def test_cloud_mutations_are_denied_before_network_access():
    with pytest.raises(HTTPException) as error:
        asyncio.run(operations.mutate_location(
            {"kind": "onedrive", "id": "cloud"}, "delete", "a", None,
            None, None, {},
        ))
    assert error.value.status_code == 403
