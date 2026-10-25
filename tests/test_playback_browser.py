import pytest
from fastapi import HTTPException
from kodi_library.playback_browser import safe_path, _full_path

@pytest.mark.parametrize("path", ["../etc", "/etc/passwd", "folder/../private", "a\\b", "x\x00y"])
def test_reject_remote_traversal(path):
    with pytest.raises(HTTPException):
        safe_path(path)

def test_allow_folder_and_filename():
    assert safe_path("Films/Star Wars.mkv") == "Films/Star Wars.mkv"

def test_reject_symlink_escape(tmp_path):
    (tmp_path / "escape").symlink_to(tmp_path.parent)
    with pytest.raises(HTTPException):
        _full_path(tmp_path.resolve(), "escape")
