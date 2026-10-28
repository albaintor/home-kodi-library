"""Native libnfs adapter tests without requiring an NFS server."""
import ctypes as C
from types import SimpleNamespace
import stat

import pytest

from home_media_catalog import native_nfs, playback_browser


def location(**extra):
    return {
        "id": "nfs-test", "kind": "nfs", "host": "192.168.1.20",
        "hostname": "kodi-nas.local", "port": 2049,
        "share": "/volume1/Media", "target": "nfs://192.168.1.20/volume1/Media",
        **extra,
    }


def test_nfs_export_and_relative_path_validation():
    assert native_nfs._remote_root(location()) == (
        "192.168.1.20", "/volume1/Media", 2049
    )
    assert native_nfs._remote_path("Films/A.mkv") == b"/Films/A.mkv"
    assert native_nfs._remote_path("Cinéma/A.mkv") == "/Cinéma/A.mkv".encode()
    for invalid in ("../other", "/abs", "a/./b", "a/../b", "a\\b"):
        with pytest.raises(ValueError):
            native_nfs._remote_path(invalid)
    for invalid in ("volume1/../other", ""):
        with pytest.raises(native_nfs.NFSUnavailable):
            native_nfs._remote_root(location(share=invalid, target="nfs://nas/"))


def test_native_nfs_read_respects_partial_range_and_suffix(monkeypatch):
    data = bytes(range(256)) * 8
    calls = []

    class FakeNFS:
        def __init__(self, location):
            self.location = location

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def stat(self, path):
            assert path == "Films/movie.mkv"
            return SimpleNamespace(mode=stat.S_IFREG | 0o444, size=len(data))

        def read_chunks(self, path, start, end, size):
            calls.append((path, start, end, size))
            end = len(data) if end is None else end + 1
            yield data[start:end]

    monkeypatch.setattr(native_nfs, "NativeNFS", FakeNFS)
    size, chunks = native_nfs.stream(location(), "Films/movie.mkv", 100, 199)
    assert size == len(data)
    assert b"".join(chunks()) == data[100:200]
    assert calls[-1] == ("Films/movie.mkv", 100, 199, len(data))

    size, chunks = native_nfs.stream(location(), "Films/movie.mkv", -19, None)
    assert b"".join(chunks()) == data[-19:]
    assert calls[-1][1] == len(data) - 19


def test_native_nfs_connector_used_without_kernel_mount(monkeypatch):
    calls = []

    def fake_browse(loc, relative):
        calls.append(("browse", loc["id"], relative))
        return [{"name": "movie.mkv", "is_dir": False, "size": 400}]

    def fake_stream(loc, relative, start, end):
        calls.append(("stream", loc["id"], relative, start, end))
        return 400, lambda: iter([b"media"])

    monkeypatch.setattr(native_nfs, "browse", fake_browse)
    monkeypatch.setattr(native_nfs, "stream", fake_stream)

    entries = playback_browser._sync_browse(
        location(), "Films", "", {}
    )
    assert entries[0]["name"] == "movie.mkv"
    size, content = playback_browser._sync_stream(
        location(), "Films/movie.mkv", "", {}, 10, 20
    )
    assert size == 400 and b"".join(content()) == b"media"
    assert calls == [
        ("browse", "nfs-test", "Films"),
        ("stream", "nfs-test", "Films/movie.mkv", 10, 20),
    ]


def test_libnfs_read_abi_argument_order(monkeypatch):
    class Function:
        argtypes = None
        restype = None

    class Library:
        def __getattr__(self, name):
            function = Function()
            setattr(self, name, function)
            return function

    fake = Library()
    monkeypatch.setattr(native_nfs, "find_library", lambda _: "libnfs.so.14")
    monkeypatch.setattr(native_nfs.C, "CDLL", lambda _: fake)
    native_nfs._library.cache_clear()
    try:
        assert native_nfs._library().nfs_pread.argtypes == [
            C.c_void_p, C.c_void_p, C.c_uint64, C.c_uint64, C.c_void_p
        ]
        assert native_nfs._library().nfs_set_timeout.argtypes == [
            C.c_void_p, C.c_int
        ]
    finally:
        native_nfs._library.cache_clear()
