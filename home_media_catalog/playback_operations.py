"""Bounded recursive network search and explicitly authorized remote mutations.

Both routes accept paths relative to a saved location, never filesystem/UNC URLs.
Cloud locations are always read-only. Mutations run only with an explicit
administrator bearer token; implicit local browser sessions are not enough.
"""
from __future__ import annotations

import asyncio
import ftplib
import shutil
import stat
from collections import deque
from pathlib import PurePosixPath
from tempfile import SpooledTemporaryFile

from fastapi import HTTPException

from .playback_browser import (
    _credentials, _ftp_connection, _full_path, _local_root, _root,
    _sftp_connection, browse_location, safe_path,
)

NETWORK_KINDS = frozenset({"smb", "nfs", "ftp", "sftp"})
SEARCH_MAX_FOLDERS = 160
SEARCH_MAX_ITEMS = 8000
SEARCH_MAX_DEPTH = 12
SEARCH_PAGE_SIZE = 80


async def search_location(
    location: dict, path: str, query: str, offset: int, limit: int,
    store, encryption, mounts: dict,
) -> dict:
    """Return an incremental page without buffering every matching file.

    A bounded breadth-first traversal makes partial results explicit. Each
    page starts at the same directory, avoiding untrusted serialized cursors.
    """
    root = safe_path(path)
    needle = query.strip().casefold()
    if len(needle) < 2 or len(needle) > 120:
        raise HTTPException(422, "Search requires 2-120 characters.")
    if offset < 0 or offset > 8000 or limit < 1 or limit > SEARCH_PAGE_SIZE:
        raise HTTPException(422, "Invalid search pagination.")
    queue = deque([(root, 0)])
    found = []
    examined = 0
    folders = 0
    matches = 0
    while queue and folders < SEARCH_MAX_FOLDERS and examined < SEARCH_MAX_ITEMS:
        folder, depth = queue.popleft()
        folders += 1
        rows = await browse_location(location, folder, store, encryption, mounts)
        for item in rows:
            examined += 1
            if needle in item["name"].casefold():
                if matches >= offset and len(found) < limit:
                    found.append(item)
                matches += 1
                if matches > offset + limit:
                    return {
                        "items": found, "next_offset": offset + len(found),
                        "truncated": False, "examined": examined,
                    }
            if item["is_dir"] and depth < SEARCH_MAX_DEPTH:
                queue.append((item["path"], depth + 1))
            if examined >= SEARCH_MAX_ITEMS:
                break
    truncated = bool(queue) or folders >= SEARCH_MAX_FOLDERS or examined >= SEARCH_MAX_ITEMS
    # Do not offer a page cursor if the full search ended; clients can still
    # explain that a safety limit prevented scanning the whole tree.
    return {
        "items": found, "next_offset": offset + len(found) if truncated and found else None,
        "truncated": truncated, "examined": examined,
    }


def _validate_paths(operation: str, path: str, destination: str | None) -> tuple[str, str | None]:
    if operation not in {"rename", "move", "copy", "delete"}:
        raise HTTPException(422, "Unsupported file operation.")
    source = safe_path(path)
    if not source:
        raise HTTPException(422, "Cannot change the root of a network share.")
    target = safe_path(destination) if destination is not None else None
    if operation != "delete":
        if not target or source == target:
            raise HTTPException(422, "A distinct destination path is required.")
        if target.startswith(source + "/"):
            raise HTTPException(422, "Cannot move/copy a folder into itself.")
        if operation == "rename" and PurePosixPath(source).parent != PurePosixPath(target).parent:
            raise HTTPException(422, "Rename must stay in the same directory.")
    elif destination is not None:
        raise HTTPException(422, "Delete does not accept a destination.")
    return source, target


def _safe_sftp_path(sftp, base: str, relative: str, *, existing: bool):
    # lstat all existing parents so SSH symlinks cannot escape the saved root.
    current = base.rstrip("/") or "."
    segments = relative.split("/")
    for i, segment in enumerate(segments):
        current = current.rstrip("/") + "/" + segment
        if i == len(segments) - 1 and not existing:
            break
        if stat.S_ISLNK(sftp.lstat(current).st_mode):
            raise HTTPException(403, "Symbolic links are not editable.")
    return current


def _mutate_sftp(location, password, operation, source, target):
    ssh, sftp = _sftp_connection(location, password)
    try:
        _, prefix = _root(location)
        base = prefix or "."
        origin = _safe_sftp_path(sftp, base, source, existing=True)
        destination = _safe_sftp_path(sftp, base, target, existing=False) if target else None
        if destination:
            try:
                sftp.lstat(destination)
            except FileNotFoundError:
                pass
            else:
                raise HTTPException(409, "Destination already exists.")

        def remove(path):
            info = sftp.lstat(path)
            if stat.S_ISDIR(info.st_mode):
                for entry in sftp.listdir_attr(path):
                    if entry.filename not in (".", ".."):
                        remove(path + "/" + entry.filename)
                sftp.rmdir(path)
            else:
                sftp.remove(path)

        def copy(src, dst):
            info = sftp.lstat(src)
            if stat.S_ISLNK(info.st_mode):
                raise HTTPException(403, "Symbolic links cannot be copied.")
            if stat.S_ISDIR(info.st_mode):
                sftp.mkdir(dst)
                for entry in sftp.listdir_attr(src):
                    if entry.filename not in (".", ".."):
                        copy(src + "/" + entry.filename, dst + "/" + entry.filename)
            else:
                with sftp.file(src, "rb") as reader, sftp.file(dst, "xb") as writer:
                    while part := reader.read(1024 * 1024):
                        writer.write(part)

        if operation == "delete":
            remove(origin)
        elif operation in ("move", "rename"):
            sftp.rename(origin, destination)
        else:
            copy(origin, destination)
    finally:
        sftp.close()
        ssh.close()


def _mutate_ftp(location, password, operation, source, target):
    conn = _ftp_connection(location, password)
    try:
        _, prefix = _root(location)
        remote = lambda path: "/".join(filter(None, (prefix, path)))
        src = remote(source)
        dst = remote(target) if target else None
        def entries(path):
            return [(name, attrs) for name, attrs in conn.mlsd(path)
                    if name not in (".", "..")]
        def exists(path):
            parent, _, name = path.rpartition("/")
            return any(entry == name for entry, _ in entries(parent or "."))
        if dst and exists(dst):
            raise HTTPException(409, "Destination already exists.")

        def remove(path):
            if any(name == path.rsplit("/", 1)[-1] and attrs.get("type") == "dir"
                   for name, attrs in entries(path.rpartition("/")[0] or ".")):
                for name, _ in entries(path):
                    remove(path.rstrip("/") + "/" + name)
                conn.rmd(path)
            else:
                conn.delete(path)

        def copy(src_path, dst_path):
            parent = src_path.rpartition("/")[0] or "."
            is_dir = any(name == src_path.rsplit("/", 1)[-1] and attrs.get("type") == "dir"
                         for name, attrs in entries(parent))
            if is_dir:
                conn.mkd(dst_path)
                for name, _ in entries(src_path):
                    copy(src_path.rstrip("/") + "/" + name, dst_path.rstrip("/") + "/" + name)
            else:
                with SpooledTemporaryFile(max_size=8 * 1024 * 1024) as temp:
                    conn.retrbinary("RETR " + src_path, temp.write, blocksize=1024 * 1024)
                    temp.seek(0)
                    conn.storbinary("STOR " + dst_path, temp, blocksize=1024 * 1024)

        if operation == "delete":
            remove(src)
        elif operation in ("move", "rename"):
            conn.rename(src, dst)
        else:
            copy(src, dst)
    finally:
        try:
            conn.quit()
        except (OSError, EOFError, ftplib.Error):
            conn.close()


def _mutate_smb(location, password, operation, source, target):
    import smbclient

    host, prefix = _root(location)
    segments = prefix.split("/")
    if not segments or not segments[0]:
        raise HTTPException(422, "SMB modifications require a configured share.")
    user = location.get("username") or ""
    domain = location.get("workgroup") or ""
    smbclient.register_session(
        host, username=domain + "\\" + user if domain and user else user,
        password=password, port=int(location.get("port") or 445), connection_timeout=8,
    )
    base = "\\\\" + host + "\\" + prefix.replace("/", "\\")
    remote = lambda path: base + "\\" + path.replace("/", "\\")
    src = remote(source)
    dst = remote(target) if target else None
    if dst and smbclient.path.exists(dst):
        raise HTTPException(409, "Destination already exists.")

    def remove(path):
        if smbclient.path.isdir(path):
            with smbclient.scandir(path) as it:
                for entry in it:
                    remove(path + "\\" + entry.name)
            smbclient.rmdir(path)
        else:
            smbclient.remove(path)

    def copy(src_path, dst_path):
        if smbclient.path.isdir(src_path):
            smbclient.mkdir(dst_path)
            with smbclient.scandir(src_path) as it:
                for entry in it:
                    copy(src_path + "\\" + entry.name, dst_path + "\\" + entry.name)
        else:
            with smbclient.open_file(src_path, mode="rb") as reader, smbclient.open_file(dst_path, mode="xb") as writer:
                while block := reader.read(1024 * 1024):
                    writer.write(block)

    if operation == "delete":
        remove(src)
    elif operation in ("move", "rename"):
        smbclient.rename(src, dst)
    else:
        copy(src, dst)


def _mutate_mounted_nfs(location, mounts, operation, source, target):
    root = _local_root(location, mounts)
    src = _full_path(root, source)
    dst = _full_path(root, target) if target else None
    # Never follow or copy symlinks, even if both endpoints resolve within root.
    for relative in filter(None, (source, target)):
        current = root
        for part in relative.split("/"):
            current = current / part
            if current.is_symlink():
                raise HTTPException(403, "Symbolic links are not editable.")
    if dst and dst.exists():
        raise HTTPException(409, "Destination already exists.")
    if not src.exists():
        raise HTTPException(404, "File not found.")
    if operation == "delete":
        if src.is_dir():
            shutil.rmtree(src)
        else:
            src.unlink()
    elif operation in ("move", "rename"):
        src.rename(dst)
    else:
        if src.is_dir():
            shutil.copytree(src, dst, symlinks=False)
        else:
            shutil.copy2(src, dst)


async def mutate_location(location, operation, path, destination, store, encryption, mounts):
    if location["kind"] not in NETWORK_KINDS:
        raise HTTPException(403, "Cloud locations are read-only.")
    source, target = _validate_paths(operation, path, destination)
    kind = location["kind"]
    if kind == "nfs":
        if location["id"] not in mounts:
            raise HTTPException(501, "Native NFS modifications require a writable server mount.")
        worker = lambda: _mutate_mounted_nfs(location, mounts, operation, source, target)
    else:
        password = _credentials(store, encryption, location["id"])
        worker = lambda: {
            "smb": _mutate_smb,
            "sftp": _mutate_sftp,
            "ftp": _mutate_ftp,
        }[kind](location, password, operation, source, target)
    try:
        await asyncio.to_thread(worker)
    except HTTPException:
        raise
    except FileNotFoundError as exc:
        raise HTTPException(404, "Source or destination parent not found.") from exc
    except FileExistsError as exc:
        raise HTTPException(409, "Destination already exists.") from exc
    except PermissionError as exc:
        raise HTTPException(403, "Remote filesystem denied the operation.") from exc
    except Exception as exc:
        raise HTTPException(502, "Remote filesystem operation failed.") from exc
    return {"ok": True}
