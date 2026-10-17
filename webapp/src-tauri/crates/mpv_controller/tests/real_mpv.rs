//! Integration tests against a real mpv binary.
//!
//! Every test skips itself (prints `SKIP: …` and returns) when mpv — or the
//! tool it needs — is not installed, so the suite still passes on a box
//! without a media stack. Unix-only: the IPC transport is a unix socket.
//!
//! These cover what the unit tests cannot: that `map_event`'s interpretation
//! of mpv v0.41's wire behaviour (no `reason` on `seek`, on-subscription
//! property emissions, double EOF signals) matches the real thing, that a
//! crashed mpv is noticed, restarted and reaped — and that after the restart
//! budget is spent nothing is left running.

#![cfg(unix)]

use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use mpv_controller::ipc::RpcClient;
use mpv_controller::{
    locate_mpv, CacheProfile, MpvOptions, MpvPlayer, MpvProcess, PlayerError, PlayerEvent,
    PlayerPort, SubStyle,
};

static SEQ: AtomicUsize = AtomicUsize::new(0);

fn sock_path(tag: &str) -> PathBuf {
    std::env::temp_dir().join(format!(
        "mpv-controller-{}-{}-{tag}.sock",
        std::process::id(),
        SEQ.fetch_add(1, Ordering::SeqCst)
    ))
}

fn fixture_dir(tag: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("mpv-ctl-{}-{tag}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).expect("create fixture dir");
    dir
}

fn start_player(tag: &str) -> MpvPlayer {
    let mut opts = MpvOptions::new(sock_path(tag));
    opts.headless = true;
    MpvPlayer::start(opts).expect("spawn mpv")
}

/// Like [`start_player`], but the caller owns the socket path — the process
/// table checks below need it to find the mpv that belongs to *this* test.
fn start_player_at(sock: &Path) -> MpvPlayer {
    let mut opts = MpvOptions::new(sock.to_path_buf());
    opts.headless = true;
    MpvPlayer::start(opts).expect("spawn mpv")
}

/// The process-table half of the "nothing was left behind" evidence.
///
/// A *live* mpv keeps its full argv, which contains the unique
/// `--input-ipc-server=<path>` this player passed, so it is findable by that
/// path. A *zombie* would have no argv left (`ps` shows it as
/// `Z [mpv] <defunct>`), so it is invisible here — that half is covered by
/// `pid_alive`, which uses `kill -0` and therefore *does* see zombies. The two
/// together say "gone from the process table", and this one is specific to
/// this test rather than to any mpv on the box.
fn ps_has_socket(sock: &Path) -> bool {
    let out = std::process::Command::new("ps")
        .args(["-eo", "args"])
        .output()
        .expect("run ps");
    let needle = sock.display().to_string();
    String::from_utf8_lossy(&out.stdout).contains(&needle)
}

/// Poll `p`, appending everything to `seen`, until an event at index `>= from`
/// matches. Already-buffered events count, so nothing observed during an
/// earlier wait is lost, and a later wait can start from the previous match.
fn wait_from(
    p: &mut MpvPlayer,
    seen: &mut Vec<PlayerEvent>,
    from: usize,
    pred: impl Fn(&PlayerEvent) -> bool,
    timeout: Duration,
) -> Option<usize> {
    for (i, ev) in seen.iter().enumerate().skip(from) {
        if pred(ev) {
            return Some(i);
        }
    }
    let deadline = Instant::now() + timeout;
    loop {
        let now = Instant::now();
        if now >= deadline {
            return None;
        }
        let base = seen.len();
        seen.extend(p.poll_events(deadline - now));
        for (i, ev) in seen.iter().enumerate().skip(base) {
            if pred(ev) {
                return Some(i);
            }
        }
    }
}

/// Keep polling for a fixed window, so a *duplicate* event that would arrive
/// later still has a chance to show up before it is counted.
fn poll_window(p: &mut MpvPlayer, seen: &mut Vec<PlayerEvent>, window: Duration) {
    let deadline = Instant::now() + window;
    loop {
        let now = Instant::now();
        if now >= deadline {
            return;
        }
        seen.extend(p.poll_events(deadline - now));
    }
}

/// Poll `get_pos` until `pred` holds (position advances on its own).
fn wait_pos(p: &mut MpvPlayer, pred: impl Fn(f64) -> bool, timeout: Duration) -> Option<f64> {
    let deadline = Instant::now() + timeout;
    loop {
        if let Ok(pos) = p.get_pos() {
            if pred(pos) {
                return Some(pos);
            }
        }
        if Instant::now() >= deadline {
            return None;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
}

/// `kill -<sig> pid` via the shell: `std` can only signal children, and this
/// pid was deliberately made someone else's problem by the test.
fn signal(pid: u32, sig: &str) {
    let st = std::process::Command::new("sh")
        .args(["-c", &format!("kill -{sig} {pid}")])
        .status()
        .expect("run kill");
    assert!(st.success(), "kill -{sig} {pid} failed");
}

fn kill_hard(pid: u32) {
    signal(pid, "9");
}

/// `kill -0`: alive *or* a zombie — so asserting `false` proves the pid was
/// reaped (a zombie would still answer) and is gone from the table.
fn pid_alive(pid: u32) -> bool {
    std::process::Command::new("sh")
        .args(["-c", &format!("kill -0 {pid} 2>/dev/null")])
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

fn have(cmd: &str) -> bool {
    std::process::Command::new(cmd)
        .arg("-version")
        .output()
        .is_ok()
}

/// A real PCM WAV, written byte by byte — no ffmpeg needed for the basics.
fn write_sine_wav(path: &std::path::Path, secs: f64) {
    const RATE: u32 = 44_100;
    let n = (RATE as f64 * secs) as u32;
    let data_len = n * 2;
    let mut buf: Vec<u8> = Vec::with_capacity(44 + data_len as usize);
    buf.extend_from_slice(b"RIFF");
    buf.extend_from_slice(&(36 + data_len).to_le_bytes());
    buf.extend_from_slice(b"WAVE");
    buf.extend_from_slice(b"fmt ");
    buf.extend_from_slice(&16u32.to_le_bytes()); // fmt chunk size
    buf.extend_from_slice(&1u16.to_le_bytes()); // PCM
    buf.extend_from_slice(&1u16.to_le_bytes()); // mono
    buf.extend_from_slice(&RATE.to_le_bytes());
    buf.extend_from_slice(&(RATE * 2).to_le_bytes()); // byte rate
    buf.extend_from_slice(&2u16.to_le_bytes()); // block align
    buf.extend_from_slice(&16u16.to_le_bytes()); // bits per sample
    buf.extend_from_slice(b"data");
    buf.extend_from_slice(&data_len.to_le_bytes());
    for i in 0..n {
        let t = i as f32 / RATE as f32;
        let sample = (t * 440.0 * 2.0 * std::f32::consts::PI).sin() * 0.2;
        buf.extend_from_slice(&((sample * i16::MAX as f32) as i16).to_le_bytes());
    }
    std::fs::write(path, buf).expect("write wav");
}

#[test]
fn plays_a_real_file_with_local_controls() {
    if locate_mpv().is_none() {
        eprintln!("SKIP: mpv not installed");
        return;
    }
    let dir = fixture_dir("controls");
    let wav = dir.join("controls.wav");
    write_sine_wav(&wav, 30.0);

    let sock = sock_path("controls");
    let mut p = start_player_at(&sock);
    let mut seen = Vec::new();

    // Load paused: Ready must arrive and the position must be at the start.
    p.load(wav.to_str().unwrap(), 0.0, true)
        .expect("load a local wav");
    wait_from(
        &mut p,
        &mut seen,
        0,
        |e| matches!(e, PlayerEvent::Ready),
        Duration::from_secs(15),
    )
    .expect("Ready after load");
    let pos = p.get_pos().expect("position after load");
    assert!(pos < 2.0, "loaded paused at the start, got {pos}");

    // Play: time must advance on its own.
    p.set_paused(false).expect("unpause");
    let playing = wait_pos(&mut p, |pos| pos > 0.3, Duration::from_secs(5))
        .expect("time advances while playing");
    assert!(playing < 5.0, "should still be near the start, got {playing}");

    // Seek: the absolute position must land.
    p.seek(12.0).expect("seek");
    wait_pos(&mut p, |pos| (11.0..13.0).contains(&pos), Duration::from_secs(5))
        .expect("seek lands near 12s");

    // Every control the port exposes is accepted by a real mpv.
    p.set_rate(2.0).expect("rate");
    p.set_aid(None).expect("aid");
    p.set_sid(None).expect("sid");
    p.set_cache_profile(CacheProfile::Metered).expect("cache profile");

    // End of file: exactly one Ended, even though mpv can signal EOF twice
    // (`eof-reached` and `end-file`).
    p.seek(29.4).expect("seek to the end");
    let ended = wait_from(
        &mut p,
        &mut seen,
        0,
        |e| matches!(e, PlayerEvent::Ended),
        Duration::from_secs(15),
    )
    .expect("Ended at end of file");
    poll_window(&mut p, &mut seen, Duration::from_millis(1500));
    let ends = seen[ended..]
        .iter()
        .filter(|e| matches!(e, PlayerEvent::Ended))
        .count();
    assert_eq!(ends, 1, "Ended exactly once per file, got {seen:?}");

    // Shutdown: no zombie left behind, and no live mpv for this player either.
    let pid = p.pid().expect("pid before shutdown");
    p.shutdown();
    assert!(!p.is_running(), "shutdown must stop the player");
    assert!(!pid_alive(pid), "mpv pid {pid} must be reaped, not a zombie");
    assert!(
        !ps_has_socket(&sock),
        "no mpv still running for this player's socket"
    );

    drop(p);
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn crash_recovery_restarts_reaps_and_eventually_gives_up() {
    if locate_mpv().is_none() {
        eprintln!("SKIP: mpv not installed");
        return;
    }
    let dir = fixture_dir("crash");
    let wav = dir.join("crash.wav");
    write_sine_wav(&wav, 60.0); // long enough that EOF never interferes

    let sock = sock_path("crash");
    let mut p = start_player_at(&sock);
    let mut seen = Vec::new();

    p.load(wav.to_str().unwrap(), 5.0, false).expect("load");
    wait_from(
        &mut p,
        &mut seen,
        0,
        |e| matches!(e, PlayerEvent::Ready),
        Duration::from_secs(15),
    )
    .expect("Ready");
    // Let it play so `last_pos` moves past the load position — that is what
    // the recovery will resume from.
    wait_pos(&mut p, |pos| pos > 5.3, Duration::from_secs(5)).expect("playing");

    // Output state the restart has to carry across the crash.
    p.set_volume(37).expect("volume");
    p.set_mute(true).expect("mute");

    // Crash 1: the very next player call must recover on its own.
    let previous = p.pid().expect("pid");
    kill_hard(previous);
    let idx = wait_from(
        &mut p,
        &mut seen,
        0,
        |e| matches!(e, PlayerEvent::ProcessCrashed { restarts: 1 }),
        Duration::from_secs(15),
    )
    .expect("restart reported");
    assert_eq!(p.restarts(), 1);
    assert!(p.is_running(), "player back after crash 1");
    assert_ne!(p.pid(), Some(previous), "a fresh process, not the dead one");
    assert!(!pid_alive(previous), "old mpv reaped, not a zombie");
    wait_from(
        &mut p,
        &mut seen,
        idx,
        |e| matches!(e, PlayerEvent::Ready),
        Duration::from_secs(15),
    )
    .expect("media reloaded after crash 1");
    // …resumed near where it was, not at the file's beginning.
    wait_pos(&mut p, |pos| (4.5..8.0).contains(&pos), Duration::from_secs(5))
        .expect("resumed near the pre-crash position");
    // …and with the output state the user had, not mpv's defaults.
    let st = p.get_state().expect("state after the restart");
    assert_eq!(st.volume, 37, "volume must survive the restart");
    assert!(st.mute, "mute must survive the restart");

    // Crashes 2 and 3 still recover.
    for attempt in 2..=3u32 {
        let before = p.pid().expect("pid");
        kill_hard(before);
        wait_from(
            &mut p,
            &mut seen,
            0,
            |e| matches!(e, PlayerEvent::ProcessCrashed { restarts } if *restarts == attempt),
            Duration::from_secs(15),
        )
        .unwrap_or_else(|| panic!("restart attempt {attempt} reported"));
        assert_eq!(p.restarts(), attempt);
        assert!(p.is_running(), "back after crash {attempt}");
        assert!(!pid_alive(before), "attempt {attempt} reaped the old mpv");
    }

    // Crash 4: the restart budget (3) is spent — one clear fatal error, then
    // silence, and nothing left running.
    let last = p.pid().expect("pid");
    kill_hard(last);
    wait_from(
        &mut p,
        &mut seen,
        0,
        |e| matches!(e, PlayerEvent::Error(m) if m.contains("giving up")),
        Duration::from_secs(15),
    )
    .expect("fatal error reported");
    assert_eq!(p.restarts(), 3);
    assert!(!p.is_running());
    assert!(!pid_alive(last), "the last mpv was reaped by the fatal check");
    assert!(
        !ps_has_socket(&sock),
        "the fatal give-up must leave no mpv running"
    );
    assert!(
        p.poll_events(Duration::from_millis(250)).is_empty(),
        "the fatal error must be reported once, not repeated"
    );

    p.shutdown();
    drop(p);
    let _ = std::fs::remove_dir_all(&dir);
}

/// A minimal HTTP server that hands out one file at a fixed rate — far below
/// what the fixture plays — so mpv *has* to stall and refill. That pair of
/// edges is `Buffering(true)` / `Buffering(false)` on the port.
struct ThrottledServer {
    addr: std::net::SocketAddr,
    stop: Arc<AtomicBool>,
    accept: Option<std::thread::JoinHandle<()>>,
}

impl ThrottledServer {
    fn start(bytes: Arc<Vec<u8>>, rate: u64) -> std::io::Result<Self> {
        let listener = TcpListener::bind(("127.0.0.1", 0))?;
        let addr = listener.local_addr()?;
        let stop = Arc::new(AtomicBool::new(false));
        let stop2 = Arc::clone(&stop);
        let accept = std::thread::spawn(move || {
            for conn in listener.incoming() {
                let Ok(stream) = conn else { break };
                if stop2.load(Ordering::SeqCst) {
                    break; // the poke connection that woke accept()
                }
                let bytes = Arc::clone(&bytes);
                std::thread::spawn(move || {
                    let _ = serve_one(stream, bytes, rate);
                });
            }
        });
        Ok(Self { addr, stop, accept: Some(accept) })
    }
}

impl Drop for ThrottledServer {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::SeqCst);
        let _ = TcpStream::connect(self.addr); // wake the accept loop
        if let Some(handle) = self.accept.take() {
            let _ = handle.join();
        }
    }
}

fn serve_one(mut stream: TcpStream, bytes: Arc<Vec<u8>>, rate: u64) -> std::io::Result<()> {
    stream.set_nodelay(true)?;
    // Read until the end of the request head.
    let mut head = Vec::new();
    let mut buf = [0u8; 1024];
    loop {
        let n = stream.read(&mut buf)?;
        if n == 0 {
            return Ok(());
        }
        head.extend_from_slice(&buf[..n]);
        if head.windows(4).any(|w| w == b"\r\n\r\n") || head.len() > 64 * 1024 {
            break;
        }
    }
    let head = String::from_utf8_lossy(&head).to_ascii_lowercase();
    let total = bytes.len() as u64;
    let digits = |s: &str| s.chars().take_while(|c| c.is_ascii_digit()).collect::<String>();

    let mut start = 0u64;
    let mut end = total;
    let ranged = if let Some(pos) = head.find("range: bytes=") {
        let spec = &head[pos + "range: bytes=".len()..];
        let from = digits(spec);
        if !from.is_empty() {
            start = from.parse().unwrap_or(0);
        }
        if let Some(dash) = spec.find('-') {
            let to = digits(&spec[dash + 1..]);
            if !to.is_empty() {
                end = (to.parse::<u64>().unwrap_or(total.saturating_sub(1)) + 1).min(total);
            }
        }
        true
    } else {
        false
    };

    if start >= total {
        stream.write_all(
            b"HTTP/1.1 416 Range Not Satisfiable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
        )?;
        return Ok(());
    }
    end = end.clamp(start + 1, total);
    let len = end - start;
    let status = if ranged { "206 Partial Content" } else { "200 OK" };
    let mut header = format!(
        "HTTP/1.1 {status}\r\nContent-Type: video/mp4\r\nAccept-Ranges: bytes\r\nContent-Length: {len}\r\n"
    );
    if ranged {
        header.push_str(&format!(
            "Content-Range: bytes {start}-{}/{total}\r\n",
            end - 1
        ));
    }
    header.push_str("Connection: close\r\n\r\n");
    stream.write_all(header.as_bytes())?;
    stream.flush()?;

    // Throttled body: 4 KiB at a time so playback really has to wait.
    let chunk: u64 = 4096;
    let mut sent: u64 = 0;
    while sent < len {
        let n = chunk.min(len - sent) as usize;
        let from = (start + sent) as usize;
        stream.write_all(&bytes[from..from + n])?;
        stream.flush()?;
        sent += n as u64;
        std::thread::sleep(Duration::from_micros(chunk * 1_000_000 / rate.max(1)));
    }
    Ok(())
}

#[test]
fn buffering_edges_appear_on_a_throttled_http_source() {
    if locate_mpv().is_none() {
        eprintln!("SKIP: mpv not installed");
        return;
    }
    if !have("ffmpeg") {
        eprintln!("SKIP: ffmpeg not installed (needed for the video fixture)");
        return;
    }
    let dir = fixture_dir("buffering");
    let mp4 = dir.join("source.mp4");
    let st = std::process::Command::new("ffmpeg")
        .args([
            "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=duration=30:size=320x240:rate=24",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=30",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            // moov at the front: without it mpv cannot demux over HTTP until
            // the throttled server has shipped the whole file.
            "-movflags", "+faststart", "-c:a", "aac", "-shortest",
        ])
        .arg(&mp4)
        .status()
        .expect("run ffmpeg");
    assert!(st.success(), "ffmpeg fixture build failed");

    // Far below the fixture's playback bitrate: mpv must starve and refill.
    let bytes = Arc::new(std::fs::read(&mp4).expect("read fixture"));
    let server = ThrottledServer::start(bytes, 12 * 1024).expect("start server");
    let url = format!("http://{}/source.mp4", server.addr);

    let mut p = start_player("buffering");
    let mut seen = Vec::new();
    p.load(&url, 0.0, false).expect("load over http");
    wait_from(
        &mut p,
        &mut seen,
        0,
        |e| matches!(e, PlayerEvent::Ready),
        Duration::from_secs(20),
    )
    .expect("file-loaded over a slow source");

    let starved = wait_from(
        &mut p,
        &mut seen,
        0,
        |e| matches!(e, PlayerEvent::Buffering(true)),
        Duration::from_secs(25),
    )
    .expect("Buffering(true) while the source cannot keep up");
    wait_from(
        &mut p,
        &mut seen,
        starved,
        |e| matches!(e, PlayerEvent::Buffering(false)),
        Duration::from_secs(30),
    )
    .expect("Buffering(false) once the cache refilled");

    p.shutdown();
    drop(p);
    drop(server);
    let _ = std::fs::remove_dir_all(&dir);
}

/// The controls S1 added, against a real mpv: output levels, a subtitle track,
/// subtitle styling, and the `get_state` snapshot. The unit tests pin the
/// *mapping* (command name, clamp, `None` = leave alone); this proves the
/// names and flag spellings are the ones mpv v0.41 actually accepts, and that
/// `sub-add … select` really produces a selected subtitle track.
#[test]
fn the_new_controls_and_state_snapshot_work_against_real_mpv() {
    if locate_mpv().is_none() {
        eprintln!("SKIP: mpv not installed");
        return;
    }
    let dir = fixture_dir("newctl");
    let wav = dir.join("newctl.wav");
    write_sine_wav(&wav, 30.0);
    let srt = dir.join("subs.srt");
    std::fs::write(
        &srt,
        "1\n00:00:00,000 --> 00:00:05,000\nhello from the test\n",
    )
    .expect("write srt");

    let sock = sock_path("newctl");
    let mut p = start_player_at(&sock);
    let mut seen = Vec::new();

    p.load(wav.to_str().unwrap(), 3.0, true).expect("load");
    wait_from(
        &mut p,
        &mut seen,
        0,
        |e| matches!(e, PlayerEvent::Ready),
        Duration::from_secs(15),
    )
    .expect("Ready");

    // `Loaded` carried real metadata, not the defaults a unit test sees: the
    // duration was fetched and the file has at least one track.
    let (duration, tracks) = seen
        .iter()
        .find_map(|e| match e {
            PlayerEvent::Loaded { duration, tracks } => Some((*duration, tracks.clone())),
            _ => None,
        })
        .expect("Loaded with metadata");
    assert!((29.0..31.0).contains(&duration), "duration ~30s, got {duration}");
    assert!(!tracks.is_empty(), "a wav has at least one track");

    // Output controls are accepted; an out-of-range volume is clamped, not
    // rejected, and `set_volume(37)` afterwards must read back as 37.
    p.set_volume(37).expect("volume");
    p.set_mute(true).expect("mute");
    p.set_rate(1.5).expect("rate");
    p.set_volume(200).expect("out-of-range volume is clamped, not rejected");
    p.set_volume(37).expect("volume again");
    p.set_fullscreen(false).expect("fullscreen");

    // A subtitle can be added, selected, and styled.
    p.add_subtitle(srt.to_str().unwrap()).expect("add subtitle");
    p.set_sub_style(SubStyle {
        scale: Some(1.5),
        color: Some("#FF0000".into()),
        border_color: None, // None means "leave mpv's value alone"
        back_color: None,
    })
    .expect("subtitle style");

    let st = p.get_state().expect("state snapshot");
    assert_eq!(st.volume, 37, "volume after the clamp round-trip");
    assert!(st.mute, "mute is on");
    assert_eq!(st.rate, 1.5, "rate");
    assert!(st.paused, "the file was loaded paused");
    assert!((2.0..4.0).contains(&st.pos), "position ~3s, got {}", st.pos);
    assert!(
        (29.0..31.0).contains(&st.duration),
        "duration ~30s, got {}",
        st.duration
    );
    assert_eq!(
        st.loaded.as_deref(),
        Some(wav.to_str().unwrap()),
        "the snapshot names the open file"
    );
    assert!(
        st.tracks.iter().any(|t| t.kind == "sub"),
        "the added subtitle is a track: {:?}",
        st.tracks
    );
    assert!(
        st.sid.is_some(),
        "`sub-add … select` made the subtitle current: sid={:?}",
        st.sid
    );

    p.shutdown();
    assert!(!ps_has_socket(&sock), "no mpv left running");
    drop(p);
    let _ = std::fs::remove_dir_all(&dir);
}

/// A crash landing in the middle of a seek: the very next command has to
/// notice the dead process, restart it, and *then* still do what it was asked.
/// The old mpv is reaped, not left as a zombie.
#[test]
fn a_crash_during_a_seek_recovers_and_still_lands() {
    if locate_mpv().is_none() {
        eprintln!("SKIP: mpv not installed");
        return;
    }
    let dir = fixture_dir("seekcrash");
    let wav = dir.join("seekcrash.wav");
    write_sine_wav(&wav, 30.0);

    let sock = sock_path("seekcrash");
    let mut p = start_player_at(&sock);
    let mut seen = Vec::new();

    p.load(wav.to_str().unwrap(), 0.0, false).expect("load");
    wait_from(
        &mut p,
        &mut seen,
        0,
        |e| matches!(e, PlayerEvent::Ready),
        Duration::from_secs(15),
    )
    .expect("Ready");
    wait_pos(&mut p, |pos| pos > 0.3, Duration::from_secs(5)).expect("playing");

    let doomed = p.pid().expect("pid");
    kill_hard(doomed);
    // The death has to be observable before the seek, or the test would be
    // racing the kernel's zombie transition rather than the player.
    let deadline = Instant::now() + Duration::from_secs(5);
    while p.is_running() && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(50));
    }
    assert!(!p.is_running(), "kill -9 must be observable");

    // One call: it recovers the crash and applies the seek.
    p.seek(20.0).expect("seek across a restart");
    wait_from(
        &mut p,
        &mut seen,
        0,
        |e| matches!(e, PlayerEvent::ProcessCrashed { restarts: 1 }),
        Duration::from_secs(15),
    )
    .expect("the crash was reported");
    assert_eq!(p.restarts(), 1);
    assert!(!pid_alive(doomed), "the crashed mpv was reaped");
    wait_pos(
        &mut p,
        |pos| (19.0..21.0).contains(&pos),
        Duration::from_secs(10),
    )
    .expect("the seek landed after the restart");

    p.shutdown();
    assert!(!ps_has_socket(&sock), "no mpv left running");
    drop(p);
    let _ = std::fs::remove_dir_all(&dir);
}

/// A *frozen* mpv is not a dead one. SIGSTOP makes it stop answering, so an
/// IPC call has to time out (rather than hang forever, or — worse — be
/// mistaken for a crash and restart a perfectly healthy player); SIGCONT must
/// bring it straight back, with the restart counter untouched.
#[test]
fn a_stopped_mpv_times_out_then_recovers_after_sigcont() {
    if locate_mpv().is_none() {
        eprintln!("SKIP: mpv not installed");
        return;
    }
    let dir = fixture_dir("stopped");
    let wav = dir.join("stopped.wav");
    write_sine_wav(&wav, 30.0);

    let sock = sock_path("stopped");
    let mut p = start_player_at(&sock);
    let mut seen = Vec::new();

    p.load(wav.to_str().unwrap(), 0.0, false).expect("load");
    wait_from(
        &mut p,
        &mut seen,
        0,
        |e| matches!(e, PlayerEvent::Ready),
        Duration::from_secs(15),
    )
    .expect("Ready");
    wait_pos(&mut p, |pos| pos > 0.3, Duration::from_secs(5)).expect("playing");

    let pid = p.pid().expect("pid");
    signal(pid, "STOP");
    let started = Instant::now();
    let err = p.get_pos().expect_err("a frozen mpv cannot answer");
    let waited = started.elapsed();
    assert!(matches!(err, PlayerError::Timeout(_)), "got {err:?}");
    assert!(
        (Duration::from_millis(2500)..Duration::from_secs(6)).contains(&waited),
        "the IPC timeout is ~3s, waited {waited:?}"
    );
    assert!(p.is_running(), "a stopped process must not read as dead");
    assert_eq!(p.restarts(), 0, "STOP must not be mistaken for a crash");

    signal(pid, "CONT");
    wait_pos(&mut p, |pos| pos >= 0.0, Duration::from_secs(10))
        .expect("the player answers again after SIGCONT");
    assert_eq!(p.restarts(), 0, "recovery without a restart");

    p.shutdown();
    assert!(!ps_has_socket(&sock), "no mpv left running");
    drop(p);
    let _ = std::fs::remove_dir_all(&dir);
}

/// The socket-EOF path, against a real mpv: a caller already waiting for a
/// reply must be released the moment mpv dies (`NotRunning`), not left to sit
/// out its own timeout. mpv is frozen first so the request is genuinely in
/// flight when the kill lands.
///
/// This talks to the socket directly — `MpvPlayer` owns its client, and mpv
/// accepts a single connection — so it spawns mpv through `MpvProcess`.
#[test]
fn a_dying_mpv_releases_a_pending_caller_instead_of_timing_out() {
    let Some(exe) = locate_mpv() else {
        eprintln!("SKIP: mpv not installed");
        return;
    };
    let sock = sock_path("pending");
    let mut opts = MpvOptions::new(sock.clone());
    opts.headless = true;
    opts.media = None; // --idle: mpv waits, socket and all
    let mut proc = MpvProcess::spawn(exe, opts).expect("spawn mpv");
    let pid = proc.pid();
    let rpc = RpcClient::connect(&sock, Duration::from_secs(10)).expect("connect");
    assert!(!rpc.is_closed(), "a fresh connection starts open");

    // Freeze mpv so it cannot answer, then put a request in flight.
    signal(pid, "STOP");
    let rpc_in_thread = Arc::clone(&rpc);
    let waiter = std::thread::spawn(move || {
        let started = Instant::now();
        let err = rpc_in_thread
            .request(
                serde_json::json!(["get_property", "time-pos"]),
                Duration::from_secs(10),
            )
            .unwrap_err();
        (err, started.elapsed())
    });
    std::thread::sleep(Duration::from_millis(250)); // let the request go out
    kill_hard(pid); // the socket closes under the waiting caller

    let (err, waited) = waiter.join().expect("the waiter thread finishes");
    assert!(matches!(err, PlayerError::NotRunning(_)), "got {err:?}");
    assert!(
        waited < Duration::from_secs(5),
        "released by EOF, not waited out as a 10s timeout: {waited:?}"
    );
    assert!(rpc.is_closed(), "EOF must mark the client closed");

    proc.kill().expect("reap the dead mpv");
    assert!(!pid_alive(pid), "mpv reaped, not a zombie");
    assert!(!ps_has_socket(&sock), "no mpv left running");
    let _ = std::fs::remove_file(&sock);
}
