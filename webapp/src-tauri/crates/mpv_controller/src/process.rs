//! mpv process lifecycle: find the binary, spawn it with an IPC socket, kill
//! and reap it. Nothing else in the crate touches `std::process`.

use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

use crate::port::{PlayerError, PlayerResult};

/// Everything needed to (re)spawn mpv. Kept so a restart after a crash can
/// rebuild the same process — except that the reload itself goes through
/// `MpvPlayer::load_inner`, not through the positional arg (see below).
#[derive(Debug, Clone)]
pub struct MpvOptions {
    pub ipc_path: PathBuf,
    /// File to open on spawn (positional argument), for direct-start uses
    /// such as tests and the spike. `MpvPlayer::respawn` clears it and
    /// reloads through the port instead, so a crash recovery opens the file
    /// exactly once; here it doubles as the resume record.
    pub media: Option<String>,
    pub start: f64,
    pub paused: bool,
    /// `true` = no window/audio device (`--vo=null --ao=null`): tests, CI,
    /// background pre-buffering. `false` = a real mpv window.
    pub headless: bool,
    pub extra: Vec<String>,
}

impl MpvOptions {
    pub fn new(ipc_path: PathBuf) -> Self {
        Self {
            ipc_path,
            media: None,
            start: 0.0,
            paused: false,
            headless: true,
            extra: Vec::new(),
        }
    }

    fn args(&self) -> Vec<String> {
        let mut args = vec![
            format!("--input-ipc-server={}", self.ipc_path.display()),
            "--idle=yes".into(),
            "--osc=no".into(),
            "--no-terminal".into(),
            // keep-open: reaching the end must not tear the process down, or
            // every playlist advance would look like a crash and restart it.
            "--keep-open=yes".into(),
        ];
        if self.headless {
            args.push("--vo=null".into());
            args.push("--ao=null".into());
        } else {
            args.push("--force-window=yes".into());
        }
        if self.paused {
            args.push("--pause".into());
        }
        if self.start > 0.0 {
            args.push(format!("--start={}", self.start));
        }
        args.extend(self.extra.iter().cloned());
        if let Some(m) = &self.media {
            args.push(m.clone());
        }
        args
    }
}

/// `$STREAM_MPV_BIN`, else `mpv` found on `PATH`.
pub fn locate_mpv() -> Option<PathBuf> {
    if let Ok(p) = std::env::var("STREAM_MPV_BIN") {
        let p = p.trim();
        if !p.is_empty() {
            return Some(PathBuf::from(p));
        }
    }
    let name = "mpv";
    let path = std::env::var_os("PATH")?;
    for dir in std::env::split_paths(&path) {
        let candidate = dir.join(name);
        if is_executable(&candidate) {
            return Some(candidate);
        }
    }
    None
}

#[cfg(unix)]
fn is_executable(p: &Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    p.is_file()
        && std::fs::metadata(p)
            .map(|m| m.permissions().mode() & 0o111 != 0)
            .unwrap_or(false)
}

#[cfg(not(unix))]
fn is_executable(p: &Path) -> bool {
    p.is_file()
}

/// A live mpv child process. Dropping it kills and reaps the child, so no
/// zombie survives a test or an app exit.
pub struct MpvProcess {
    child: Child,
    pub exe: PathBuf,
    pub options: MpvOptions,
}

impl MpvProcess {
    pub fn spawn(exe: PathBuf, options: MpvOptions) -> PlayerResult<Self> {
        // A stale socket from a previous run would make the IPC client talk to
        // a dead mpv, so it goes before anything is spawned.
        let _ = std::fs::remove_file(&options.ipc_path);
        let args = options.args();
        let child = Command::new(&exe)
            .args(&args)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|e| PlayerError::NotRunning(format!("spawn {}: {e}", exe.display())))?;
        Ok(Self { child, exe, options })
    }

    pub fn pid(&self) -> u32 {
        self.child.id()
    }

    /// `Ok(true)` while the child is alive; reaps it once it exited.
    pub fn is_running(&mut self) -> bool {
        matches!(self.child.try_wait(), Ok(None))
    }

    /// SIGKILL + wait: the process is gone and reaped (no zombies), whatever
    /// state it was in.
    pub fn kill(&mut self) -> PlayerResult<()> {
        match self.child.kill() {
            Ok(()) => {}
            Err(e) if e.kind() == std::io::ErrorKind::InvalidInput => {}
            Err(e) => return Err(PlayerError::Protocol(format!("kill mpv: {e}"))),
        }
        self.child
            .wait()
            .map_err(|e| PlayerError::Protocol(format!("reap mpv: {e}")))?;
        let _ = std::fs::remove_file(&self.options.ipc_path);
        Ok(())
    }

    /// Wait up to `timeout` for the process to exit by itself.
    pub fn wait_for_exit(&mut self, timeout: Duration) -> Option<std::process::ExitStatus> {
        let deadline = Instant::now() + timeout;
        loop {
            match self.child.try_wait() {
                Ok(Some(status)) => return Some(status),
                Ok(None) if Instant::now() < deadline => {
                    std::thread::sleep(Duration::from_millis(20))
                }
                Ok(None) => return None,
                Err(_) => return None,
            }
        }
    }
}

impl Drop for MpvProcess {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
        let _ = std::fs::remove_file(&self.options.ipc_path);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn options_build_the_flags_the_spike_proved() {
        let mut o = MpvOptions::new(PathBuf::from("/tmp/x.sock"));
        o.media = Some("file:///m.mkv".into());
        o.start = 12.5;
        o.paused = true;
        let args = o.args();
        let joined = args.join(" ");
        assert!(joined.contains("--input-ipc-server=/tmp/x.sock"));
        for flag in ["--idle=yes", "--osc=no", "--no-terminal", "--keep-open=yes", "--pause"] {
            assert!(args.iter().any(|a| a == flag), "missing {flag} in {joined}");
        }
        assert!(args.iter().any(|a| a == "--vo=null"), "headless must be silent");
        assert!(args.iter().any(|a| a == "--start=12.5"));
        assert_eq!(args.last().map(String::as_str), Some("file:///m.mkv"));

        o.headless = false;
        let args = o.args();
        assert!(args.iter().any(|a| a == "--force-window=yes"));
        assert!(!args.iter().any(|a| a == "--vo=null"));
    }

    #[test]
    fn locate_mpv_finds_something_or_nothing_without_panicking() {
        // Not asserting presence: this test has to pass on machines without
        // mpv. The contract is "returns a path that exists, or None".
        if let Some(p) = locate_mpv() {
            assert!(p.exists(), "locate_mpv returned a missing path: {}", p.display());
        }
    }

    #[cfg(unix)]
    #[test]
    fn drop_kills_and_reaps_the_child() {
        let Some(exe) = locate_mpv() else {
            eprintln!("SKIP: mpv not installed");
            return;
        };
        let sock = std::env::temp_dir().join(format!("mpv-proc-{}.sock", std::process::id()));
        let mut opts = MpvOptions::new(sock);
        opts.media = None; // --idle: mpv sits waiting for a file
        let mut proc = MpvProcess::spawn(exe, opts).expect("spawn mpv");
        let pid = proc.pid();
        std::thread::sleep(Duration::from_millis(300));
        assert!(proc.is_running(), "mpv should be idling");
        drop(proc); // kills + reaps
        // After wait() the pid must be reaped: kill(pid, 0) fails with ESRCH
        // unless another process reused it (vanishingly unlikely this fast).
        let alive = Command::new("sh")
            .args(["-c", &format!("kill -0 {pid} 2>/dev/null")])
            .status()
            .map(|s| s.success())
            .unwrap_or(false);
        assert!(!alive, "mpv pid {pid} survived Drop — zombie or leak");
    }
}
