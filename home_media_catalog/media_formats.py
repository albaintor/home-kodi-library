"""Display labels from explicit source metadata; never infer HDR/Atmos from a disc."""

import re

from .utils import number

# Kodi/Matroska stereoscopic layouts. Unknown values are not evidence of 3D.
# https://github.com/xbmc/xbmc/blob/master/xbmc/guilib/StereoscopicsManager.cpp
STEREOSCOPIC_LAYOUTS = {
    "left_right",
    "right_left",
    "top_bottom",
    "bottom_top",
    "checkerboard_rl",
    "checkerboard_lr",
    "row_interleaved_rl",
    "row_interleaved_lr",
    "col_interleaved_rl",
    "col_interleaved_lr",
    "anaglyph_cyan_red",
    "anaglyph_green_magenta",
    "anaglyph_yellow_blue",
    "block_lr",
    "block_rl",
}


def canonical_format(value: str) -> str:
    """Normalize video resolution without changing disc-format identities."""
    label = value.strip()
    return "4K" if label.casefold() in {"4k", "4k uhd"} else label


def canonical_source_format(value: str, source_type: str) -> str:
    """Do not confuse an Ultra HD Blu-ray disc with Kodi's 4K video resolution."""
    label = value.strip()
    if source_type == "clz" and label.casefold() in {
        "4k uhd",
        "blu-ray 4k",
        "4k blu-ray",
        "uhd blu-ray",
        "ultra hd blu-ray",
    }:
        return "Blu-ray 4K"
    return canonical_format(label)


THREE_D_FILENAME = re.compile(
    r"(?<![a-z0-9])(?:3d|hsbs|fsbs|sbs|htab|ftab|tab|mvc)(?![a-z0-9])",
    re.IGNORECASE,
)


def technical_formats(labels: list[str]) -> list[str]:
    found = set()
    for label in labels:
        value = re.sub(r"[\s_\-]", "", str(label).casefold()).split("[", 1)[0]
        if "atmos" in value:
            found.add("Dolby Atmos")
        if "dolbyvision" in value or value in {"dv", "dovi"}:
            found.add("Dolby Vision")
        if "hdr10plus" in value or "hdr10+" in value:
            found.add("HDR10+")
        elif "hdr10" in value:
            found.add("HDR10")
        if value == "hlg":
            found.add("HLG")
        if "3d" in value:
            found.add("3D")
        if "truehd" in value:
            found.add("Dolby TrueHD")
        if "dtshdmasteraudio" in value or value in {"dtshdma", "dtshd_ma"}:
            found.add("DTS-HD MA")
        elif "dtshd" in value:
            found.add("DTS-HD")
        if "dts:x" in value or value == "dtsx":
            found.add("DTS:X")
        elif value == "dts" or value.startswith("dts5"):
            found.add("DTS")
        if "dolbydigitalplus" in value or value in {"eac3", "eac3atmos"}:
            found.add("Dolby Digital+")
        elif "dolbydigital" in value or value == "ac3":
            found.add("Dolby Digital")
    return sorted(found)


def stream_metadata(rows: list[dict], filename: str | None = None) -> dict:
    video, audio, subtitles, labels = [], [], [], []
    resolutions = set()
    confirmed_3d = False
    for original in rows:
        row = {key.casefold(): value for key, value in original.items()}
        kind = int(number(row.get("istreamtype"), -1))
        if kind == 0:
            width, height = (
                int(number(row.get("ivideowidth"))),
                int(number(row.get("ivideoheight"))),
            )
            resolution = (
                "8K"
                if width >= 7000 or height >= 4000
                else "4K"
                if width >= 3000 or height >= 2000
                else "1080p"
                if width >= 1800 or height >= 1000
                else "720p"
                if width >= 1200 or height >= 700
                else None
            )
            if resolution:
                resolutions.add(resolution)
            stereo = str(row.get("strstereomode") or "")
            hdr = str(row.get("strhdrtype") or "")
            detail = str(row.get("strhdrdetail") or "")
            labels.extend([hdr, detail])
            # Kodi's stereo mode can be incorrectly set on a regular 2D file.
            # Corroborate with an explicit 3D indicator in the file name.
            if stereo.strip().casefold() in STEREOSCOPIC_LAYOUTS and THREE_D_FILENAME.search(
                (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
            ):
                confirmed_3d = True
            video.append(
                {
                    "codec": row.get("strvideocodec"),
                    "width": width,
                    "height": height,
                    "hdr_type": hdr or None,
                    "hdr_detail": detail or None,
                    "stereo_mode": stereo or None,
                    "language": row.get("strvideolanguage"),
                }
            )
        elif kind == 1:
            codec = str(row.get("straudiocodec") or "")
            labels.append(codec)
            audio.append(
                {
                    "codec": codec,
                    "channels": int(number(row.get("iaudiochannels"))),
                    "language": row.get("straudiolanguage"),
                }
            )
        elif kind == 2 and row.get("strsubtitlelanguage"):
            subtitles.append(str(row["strsubtitlelanguage"]))
    return {
        "video_streams": video,
        "audio_streams": audio,
        "subtitles": sorted(set(subtitles)),
        "formats": sorted(
            resolutions
            | (set(technical_formats(labels)) - {"3D"})
            | ({"3D"} if confirmed_3d else set())
        ),
    }
