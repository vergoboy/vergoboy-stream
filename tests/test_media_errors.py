"""Unit tests for the failure taxonomy.

Classification is the input to every retry/fallback decision, so it is tested
directly rather than only through the runner. The banner cases matter most:
ffmpeg's own startup output mentions every hardware backend the build supports,
which is exactly the kind of string that makes a naive "does stderr mention
nvenc?" test wrong.

    python3 -m unittest tests.test_media_errors
"""

import unittest

from media_pipeline.errors import (
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
    retryable_by_operator,
)
from media_pipeline.probe import ProbeError as ProbeErrorViaProbe

#: The real startup output of a GPU-enabled ffmpeg build. Truncated, but the
#: parts that matter are verbatim: the configure line names every encoder.
REAL_BANNER = """ffmpeg version n9.0.2 Copyright (c) 2000-2026 the FFmpeg developers
  built with gcc 16 (GCC)
  configuration: --prefix=/usr --enable-libdrm --enable-nvenc --enable-nvdec
  libavutil      61.  1.102 / 61.  1.102
  libavcodec     63.  1.102 / 63.  1.102
"""


class Hierarchy(unittest.TestCase):
    def test_every_error_is_a_media_error(self):
        for cls in (ProbeError, UnsupportedMediaError, PlanError, SubtitleError, EncodeError):
            self.assertTrue(issubclass(cls, MediaError), cls)

    def test_probe_uses_the_shared_error(self):
        """probe.py must not keep its own ProbeError, or callers would have to
        catch two unrelated classes for the same failure."""
        self.assertIs(ProbeErrorViaProbe, ProbeError)

    def test_exceptions_carry_their_message(self):
        self.assertIsInstance(EncodeError(ErrorKind.STALL, "wedged"), MediaError)
        self.assertIn("stall", str(EncodeError(ErrorKind.STALL, "wedged")))


class Classification(unittest.TestCase):
    def test_corrupt_input(self):
        for text in (
            "moov atom not found",
            "Invalid data found when processing input",
            "error while decoding MB",
            "truncated file",
        ):
            self.assertIs(classify_stderr(text), ErrorKind.CORRUPT, text)

    def test_disk_full(self):
        for text in ("No space left on device", "disk quota exceeded", "ENOSPC"):
            self.assertIs(classify_stderr(text), ErrorKind.DISK_FULL, text)

    def test_out_of_memory(self):
        for text in ("Cannot allocate memory", "std::bad_alloc", "mmap failed"):
            self.assertIs(classify_stderr(text), ErrorKind.OOM, text)

    def test_hardware_failure(self):
        for text in ("VADisplay: No VAAPI driver", "Failed to initialise VAAPI",
                     "Impossible to open MFX handle", "device or resource busy"):
            self.assertIs(classify_stderr(text), ErrorKind.HW_FAILURE, text)

    def test_missing_hardware_encoder_is_a_hardware_failure(self):
        """The verbatim failure of a build without QSV/NVENC.

        Getting this wrong means the job never falls back to software, which is
        the entire point of having a fallback.
        """
        for text in ("[vost#0:0 @ 0x56d2f62e94c0] Unknown encoder 'qsv'",
                     "Unknown encoder 'h264_nvenc'",
                     "Unknown encoder 'h264_vaapi'"):
            self.assertIs(classify_stderr(text), ErrorKind.HW_FAILURE, text)

    def test_missing_software_encoder_is_not_a_hardware_failure(self):
        """Same wording, different cause: blaming the GPU would send the retry
        policy down the wrong path."""
        self.assertIsNone(classify_stderr("Unknown encoder 'libx264'"))
        self.assertIsNone(classify_stderr("Unknown encoder 'aac'"))

    def test_no_match_returns_none_rather_than_guessing(self):
        self.assertIsNone(classify_stderr("something nobody has seen before"))
        self.assertIsNone(classify_stderr(""))

    def test_distinctive_string_beats_a_generic_one(self):
        """Order matters: a full disk often *also* says 'error', and a driver
        complaint often also says 'failed'."""
        self.assertIs(
            classify_stderr("Cannot allocate memory\nError initializing encoder"),
            ErrorKind.OOM,
        )
        self.assertIs(
            classify_stderr("No space left on device\nError writing trailer"),
            ErrorKind.DISK_FULL,
        )


class BannerIndependence(unittest.TestCase):
    """The regression that made every corrupt input look like a GPU fault."""

    def test_banner_alone_is_not_a_hardware_failure(self):
        self.assertIsNone(classify_stderr(REAL_BANNER))

    def test_corrupt_input_with_a_banner_is_still_corrupt(self):
        text = REAL_BANNER + "[in#0] moov atom not found\nError opening input file x.mp4."
        self.assertIs(classify_stderr(text), ErrorKind.CORRUPT)

    def test_a_real_hw_failure_is_still_detected_past_a_banner(self):
        text = REAL_BANNER + "VADisplay: No VAAPI driver"
        self.assertIs(classify_stderr(text), ErrorKind.HW_FAILURE)

    def test_banner_does_not_become_the_operator_detail(self):
        err = classify_failure(183, REAL_BANNER + "Error opening input files: broken")
        self.assertNotIn("configuration", err.detail)
        self.assertNotIn("--enable", err.detail)
        self.assertIn("broken", err.detail)


class FailureSemantics(unittest.TestCase):
    def test_success_is_never_a_failure(self):
        """A zero exit is not an error; if a caller reports one anyway, say so
        plainly instead of inventing a cause."""
        err = classify_failure(0, "")
        self.assertIs(err.kind, ErrorKind.UNKNOWN)
        self.assertIn("exit code was 0", err.detail)

    def test_signal_death_is_killed(self):
        import signal
        for sig in (signal.SIGKILL, signal.SIGTERM):
            err = classify_failure(-int(sig), "")
            self.assertIs(err.kind, ErrorKind.KILLED, sig.name)
            self.assertIn(sig.name, err.detail)

    def test_sigxcpu_is_oom(self):
        import signal
        self.assertIs(classify_failure(-int(signal.SIGXCPU), "").kind, ErrorKind.OOM)

    def test_timeout_beats_everything(self):
        """We killed it, so no text match can improve on that."""
        err = classify_failure(1, "No space left on device", timed_out=True)
        self.assertIs(err.kind, ErrorKind.TIMEOUT)

    def test_stall_beats_stderr_text(self):
        err = classify_failure(1, "VADisplay: No VAAPI driver", stalled=True)
        self.assertIs(err.kind, ErrorKind.STALL)

    def test_returncode_is_preserved_for_the_operator(self):
        self.assertEqual(classify_failure(183, "boom").returncode, 183)

    def test_stderr_tail_is_capped(self):
        err = classify_failure(1, "x" * 10_000)
        self.assertLessEqual(len(err.stderr_tail), 2000)


class RetryPolicy(unittest.TestCase):
    def test_operator_may_retry_anything_but_corruption(self):
        self.assertFalse(retryable_by_operator(ErrorKind.CORRUPT))
        for kind in ErrorKind:
            if kind is not ErrorKind.CORRUPT:
                self.assertTrue(retryable_by_operator(kind), kind)

    def test_automatic_retries_are_the_transient_kinds(self):
        self.assertEqual(
            RETRYABLE_KINDS,
            frozenset({ErrorKind.TIMEOUT, ErrorKind.STALL,
                       ErrorKind.HW_FAILURE, ErrorKind.KILLED, ErrorKind.UNKNOWN}),
        )
        # Retrying these would burn CPU to produce the identical failure.
        for kind in (ErrorKind.DISK_FULL, ErrorKind.OOM, ErrorKind.CORRUPT):
            self.assertNotIn(kind, RETRYABLE_KINDS)
            self.assertFalse(EncodeError(kind).retryable, kind)

    def test_retryable_can_be_overridden(self):
        """A corrupt *container* can still be a timestamp problem, which the
        runner signals by forcing the flag on."""
        err = EncodeError(ErrorKind.CORRUPT, "bad dts", retryable=True)
        self.assertTrue(err.retryable)


class ClientSafety(unittest.TestCase):
    def test_every_kind_has_a_user_message(self):
        for kind in ErrorKind:
            self.assertTrue(EncodeError(kind).user_message.strip(), kind)

    def test_user_message_never_leaks_detail(self):
        detail = ("/srv/media/uploads/secret-client-name.mkv failed: "
                  "No space left on device, exit 183")
        for kind in ErrorKind:
            message = EncodeError(kind, detail).user_message
            for leak in ("/srv/", "secret-client-name", "183", "exit", "ffmpeg"):
                self.assertNotIn(leak, message, f"{kind}: leaked {leak!r}")

    def test_messages_are_human_readable_persian(self):
        """The UI is Persian; an English error string would reach the viewer."""
        for kind in ErrorKind:
            message = EncodeError(kind).user_message
            self.assertTrue(
                any("؀" <= ch <= "ۿ" for ch in message),
                f"{kind.value} has no Persian script: {message!r}",
            )

    def test_client_payload_shape(self):
        payload = EncodeError(ErrorKind.DISK_FULL, "/srv/x.mkv").client_payload("abc123")
        self.assertEqual(set(payload), {"item_id", "kind", "user_message", "retryable"})
        self.assertEqual(payload["item_id"], "abc123")
        self.assertEqual(payload["kind"], "disk_full")
        self.assertIs(payload["retryable"], False)
        self.assertNotIn("/srv", payload["user_message"])


if __name__ == "__main__":
    unittest.main()