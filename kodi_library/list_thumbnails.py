"""Persist square collages for custom lists; never regenerate on ordinary reads."""

import asyncio
import io
import json
from pathlib import Path
from uuid import UUID, uuid4

from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError

from .artwork import Artwork, ArtworkError

SIZE = 512
GAP = 6
Image.MAX_IMAGE_PIXELS = 24_000_000


def _layout(count: int) -> list[tuple[int, int, int, int]]:
    half = SIZE // 2
    if count == 1:
        return [(0, 0, SIZE, SIZE)]
    if count == 2:
        return [(0, 0, half, SIZE), (half, 0, SIZE, SIZE)]
    if count == 3:
        return [
            (0, 0, half, SIZE),
            (half, 0, SIZE, half),
            (half, half, SIZE, SIZE),
        ]
    return [
        (0, 0, half, half),
        (half, 0, SIZE, half),
        (0, half, half, SIZE),
        (half, half, SIZE, SIZE),
    ]


def render_thumbnail(images: list[bytes | None]) -> bytes:
    """Compose the first one to four posters, retaining missing-poster placeholders."""
    count = min(len(images), 4)
    canvas = Image.new("RGB", (SIZE, SIZE), "#1b2534")
    draw = ImageDraw.Draw(canvas)
    if count == 0:
        draw.rounded_rectangle((110, 110, 402, 402), radius=24, outline="#51617a", width=5)
        draw.text((230, 240), "+", fill="#a5b7cd")
    for index, (x0, y0, x1, y1) in enumerate(_layout(count)):
        # Leave a narrow gutter between panels and around the outer edge.
        left = x0 + GAP // 2
        top = y0 + GAP // 2
        right = x1 - GAP // 2
        bottom = y1 - GAP // 2
        draw.rectangle((left, top, right - 1, bottom - 1), fill="#2b394d")
        raw = images[index]
        if raw:
            try:
                with Image.open(io.BytesIO(raw)) as source:
                    source.load()
                    poster = ImageOps.fit(
                        ImageOps.exif_transpose(source).convert("RGB"),
                        (right - left, bottom - top),
                        method=Image.Resampling.LANCZOS,
                    )
                canvas.paste(poster, (left, top))
                continue
            except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
                pass
        draw.text((left + 17, top + 17), str(index + 1), fill="#8fa4bb")
    output = io.BytesIO()
    canvas.save(output, format="JPEG", quality=85, optimize=True)
    return output.getvalue()


class ListThumbnails:
    def __init__(self, data_dir: Path, artwork: Artwork):
        self.root = data_dir / "list_thumbnails"
        self.artwork = artwork
        self.lock = asyncio.Lock()

    def _paths(self, list_id: str) -> tuple[Path, Path]:
        # Reject path segments supplied through an untrusted API route.
        canonical_id = str(UUID(list_id))
        if list_id != canonical_id:
            raise KeyError("Unknown list.")
        return (
            self.root / f"{canonical_id}.jpg",
            self.root / f"{canonical_id}.json",
        )

    @staticmethod
    def _stored_revision(meta: Path) -> int | None:
        try:
            return int(json.loads(meta.read_text())["revision"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    async def _generate(self, snapshot: dict, image_path: Path, meta_path: Path) -> None:
        async def load_poster(entry: dict) -> bytes | None:
            if not entry["posters"]:
                return None
            try:
                raw, _ = await self.artwork.get(entry["posters"])
                return raw
            except ArtworkError:
                return None

        posters = await asyncio.gather(*(load_poster(row) for row in snapshot["items"]))
        contents = await asyncio.to_thread(render_thumbnail, posters)

        def save():
            self.root.mkdir(parents=True, exist_ok=True)
            temporary = self.root / f"{snapshot['id']}.{uuid4().hex}.tmp"
            try:
                temporary.write_bytes(contents)
                temporary.replace(image_path)
                meta_path.write_text(json.dumps({"revision": snapshot["revision"]}))
            finally:
                temporary.unlink(missing_ok=True)

        await asyncio.to_thread(save)

    async def refresh(self, store, list_id: str) -> None:
        async with self.lock:
            snapshot = await asyncio.to_thread(store.list_thumbnail_data, list_id)
            path, meta = self._paths(list_id)
            await self._generate(snapshot, path, meta)

    async def get(self, store, list_id: str) -> bytes:
        async with self.lock:
            snapshot = await asyncio.to_thread(store.list_thumbnail_data, list_id)
            path, meta = self._paths(list_id)
            if (
                not await asyncio.to_thread(path.is_file)
                or await asyncio.to_thread(self._stored_revision, meta)
                != snapshot["revision"]
            ):
                await self._generate(snapshot, path, meta)
            return await asyncio.to_thread(path.read_bytes)

    async def delete(self, list_id: str) -> None:
        async with self.lock:
            path, meta = self._paths(list_id)
            for target in (path, meta):
                await asyncio.to_thread(target.unlink, missing_ok=True)
