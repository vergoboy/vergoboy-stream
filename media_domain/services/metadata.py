"""Reading facts out of an ffprobe result.

Pure functions over an already-parsed probe dict: no subprocess, no I/O. The
ffprobe wrapper itself stays in app.py until the encode slice moves it — the
capabilities endpoint needs these numbers, not the probe runner.

`TEXT_SUBTITLE_CODECS` moved with them: its only consumer was the accessor
below it, so keeping a copy in app.py would be the kind of duplicated
definition rule 2' forbids.
"""
from __future__ import annotations

TEXT_SUBTITLE_CODECS = {"subrip", "ass", "ssa", "mov_text", "webvtt", "text"}

_MAX_LANG_LEN = 8


def find_text_subtitle_streams(probe_data: dict) -> list:
    """Subtitle streams a browser can render as text.

    Bitmap subs (hdmv_pgs_subtitle, dvd_subtitle) are excluded: they need
    OCR/burn-in, which is a transcode decision, not a playback one.
    """
    subs = []
    for s in probe_data.get("streams", []):
        if s.get("codec_type") == "subtitle" and s.get("codec_name") in TEXT_SUBTITLE_CODECS:
            tags = s.get("tags") or {}
            subs.append({
                "index": s["index"],
                "lang": (tags.get("language") or "")[:_MAX_LANG_LEN],
                "title": tags.get("title") or "",
            })
    return subs


def get_duration_s(probe_data: dict) -> float:
    """Duration in seconds, preferring the container's value."""
    try:
        d = float(probe_data.get("format", {}).get("duration") or 0)
        if d > 0:
            return d
    except (TypeError, ValueError):
        pass
    for s in probe_data.get("streams", []):
        try:
            d = float(s.get("duration") or 0)
            if d > 0:
                return d
        except (TypeError, ValueError):
            pass
    return 0.0


def get_video_height(probe_data: dict) -> int:
    for s in probe_data.get("streams", []):
        if s.get("codec_type") == "video" and s.get("height"):
            try:
                return int(s["height"])
            except (TypeError, ValueError):
                continue
    return 0


def get_video_width(probe_data: dict) -> int:
    for s in probe_data.get("streams", []):
        if s.get("codec_type") == "video" and s.get("width"):
            try:
                return int(s["width"])
            except (TypeError, ValueError):
                continue
    return 0