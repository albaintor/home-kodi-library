"""Display labels from explicit source metadata; never infer HDR/Atmos from a disc."""

import re

from .utils import number


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


def stream_metadata(rows: list[dict]) -> dict:
    video, audio, subtitles, labels = [], [], [], []
    resolutions = set()
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
            if stereo.casefold() not in {"", "mono", "0", "none"}:
                labels.append("3D")
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
        "formats": sorted(resolutions | set(technical_formats(labels))),
    }
