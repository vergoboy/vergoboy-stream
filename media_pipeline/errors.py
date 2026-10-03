"""Typed failure hierarchy for the media pipeline.

The important property here is the split between what an operator needs and what
a client is allowed to see:

* :attr:`EncodeError.detail` may contain ffmpeg's stderr and filesystem paths.
  It exists for logs and bug reports only.
* :attr:`EncodeError.user_message` is a short, translated sentence with no
  stderr text and no paths. It is the *only* thing that may cross the socket.

Keeping them on one object (rather than passing the exception through) makes it
impossible to leak a path by forgetting to sanitise somewhere else: there is no
code path from ``detail`` to the network except a deliberate choice to use it.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Optional


class ErrorKind(str, Enum):
    """Why an encode failed, in terms that drive the retry/fallback policy."""

    OOM = "oom"
    TIMEOUT = "timeout"
    STALL = "stall"
    CORRUPT = "corrupt"
    DISK_FULL = "disk_full"
    HW_FAILURE = "hw_failure"
    KILLED = "killed"
    UNKNOWN = "unknown"


#: Retrying is only ever attempted for these. The rest need something to change
#: first: a fallback rung (corrupt timestamps), or an operator (full disk, OOM).
#: Retrying those would just burn CPU and produce the same failure.
RETRYABLE_KINDS = frozenset({
    ErrorKind.TIMEOUT,
    ErrorKind.STALL,
    ErrorKind.HW_FAILURE,
    ErrorKind.KILLED,
    ErrorKind.UNKNOWN,
})


#: Human-facing text. Deliberately free of file names, paths, exit codes and
#: ffmpeg's own wording: none of that means anything to a viewer, and the exit
#: code or path can leak the server's directory layout.
_USER_MESSAGES = {
    ErrorKind.OOM: "حافظه سرور برای تبدیل این ویدیو کافی نبود. لطفاً کمی بعد دوباره تلاش کنید.",
    ErrorKind.TIMEOUT: "تبدیل ویدیو بیش از حد طول کشید. لطفاً دوباره تلاش کنید.",
    ErrorKind.STALL: "تبدیل ویدیو متوقف شد. لطفاً دوباره تلاش کنید.",
    ErrorKind.CORRUPT: "فایل ویدیو آسیب دیده یا قابل خواندن نیست.",
    ErrorKind.DISK_FULL: "فضای آزاد دیسک سرور کافی نیست. لطفاً به مدیر سایت اطلاع دهید.",
    ErrorKind.HW_FAILURE: "تبدیل با شتاب‌دهنده سخت‌افزاری انجام نشد؛ با نرم‌افزار دوباره تلاش شد و همچنان ناموفق بود.",
    ErrorKind.KILLED: "تبدیل ویدیو لغو شد.",
    ErrorKind.UNKNOWN: "تبدیل ویدیو ناموفق بود.",
}

#: Kinds where an admin pressing "retry" is a reasonable thing to do, because
#: the cause may well have gone away (a busy GPU, a full disk that was cleaned).
def retryable_by_operator(kind: ErrorKind) -> bool:
    return kind is not ErrorKind.CORRUPT


class MediaError(Exception):
    """Base class for every media-pipeline failure."""


class ProbeError(MediaError):
    """ffprobe could not describe the source at all.

    Distinct from :class:`UnsupportedMediaError`: this means "we could not
    find out what this is", not "we found out and we do not support it".
    """


class UnsupportedMediaError(MediaError):
    """The source was understood but cannot be turned into playable output."""


class PlanError(MediaError):
    """No valid plan could be produced for a source the planner accepted."""


class SubtitleError(MediaError):
    """A subtitle track could not be extracted. Never fatal to playback."""


class EncodeError(MediaError):
    """An ffmpeg run failed.

    ``detail`` is for logs only. ``user_message`` is what clients see.
    """

    def __init__(
        self,
        kind: ErrorKind = ErrorKind.UNKNOWN,
        detail: str = "",
        *,
        returncode: Optional[int] = None,
        retryable: Optional[bool] = None,
        stdout_tail: str = "",
        stderr_tail: str = "",
    ) -> None:
        self.kind = kind
        self.detail = detail
        self.returncode = returncode
        self.stdout_tail = stdout_tail
        self.stderr_tail = stderr_tail
        self.retryable = kind in RETRYABLE_KINDS if retryable is None else retryable
        super().__init__(f"{kind.value}: {detail}" if detail else kind.value)

    @property
    def user_message(self) -> str:
        """Client-safe text. Contains no stderr, no path, no exit code."""
        return _USER_MESSAGES.get(self.kind, _USER_MESSAGES[ErrorKind.UNKNOWN])

    def client_payload(self, item_id: str) -> dict:
        """Exactly the shape of the ``transcode_error`` socket event."""
        return {
            "item_id": item_id,
            "kind": self.kind.value,
            "user_message": self.user_message,
            "retryable": self.retryable,
        }


# ── classification ───────────────────────────────────────────────────────────
#
# stderr patterns, most specific first. Order matters: a hardware error often
# also mentions "error", and "No space left" can appear alongside other noise,
# so the distinctive strings have to win before the generic ones.

_STDERR_PATTERNS: tuple[tuple[ErrorKind, tuple[str, ...]], ...] = (
    (ErrorKind.DISK_FULL, (
        "no space left on device",
        "no space left",
        "disk quota exceeded",
        "enospc",
    )),
    (ErrorKind.OOM, (
        "cannot allocate memory",
        "out of memory",
        "mmap failed",
        "memory exhausted",
        "std::bad_alloc",
        "virtual memory exhausted",
    )),
    (ErrorKind.HW_FAILURE, (
        "vaapi",
        "qsv",
        "nvenc",
        "nvdec",
        "vadisplay",
        "drm:",
        "cannot open the device",
        "device or resource busy",
        "failed to initialise vaapi",
        "hardware accelerator",
        "mfx",
        "cuda",
    )),
    (ErrorKind.CORRUPT, (
        "invalid data found when processing input",
        "moov atom not found",
        "invalid nal unit",
        "error while decoding",
        "truncat",
        "partial file",
        "invalid argument",
        "does not contain any stream",
        "corrupt",
    )),
)


#: Names that identify an encoder as hardware-backed. Used to interpret
#: "Unknown encoder '<name>'", which is what a build without QSV/NVENC actually
#: says — and which must not be confused with a missing *software* encoder.
_HW_ENCODER_NAMES = ("vaapi", "qsv", "nvenc", "nvdec", "cuda", "videotoolbox", "v4l2m2m")

#: Verbatim from a build with no QSV support:
#:   [vost#0:0 @ ...] Unknown encoder 'qsv'
_UNKNOWN_ENCODER_RE = re.compile(r"unknown encoder '([^']+)'")


def _strip_banner(stderr_text: str) -> str:
    """Remove ffmpeg's startup banner before any matching happens.

    This is not cosmetic. The banner's configuration line contains
    ``--enable-nvenc``, ``--enable-libdrm`` and friends, so a corrupt input on a
    GPU-enabled build otherwise matches the hardware patterns and gets reported
    as a hardware failure — sending the job down the software fallback for no
    reason, and blaming the wrong thing in the logs.
    """
    kept: list[str] = []
    for line in (stderr_text or "").splitlines():
        stripped = line.strip()
        if (stripped.startswith("ffmpeg version")
                or stripped.startswith("built with ")
                or stripped.startswith("configuration:")
                or stripped.startswith("libav")
                or stripped.startswith("libsw")):
            continue
        # The configuration block can wrap onto continuation lines that hold
        # nothing but --enable/--disable flags.
        if stripped.startswith("--enable") or stripped.startswith("--disable"):
            continue
        kept.append(line)
    return "\n".join(kept)


def classify_stderr(stderr_text: str) -> Optional[ErrorKind]:
    """Best-effort kind for a failure, from ffmpeg's own diagnostics.

    Returns ``None`` when nothing matches, so the caller can fall back to the
    exit code / explicit flags rather than guessing.
    """
    if not stderr_text:
        return None
    low = _strip_banner(stderr_text).lower()
    if not low.strip():
        return None

    # A missing hardware encoder is a hardware problem even though the wording
    # ("Unknown encoder") looks generic — and it is the exact failure the
    # software fallback exists to answer.
    match = _UNKNOWN_ENCODER_RE.search(low)
    if match and any(name in match.group(1) for name in _HW_ENCODER_NAMES):
        return ErrorKind.HW_FAILURE

    for kind, needles in _STDERR_PATTERNS:
        if any(n in low for n in needles):
            return kind
    return None


def classify_failure(
    returncode: int,
    stderr_text: str = "",
    *,
    timed_out: bool = False,
    stalled: bool = False,
) -> EncodeError:
    """Turn a finished ffmpeg run into a typed :class:`EncodeError`.

    Precedence is deliberate and matters in practice:

    1. An explicit reason we already know (we killed it) beats any text match.
    2. Otherwise the exit code, because a signal death is unambiguous.
    3. Otherwise stderr pattern matching, which is a hint rather than proof.
    """
    if timed_out:
        return EncodeError(ErrorKind.TIMEOUT, "hard timeout reached",
                           returncode=returncode, stderr_tail=stderr_text[-2000:])
    if stalled:
        return EncodeError(ErrorKind.STALL, "no progress within the stall window",
                           returncode=returncode, stderr_tail=stderr_text[-2000:])

    if returncode == 0:
        return EncodeError(ErrorKind.UNKNOWN, "reported failure but exit code was 0",
                           returncode=returncode, stderr_tail=stderr_text[-2000:])

    # Negative returncode means "killed by signal N" (Python's convention), which
    # is a fact about what happened, not a guess from text.
    if returncode < 0:
        import signal as _signal
        try:
            signame = _signal.Signals(-returncode).name
        except ValueError:
            signame = f"signal {-returncode}"
        if -returncode in (int(_signal.SIGKILL), int(_signal.SIGTERM)):
            kind = ErrorKind.KILLED
        elif -returncode in (int(_signal.SIGXCPU), int(getattr(_signal, "SIGXFSZ", 25))):
            kind = ErrorKind.OOM
        else:
            kind = ErrorKind.UNKNOWN
        return EncodeError(kind, f"killed by {signame}", returncode=returncode,
                           stderr_tail=stderr_text[-2000:])

    kind = classify_stderr(stderr_text) or ErrorKind.UNKNOWN
    return EncodeError(kind, _last_meaningful_line(stderr_text) or f"exit code {returncode}",
                       returncode=returncode, stderr_tail=stderr_text[-2000:])


def _last_meaningful_line(stderr_text: str) -> str:
    """The last non-empty, non-banner stderr line, for the operator detail."""
    for line in reversed(_strip_banner(stderr_text).splitlines()):
        line = line.strip()
        if line:
            return line[:500]
    return ""