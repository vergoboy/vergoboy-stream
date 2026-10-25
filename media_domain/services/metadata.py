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

# --------------------------------------------------------------------------- summary for clients
import re as _re

_MAX_STREAMS = 32
_MAX_TEXT = 120
_BITS_SUFFIX = _re.compile(r"(\d+)(?:le|be)$")
_CTRL = _re.compile(r"[\x00-\x1f\x7f]")


def _clean(value, limit: int = _MAX_TEXT) -> str:
    """Tags come from the file, i.e. from whoever made it: strip control chars, bound the length."""
    return _CTRL.sub("", str(value or "")).strip()[:limit]


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _fps(stream: dict):
    for key in ("avg_frame_rate", "r_frame_rate"):
        raw = str(stream.get(key) or "")
        if "/" in raw:
            num, _, den = raw.partition("/")
            try:
                n, d = float(num), float(den)
            except ValueError:
                continue
            if n > 0 and d > 0:
                return round(n / d, 3)
    return None


def _bit_depth(stream: dict):
    explicit = _int(stream.get("bits_per_raw_sample"))
    if explicit:
        return explicit
    pix = str(stream.get("pix_fmt") or "")
    m = _BITS_SUFFIX.search(pix)
    if m:
        return int(m.group(1))
    return 8 if pix else None


def _hdr(stream: dict):
    side = [str(x.get("side_data_type") or "").upper() for x in stream.get("side_data_list") or []]
    if any("DOVI" in t or "DOLBY" in t for t in side):
        return "dolby_vision"
    transfer = stream.get("color_transfer")
    if transfer == "smpte2084":
        return "hdr10"
    if transfer == "arib-std-b67":
        return "hlg"
    return None


def summarize_media(probe_data: dict) -> dict:
    """What a native client needs to know about an ORIGINAL file, without playing it.

    Small, JSON-safe and bounded: it is stored on the item and sent to every client in the room.
    Returns {} when there is nothing usable (a failed probe)."""
    streams = probe_data.get("streams") or []
    if not streams:
        return {}
    fmt = probe_data.get("format") or {}
    out = {
        "duration": round(get_duration_s(probe_data), 3),
        "container": _clean(fmt.get("format_name"), 60),
        "size": _int(fmt.get("size")),
        "bit_rate": _int(fmt.get("bit_rate")),
        "video": [], "audio": [], "subtitles": [],
    }
    for s in streams:
        kind = s.get("codec_type")
        disp = s.get("disposition") or {}
        tags = s.get("tags") or {}
        base = {"index": _int(s.get("index")), "codec": _clean(s.get("codec_name"), 40),
                "default": bool(disp.get("default"))}
        if kind == "video":
            if disp.get("attached_pic"):          # cover art is not a video track
                continue
            out["video"].append({**base, "profile": _clean(s.get("profile"), 40) or None,
                                 "width": _int(s.get("width")), "height": _int(s.get("height")),
                                 "fps": _fps(s), "pix_fmt": _clean(s.get("pix_fmt"), 40) or None,
                                 "bit_depth": _bit_depth(s), "hdr": _hdr(s)})
        elif kind == "audio":
            out["audio"].append({**base, "lang": _clean(tags.get("language"), _MAX_LANG_LEN),
                                 "title": _clean(tags.get("title")), "channels": _int(s.get("channels")),
                                 "layout": _clean(s.get("channel_layout"), 40) or None})
        elif kind == "subtitle":
            out["subtitles"].append({**base, "lang": _clean(tags.get("language"), _MAX_LANG_LEN),
                                     "title": _clean(tags.get("title")), "forced": bool(disp.get("forced")),
                                     "text": s.get("codec_name") in TEXT_SUBTITLE_CODECS})
    for k in ("video", "audio", "subtitles"):
        out[k] = out[k][:_MAX_STREAMS]
    return out
