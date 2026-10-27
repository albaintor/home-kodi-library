import asyncio
import shutil
import subprocess

import pytest
from fastapi import HTTPException

from home_media_catalog import playback_media
from home_media_catalog.playback_media import (
    RangeSource,
    media_stream,
    normalize_probe,
    parse_video_copy,
    probe,
    stream_command,
    subtitle_vtt,
)

FFPROBE_SAMPLE = {
    "format": {"duration": "5400.5", "format_name": "matroska,webm", "bit_rate": "25000000"},
    "streams": [
        {
            "index": 0,
            "codec_type": "video",
            "codec_name": "hevc",
            "width": 3840,
            "height": 2160,
            "pix_fmt": "yuv420p10le",
            "color_transfer": "smpte2084",
            "avg_frame_rate": "24000/1001",
            "disposition": {"default": 1},
        },
        {
            "index": 1,
            "codec_type": "audio",
            "codec_name": "truehd",
            "channels": 8,
            "tags": {"language": "fre", "title": "VF Atmos"},
            "disposition": {"default": 1},
        },
        {"index": 2, "codec_type": "audio", "codec_name": "ac3", "tags": {"language": "eng"}},
        {"index": 3, "codec_type": "subtitle", "codec_name": "subrip", "tags": {"language": "fre"}},
        {
            "index": 4,
            "codec_type": "subtitle",
            "codec_name": "hdmv_pgs_subtitle",
            "tags": {"language": "und"},
            "disposition": {"forced": 1},
        },
        {
            "index": 5,
            "codec_type": "video",
            "codec_name": "mjpeg",
            "disposition": {"attached_pic": 1},
        },
    ],
    "chapters": [
        {"start_time": "0.000", "end_time": "600.0", "tags": {"title": "Ouverture"}},
        {"start_time": "600.0", "end_time": "5400.5", "tags": {}},
    ],
}


def test_normalize_probe_lists_tracks_chapters_and_hdr():
    info = normalize_probe(FFPROBE_SAMPLE)
    assert info["duration"] == 5400.5
    assert info["video"]["codec"] == "hevc" and info["video"]["hdr"] == "HDR10"
    assert info["video"]["frame_rate"] == 23.976
    assert [(a["index"], a["order"], a["language"]) for a in info["audio"]] == [
        (1, 0, "fre"),
        (2, 1, "eng"),
    ]
    assert info["audio"][0]["title"] == "VF Atmos" and info["audio"][0]["default"]
    assert [(s["index"], s["kind"]) for s in info["subtitles"]] == [(3, "text"), (4, "bitmap")]
    assert info["subtitles"][1]["language"] is None and info["subtitles"][1]["forced"]
    assert info["chapters"] == [
        {"start": 0.0, "end": 600.0, "title": "Ouverture"},
        {"start": 600.0, "end": 5400.5, "title": None},
    ]


def test_stream_command_copies_compatible_video_and_maps_selected_audio():
    info = normalize_probe(FFPROBE_SAMPLE)
    info["video"].update(codec="h264", pix_fmt="yuv420p", hdr=None)
    command, mode = stream_command(
        "http://127.0.0.1:1/x", info, start=120.5, audio=2, video_copy=parse_video_copy("h264")
    )
    assert mode == "copy"
    assert command[command.index("-ss") + 1] == "120.500"
    assert command.index("-ss") < command.index("-i")
    assert ["-map", "0:0", "-c:v", "copy"] == command[
        command.index("-map") : command.index("-map") + 4
    ]
    assert "0:2" in command and "0:1" not in command
    assert command[-1] == "pipe:1" and "frag_keyframe+empty_moov+default_base_moof" in command


def test_stream_command_burns_bitmap_subtitles_and_rejects_text_burn():
    info = normalize_probe(FFPROBE_SAMPLE)
    command, mode = stream_command("u", info, subtitle=4, video_copy=parse_video_copy("hevc,h264"))
    assert mode == "transcode"
    graph = command[command.index("-filter_complex") + 1]
    assert graph.startswith("[0:4][0:0]scale2ref") and "overlay" in graph
    # Default audio track is the one flagged default.
    assert "0:1" in command
    with pytest.raises(HTTPException) as error:
        stream_command("u", info, subtitle=3)
    assert error.value.status_code == 422
    with pytest.raises(HTTPException):
        stream_command("u", info, audio=9)


def test_parse_video_copy_ignores_unknown_codecs():
    assert parse_video_copy("h264,vp9,hevc,../x") == {"h264", "hevc"}


needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="FFmpeg is not installed"
)


@pytest.fixture
def sample(tmp_path):
    srt = tmp_path / "fr.srt"
    srt.write_text("1\n00:00:00,500 --> 00:00:02,000\nBonjour\n\n", encoding="utf-8")
    meta = tmp_path / "chapters.txt"
    meta.write_text(
        ";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=2000\ntitle=Début\n"
        "[CHAPTER]\nTIMEBASE=1/1000\nSTART=2000\nEND=4000\ntitle=Fin\n",
        encoding="utf-8",
    )
    movie = tmp_path / "movie.mkv"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=24:duration=4",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
            "-f", "lavfi", "-i", "sine=frequency=880:duration=4",
            "-i", str(srt), "-i", str(meta),
            "-map", "0", "-map", "1", "-map", "2", "-map", "3", "-map_chapters", "4",
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "ac3", "-c:s", "srt",
            "-metadata:s:a:1", "language=eng", "-metadata:s:s:0", "language=fre",
            str(movie),
        ],
        check=True,
    )  # fmt: skip
    data = movie.read_bytes()
    reads = []

    def opener(start, end):
        reads.append((start, end))
        yield data[start : end + 1]

    return RangeSource(f"test:{tmp_path}", len(data), opener), reads


@needs_ffmpeg
def test_probe_extract_and_stream_through_loopback_bridge(sample, tmp_path):
    source, reads = sample
    playback_media._PROBES.clear()

    async def scenario():
        info = await probe(source)
        assert [a["index"] for a in info["audio"]] == [1, 2]
        assert info["subtitles"][0]["kind"] == "text"
        assert [c["title"] for c in info["chapters"]] == ["Début", "Fin"]
        before = len(reads)
        assert await probe(source) is info and len(reads) == before

        vtt = await subtitle_vtt(source, info, 3, tmp_path / "data")
        assert vtt.read_text(encoding="utf-8").startswith("WEBVTT")
        assert "Bonjour" in vtt.read_text(encoding="utf-8")

        response = await media_stream(
            source, info, start=1, audio=2, video_copy=parse_video_copy("h264")
        )
        assert response.headers["x-playback-mode"] == "copy"
        body = b"".join([chunk async for chunk in response.body_iterator])
        assert body[4:8] == b"ftyp" and b"moof" in body
        return info

    info = asyncio.run(scenario())
    assert info["video"]["codec"] == "h264"
