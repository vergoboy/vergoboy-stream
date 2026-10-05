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
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use mpv_controller::{
    locate_mpv, CacheProfile, MpvOptions, MpvPlayer, PlayerEvent, PlayerPort,
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

/// SIGKILL via the shell: `std` can only signal children, and this pid was
/// deliberately made someone else's problem by the test.
fn kill_hard(pid: u32) {
    let st = std::process::Command::new("sh")
        .args(["-c", &format!("kill -9 {pid}")])
        .status()
        .expect("run kill");
    assert!(st.success(), "kill -9 {pid} failed");
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

    let mut p = start_player("controls");
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

    // Shutdown: no zombie left behind.
    let pid = p.pid().expect("pid before shutdown");
    p.shutdown();
    assert!(!p.is_running(), "shutdown must stop the player");
    assert!(!pid_alive(pid), "mpv pid {pid} must be reaped, not a zombie");

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

    let mut p = start_player("crash");
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

    // Crash 1: the very next player call must recover on its own.
    let previous = p.pid().expect("pid");
    kill_hard(previous);
    let idx = wait_from(
        &mut p,
        &mut seen,
        0,
        |e| matches!(e, PlayerEvent::Error(m) if m.contains("attempt 1")),
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

    // Crashes 2 and 3 still recover.
    for attempt in 2..=3u32 {
        let before = p.pid().expect("pid");
        kill_hard(before);
        wait_from(
            &mut p,
            &mut seen,
            0,
            |e| matches!(e, PlayerEvent::Error(m) if m.contains(&format!("attempt {attempt}"))),
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
