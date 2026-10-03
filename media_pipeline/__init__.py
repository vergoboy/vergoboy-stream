"""Media pipeline: probe → plan → ffmpeg argv → supervised execution.

Deliberately layered so each piece is testable on its own, and so that the only
code allowed to do a given job is the only code that does it:

* :mod:`media_pipeline.probe` — the only thing that talks to ffprobe.
* :mod:`media_pipeline.planner` — pure decision logic, no I/O.
* :mod:`media_pipeline.ffmpeg_cmd` — the only thing that builds argv.
* :mod:`media_pipeline.runner` — the only thing that spawns ffmpeg.
* :mod:`media_pipeline.errors` — the failure taxonomy everything reports into.

Nothing here knows about rooms, sockets, or Flask.
"""

from .errors import (
    RETRYABLE_KINDS,
    EncodeError,
    ErrorKind,
    MediaError,
    PlanError,
    ProbeError,
    SubtitleError,
    UnsupportedMediaError,
    classify_failure,
    classify_stderr,
)
from .planner import (
    AudioAction,
    ClientCaps,
    HwAccel,
    Mode,
    Plan,
    SubtitleAction,
    plan,
    plan_compat,
)
from .probe import MediaInfo, probe
from .runner import (
    Attempt,
    EncodeRequest,
    EncodeRunner,
    HwCircuitBreaker,
    RunResult,
    build_fallback_chain,
    cleanup_orphaned_partials,
)

__all__ = [
    "AudioAction",
    "Attempt",
    "ClientCaps",
    "EncodeError",
    "EncodeRequest",
    "EncodeRunner",
    "ErrorKind",
    "HwAccel",
    "HwCircuitBreaker",
    "MediaError",
    "MediaInfo",
    "Mode",
    "Plan",
    "PlanError",
    "ProbeError",
    "RETRYABLE_KINDS",
    "RunResult",
    "SubtitleAction",
    "SubtitleError",
    "UnsupportedMediaError",
    "build_fallback_chain",
    "classify_failure",
    "classify_stderr",
    "cleanup_orphaned_partials",
    "plan",
    "plan_compat",
    "probe",
]