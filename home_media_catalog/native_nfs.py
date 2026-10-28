"""Native, read-only NFSv3/v4 client backed by libnfs (no kernel mount).

Each request owns an independent libnfs context. This avoids sharing a mutable
file handle across overlapping HTTP Range requests and keeps seeks isolated.
"""
from __future__ import annotations

import ctypes as C
from ctypes.util import find_library
from functools import lru_cache
import stat
from urllib.parse import urlsplit


class NFSUnavailable(RuntimeError):
    pass


class _NFSStat(C.Structure):
    _fields_ = [(field, C.c_uint64) for field in (
        "dev", "ino", "mode", "nlink", "uid", "gid", "rdev", "size",
        "blksize", "blocks", "atime", "mtime", "ctime", "atime_nsec",
        "mtime_nsec", "ctime_nsec", "used",
    )]


class _NFSDirent(C.Structure):
    _fields_ = [
        ("next", C.c_void_p), ("name", C.c_char_p), ("inode", C.c_uint64),
        ("type", C.c_uint32), ("mode", C.c_uint32), ("size", C.c_uint64),
    ]


@lru_cache(maxsize=1)
def _library():
    name = find_library("nfs")
    if name is None:
        raise NFSUnavailable("libnfs is not installed in the server image.")
    lib = C.CDLL(name)
    signatures = {
        "nfs_init_context": (C.c_void_p, []),
        "nfs_destroy_context": (None, [C.c_void_p]),
        "nfs_get_error": (C.c_char_p, [C.c_void_p]),
        "nfs_set_version": (C.c_int, [C.c_void_p, C.c_int]),
        "nfs_set_nfsport": (None, [C.c_void_p, C.c_int]),
        "nfs_set_autoreconnect": (None, [C.c_void_p, C.c_int]),
        "nfs_mount": (C.c_int, [C.c_void_p, C.c_char_p, C.c_char_p]),
        "nfs_stat64": (C.c_int, [C.c_void_p, C.c_char_p, C.POINTER(_NFSStat)]),
        "nfs_opendir": (C.c_int, [C.c_void_p, C.c_char_p, C.POINTER(C.c_void_p)]),
        "nfs_readdir": (C.POINTER(_NFSDirent), [C.c_void_p, C.c_void_p]),
        "nfs_closedir": (None, [C.c_void_p, C.c_void_p]),
        "nfs_open": (C.c_int, [C.c_void_p, C.c_char_p, C.c_int,
                               C.POINTER(C.c_void_p)]),
        "nfs_pread": (C.c_int, [C.c_void_p, C.c_void_p, C.c_void_p,
                               C.c_size_t, C.c_uint64]),
        "nfs_close": (C.c_int, [C.c_void_p, C.c_void_p]),
    }
    for symbol, (restype, args) in signatures.items():
        func = getattr(lib, symbol)
        func.restype = restype
        func.argtypes = args
    return lib


def _remote_root(location: dict) -> tuple[str, str, int]:
    parsed = urlsplit(location.get("target") or "")
    host = (location.get("host") or parsed.hostname or "").strip()
    root = (location.get("share") or parsed.path or "").strip("/")
    port = int(location.get("port") or parsed.port or 2049)
    if not host or not root or not 1 <= port <= 65535:
        raise NFSUnavailable("NFS requires an export path and reachable host.")
    if any(part in ("", ".", "..") or "\\" in part for part in root.split("/")):
        raise NFSUnavailable("NFS export path contains invalid segments.")
    return host, "/" + root, port


def _remote_path(relative: str) -> bytes:
    if relative.startswith("/") or "\\" in relative or any(
        part in (".", "..") for part in relative.split("/")
    ):
        raise ValueError("Invalid path relative to NFS export.")
    return ("/" + relative.strip("/") if relative else "/").encode("utf-8")


class NativeNFS:
    def __init__(self, location: dict):
        self.lib = _library()
        host, export, port = _remote_root(location)
        self.ctx = None
        errors = []
        # v3 requires MOUNT/portmapper. Fall back to NFSv4 for v4-only NASes.
        for version in (3, 4):
            ctx = self.lib.nfs_init_context()
            if not ctx:
                raise NFSUnavailable("Cannot create NFS context.")
            try:
                if self.lib.nfs_set_version(ctx, version) != 0:
                    raise NFSUnavailable("Unsupported NFS version.")
                self.lib.nfs_set_nfsport(ctx, port)
                self.lib.nfs_set_autoreconnect(ctx, 2)
                result = self.lib.nfs_mount(ctx, host.encode(), export.encode())
                if result == 0:
                    self.ctx = ctx
                    break
                detail = self.lib.nfs_get_error(ctx)
                errors.append(f"v{version}: " + (detail.decode(errors="replace") if detail else "mount failed"))
            finally:
                if self.ctx != ctx:
                    self.lib.nfs_destroy_context(ctx)
        if not self.ctx:
            raise NFSUnavailable("Cannot connect to configured NFS export: " + "; ".join(errors))

    def _check(self, status: int, operation: str):
        if status >= 0:
            return
        msg = self.lib.nfs_get_error(self.ctx)
        detail = msg.decode(errors="replace") if msg else str(status)
        raise OSError(f"NFS {operation} failed: {detail}")

    def stat(self, path: str) -> _NFSStat:
        result = _NFSStat()
        self._check(self.lib.nfs_stat64(self.ctx, _remote_path(path), C.byref(result)), "stat")
        return result

    def listing(self, path: str) -> list[dict]:
        directory = C.c_void_p()
        self._check(self.lib.nfs_opendir(self.ctx, _remote_path(path), C.byref(directory)), "opendir")
        try:
            entries = []
            while len(entries) < 2000:
                ent = self.lib.nfs_readdir(self.ctx, directory)
                if not ent:
                    break
                item = ent.contents
                name = item.name.decode("utf-8", errors="replace") if item.name else ""
                if not name or name in (".", "..") or name.startswith("."):
                    continue
                # 5 = NF3LNK. Skip links to avoid crossing export boundaries.
                if item.type == 5 or "/" in name or "\\" in name:
                    continue
                entries.append({
                    "name": name, "is_dir": item.type == 2,
                    "size": int(item.size) if item.type == 1 else 0,
                })
            return entries
        finally:
            self.lib.nfs_closedir(self.ctx, directory)

    def read_chunks(self, path: str, start: int, end: int | None, size: int):
        handle = C.c_void_p()
        self._check(self.lib.nfs_open(self.ctx, _remote_path(path), 0, C.byref(handle)), "open")
        try:
            offset = start
            final = size if end is None else min(size, end + 1)
            while offset < final:
                count = min(256 * 1024, final - offset)
                buf = C.create_string_buffer(count)
                read = self.lib.nfs_pread(self.ctx, handle, buf, count, offset)
                self._check(read, "read")
                if read == 0:
                    break
                offset += read
                yield buf.raw[:read]
        finally:
            self.lib.nfs_close(self.ctx, handle)

    def close(self):
        if self.ctx:
            self.lib.nfs_destroy_context(self.ctx)
            self.ctx = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def browse(location: dict, relative: str) -> list[dict]:
    with NativeNFS(location) as nfs:
        return nfs.listing(relative)


def stream(location: dict, relative: str, start: int, end: int | None):
    with NativeNFS(location) as nfs:
        info = nfs.stat(relative)
        if not stat.S_ISREG(info.mode):
            raise FileNotFoundError("NFS file is not regular.")
        size = int(info.size)
    if start < 0:
        start = max(0, size + start)

    def content():
        with NativeNFS(location) as nfs:
            yield from nfs.read_chunks(relative, start, end, size)

    return size, content
