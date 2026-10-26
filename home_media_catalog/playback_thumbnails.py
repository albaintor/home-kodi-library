"""Bounded, cached media thumbnails shared by the web and native API.

Do not persist remote passwords or OAuth access tokens in thumbnails. Decode
images with Pillow and video keyframes with the system ffmpeg, as Kodi does.
The input is fetched in bounded chunks: a listing never downloads the media.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import os
import subprocess
import time
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import httpx
from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError

from .playback_browser import _credentials, _sync_stream, safe_path
from .playback_video_preview import extract_video_frame

LOG = logging.getLogger(__name__)
IMAGE_TYPES = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".bmp",
    ".tif",
    ".tiff",
    ".heic",
    ".heif",
    ".avif",
}
VIDEO_TYPES = {".mp4", ".m4v", ".mov", ".mkv", ".avi", ".ts", ".m2ts", ".webm", ".mpg", ".mpeg"}
MAX_IMAGE = 12 * 1024 * 1024
MAX_VIDEO = 28 * 1024 * 1024
MAX_OUTPUT = 512 * 1024
MAX_AGE = 7 * 86400

# An image decompression bomb must not exhaust a small NAS.
Image.MAX_IMAGE_PIXELS = 48_000_000


def _jpeg_thumbnail(source: bytes, extension: str) -> bytes | None:
    if extension in IMAGE_TYPES:
        try:
            with Image.open(io.BytesIO(source)) as original:
                image = ImageOps.exif_transpose(original)
                image.thumbnail((360, 256), Image.Resampling.LANCZOS)
                if image.mode != "RGB":
                    image = image.convert("RGB")
                output = io.BytesIO()
                image.save(output, format="JPEG", quality=78, optimize=True)
                result = output.getvalue()
                return result if len(result) <= MAX_OUTPUT else None
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
            return None
    if extension not in VIDEO_TYPES:
        return None
    # Fallback for providers unable to offer a seekable Range source:
    # decode the initial stream progressively. Network shares normally use
    # the Kodi one-third position via extract_video_frame() below.
    # Cloud providers without arbitrary byte-range support still give us a
    # bounded prefix. Prefer a frame several seconds in (rather than black
    # intro frames), falling back to the first decodable frame for short clips.
    for skip in (5, 0):
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-analyzeduration",
            "3000000",
            "-probesize",
            "6000000",
            "-i",
            "pipe:0",
        ]
        if skip:
            # Output-side -ss can advance on non-seekable stdin.
            command.extend(["-ss", str(skip)])
        command.extend(
            [
                "-map",
                "0:v:0",
                "-frames:v",
                "1",
                "-vf",
                "scale=360:256:force_original_aspect_ratio=decrease",
                "-f",
                "image2pipe",
                "-vcodec",
                "mjpeg",
                "-q:v",
                "5",
                "pipe:1",
            ]
        )
        try:
            child = subprocess.run(
                command, input=source, capture_output=True, timeout=14, check=False
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            continue
        if child.returncode == 0 and 0 < len(child.stdout) <= MAX_OUTPUT:
            return child.stdout
    return None


def _read_limited(generator, limit: int) -> bytes:
    data = bytearray()
    iterator = generator()
    try:
        for chunk in iterator:
            if not chunk:
                break
            remaining = limit - len(data)
            if remaining <= 0:
                break
            data.extend(chunk[:remaining])
            if len(data) >= limit:
                break
    finally:
        closer = getattr(iterator, "close", None)
        if closer:
            closer()
    return bytes(data)


def _cloud_data(location: dict, path: str, store, encryption, limit: int) -> bytes:
    saved = store.playback_cloud_credentials(location["id"])
    if not saved or saved["provider"] != location["kind"]:
        raise HTTPException(409, "Connect the cloud account on the web server first.")
    try:
        token = json.loads(encryption.decrypt_secret(saved["token_cipher"]))
        access = token["access_token"]
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(409, "Reconnect the cloud account.") from exc

    provider = location["kind"]
    headers = {"Authorization": "Bearer " + access, "Range": f"bytes=0-{limit - 1}"}
    with httpx.Client(timeout=18, follow_redirects=True) as client:
        if provider == "onedrive":
            url = (
                "https://graph.microsoft.com/v1.0/me/drive/items/"
                + quote(path, safe="")
                + "/content"
            )
            method, kwargs = "GET", {"headers": headers}
        elif provider == "google_drive":
            url = "https://www.googleapis.com/drive/v3/files/" + quote(path, safe="")
            method, kwargs = "GET", {"headers": headers, "params": {"alt": "media"}}
        elif provider == "box":
            url = "https://api.box.com/2.0/files/" + quote(path, safe="") + "/content"
            method, kwargs = "GET", {"headers": headers}
        elif provider == "dropbox":
            headers.pop("Range", None)
            headers["Dropbox-API-Arg"] = json.dumps({"path": "/" + path.lstrip("/")})
            url = "https://content.dropboxapi.com/2/files/download"
            method, kwargs = "POST", {"headers": headers}
        elif provider == "pcloud":
            host = token.get("api_hostname", "api.pcloud.com")
            if host not in {"api.pcloud.com", "eapi.pcloud.com"}:
                raise HTTPException(422, "Untrusted pCloud host.")
            link = client.get(
                "https://" + host + "/getfilelink",
                headers={"Authorization": "Bearer " + access},
                params={"fileid": path},
            )
            link.raise_for_status()
            metadata = link.json()
            hosts = metadata.get("hosts") or []
            remote_path = metadata.get("path", "")
            if metadata.get("result") != 0 or not hosts or not remote_path.startswith("/"):
                raise HTTPException(502, "pCloud thumbnail link unavailable.")
            url = "https://" + hosts[0] + remote_path
            method, kwargs = "GET", {"headers": {"Range": headers["Range"]}}
        else:
            raise HTTPException(501, "Cloud thumbnail is unavailable.")
        # Stream the response: httpx.get() would consume an entire movie
        # before this function could apply its thumbnail byte cap.
        with client.stream(method, url, **kwargs) as response:
            if response.status_code not in (200, 206):
                raise HTTPException(502, "Cannot read source media for its thumbnail.")
            chunks = bytearray()
            for chunk in response.iter_bytes():
                if not chunk:
                    continue
                remaining = limit - len(chunks)
                chunks.extend(chunk[:remaining])
                if len(chunks) >= limit:
                    break
            return bytes(chunks)


def _thumbnail_sync(
    location: dict,
    path: str,
    store,
    encryption,
    mounts: dict,
    cache: Path,
    filename: str = "",
) -> bytes:
    path = safe_path(path)
    # OneDrive uses opaque IDs; the display name supplies the extension.
    if (
        not isinstance(filename, str)
        or len(filename) > 512
        or any(ord(char) < 32 for char in filename)
    ):
        raise HTTPException(422, "Invalid media filename.")
    extension = PurePosixPath(filename or path).suffix.lower()
    if extension not in IMAGE_TYPES | VIDEO_TYPES or not path:
        raise HTTPException(415, "No thumbnail decoder for this file type.")
    limit = MAX_IMAGE if extension in IMAGE_TYPES else MAX_VIDEO
    cache.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(f"{location['id']}\0{path}:{filename}\0v4".encode()).hexdigest()
    target = cache / f"{digest}.jpg"
    if target.is_file() and time.time() - target.stat().st_mtime < MAX_AGE:
        return target.read_bytes()
    if location["kind"] in {"smb", "ftp", "sftp", "nfs"}:
        secret = _credentials(store, encryption, location["id"])
        result = None
        if extension in VIDEO_TYPES:
            # ffprobe reads duration and ffmpeg seeks to duration/3 over an
            # ephemeral localhost Range relay. No 24 GB file is staged.
            try:
                result = extract_video_frame(location, path, secret, mounts)
            except Exception as exc:
                LOG.debug("One-third preview unavailable (%s)", type(exc).__name__)
        if result is None:
            size, supplier = _sync_stream(location, path, secret, mounts, 0, limit - 1)
            raw = _read_limited(supplier, min(size, limit))
            if not raw:
                raise HTTPException(422, "The remote file is empty.")
            result = _jpeg_thumbnail(raw, extension)
    else:
        result = None
        if location["kind"] == "onedrive":
            # Prefer Microsoft's own thumbnail, including for videos.
            saved = store.playback_cloud_credentials(location["id"])
            if saved:
                try:
                    auth = json.loads(encryption.decrypt_secret(saved["token_cipher"]))
                    with httpx.Client(timeout=14, follow_redirects=True) as client:
                        image = client.get(
                            "https://graph.microsoft.com/v1.0/me/drive/items/"
                            + quote(path, safe="")
                            + "/thumbnails/0/medium/content",
                            headers={"Authorization": "Bearer " + auth["access_token"]},
                        )
                        if image.status_code == 200 and len(image.content) <= MAX_IMAGE:
                            result = _jpeg_thumbnail(image.content, ".jpg")
                except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                    LOG.debug("OneDrive thumbnail unavailable: %s", type(exc).__name__)
        if result is None:
            raw = _cloud_data(location, path, store, encryption, limit)
            if not raw:
                raise HTTPException(422, "The remote file is empty.")
            result = _jpeg_thumbnail(raw, extension)
    if result is None:
        raise HTTPException(
            422,
            "No thumbnail could be extracted from this file. "
            "For videos, the container may need bytes beyond the bounded preview.",
        )
    temporary = target.with_suffix(".tmp-" + str(os.getpid()))
    try:
        temporary.write_bytes(result)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return result


async def thumbnail_location(
    location: dict,
    path: str,
    store,
    encryption,
    mounts: dict,
    data_dir: Path,
    filename: str = "",
) -> bytes:
    try:
        if location["kind"] in {"onedrive", "google_drive", "dropbox", "box", "pcloud"}:
            from .playback_cloud import CloudOAuthError
            try:
                await encryption.credentials(location["id"], location["kind"])
            except CloudOAuthError as exc:
                raise HTTPException(409, str(exc)) from exc
        return await asyncio.wait_for(
            asyncio.to_thread(
                _thumbnail_sync,
                location,
                path,
                store,
                encryption,
                mounts,
                data_dir / "playback-thumbnails",
                filename,
            ),
            timeout=42,
        )
    except HTTPException:
        raise
    except TimeoutError as exc:
        raise HTTPException(504, "Thumbnail extraction timed out.") from exc
    except Exception as exc:
        LOG.warning("Remote thumbnail failed: %s (%s)", location["kind"], type(exc).__name__)
        raise HTTPException(502, "Unable to extract remote thumbnail.") from exc
