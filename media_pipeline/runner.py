"""The single place that spawns ffmpeg.

Everything an encode needs in order to be *safe* lives here, because the
alternative is each call site re-implementing a slightly different subset of it:

* ``-progress pipe:1`` parsing for real progress (no invented percentages);
* a bounded ring buffer of recent stderr, so an error report is possible
  without keeping megabytes of log in memory;
* a stall watchdog, so a wedged ffmpeg cannot hold a slot forever;
* a hard timeout scaled to the media duration;
* its own process group, killed as a group — ``proc.kill()`` kills only the
  direct child and would leave grandchildren behind;
* a typed failure classification (see :mod:`media_pipeline.errors`);
* an ordered fallback chain with logging at WARNING for every step;
* bounded retries with exponential backoff and jitter;
* a circuit breaker so a broken GPU stops being retried into the ground;
* preflight checks (input readable, enough free disk);
* atomic output: everything is built in a ``.partial`` directory and renamed
  into place only on success.

It works both under gevent (where ``subprocess`` is monkey-patched cooperative)
and under plain CPython in tests. Nothing here imports gevent at module scope
beyond an optional handle.
"""

from __future__ import annotations

import logging
import os
import random
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .errors import EncodeError, ErrorKind, classify_failure

log = logging.getLogger(__name__)

try:  # optional: present in the Flask app, absent in plain unit tests
    import gevent as _gevent
except Exception:  # pragma: no cover - only without gevent installed
    _gevent = None

PARTIAL_SUFFIX = ".partial"

#: Recent stderr lines kept for an error report. Enough to contain the actual
#: cause (which is always at the end) without letting a chatty encode grow
#: without bound.
STDERR_RING_LINES = 50

#: How often progress is reported to the caller. ffmpeg emits a progress line
#: several times a second; forwarding every one would flood the socket.
PROGRESS_THROTTLE_S = 0.5

#: Grace period between SIGTERM and SIGKILL when cancelling a run.
TERM_GRACE_S = 3.0


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        log.warning("%s=%r is not an integer; using %d", name, raw, default)
        return default


def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        log.warning("%s=%r is not a number; using %s", name, raw, default)
        return default


class RunnerConfig:
    """Tunables, read from the environment at construction time."""

    STALL_SECONDS = _env_float("STREAM_ENCODE_STALL_SECONDS", 120.0)
    #: A run is killed at ``TIMEOUT_BASE + duration * TIMEOUT_PER_SECOND``,
    #: capped at MAX_TIMEOUT. Scaling by duration is what keeps a 3-hour film
    #: from being killed at the same 5-minute mark as a 30-second clip.
    TIMEOUT_BASE = _env_float("STREAM_ENCODE_TIMEOUT_BASE", 300.0)
    TIMEOUT_PER_SECOND = _env_float("STREAM_ENCODE_TIMEOUT_PER_SECOND", 2.0)
    MAX_TIMEOUT = _env_float("STREAM_ENCODE_MAX_TIMEOUT", 10800.0)
    MAX_RETRIES = _env_int("STREAM_ENCODE_MAX_RETRIES", 2)
    RETRY_BASE_DELAY = _env_float("STREAM_ENCODE_RETRY_BASE_DELAY", 2.0)
    RETRY_MAX_DELAY = _env_float("STREAM_ENCODE_RETRY_MAX_DELAY", 60.0)
    HW_FAILURES_BEFORE_DISABLE = _env_int("STREAM_ENCODE_HW_FAILURES", 3)
    HW_DISABLE_SECONDS = _env_float("STREAM_ENCODE_HW_DISABLE_SECONDS", 600.0)
    MIN_FREE_MB = _env_int("STREAM_ENCODE_MIN_FREE_MB", 500)
    #: Headroom over the size estimate, so a job cannot fill the disk exactly.
    DISK_HEADROOM = _env_float("STREAM_ENCODE_DISK_HEADROOM", 1.25)

    @classmethod
    def timeout_for(cls, duration_s: float) -> float:
        if duration_s and duration_s > 0:
            return min(cls.MAX_TIMEOUT, cls.TIMEOUT_BASE + duration_s * cls.TIMEOUT_PER_SECOND)
        # Unknown duration: trust the cap alone rather than guessing low and
        # killing legitimate long encodes.
        return cls.MAX_TIMEOUT


# ── hardware circuit breaker ─────────────────────────────────────────────────


class HwCircuitBreaker:
    """Trips after repeated hardware failures and reopens on a timer.

    Process-wide on purpose: GPU health is a property of the machine, not of
    one room. If VAAPI has stopped working, every room must stop trying.
    """

    def __init__(self, threshold: int = RunnerConfig.HW_FAILURES_BEFORE_DISABLE,
                 cooldown_s: float = RunnerConfig.HW_DISABLE_SECONDS) -> None:
        self._threshold = max(1, threshold)
        self._cooldown = cooldown_s
        self._consecutive = 0
        self._disabled_until = 0.0
        self._lock = threading.Lock()

    @property
    def disabled(self) -> bool:
        now = time.time()
        if now < self._disabled_until:
            return True
        if self._disabled_until and now >= self._disabled_until:
            # Cooldown elapsed: half-open. Let a single attempt through.
            with self._lock:
                if now >= self._disabled_until:
                    self._disabled_until = 0.0
                    self._consecutive = 0
                    log.info("hardware circuit breaker closed again; trying hardware")
        return False

    def remaining_cooldown(self) -> float:
        return max(0.0, self._disabled_until - time.time())

    def record_failure(self) -> None:
        with self._lock:
            self._consecutive += 1
            if self._consecutive >= self._threshold and not self._disabled_until:
                self._disabled_until = time.time() + self._cooldown
                log.warning(
                    "hardware encoder disabled for %.0fs after %d consecutive failures",
                    self._cooldown, self._consecutive,
                )

    def record_success(self) -> None:
        with self._lock:
            self._consecutive = 0

    def reset(self) -> None:
        with self._lock:
            self._consecutive = 0
            self._disabled_until = 0.0


#: Shared by the whole process.
HW_BREAKER = HwCircuitBreaker()


# ── request / result ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Attempt:
    """One way of running the job. The runner walks these in order.

    ``output_paths`` must list every absolute path the argv writes (segment
    template, playlist, fMP4 init file). The runner needs them to redirect a
    try into the partial directory, and it matches them by exact string — it
    deliberately does *not* try to guess which trailing arguments are outputs,
    because guessing walks backwards into the input arguments.
    """

    argv: list[str]
    label: str
    output_paths: tuple[str, ...] = ()
    uses_hw: bool = False
    is_copy: bool = False
    #: Logged at WARNING when this attempt is chosen *after* a failure, so the
    #: operator can see that a fallback fired and why.
    fallback_reason: str = ""

    def argv_for(self, output_dir: str) -> list[str]:
        """This attempt's argv, writing into ``output_dir`` instead."""
        if not output_dir:
            return list(self.argv)
        mapping = {
            path: os.path.join(output_dir, os.path.basename(path))
            for path in self.output_paths
        }
        return [mapping.get(token, token) for token in self.argv]


@dataclass
class EncodeRequest:
    item_id: str = ""
    label: str = ""
    source: str = ""
    duration_s: float = 0.0
    #: Final output directory. Built as ``<output_dir>.partial`` then renamed.
    output_dir: str = ""
    #: Rough size of the finished output, for the disk preflight.
    expected_output_bytes: Optional[int] = None
    on_progress: Optional[Callable[[dict], None]] = None
    on_ready: Optional[Callable[[float], None]] = None
    ready_after_s: float = 20.0
    should_cancel: Optional[Callable[[], bool]] = None
    log_prefix: str = ""


@dataclass
class AttemptOutcome:
    returncode: int = -1
    stderr_text: str = ""
    encoded_seconds: float = 0.0
    duration: Optional[float] = None
    timed_out: bool = False
    stalled: bool = False
    canceled: bool = False
    ready_fired: bool = False


@dataclass
class RunResult:
    ok: bool
    returncode: int = 0
    error: Optional[EncodeError] = None
    encoded_seconds: float = 0.0
    duration: Optional[float] = None
    output_dir: str = ""
    ready_fired: bool = False
    #: (attempt label, outcome) for every try, for logs and for debugging.
    history: list[tuple[str, str]] = field(default_factory=list)
    #: True when at least one fallback rung fired.
    used_fallback: bool = False


# ── helpers ──────────────────────────────────────────────────────────────────


def _spawn(fn: Callable[[], None]) -> threading.Thread:
    """Run ``fn`` on a worker thread.

    Deliberately always a thread, never ``gevent.spawn``: a greenlet only runs
    when the main loop yields to the hub, which only happens if
    ``monkey.patch_all()`` actually patched this process. In any other process
    (a test runner, a CLI) a greenlet watchdog would silently never fire and a
    stalled ffmpeg would hold its slot until the hard timeout — or forever.

    Under ``patch_all()`` ``threading.Thread`` *is* a greenlet, so this is the
    cooperative unit there too; elsewhere it is a genuine OS thread. Correct
    either way, which is the property that matters.
    """
    thread = threading.Thread(target=fn, daemon=True)
    thread.start()
    return thread


def _sleep(seconds: float) -> None:
    if _gevent is not None:
        _gevent.sleep(seconds)
    else:
        time.sleep(seconds)


def parse_progress_line(line: str) -> Optional[float]:
    """Extract encoded seconds from one ``-progress`` line.

    Prefers ``out_time_us``. ffmpeg's ``out_time_ms`` is a long-standing
    misnomer — it carries *micro*seconds — so depending on it means any change
    in that behaviour silently rescales progress by 1000x. ``out_time``
    (HH:MM:SS.ffffff) is the last resort.
    """
    line = line.strip()
    if line.startswith("out_time_us="):
        try:
            return int(line.split("=", 1)[1]) / 1_000_000
        except ValueError:
            return None
    if line.startswith("out_time_ms="):
        value = line.split("=", 1)[1]
        if value == "N/A":
            return None
        try:
            return int(value) / 1_000_000
        except ValueError:
            return None
    if line.startswith("out_time="):
        value = line.split("=", 1)[1]
        if value in ("N/A", ""):
            return None
        try:
            hours, minutes, seconds = value.split(":")
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        except ValueError:
            return None
    return None


def build_fallback_chain(
    primary: Attempt,
    *,
    software: Optional[Attempt] = None,
    genpts_copy: Optional[Attempt] = None,
    transcode: Optional[Attempt] = None,
) -> list[Attempt]:
    """Order the attempts a job should walk.

    * a hardware failure drops to the software encoder;
    * a copy failure retries with regenerated timestamps, then falls all the way
      back to a full transcode, because a copy that cannot even be demuxed
      correctly is not going to become trustworthy by being tried again.
    """
    chain = [primary]
    if primary.uses_hw and software is not None:
        chain.append(software)
    if primary.is_copy:
        if genpts_copy is not None:
            chain.append(genpts_copy)
        if transcode is not None:
            chain.append(transcode)
    return chain


def _free_bytes(path: str) -> int:
    probe_dir = path if os.path.isdir(path) else os.path.dirname(path) or "."
    try:
        usage = shutil.disk_usage(probe_dir)
    except OSError:
        return -1
    return usage.free


def cleanup_orphaned_partials(root: str) -> list[str]:
    """Delete leftover ``*.partial`` directories anywhere under ``root``.

    Called on boot: a crash or ``kill -9`` mid-encode leaves a partial directory
    behind, and nothing will ever finish it. The walk is recursive because
    renditions nest (``<item_id>/<label>.partial``) while items do not
    (``<item_id>.partial``), and both kinds can be orphaned.
    """
    removed: list[str] = []
    if not root or not os.path.isdir(root):
        return removed
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        # Prune as we go so the walk never descends into something we are about
        # to delete.
        for name in list(dirnames):
            if not name.endswith(PARTIAL_SUFFIX):
                continue
            path = os.path.join(dirpath, name)
            shutil.rmtree(path, ignore_errors=True)
            dirnames.remove(name)
            removed.append(path)
        for name in filenames:
            if not name.endswith(PARTIAL_SUFFIX):
                continue
            path = os.path.join(dirpath, name)
            try:
                os.remove(path)
                removed.append(path)
            except OSError:
                continue
    if removed:
        log.warning("removed %d orphaned %s path(s) at boot: %s",
                    len(removed), PARTIAL_SUFFIX, ", ".join(removed[:5]))
    return removed


def _looks_like_timestamp_problem(stderr_text: str) -> bool:
    low = (stderr_text or "").lower()
    return any(n in low for n in (
        "non monotonically increasing dts",
        "non-monotonic dts",
        "invalid dts",
        "dts out of order",
        "pts out of order",
    ))


def _partial_path(output_dir: str) -> str:
    return output_dir + PARTIAL_SUFFIX if output_dir else ""


# ── the runner ───────────────────────────────────────────────────────────────


class EncodeRunner:
    """Runs one encode job through its full fallback/retry policy."""

    def __init__(self, config: Optional[type[RunnerConfig]] = None,
                 breaker: Optional[HwCircuitBreaker] = None) -> None:
        self.config = config or RunnerConfig
        self.breaker = breaker if breaker is not None else HW_BREAKER
        #: Process ids currently running, so shutdown can clean up.
        self._active: set[int] = set()
        self._active_lock = threading.Lock()

    # -- preflight ----------------------------------------------------------

    def preflight(self, req: EncodeRequest) -> Optional[EncodeError]:
        """Cheap checks that are better done before spawning anything."""
        source = req.source
        if source and not source.startswith(("http://", "https://")):
            if not os.path.exists(source):
                return EncodeError(ErrorKind.CORRUPT, "input file does not exist")
            try:
                if os.path.getsize(source) <= 0:
                    return EncodeError(ErrorKind.CORRUPT, "input file is empty")
                with open(source, "rb") as fh:
                    if not fh.read(1):
                        return EncodeError(ErrorKind.CORRUPT, "input file has no readable content")
            except OSError as exc:
                return EncodeError(ErrorKind.CORRUPT, f"input file is unreadable: {exc}")

        if req.output_dir:
            needed = int((req.expected_output_bytes or 0) * self.config.DISK_HEADROOM)
            minimum = self.config.MIN_FREE_MB * 1024 * 1024
            target = max(minimum, needed)
            free = _free_bytes(req.output_dir)
            if free >= 0 and free < target:
                return EncodeError(
                    ErrorKind.DISK_FULL,
                    f"need {target} bytes, {free} free",
                )
        return None

    # -- process lifecycle ---------------------------------------------------

    def _kill_group(self, proc: subprocess.Popen, reason: str) -> None:
        """Kill ffmpeg *and its children*.

        ``proc.kill()`` sends SIGKILL to the direct child only. When ffmpeg has
        spawned anything, that leaves orphans holding the output files — which
        is exactly the zombie this module exists to avoid.
        """
        log.warning("killing ffmpeg process group (%s)", reason)
        try:
            pgid = os.getpgid(proc.pid)
        except OSError:
            pgid = None
        for sig in (signal.SIGTERM, signal.SIGKILL):
            if sig == signal.SIGKILL and proc.poll() is not None:
                break
            try:
                if pgid is not None:
                    os.killpg(pgid, sig)
                else:
                    proc.send_signal(sig)
            except (ProcessLookupError, OSError):
                break
            deadline = time.time() + (TERM_GRACE_S if sig == signal.SIGTERM else 2.0)
            while time.time() < deadline:
                if proc.poll() is not None:
                    break
                _sleep(0.05)
            if proc.poll() is not None:
                break
        try:
            proc.wait(timeout=5)
        except Exception:
            pass

    # -- one attempt ---------------------------------------------------------

    def _run_attempt(self, req: EncodeRequest, attempt: Attempt) -> AttemptOutcome:
        """Run a single attempt to completion, or kill it if it misbehaves."""
        argv = list(attempt.argv)
        if argv and os.path.basename(argv[0]).startswith("ffmpeg"):
            # -progress must come before the input. -nostats silences the
            # carriage-return progress line that would otherwise spam stderr,
            # and -hide_banner keeps the "configuration: --enable-nvenc ..."
            # line out of the ring buffer, where it would otherwise be
            # indistinguishable from a real hardware error.
            argv = [argv[0], "-progress", "pipe:1", "-nostats", "-hide_banner"] + argv[1:]

        outcome = AttemptOutcome()
        ring: list[str] = []
        state = {
            "last_progress": time.time(),
            "started": time.time(),
            "duration": 0.0,
            "last_emit": 0.0,
        }

        try:
            proc = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                # start_new_session => setsid => its own process group, which is
                # what makes the group kill above safe.
                start_new_session=True,
            )
        except FileNotFoundError:
            raise EncodeError(ErrorKind.UNKNOWN, "ffmpeg is not installed")
        except OSError as exc:
            raise EncodeError(ErrorKind.UNKNOWN, f"could not start ffmpeg: {exc}")

        with self._active_lock:
            self._active.add(proc.pid)

        def read_stderr() -> None:
            try:
                for raw in proc.stderr:
                    text = raw.decode(errors="ignore").rstrip()
                    if not text:
                        continue
                    # Bounded history: the cause of a failure is at the end, and
                    # a long encode must not accumulate unbounded memory.
                    ring.append(text)
                    if len(ring) > STDERR_RING_LINES:
                        del ring[:len(ring) - STDERR_RING_LINES]
                    if not state["duration"] and "Duration:" in text:
                        # ffmpeg announces the real duration on stderr, which
                        # beats guessing for live/unknown-length inputs.
                        head = text.split("Duration:", 1)[1].split(",")[0].strip()
                        parts = head.split(":")
                        if len(parts) == 3:
                            try:
                                state["duration"] = (
                                    int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
                                )
                            except ValueError:
                                pass
            except Exception:
                pass
            finally:
                try:
                    proc.stderr.close()
                except Exception:
                    pass

        duration_guess = req.duration_s or 0.0
        timeout = self.config.timeout_for(duration_guess)

        def watchdog() -> None:
            while proc.poll() is None:
                idle = time.time() - state["last_progress"]
                if idle > self.config.STALL_SECONDS:
                    outcome.stalled = True
                    self._kill_group(proc, f"stalled {idle:.0f}s without progress")
                    return
                if time.time() - state["started"] > timeout:
                    outcome.timed_out = True
                    self._kill_group(proc, f"hard timeout {timeout:.0f}s")
                    return
                if req.should_cancel and req.should_cancel():
                    outcome.canceled = True
                    self._kill_group(proc, "cancel requested")
                    return
                _sleep(0.5)

        _spawn(read_stderr)
        _spawn(watchdog)

        try:
            for raw in proc.stdout:
                line = raw.decode(errors="ignore")
                if line.strip() == "progress=end":
                    state["last_progress"] = time.time()
                    continue
                seconds = parse_progress_line(line)
                if seconds is None:
                    continue
                state["last_progress"] = time.time()
                outcome.encoded_seconds = seconds

                # The watchdog may never get to run its cancel check (a fast
                # process can exit first), so honour a cancel here too.
                if req.should_cancel and req.should_cancel():
                    outcome.canceled = True
                    self._kill_group(proc, "cancel requested")
                    break

                if (not outcome.ready_fired and req.on_ready and duration_guess
                        and seconds >= req.ready_after_s):
                    outcome.ready_fired = True
                    try:
                        req.on_ready(seconds)
                    except Exception as exc:
                        log.warning("on_ready callback failed: %s", exc)

                if req.on_progress and (time.time() - state["last_emit"]) >= PROGRESS_THROTTLE_S:
                    state["last_emit"] = time.time()
                    try:
                        req.on_progress({
                            "encoded_seconds": round(seconds, 1),
                            "duration": round(state["duration"] or duration_guess, 1) or None,
                        })
                    except Exception as exc:
                        log.warning("on_progress callback failed: %s", exc)
        finally:
            for stream in (proc.stdout, proc.stderr):
                try:
                    stream.close()
                except Exception:
                    pass

        try:
            proc.wait(timeout=TERM_GRACE_S + 5)
        except Exception:
            self._kill_group(proc, "wait timed out")

        with self._active_lock:
            self._active.discard(proc.pid)

        outcome.returncode = proc.returncode if proc.returncode is not None else -1
        outcome.stderr_text = "\n".join(ring)
        outcome.duration = state["duration"] or duration_guess or None
        return outcome

    # -- the whole policy ----------------------------------------------------

    def run(self, req: EncodeRequest, chain: list[Attempt]) -> RunResult:
        """Walk the fallback chain, retrying retryable failures with backoff."""
        if not chain:
            raise ValueError("run() needs at least one attempt")

        result = RunResult(ok=False, output_dir=req.output_dir)

        preflight_error = self.preflight(req)
        if preflight_error is not None:
            result.error = preflight_error
            result.history.append(("preflight", preflight_error.detail))
            return result

        partial_dir = _partial_path(req.output_dir)
        if req.output_dir:
            shutil.rmtree(partial_dir, ignore_errors=True)
            os.makedirs(partial_dir, exist_ok=True)

        prefix = req.log_prefix or f"{req.item_id}/{req.label}".strip("/")
        last_error: Optional[EncodeError] = None

        for index, attempt in enumerate(chain):
            # Checked before spawning: the whole point of a cancel is to not do
            # the work, and a fallback rung is still work.
            if req.should_cancel and req.should_cancel():
                if partial_dir:
                    shutil.rmtree(partial_dir, ignore_errors=True)
                result.error = EncodeError(ErrorKind.KILLED, "cancelled before starting")
                result.history.append((attempt.label, "cancelled"))
                return result

            if attempt.uses_hw and self.breaker.disabled:
                result.history.append((attempt.label, "skipped: hardware circuit breaker open"))
                log.warning("%s: skipping %s, hardware disabled for %.0fs more",
                            prefix, attempt.label, self.breaker.remaining_cooldown())
                continue

            if index > 0:
                reason = attempt.fallback_reason or f"previous attempt ({chain[index - 1].label}) failed"
                log.warning("%s: falling back to %s — %s", prefix, attempt.label, reason)
                result.used_fallback = True

            # Retries only ever apply to the final rung. When a fallback is
            # waiting, retrying the attempt that just failed is worse than
            # useless: the software encoder is free, the GPU is not, and the
            # backoff delay is time the room sits waiting on a black screen.
            is_last_rung = index == len(chain) - 1

            attempt_error: Optional[EncodeError] = None
            for retry in range(self.config.MAX_RETRIES + 1):
                # A failed try may have left stale segments behind; a fallback
                # changes the codec, so mixing the two would be wrong.
                if partial_dir:
                    shutil.rmtree(partial_dir, ignore_errors=True)
                    os.makedirs(partial_dir, exist_ok=True)

                try:
                    outcome = self._run_attempt(
                        req,
                        Attempt(argv=attempt.argv_for(partial_dir), label=attempt.label,
                                uses_hw=attempt.uses_hw, is_copy=attempt.is_copy),
                    )
                except EncodeError as exc:
                    attempt_error = exc
                    result.history.append((attempt.label, exc.detail))
                    if attempt.uses_hw:
                        self.breaker.record_failure()
                    break

                if outcome.ready_fired:
                    result.ready_fired = True

                if outcome.returncode == 0:
                    result.history.append((attempt.label, "ok"))
                    if attempt.uses_hw:
                        self.breaker.record_success()
                    if partial_dir and req.output_dir:
                        if not self._promote(partial_dir, req.output_dir):
                            result.error = EncodeError(
                                ErrorKind.DISK_FULL, "could not move output into place")
                            result.history.append((attempt.label, "promote failed"))
                            return result
                    result.ok = True
                    result.returncode = 0
                    result.encoded_seconds = outcome.encoded_seconds
                    result.duration = outcome.duration
                    return result

                attempt_error = classify_failure(
                    outcome.returncode, outcome.stderr_text,
                    timed_out=outcome.timed_out, stalled=outcome.stalled,
                )
                result.history.append((attempt.label, attempt_error.detail))

                if attempt.uses_hw and attempt_error.kind is ErrorKind.HW_FAILURE:
                    self.breaker.record_failure()
                elif attempt.uses_hw:
                    self.breaker.record_success()

                log.warning("%s: %s failed (%s): %s", prefix, attempt.label,
                            attempt_error.kind.value, attempt_error.detail)

                if outcome.canceled:
                    # A cancel is not a failure to route around: every rung
                    # would be cancelled too.
                    if partial_dir:
                        shutil.rmtree(partial_dir, ignore_errors=True)
                    result.error = EncodeError(ErrorKind.KILLED, "cancelled by request")
                    result.history.append((attempt.label, "cancelled"))
                    return result

                # A copy that dies on timestamps gets one regenerated-timestamp
                # try here; the full-transcode rung is handled by the chain.
                if (not attempt_error.retryable and attempt.is_copy
                        and _looks_like_timestamp_problem(outcome.stderr_text)):
                    attempt_error.retryable = True

                if attempt_error.retryable and is_last_rung and retry < self.config.MAX_RETRIES:
                    delay = self._backoff_delay(retry)
                    log.warning("%s: retrying %s in %.1fs (attempt %d/%d, kind=%s)",
                                prefix, attempt.label, delay, retry + 1,
                                self.config.MAX_RETRIES, attempt_error.kind.value)
                    _sleep(delay)
                    continue
                break

            last_error = attempt_error

        # Every rung is exhausted: leave nothing half-written behind.
        if partial_dir:
            shutil.rmtree(partial_dir, ignore_errors=True)
        result.ok = False
        result.error = last_error or EncodeError(ErrorKind.UNKNOWN, "no attempt succeeded")
        return result

    def _backoff_delay(self, attempt_number: int) -> float:
        capped = min(self.config.RETRY_BASE_DELAY * (2 ** attempt_number),
                     self.config.RETRY_MAX_DELAY)
        # Full jitter: without it, everything that failed together retries
        # together and the overload that caused the failure just repeats.
        return random.uniform(capped * 0.5, capped)

    def _promote(self, partial_dir: str, final_dir: str) -> bool:
        """Atomically move a finished partial directory into place."""
        try:
            if os.path.exists(final_dir):
                doomed = final_dir + ".old"
                shutil.rmtree(doomed, ignore_errors=True)
                os.rename(final_dir, doomed)
                os.rename(partial_dir, final_dir)
                shutil.rmtree(doomed, ignore_errors=True)
            else:
                os.rename(partial_dir, final_dir)
            return True
        except OSError as exc:
            log.error("could not promote %s -> %s: %s", partial_dir, final_dir, exc)
            return False

    def shutdown(self) -> None:
        """Kill anything still running. Called on process exit."""
        with self._active_lock:
            pids = list(self._active)
        for pid in pids:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except OSError:
                pass
        with self._active_lock:
            self._active.clear()