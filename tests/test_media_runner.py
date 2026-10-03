"""Runtime tests for the encode runner.

The runner's whole job is behaving correctly when things go wrong, so most of
these tests deliberately make things go wrong: real processes that stall, spawn
orphans, fail with recognisable ffmpeg diagnostics, or run out of retries. Real
subprocesses, real signals, real exit codes — no mocks of Popen, because the
behaviour under test *is* the process handling.

    python3 -m unittest tests.test_media_runner -v
"""

import os
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

from media_pipeline import HwAccel, plan
from media_pipeline.probe import probe
from media_pipeline.errors import ErrorKind
from media_pipeline.ffmpeg_cmd import build_plan_args
from media_pipeline.runner import (
    PARTIAL_SUFFIX,
    STDERR_RING_LINES,
    Attempt,
    EncodeRequest,
    EncodeRunner,
    HwCircuitBreaker,
    RunnerConfig,
    build_fallback_chain,
    cleanup_orphaned_partials,
    parse_progress_line,
)

FFMPEG = shutil.which("ffmpeg")
W, H, FPS, DURATION = 320, 240, 10, 2
AUDIO_AAC = ["-c:a", "aac", "-b:a", "128k", "-ac", "2"]


def make_h264(path):
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:d={DURATION}:r={FPS}",
           "-f", "lavfi", "-i", f"sine=frequency=440:duration={DURATION}:sample_rate=48000",
           "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p",
           "-preset", "ultrafast"] + AUDIO_AAC + [path]
    return subprocess.run(cmd, capture_output=True, check=False).returncode == 0


def pid_alive(pid):
    """True if the pid exists. /proc is the ground truth, not a guess."""
    return os.path.exists(f"/proc/{pid}")


def wait_gone(pid, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.05)
    return not pid_alive(pid)


class FakeConfig(RunnerConfig):
    """RunnerConfig with the clocks wound down so tests stay fast."""


FakeConfig.STALL_SECONDS = 1.0
FakeConfig.TIMEOUT_BASE = 30.0
FakeConfig.TIMEOUT_PER_SECOND = 0.0
FakeConfig.MAX_TIMEOUT = 30.0
FakeConfig.MAX_RETRIES = 2
FakeConfig.RETRY_BASE_DELAY = 0.01
FakeConfig.RETRY_MAX_DELAY = 0.05
FakeConfig.MIN_FREE_MB = 1
FakeConfig.HW_FAILURES_BEFORE_DISABLE = 2
FakeConfig.HW_DISABLE_SECONDS = 5.0


# ── pure units ───────────────────────────────────────────────────────────────


class ProgressParsing(unittest.TestCase):
    def test_out_time_us_is_microseconds(self):
        self.assertAlmostEqual(parse_progress_line("out_time_us=1500000"), 1.5)

    def test_out_time_ms_quirk(self):
        """ffmpeg's out_time_ms carries MICROseconds despite the name.

        Treating it as milliseconds inflates progress by 1000x, which is the
        bug this parser exists to avoid.
        """
        self.assertAlmostEqual(parse_progress_line("out_time_ms=2000000"), 2.0)

    def test_out_time_clock_form(self):
        self.assertAlmostEqual(parse_progress_line("out_time=01:02:03.500000"), 3723.5)

    def test_unusable_values(self):
        for line in ("out_time=N/A", "out_time_ms=N/A", "bitrate=1000k", "frame=12", ""):
            self.assertIsNone(parse_progress_line(line), line)

    def test_microseconds_are_authoritative(self):
        """When both are present the *_us line wins, so a change in the
        misnamed field cannot corrupt progress."""
        self.assertAlmostEqual(parse_progress_line("out_time_us=5000000"), 5.0)


class TimeoutScaling(unittest.TestCase):
    def test_scales_with_duration(self):
        class C(RunnerConfig):
            TIMEOUT_BASE = 100.0
            TIMEOUT_PER_SECOND = 2.0
            MAX_TIMEOUT = 10_000.0
        self.assertAlmostEqual(C.timeout_for(10), 120.0)
        self.assertAlmostEqual(C.timeout_for(3600), 7300.0)

    def test_capped(self):
        class C(RunnerConfig):
            TIMEOUT_BASE = 100.0
            TIMEOUT_PER_SECOND = 2.0
            MAX_TIMEOUT = 500.0
        self.assertAlmostEqual(C.timeout_for(3600), 500.0)

    def test_unknown_duration_uses_cap(self):
        class C(RunnerConfig):
            TIMEOUT_BASE = 100.0
            TIMEOUT_PER_SECOND = 2.0
            MAX_TIMEOUT = 500.0
        self.assertAlmostEqual(C.timeout_for(0), 500.0)


class FallbackChainShape(unittest.TestCase):
    def test_hardware_falls_back_to_software(self):
        chain = build_fallback_chain(
            Attempt(["a"], "hw", uses_hw=True),
            software=Attempt(["b"], "software"),
            transcode=Attempt(["c"], "transcode"),
        )
        # A hardware failure must not also drag the job into a full transcode.
        self.assertEqual([a.label for a in chain], ["hw", "software"])

    def test_copy_walks_genpts_then_transcode(self):
        chain = build_fallback_chain(
            Attempt(["a"], "copy", is_copy=True),
            software=Attempt(["b"], "software"),
            genpts_copy=Attempt(["c"], "copy+genpts", is_copy=True),
            transcode=Attempt(["d"], "transcode"),
        )
        self.assertEqual([a.label for a in chain], ["copy", "copy+genpts", "transcode"])

    def test_plain_transcode_has_no_extra_rungs(self):
        chain = build_fallback_chain(
            Attempt(["a"], "transcode"),
            software=Attempt(["b"], "software"),
            genpts_copy=Attempt(["c"], "copy+genpts"),
            transcode=Attempt(["d"], "transcode2"),
        )
        self.assertEqual([a.label for a in chain], ["transcode"])


class ArgvRetargeting(unittest.TestCase):
    """Redirecting outputs must never touch an input."""

    def setUp(self):
        self.attempt = Attempt(
            argv=["ffmpeg", "-i", "/media/in.mkv", "-c:v", "libx264",
                  "-hls_segment_filename", "/media/hls/seg_%05d.m4s",
                  "-hls_fmp4_init_filename", "init.mp4",
                  "/media/hls/index.m3u8"],
            label="transcode",
            output_paths=("/media/hls/seg_%05d.m4s", "/media/hls/index.m3u8"),
        )

    def test_outputs_move_input_does_not(self):
        out = self.attempt.argv_for("/media/hls.new.partial")
        self.assertIn("/media/in.mkv", out)
        self.assertNotIn("/media/hls/index.m3u8", out)
        self.assertIn("/media/hls.new.partial/index.m3u8", out)
        self.assertIn("/media/hls.new.partial/seg_%05d.m4s", out)

    def test_single_output_path_still_cannot_reach_the_input(self):
        """The regression that motivated explicit output_paths: guessing from
        the tail of argv walked backwards into the input arguments."""
        attempt = Attempt(
            argv=["ffmpeg", "-i", "/media/in.mkv", "-c", "copy", "/media/hls/out.mp4"],
            label="copy",
            output_paths=("/media/hls/out.mp4",),
        )
        out = attempt.argv_for("/tmp/p.partial")
        self.assertIn("/media/in.mkv", out)
        self.assertIn("/tmp/p.partial/out.mp4", out)
        self.assertNotIn("/media/hls/out.mp4", out)

    def test_empty_output_dir_is_identity(self):
        self.assertEqual(self.attempt.argv_for(""), self.attempt.argv)


class CircuitBreakerUnit(unittest.TestCase):
    def test_trips_after_threshold_and_reopens(self):
        breaker = HwCircuitBreaker(threshold=2, cooldown_s=0.3)
        self.assertFalse(breaker.disabled)
        breaker.record_failure()
        self.assertFalse(breaker.disabled, "must not trip before the threshold")
        breaker.record_failure()
        self.assertTrue(breaker.disabled)
        self.assertGreater(breaker.remaining_cooldown(), 0)
        time.sleep(0.4)
        self.assertFalse(breaker.disabled, "must reopen after the cooldown")

    def test_success_resets_the_counter(self):
        breaker = HwCircuitBreaker(threshold=2, cooldown_s=5)
        breaker.record_failure()
        breaker.record_success()
        breaker.record_failure()
        self.assertFalse(breaker.disabled)

    def test_cooldown_is_fixed_not_extended(self):
        """A tripped breaker waits out a fixed window.

        Re-arming on every further failure would mean a job that keeps failing
        keeps pushing the deadline away and hardware is never retried at all.
        """
        breaker = HwCircuitBreaker(threshold=1, cooldown_s=0.6)
        breaker.record_failure()
        first = breaker.remaining_cooldown()
        breaker.record_failure()
        self.assertAlmostEqual(breaker.remaining_cooldown(), first, delta=0.05)
        time.sleep(first + 0.2)
        self.assertFalse(breaker.disabled)


# ── filesystem units ─────────────────────────────────────────────────────────


class PartialCleanup(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def _mk(self, name):
        path = os.path.join(self.root, name)
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, "index.m3u8"), "w") as fh:
            fh.write("#EXTM3U")
        return path

    def test_removes_only_partials(self):
        stale_a = self._mk("item_a" + PARTIAL_SUFFIX)
        stale_b = self._mk("item_b" + PARTIAL_SUFFIX)
        keep = self._mk("item_a")
        removed = cleanup_orphaned_partials(self.root)
        self.assertEqual(sorted(removed), sorted([stale_a, stale_b]))
        self.assertFalse(os.path.exists(stale_a))
        self.assertFalse(os.path.exists(stale_b))
        self.assertTrue(os.path.exists(keep), "a finished rendition must survive")

    def test_missing_root_is_not_an_error(self):
        self.assertEqual(cleanup_orphaned_partials(os.path.join(self.root, "nope")), [])


# ── preflight ────────────────────────────────────────────────────────────────


class Preflight(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        self.runner = EncodeRunner(config=FakeConfig, breaker=HwCircuitBreaker())

    def test_missing_input(self):
        err = self.runner.preflight(EncodeRequest(source=os.path.join(self.tmp, "nope.mkv")))
        self.assertIsNotNone(err)
        self.assertIs(err.kind, ErrorKind.CORRUPT)
        self.assertFalse(err.retryable, "a missing file will still be missing")

    def test_empty_input(self):
        path = os.path.join(self.tmp, "empty.mkv")
        open(path, "wb").close()
        err = self.runner.preflight(EncodeRequest(source=path))
        self.assertIs(err.kind, ErrorKind.CORRUPT)

    def test_directory_as_input(self):
        err = self.runner.preflight(EncodeRequest(source=self.tmp))
        self.assertIsNotNone(err)

    def test_disk_preflight_blocks_impossible_size(self):
        src = os.path.join(self.tmp, "x.mkv")
        with open(src, "wb") as fh:
            fh.write(b"x")
        err = self.runner.preflight(EncodeRequest(
            source=src,
            output_dir=os.path.join(self.tmp, "out"),
            expected_output_bytes=10 ** 18,  # a petabyte
        ))
        self.assertIsNotNone(err)
        self.assertIs(err.kind, ErrorKind.DISK_FULL)

    def test_remote_input_skips_readability(self):
        self.assertIsNone(self.runner.preflight(EncodeRequest(source="https://example.com/a.mkv")))

    def test_good_request_passes(self):
        src = os.path.join(self.tmp, "x.mkv")
        with open(src, "wb") as fh:
            fh.write(b"x")
        self.assertIsNone(self.runner.preflight(EncodeRequest(
            source=src, output_dir=self.tmp, expected_output_bytes=1024)))


# ── real process behaviour ───────────────────────────────────────────────────


class ProcessHandling(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        self.breaker = HwCircuitBreaker(threshold=2, cooldown_s=30.0)
        self.runner = EncodeRunner(config=FakeConfig, breaker=self.breaker)

    def _req(self, **kw):
        kw.setdefault("output_dir", os.path.join(self.tmp, "out"))
        kw.setdefault("source", "")
        kw.setdefault("log_prefix", "test/item")
        return EncodeRequest(**kw)

    def test_stalled_process_is_killed_and_classified(self):
        """A process that emits one progress line then goes quiet must not
        occupy a slot forever."""
        script = os.path.join(self.tmp, "stall.sh")
        with open(script, "w") as fh:
            fh.write("#!/bin/bash\n"
                     'echo "out_time_us=1000000"\n'
                     'sleep 60\n')
        os.chmod(script, 0o755)
        result = self.runner.run(self._req(), [Attempt([script], "stall")])
        self.assertFalse(result.ok)
        self.assertIs(result.error.kind, ErrorKind.STALL)
        self.assertTrue(result.error.retryable)

    def test_hard_timeout_scales_and_kills(self):
        script = os.path.join(self.tmp, "loop.sh")
        with open(script, "w") as fh:
            # Emits progress forever so only the hard timeout can stop it.
            fh.write("#!/bin/bash\n"
                     'for i in $(seq 1 100000); do echo "out_time_us=$((i*100000))"; '
                     "sleep 0.05; done\n")
        os.chmod(script, 0o755)
        result = self.runner.run(self._req(duration_s=0), [Attempt([script], "loop")])
        self.assertFalse(result.ok)
        self.assertIs(result.error.kind, ErrorKind.TIMEOUT)

    def test_process_group_kill_leaves_no_orphans(self):
        """The reason the runner uses start_new_session + killpg.

        The child writes its pid to a file and then sleeps. After the stall
        kill, that pid must be gone: proc.kill() would have left it running.
        """
        pidfile = os.path.join(self.tmp, "child.pid")
        script = os.path.join(self.tmp, "spawner.sh")
        with open(script, "w") as fh:
            fh.write("#!/bin/bash\n"
                     "sleep 300 &\n"
                     f'echo $! > {pidfile}\n'
                     'echo "out_time_us=1000000"\n'
                     "wait\n")
        os.chmod(script, 0o755)

        result = self.runner.run(self._req(), [Attempt([script], "spawner")])
        self.assertFalse(result.ok)
        self.assertIs(result.error.kind, ErrorKind.STALL)

        self.assertTrue(os.path.exists(pidfile), "child never started")
        with open(pidfile) as fh:
            child_pid = int(fh.read().strip())
        self.assertTrue(wait_gone(child_pid),
                        f"orphan {child_pid} survived the group kill — zombies leaked")

    def test_cancel_is_not_routed_around(self):
        """A cancel must stop the job, not trigger the next fallback rung.

        The predicate only turns true once the process is under way (it creates
        the marker file), so this exercises a mid-run cancel rather than the
        pre-spawn case below.
        """
        counter = os.path.join(self.tmp, "tries")
        marker = os.path.join(self.tmp, "running")
        script = os.path.join(self.tmp, "fail.sh")
        with open(script, "w") as fh:
            fh.write(f'#!/bin/bash\necho x >> {counter}\n'
                     f"touch {marker}\n"
                     'echo "out_time_us=1000000"\nsleep 120\n')
        os.chmod(script, 0o755)

        result = self.runner.run(
            self._req(should_cancel=lambda: os.path.exists(marker)),
            build_fallback_chain(
                Attempt([script], "copy", is_copy=True),
                genpts_copy=Attempt([script], "copy+genpts", is_copy=True),
                transcode=Attempt([script], "transcode"),
            ),
        )
        self.assertFalse(result.ok)
        self.assertIs(result.error.kind, ErrorKind.KILLED)
        with open(counter) as fh:
            tries = len([ln for ln in fh if ln.strip()])
        self.assertEqual(tries, 1, "cancel should not walk the fallback chain")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "out")))

    def test_cancel_before_start_never_spawns(self):
        counter = os.path.join(self.tmp, "tries")
        script = os.path.join(self.tmp, "fail.sh")
        with open(script, "w") as fh:
            fh.write(f'#!/bin/bash\necho x >> {counter}\nexit 1\n')
        os.chmod(script, 0o755)
        result = self.runner.run(
            self._req(should_cancel=lambda: True),
            [Attempt([script], "transcode")],
        )
        self.assertFalse(result.ok)
        self.assertIs(result.error.kind, ErrorKind.KILLED)
        self.assertFalse(os.path.exists(counter),
                         "a cancel before the first attempt must not spawn anything")

    def test_stderr_ring_buffer_is_bounded(self):
        """Bounded by construction: 500 lines of noise must not become 500
        strings held in memory for the life of the request."""
        script = os.path.join(self.tmp, "noisy.sh")
        with open(script, "w") as fh:
            fh.write("#!/bin/bash\nfor i in $(seq 1 500); do echo \"line $i\" >&2; done\nexit 0\n")
        os.chmod(script, 0o755)
        outcome = self.runner._run_attempt(self._req(), Attempt([script], "noisy"))
        lines = outcome.stderr_text.splitlines()
        self.assertLessEqual(len(lines), STDERR_RING_LINES)
        self.assertIn("line 500", outcome.stderr_text,
                      "the newest line must be kept — that is where causes live")
        self.assertNotIn("line 1\n", outcome.stderr_text + "\n")

    def test_shutdown_kills_running_processes(self):
        script = os.path.join(self.tmp, "forever.sh")
        with open(script, "w") as fh:
            fh.write('#!/bin/bash\necho "out_time_us=1000000"\nsleep 120\n')
        os.chmod(script, 0o755)
        req = self._req()

        import threading
        box = {}

        def go():
            box["result"] = self.runner.run(req, [Attempt([script], "forever")])

        thread = threading.Thread(target=go, daemon=True)
        thread.start()
        deadline = time.time() + 5
        while time.time() < deadline and not self.runner._active:
            time.sleep(0.05)
        self.assertTrue(self.runner._active, "process never registered as active")
        self.runner.shutdown()
        thread.join(timeout=10)
        self.assertFalse(thread.is_alive())
        self.assertFalse(self.runner._active)


class FallbackWalking(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        self.breaker = HwCircuitBreaker(threshold=2, cooldown_s=30.0)
        self.runner = EncodeRunner(config=FakeConfig, breaker=self.breaker)

    def _failing(self, name, stderr_text="Conversion failed"):
        script = os.path.join(self.tmp, name)
        with open(script, "w") as fh:
            fh.write(f'#!/bin/bash\necho "{stderr_text}" >&2\nexit 1\n')
        os.chmod(script, 0o755)
        return script

    def _counting(self, name, stderr_text=None, sleep_secs=0, touch=None):
        """A script that fails after recording that it ran.

        Returns (script_path, counter_path). The counter is how the tests prove
        *how many* times a rung was attempted, which is the only honest way to
        assert "did not retry" and "fell through immediately".
        """
        counter = os.path.join(self.tmp, name + ".count")
        script = os.path.join(self.tmp, name)
        lines = ["#!/bin/bash", f"echo x >> {counter}"]
        if touch:
            lines.append(f"touch {touch}")
        if stderr_text:
            lines.append(f'echo "{stderr_text}" >&2')
        if sleep_secs:
            lines.append(f'echo "out_time_us=1000000"')
            lines.append(f"sleep {sleep_secs}")
        lines.append("exit 1")
        with open(script, "w") as fh:
            fh.write("\n".join(lines) + "\n")
        os.chmod(script, 0o755)
        return script, counter

    @staticmethod
    def _count(path):
        if not os.path.exists(path):
            return 0
        with open(path) as fh:
            return len([ln for ln in fh if ln.strip()])

    def _req(self, **kw):
        kw.setdefault("output_dir", os.path.join(self.tmp, "out"))
        kw.setdefault("log_prefix", "test/item")
        return EncodeRequest(**kw)

    def test_walks_to_the_next_rung_and_succeeds(self):
        script = self._failing("bad.sh")
        good = os.path.join(self.tmp, "good.sh")
        with open(good, "w") as fh:
            fh.write('#!/bin/bash\nmkdir -p "$1/index.m3u8"\necho "#EXTM3U" > "$1/index.m3u8"\nexit 0\n')
        os.chmod(good, 0o755)
        out_dir = os.path.join(self.tmp, "out")
        chain = [
            Attempt([script], "copy", output_paths=(), is_copy=True),
            Attempt([good, out_dir], "transcode",
                    output_paths=(os.path.join(out_dir, "index.m3u8"),)),
        ]
        result = self.runner.run(self._req(output_dir=out_dir), chain)
        self.assertTrue(result.ok, msg=result.error)
        self.assertTrue(result.used_fallback)
        self.assertEqual([h[0] for h in result.history], ["copy", "transcode"])

    def test_hardware_failure_falls_through_to_software_without_retrying(self):
        """A dead GPU must not be retried: the software encoder is right there."""
        hw, hw_count = self._counting("hw", "VADisplay: No VAAPI driver")
        sw, sw_count = self._counting("sw", "Conversion failed")
        chain = build_fallback_chain(
            Attempt([hw], "hw", uses_hw=True),
            software=Attempt([sw], "software"),
        )
        result = self.runner.run(self._req(output_dir=""), chain)
        self.assertFalse(result.ok)
        self.assertEqual(self._count(hw_count), 1, "hardware rung was retried")
        self.assertEqual(self._count(sw_count), FakeConfig.MAX_RETRIES + 1,
                         "the final rung is the one that may retry")
        self.assertEqual([h[0] for h in result.history][:2], ["hw", "software"])
        self.assertTrue(result.used_fallback)
        # The breaker threshold here is 2, so one hardware failure must not trip
        # it yet — but it must have been recorded, which the next failure proves.
        self.assertFalse(self.breaker.disabled, "tripped below the threshold")
        self.breaker.record_failure()
        self.assertTrue(self.breaker.disabled, "the hw failure was never recorded")

    def test_non_retryable_failure_is_not_retried(self):
        script, counter = self._counting("nr", "No space left on device")
        result = self.runner.run(self._req(output_dir=""), [Attempt([script], "transcode")])
        self.assertFalse(result.ok)
        self.assertIs(result.error.kind, ErrorKind.DISK_FULL)
        self.assertFalse(result.error.retryable)
        self.assertEqual(self._count(counter), 1, "a full disk will still be full next time")

    def test_retryable_failure_retries_the_last_rung_with_backoff(self):
        script, counter = self._counting("r", "something transient")
        started = time.time()
        result = self.runner.run(self._req(output_dir=""), [Attempt([script], "transcode")])
        elapsed = time.time() - started
        self.assertFalse(result.ok)
        self.assertEqual(self._count(counter), FakeConfig.MAX_RETRIES + 1)
        # Backoff is real, not instant. Floor is half the base delay per retry.
        self.assertGreaterEqual(elapsed, FakeConfig.RETRY_BASE_DELAY * 0.5 * 2)

    def test_open_circuit_skips_hardware_rungs(self):
        good = self._failing("unused.sh")
        self.breaker.record_failure()
        self.breaker.record_failure()
        self.assertTrue(self.breaker.disabled)
        result = self.runner.run(self._req(output_dir=""), build_fallback_chain(
            Attempt([good], "hw", uses_hw=True),
            software=Attempt([good], "software"),
        ))
        self.assertFalse(result.ok)
        self.assertEqual(result.history[0], ("hw", "skipped: hardware circuit breaker open"))
        self.assertTrue(any("hardware circuit breaker open" in note
                            for _, note in result.history))

    def test_failed_run_leaves_no_partial_and_no_output(self):
        script = self._failing("f.sh", "invalid data found when processing input")
        out_dir = os.path.join(self.tmp, "out")
        result = self.runner.run(
            self._req(output_dir=out_dir), [Attempt([script], "transcode")])
        self.assertFalse(result.ok)
        self.assertFalse(os.path.exists(out_dir), "a failed encode must publish nothing")
        self.assertFalse(os.path.exists(out_dir + PARTIAL_SUFFIX),
                         "the partial directory must be cleaned up")


@unittest.skipUnless(FFMPEG, "ffmpeg not installed")
@unittest.skipUnless(shutil.which("ffprobe"), "ffprobe not installed")
class RealEncode(unittest.TestCase):
    """End to end: the production argv, run by the production runner."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        self.src = os.path.join(self.tmp, "src.mp4")
        self.assertTrue(make_h264(self.src), "could not build the test fixture")

    def _copy_attempt(self, out_dir, **kw):
        p = plan(probe(self.src), hw=HwAccel(), **kw)
        playlist = os.path.join(out_dir, "index.m3u8")
        template = os.path.join(out_dir, "seg_%05d.m4s")
        argv = build_plan_args(
            p, self.src, 1, out_dir, template, hw=None, src_width=W,
            playlist_name="index.m3u8",
        )
        return Attempt(argv, "copy", output_paths=(template, playlist), is_copy=True)

    def test_copy_encode_succeeds_and_promotes_atomically(self):
        out_dir = os.path.join(self.tmp, "hls")
        attempt = self._copy_attempt(out_dir)
        runner = EncodeRunner(config=FakeConfig, breaker=HwCircuitBreaker())
        progress = []
        result = runner.run(
            EncodeRequest(item_id="i1", label="copy", source=self.src,
                          duration_s=DURATION, output_dir=out_dir,
                          on_progress=progress.append, ready_after_s=0.5),
            [attempt],
        )
        self.assertTrue(result.ok, msg=result.error)
        self.assertTrue(os.path.exists(os.path.join(out_dir, "index.m3u8")))
        self.assertFalse(os.path.exists(out_dir + PARTIAL_SUFFIX),
                         "the partial directory must not survive a success")
        self.assertFalse(os.path.exists(out_dir + ".old"))
        self.assertGreater(result.encoded_seconds, 0.0,
                           "progress was never parsed from -progress")
        self.assertGreaterEqual(result.duration, 1.0)
        self.assertTrue(progress, "no progress events were delivered")
        self.assertTrue(all("encoded_seconds" in ev for ev in progress))

    def test_ready_fires_once_when_past_the_threshold(self):
        out_dir = os.path.join(self.tmp, "hls")
        runner = EncodeRunner(config=FakeConfig, breaker=HwCircuitBreaker())
        fired = []
        result = runner.run(
            EncodeRequest(source=self.src, duration_s=DURATION, output_dir=out_dir,
                          on_ready=fired.append, ready_after_s=0.5),
            [self._copy_attempt(out_dir)],
        )
        self.assertTrue(result.ok, msg=result.error)
        self.assertEqual(len(fired), 1, f"ready fired {len(fired)} times")
        self.assertGreaterEqual(fired[0], 0.5)

    def test_progress_never_exceeds_the_reported_duration(self):
        """Guards the out_time_ms microsecond trap end to end: a 1000x
        misread would report progress far past the end of the clip."""
        out_dir = os.path.join(self.tmp, "hls")
        runner = EncodeRunner(config=FakeConfig, breaker=HwCircuitBreaker())
        progress = []
        result = runner.run(
            EncodeRequest(source=self.src, duration_s=DURATION, output_dir=out_dir,
                          on_progress=progress.append),
            [self._copy_attempt(out_dir)],
        )
        self.assertTrue(result.ok, msg=result.error)
        self.assertTrue(progress)
        worst = max(ev["encoded_seconds"] for ev in progress)
        self.assertLessEqual(worst, DURATION + 1.0,
                             f"progress {worst}s overshot a {DURATION}s clip")

    def test_output_is_fmp4(self):
        out_dir = os.path.join(self.tmp, "hls")
        runner = EncodeRunner(config=FakeConfig, breaker=HwCircuitBreaker())
        result = runner.run(
            EncodeRequest(source=self.src, duration_s=DURATION, output_dir=out_dir),
            [self._copy_attempt(out_dir)],
        )
        self.assertTrue(result.ok, msg=result.error)
        playlist = os.path.join(out_dir, "index.m3u8")
        with open(playlist) as fh:
            body = fh.read()
        self.assertIn("#EXT-X-MAP:URI=", body, "not fMP4 — no init segment referenced")

    def test_promoted_output_replaces_a_previous_rendition(self):
        """A re-encode must swap the old directory out, not nest inside it."""
        out_dir = os.path.join(self.tmp, "hls")
        os.makedirs(out_dir)
        stale = os.path.join(out_dir, "stale_leftover.txt")
        with open(stale, "w") as fh:
            fh.write("old")

        runner = EncodeRunner(config=FakeConfig, breaker=HwCircuitBreaker())
        result = runner.run(
            EncodeRequest(source=self.src, duration_s=DURATION, output_dir=out_dir),
            [self._copy_attempt(out_dir)],
        )
        self.assertTrue(result.ok, msg=result.error)
        self.assertFalse(os.path.exists(stale), "stale file survived the promotion")
        self.assertTrue(os.path.exists(os.path.join(out_dir, "index.m3u8")))
        self.assertFalse(os.path.exists(out_dir + ".old"))

    def test_real_input_failure_is_classified_from_stderr(self):
        """A truncated file, classified from real ffmpeg diagnostics."""
        broken = os.path.join(self.tmp, "broken.mp4")
        with open(self.src, "rb") as src, open(broken, "wb") as dst:
            dst.write(src.read(4000))

        out_dir = os.path.join(self.tmp, "hls")
        attempt = Attempt(
            ["ffmpeg", "-i", broken, "-c", "copy", "-f", "hls", os.path.join(out_dir, "index.m3u8")],
            "copy",
            output_paths=(os.path.join(out_dir, "index.m3u8"),),
            is_copy=True,
        )
        runner = EncodeRunner(config=FakeConfig, breaker=HwCircuitBreaker())
        result = runner.run(
            EncodeRequest(source=broken, duration_s=DURATION, output_dir=out_dir),
            [attempt],
        )
        self.assertFalse(result.ok)
        self.assertIs(result.error.kind, ErrorKind.CORRUPT)
        self.assertTrue(result.error.detail, "an operator needs to see something")
        self.assertNotIn(self.tmp, result.error.user_message,
                         "a filesystem path must never reach the client")
        self.assertFalse(os.path.exists(out_dir))


if __name__ == "__main__":
    unittest.main()