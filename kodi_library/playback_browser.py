"""Read-only remote media browser for saved playback locations.

Only admin-created locations are read. End-user paths are relative and strictly
validated. Credentials never leave the server. No file modification API.
"""

from __future__ import annotations

import ftplib
import logging
import mimetypes
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import httpx
from cryptography.fernet import InvalidToken
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

LOG = logging.getLogger(__name__)


def safe_path(path: str) -> str:
    if (
        not isinstance(path, str)
        or len(path) > 2048
        or any(ord(char) < 32 or ord(char) == 127 for char in path)
        or "\\" in path
    ):
        raise HTTPException(422, "Invalid media path.")
    parts = path.split("/")
    if any(p in (".", "..") for p in parts) or path.startswith("/"):
        raise HTTPException(422, "Path must be relative to the configured source.")
    return "/".join(p for p in parts if p)


def _credentials(store, encryption, location_id: str) -> str:
    with store.connect() as db:
        row = db.execute(
            "SELECT password_cipher FROM playback_location_options WHERE location_id=?",
            (location_id,),
        ).fetchone()
    cipher = row["password_cipher"] if row else None
    if not cipher:
        return ""
    try:
        return encryption.decrypt_secret(cipher)
    except (InvalidToken, ValueError, TypeError, UnicodeError) as exc:
        LOG.warning("Cannot decrypt saved playback password for location %s", location_id)
        raise HTTPException(
            409, "Saved network password could not be decrypted. Re-enter it in Network settings."
        ) from exc


def _root(location: dict) -> tuple[str, str]:
    from urllib.parse import urlsplit

    parts = urlsplit(location.get("target") or "")
    host = (location.get("host") or parts.hostname or "").strip()
    prefix = (location.get("share") or parts.path.lstrip("/")).strip("/")
    if not host:
        raise HTTPException(422, "Configured network host is missing.")
    return host, prefix


def _ftp_connection(location: dict, password: str) -> ftplib.FTP:
    host, _ = _root(location)
    conn = ftplib.FTP()
    conn.connect(host, int(location.get("port") or 21), timeout=8)
    conn.login(location.get("username") or "anonymous", password)
    return conn


def _sftp_connection(location: dict, password: str):
    """SSH host keys must already be trusted by the server administrator."""
    import paramiko

    host, _ = _root(location)
    ssh = paramiko.SSHClient()
    ssh.load_system_host_keys()
    ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
    ssh.connect(
        hostname=host,
        port=int(location.get("port") or 22),
        username=location.get("username") or None,
        password=password or None,
        timeout=8,
        auth_timeout=8,
        allow_agent=False,
        look_for_keys=False,
    )
    return ssh, ssh.open_sftp()


def _local_root(location: dict, mounts: dict[str, str]) -> Path:
    configured = mounts.get(location["id"])
    if not configured:
        raise HTTPException(
            501,
            "NFS browsing requires a read-only mount on the web server "
            "and PLAYBACK_NFS_MOUNTS configuration.",
        )
    root = Path(configured).resolve()
    if not root.is_dir():
        raise HTTPException(503, "Configured NFS mount is inaccessible.")
    return root


def _full_path(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root):
        raise HTTPException(403, "File is outside of the configured share.")
    return candidate


def _sync_browse(
    location: dict,
    relative: str,
    password: str,
    mounts: dict[str, str],
) -> list[dict]:
    kind = location["kind"]
    host, prefix = _root(location) if kind != "nfs" else ("", "")
    if kind == "nfs":
        folder = _full_path(_local_root(location, mounts), relative)
        if not folder.is_dir():
            raise HTTPException(404, "Directory not found.")
        return [
            {
                "name": child.name,
                "is_dir": child.is_dir(),
                "size": child.stat().st_size if child.is_file() else 0,
            }
            for child in folder.iterdir()
            if not child.is_symlink() and not child.name.startswith(".")
        ][:2000]
    if kind == "sftp":
        ssh, sftp = _sftp_connection(location, password)
        try:
            folder = "/".join((prefix, relative)).strip("/") or "."
            rows = []
            for item in sftp.listdir_attr(folder):
                import stat

                if item.filename.startswith("."):
                    continue
                directory = stat.S_ISDIR(item.st_mode)
                rows.append(
                    {
                        "name": item.filename,
                        "is_dir": directory,
                        "size": 0 if directory else int(item.st_size),
                    }
                )
                if len(rows) >= 2000:
                    break
            return rows
        finally:
            sftp.close()
            ssh.close()
    if kind == "ftp":
        conn = _ftp_connection(location, password)
        try:
            folder = "/".join((prefix, relative)).strip("/")
            rows = []
            for name, info in conn.mlsd(folder):
                if name in (".", "..") or name.startswith("."):
                    continue
                rows.append(
                    {
                        "name": name,
                        "is_dir": info.get("type") == "dir",
                        "size": int(info.get("size") or 0),
                    }
                )
                if len(rows) >= 2000:
                    break
            return rows
        finally:
            conn.quit()
    if kind == "smb":
        import smbclient

        username = location.get("username") or ""
        domain = location.get("workgroup") or ""
        smbclient.register_session(
            host,
            username=domain + "\\" + username if domain and username else username,
            password=password,
            port=int(location.get("port") or 445),
            connection_timeout=8,
        )
        sections = [s for s in (prefix + "/" + relative).split("/") if s]
        if not sections:
            raise HTTPException(422, "Configure an SMB share name to browse.")
        folder = "\\\\" + host + "\\" + "\\".join(sections)
        rows = []
        with smbclient.scandir(folder) as it:
            for entry in it:
                if entry.name.startswith("."):
                    continue
                is_dir = entry.is_dir()
                rows.append(
                    {
                        "name": entry.name,
                        "is_dir": is_dir,
                        "size": 0 if is_dir else entry.stat().st_size,
                    }
                )
                if len(rows) >= 2000:
                    break
        return rows
    raise HTTPException(501, "This network protocol needs a separate connector.")


def _sync_stream(
    location: dict,
    relative: str,
    password: str,
    mounts: dict[str, str],
    start: int,
    end: int | None,
):
    kind = location["kind"]
    chunk = 512 * 1024
    if kind == "nfs":
        file = _full_path(_local_root(location, mounts), relative)
        if not file.is_file():
            raise HTTPException(404, "File not found.")
        size = file.stat().st_size

        def content():
            with file.open("rb") as stream:
                stream.seek(start)
                left = max(0, (size if end is None else min(size, end + 1)) - start)
                while left:
                    data = stream.read(min(left, chunk))
                    if not data:
                        break
                    left -= len(data)
                    yield data

        return size, content
    host, prefix = _root(location)
    remote = "/".join(filter(None, (prefix, relative)))
    if kind == "smb":
        import smbclient

        username = location.get("username") or ""
        domain = location.get("workgroup") or ""
        smbclient.register_session(
            host,
            username=domain + "\\" + username if domain and username else username,
            password=password,
            port=int(location.get("port") or 445),
            connection_timeout=8,
        )
        file = "\\\\" + host + "\\" + remote.replace("/", "\\")
        size = smbclient.stat(file).st_size

        def content():
            with smbclient.open_file(file, mode="rb") as stream:
                stream.seek(start)
                left = max(0, (size if end is None else min(size, end + 1)) - start)
                while left:
                    data = stream.read(min(left, chunk))
                    if not data:
                        break
                    left -= len(data)
                    yield data

        return size, content
    if kind == "sftp":
        ssh, sftp = _sftp_connection(location, password)
        try:
            size = sftp.stat(remote).st_size
        finally:
            sftp.close()
            ssh.close()

        def content():
            session, client = _sftp_connection(location, password)
            try:
                with client.file(remote, mode="rb") as stream:
                    stream.seek(start)
                    left = max(0, (size if end is None else min(size, end + 1)) - start)
                    while left:
                        data = stream.read(min(left, chunk))
                        if not data:
                            break
                        left -= len(data)
                        yield data
            finally:
                client.close()
                session.close()

        return size, content
    if kind == "ftp":
        conn = _ftp_connection(location, password)
        try:
            size = conn.size(remote)
        finally:
            conn.quit()
        if size is None:
            raise HTTPException(404, "FTP file size unavailable.")

        def content():
            connection = _ftp_connection(location, password)
            try:
                with connection.transfercmd("RETR " + remote, rest=start or None) as sock:
                    left = max(0, (size if end is None else min(size, end + 1)) - start)
                    while left:
                        data = sock.recv(min(left, chunk))
                        if not data:
                            break
                        left -= len(data)
                        yield data
            finally:
                connection.close()

        return size, content
    raise HTTPException(501, "Streaming for this protocol is not implemented.")


async def browse_location(location: dict, path: str, store, encryption, mounts: dict) -> list[dict]:
    import asyncio

    relative = safe_path(path)
    if location["kind"] in {"smb", "ftp", "sftp", "nfs"}:
        password = _credentials(store, encryption, location["id"])
        try:
            rows = await asyncio.wait_for(
                asyncio.to_thread(_sync_browse, location, relative, password, mounts),
                timeout=25,
            )
        except HTTPException:
            raise
        except ImportError as exc:
            LOG.error("Network connector dependency missing for %s: %s", location["kind"], exc)
            raise HTTPException(
                503, "Network connector unavailable on the server. Update the server image."
            ) from exc
        except Exception as exc:
            # Remote protocols raise protocol-specific exceptions that do not
            # necessarily inherit OSError (e.g. SSH authentication failures).
            # Return JSON rather than leaking an HTML/plain-text HTTP 500.
            LOG.warning(
                "Playback browse failed for %s (%s): %s", location["kind"], type(exc).__name__, exc
            )
            raise HTTPException(
                502,
                "Could not browse this network location. Check its host, share and credentials.",
            ) from exc
        rows.sort(key=lambda row: (not row["is_dir"], row["name"].casefold()))
        return [{"path": "/".join(filter(None, (relative, row["name"]))), **row} for row in rows]
    if location["kind"] in {"onedrive", "google_drive", "dropbox", "box", "pcloud"}:
        return await cloud_browse(location, relative, store, encryption)
    raise HTTPException(501, "Browsing is unavailable for this location type.")


async def cloud_browse(location: dict, path: str, store, encryption) -> list[dict]:
    import json

    saved = store.playback_cloud_credentials(location["id"])
    if not saved or saved["provider"] != location["kind"]:
        raise HTTPException(409, "Connect this cloud account first in web settings.")
    try:
        token = json.loads(encryption.decrypt_secret(saved["token_cipher"]))
        access = token["access_token"]
    except (ValueError, KeyError, TypeError, InvalidToken, UnicodeError) as exc:
        raise HTTPException(
            409, "Cloud authentication needs renewing. Reconnect this provider in Network settings."
        ) from exc
    kind = location["kind"]
    headers = {"Authorization": "Bearer " + access}
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            if kind == "google_drive":
                parent = path or "root"
                # IDs are provider opaque IDs, not local file paths.
                if "/" in parent or "'" in parent:
                    raise HTTPException(422, "Invalid Google Drive folder ID.")
                response = await client.get(
                    "https://www.googleapis.com/drive/v3/files",
                    headers=headers,
                    params={
                        "q": f"'{parent}' in parents and trashed = false",
                        "fields": "nextPageToken,files(id,name,mimeType,size)",
                        "page_size": 200,
                    },
                )
                response.raise_for_status()
                nodes = response.json().get("files", [])
                return [
                    {
                        "name": x["name"],
                        "path": x["id"],
                        "is_dir": x.get("mimeType") == "application/vnd.google-apps.folder",
                        "size": int(x.get("size") or 0),
                    }
                    for x in nodes
                ]
            if kind == "onedrive":
                base = "https://graph.microsoft.com/v1.0/me/drive/"
                endpoint = base + (
                    "root/children" if not path else "items/" + quote(path, safe="") + "/children"
                )
                response = await client.get(endpoint, headers=headers)
                response.raise_for_status()
                return [
                    {
                        "name": x["name"],
                        "path": x["id"],
                        "is_dir": "folder" in x,
                        "size": int(x.get("size") or 0),
                    }
                    for x in response.json().get("value", [])
                ]
            if kind == "dropbox":
                response = await client.post(
                    "https://api.dropboxapi.com/2/files/list_folder",
                    headers=headers,
                    json={"path": "/" + path if path else "", "limit": 200},
                )
                response.raise_for_status()
                return [
                    {
                        "name": x["name"],
                        "path": x.get("path_display", "").lstrip("/"),
                        "is_dir": x.get(".tag") == "folder",
                        "size": x.get("size", 0),
                    }
                    for x in response.json().get("entries", [])
                ]
            if kind == "box":
                response = await client.get(
                    "https://api.box.com/2.0/folders/" + quote(path or "0", safe="") + "/items",
                    headers=headers,
                    params={"limit": 200, "fields": "id,name,size,type"},
                )
                response.raise_for_status()
                return [
                    {
                        "name": x["name"],
                        "path": x["id"],
                        "is_dir": x["type"] == "folder",
                        "size": int(x.get("size") or 0),
                    }
                    for x in response.json().get("entries", [])
                ]
            if kind == "pcloud":
                host = token.get("api_hostname", "api.pcloud.com")
                if host not in ("api.pcloud.com", "eapi.pcloud.com"):
                    raise HTTPException(422, "Untrusted pCloud endpoint.")
                response = await client.get(
                    "https://" + host + "/listfolder",
                    headers=headers,
                    params={"folderid": path or "0"},
                )
                response.raise_for_status()
                data = response.json()
                if data.get("result") != 0:
                    raise HTTPException(502, "pCloud refused folder listing.")
                return [
                    {
                        "name": x["name"],
                        "path": str(x.get("folderid") or x.get("fileid")),
                        "is_dir": bool(x.get("isfolder")),
                        "size": int(x.get("size") or 0),
                    }
                    for x in data.get("metadata", {}).get("contents", [])
                ]
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        raise HTTPException(
            409 if status in (401, 403) else 502,
            "Cloud authorization expired."
            if status in (401, 403)
            else "Cloud provider unavailable.",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Cloud network request failed.") from exc
    except (ValueError, KeyError, TypeError) as exc:
        LOG.warning("Unexpected cloud provider response for %s: %s", kind, type(exc).__name__)
        raise HTTPException(502, "Cloud provider returned an invalid folder listing.") from exc
    raise HTTPException(501, "Cloud provider not implemented.")


async def stream_location(
    location: dict,
    path: str,
    range_header: str | None,
    store,
    encryption,
    mounts: dict,
):
    import asyncio
    import re

    relative = safe_path(path)
    if not relative or location["kind"] not in {"smb", "ftp", "sftp", "nfs"}:
        raise HTTPException(501, "Direct streaming is not enabled for this source.")
    start, end = 0, None
    if range_header:
        match = re.fullmatch(r"bytes=(\d+)-(\d*)", range_header)
        if not match:
            raise HTTPException(416, "Unsupported byte range.")
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else None
    password = _credentials(store, encryption, location["id"])
    try:
        size, content = await asyncio.wait_for(
            asyncio.to_thread(_sync_stream, location, relative, password, mounts), timeout=18
        )
    except HTTPException:
        raise
    except ImportError as exc:
        raise HTTPException(
            503, "Network streaming connector unavailable. Update the server image."
        ) from exc
    except Exception as exc:
        LOG.warning(
            "Playback streaming failed for %s (%s): %s", location["kind"], type(exc).__name__, exc
        )
        raise HTTPException(
            502, "Cannot open the remote file. Check the network and credentials."
        ) from exc
    if start >= size or (end is not None and end < start):
        raise HTTPException(416, "Requested range out of bounds.")
    finish = size - 1 if end is None else min(end, size - 1)
    mime = mimetypes.guess_type(relative)[0] or "application/octet-stream"
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(finish - start + 1),
        "Content-Disposition": "inline; filename*=UTF-8''" + quote(PurePosixPath(relative).name),
    }
    if range_header:
        headers["Content-Range"] = f"bytes {start}-{finish}/{size}"
    return StreamingResponse(
        content(), status_code=206 if range_header else 200, media_type=mime, headers=headers
    )
