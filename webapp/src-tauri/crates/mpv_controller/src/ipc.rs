//! JSON IPC client: request/response correlation by `request_id`, plus the
//! asynchronous event stream mpv pushes on the same socket.
//!
//! One reader thread owns the socket's read half and routes every line:
//! replies go to the waiting caller through a per-request channel, events go
//! into a queue drained by [`RpcClient::drain_events`] /
//! [`RpcClient::wait_event`]. Nothing here knows what the commands mean —
//! that belongs to `mpv.rs`.

use std::collections::{HashMap, VecDeque};
use std::io::{BufRead, BufReader, Read, Write};
use std::path::Path;
use std::sync::atomic::{AtomicBool, AtomicI64, Ordering};
use std::sync::mpsc::{sync_channel, SyncSender};
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

use crate::port::PlayerError;

type Json = serde_json::Value;

const EVENT_QUEUE_LIMIT: usize = 512;

/// The write half of the transport, the only platform-specific piece: a unix
/// socket on unix, mpv's named pipe on Windows. Both satisfy the two things
/// the client needs of a writer — `Write` and `try_clone`. The read half only
/// needs `Read`, so [`reader_loop`] is generic over it.
#[cfg(unix)]
type IpcWriter = std::os::unix::net::UnixStream;
#[cfg(windows)]
type IpcWriter = std::fs::File;

/// Present on every platform that has a transport. Elsewhere the stub at the
/// bottom of this file takes its place, so the crate still compiles and
/// reports the gap rather than pretending.
#[cfg(any(unix, windows))]
pub struct RpcClient {
    writer: Mutex<IpcWriter>,
    inner: Arc<Inner>,
}

#[cfg(any(unix, windows))]
struct Inner {
    /// `request_id` → the caller waiting for that reply.
    pending: Mutex<HashMap<i64, SyncSender<Json>>>,
    events: Mutex<VecDeque<Json>>,
    cv: Condvar,
    next_id: AtomicI64,
    closed: AtomicBool,
}

/// Connect to an already-listening IPC socket, waiting up to `timeout` for it
/// to appear (mpv creates it asynchronously after spawn).
#[cfg(unix)]
impl RpcClient {
    pub fn connect(path: &Path, timeout: Duration) -> Result<Arc<Self>, PlayerError> {
        let deadline = Instant::now() + timeout;
        let stream = loop {
            match std::os::unix::net::UnixStream::connect(path) {
                Ok(s) => break s,
                Err(e) if matches!(e.kind(), std::io::ErrorKind::NotFound
                    | std::io::ErrorKind::ConnectionRefused) && Instant::now() < deadline =>
                {
                    std::thread::sleep(Duration::from_millis(25))
                }
                Err(e) => {
                    return Err(PlayerError::Protocol(format!(
                        "cannot connect to {}: {e}",
                        path.display()
                    )))
                }
            }
        };
        Self::from_stream(stream, &path)
    }
}

/// Windows transport: mpv's IPC is a named pipe, opened with
/// `std::fs::OpenOptions` (`read + write`), which is the one way `std` can
/// open `\\.\pipe\…` — no extra crate, no `unsafe`. The returned handle is a
/// plain `File`, so the reader thread and the client are the same code as on
/// unix.
#[cfg(windows)]
impl RpcClient {
    pub fn connect(path: &Path, timeout: Duration) -> Result<Arc<Self>, PlayerError> {
        let deadline = Instant::now() + timeout;
        let file = loop {
            match std::fs::OpenOptions::new().read(true).write(true).open(path) {
                Ok(f) => break f,
                Err(e) if retryable_pipe_error(&e) && Instant::now() < deadline => {
                    std::thread::sleep(Duration::from_millis(25))
                }
                Err(e) => {
                    return Err(PlayerError::Protocol(format!(
                        "cannot connect to {}: {e}",
                        path.display()
                    )))
                }
            }
        };
        Self::from_stream(file, &path)
    }
}

/// mpv creates the pipe asynchronously after spawn, and a busy pipe must be
/// waited out rather than treated as fatal: retry on "not there yet"
/// (`NotFound`) and on `ERROR_PIPE_BUSY` (231).
#[cfg(windows)]
fn retryable_pipe_error(e: &std::io::Error) -> bool {
    matches!(e.kind(), std::io::ErrorKind::NotFound) || e.raw_os_error() == Some(231)
}

#[cfg(any(unix, windows))]
impl RpcClient {
    /// Shared by both transports: clone the read half, start the reader
    /// thread, and keep the write half behind a mutex.
    fn from_stream(stream: IpcWriter, path: &Path) -> Result<Arc<Self>, PlayerError> {
        let inner = Arc::new(Inner {
            pending: Mutex::new(HashMap::new()),
            events: Mutex::new(VecDeque::new()),
            cv: Condvar::new(),
            next_id: AtomicI64::new(1),
            closed: AtomicBool::new(false),
        });

        let reader = stream.try_clone().map_err(|e| {
            PlayerError::Protocol(format!("clone IPC stream for {}: {e}", path.display()))
        })?;
        std::thread::Builder::new()
            .name("mpv-ipc-reader".into())
            .spawn({
                let inner = Arc::clone(&inner);
                move || reader_loop(reader, inner)
            })
            .map_err(|e| PlayerError::Protocol(format!("spawn reader: {e}")))?;

        Ok(Arc::new(Self { writer: Mutex::new(stream), inner }))
    }

    /// Send `command` (already an mpv command array) and wait for its reply.
    pub fn request(&self, command: Json, timeout: Duration) -> Result<Json, PlayerError> {
        if self.inner.closed.load(Ordering::SeqCst) {
            return Err(PlayerError::NotRunning("IPC socket closed".into()));
        }
        let id = self.inner.next_id.fetch_add(1, Ordering::SeqCst);
        let (tx, rx) = sync_channel::<Json>(1);
        self.inner.pending.lock().unwrap().insert(id, tx);

        let mut line = serde_json::json!({ "command": command, "request_id": id }).to_string();
        // mpv's IPC socket is newline-delimited JSON: without this terminator
        // the peer's read_line never completes and every request times out.
        line.push('\n');
        {
            let mut w = self.writer.lock().unwrap();
            if let Err(e) = w.write_all(line.as_bytes()).and_then(|_| w.flush()) {
                self.inner.pending.lock().unwrap().remove(&id);
                return Err(PlayerError::Protocol(format!("write failed: {e}")));
            }
        }

        match rx.recv_timeout(timeout) {
            Ok(reply) => {
                let err = reply.get("error").and_then(|e| e.as_str()).unwrap_or("");
                if err.is_empty() || err == "success" {
                    Ok(reply)
                } else {
                    Err(PlayerError::Rejected {
                        command: command.to_string(),
                        detail: err.to_string(),
                    })
                }
            }
            Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {
                self.inner.pending.lock().unwrap().remove(&id);
                Err(PlayerError::Timeout(format!(
                    "no reply to {} within {:?}",
                    command, timeout
                )))
            }
            Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => {
                Err(PlayerError::NotRunning("player went away".into()))
            }
        }
    }

    /// Wait up to `timeout` for the next event; `None` on timeout.
    pub fn wait_event(&self, timeout: Duration) -> Option<Json> {
        let mut events = self.inner.events.lock().unwrap();
        if let Some(ev) = events.pop_front() {
            return Some(ev);
        }
        let (mut guard, _res) = self
            .inner
            .cv
            .wait_timeout(events, timeout)
            .unwrap_or_else(|e| e.into_inner());
        guard.pop_front()
    }

    /// Everything queued right now (non-blocking).
    pub fn drain_events(&self) -> Vec<Json> {
        self.inner
            .events
            .lock()
            .unwrap()
            .drain(..)
            .collect()
    }

    /// True once the reader thread saw EOF or a socket error.
    pub fn is_closed(&self) -> bool {
        self.inner.closed.load(Ordering::SeqCst)
    }

    /// Convenience: `["set_property", name, value]`.
    pub fn set_property(&self, name: &str, value: Json, timeout: Duration) -> Result<(), PlayerError> {
        self.request(serde_json::json!(["set_property", name, value]), timeout)
            .map(|_| ())
    }

    /// Convenience: `["get_property", name]` → the `data` field.
    pub fn get_property(&self, name: &str, timeout: Duration) -> Result<Json, PlayerError> {
        let reply = self.request(serde_json::json!(["get_property", name]), timeout)?;
        Ok(reply.get("data").cloned().unwrap_or(Json::Null))
    }
}

/// Routes every line mpv sends: replies to their waiting caller, everything
/// with an `event` field to the queue. Ends only on EOF/error, then releases
/// every pending caller (they see `NotRunning`) and wakes event waiters.
///
/// Generic over the read half so unix sockets and Windows pipes share it.
#[cfg(any(unix, windows))]
fn reader_loop(stream: impl Read, inner: Arc<Inner>) {
    let mut reader = BufReader::new(stream);
    let mut line = String::new();
    loop {
        line.clear();
        match reader.read_line(&mut line) {
            Ok(0) | Err(_) => break,
            Ok(_) => {}
        }
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        let Ok(msg) = serde_json::from_str::<Json>(trimmed) else {
            continue; // malformed line: mpv does not produce these, ignore
        };
        if let Some(id) = msg.get("request_id").and_then(|v| v.as_i64()) {
            if let Some(tx) = inner.pending.lock().unwrap().remove(&id) {
                let _ = tx.send(msg);
            }
            continue; // a reply is not an event
        }
        if msg.get("event").is_some() {
            let mut q = inner.events.lock().unwrap();
            if q.len() >= EVENT_QUEUE_LIMIT {
                q.pop_front(); // drop oldest: the UI only acts on the latest
            }
            q.push_back(msg);
            inner.cv.notify_all();
        }
    }
    inner.closed.store(true, Ordering::SeqCst);
    inner.pending.lock().unwrap().clear(); // senders drop → callers unblock
    inner.cv.notify_all();
}

/// Neither unix nor Windows: no IPC transport exists, so the surface is a stub
/// that reports the gap instead of pretending. (On Windows the real named-pipe
/// transport above is used.)
#[cfg(not(any(unix, windows)))]
pub struct RpcClient;

#[cfg(not(any(unix, windows)))]
impl RpcClient {
    fn unsupported() -> PlayerError {
        PlayerError::Unsupported("mpv IPC is not supported on this platform".into())
    }
    pub fn connect(_p: &Path, _t: Duration) -> Result<Arc<Self>, PlayerError> {
        Err(Self::unsupported())
    }
    pub fn request(&self, _c: Json, _t: Duration) -> Result<Json, PlayerError> {
        Err(Self::unsupported())
    }
    pub fn wait_event(&self, _t: Duration) -> Option<Json> { None }
    pub fn drain_events(&self) -> Vec<Json> { Vec::new() }
    pub fn is_closed(&self) -> bool { true }
    pub fn set_property(&self, _n: &str, _v: Json, _t: Duration) -> Result<(), PlayerError> {
        Err(Self::unsupported())
    }
    pub fn get_property(&self, _n: &str, _t: Duration) -> Result<Json, PlayerError> {
        Err(Self::unsupported())
    }
}

#[cfg(all(unix, test))]
mod tests {
    use super::*;
    use std::os::unix::net::UnixListener;
    use std::sync::atomic::AtomicU32;

    static N: AtomicU32 = AtomicU32::new(0);

    fn sock_path(tag: &str) -> std::path::PathBuf {
        std::env::temp_dir().join(format!(
            "mpv-ipc-{}-{}-{}.sock",
            std::process::id(),
            N.fetch_add(1, Ordering::SeqCst),
            tag
        ))
    }

    /// Runs `server` on its own thread against a fresh socket; returns the
    /// path. The handler is called once per incoming message, so replies go
    /// out while the client is still waiting (a read-to-EOF server would
    /// deadlock every test).
    fn scripted_server(
        tag: &str,
        server: impl Fn(Json, &mut std::os::unix::net::UnixStream) + Send + 'static,
    ) -> std::path::PathBuf {
        let path = sock_path(tag);
        let _ = std::fs::remove_file(&path);
        let listener = UnixListener::bind(&path).expect("bind test socket");
        std::thread::spawn(move || {
            if let Ok((mut writer, _)) = listener.accept() {
                let mut reader = BufReader::new(writer.try_clone().expect("clone"));
                let mut line = String::new();
                loop {
                    line.clear();
                    match reader.read_line(&mut line) {
                        Ok(0) | Err(_) => break,
                        Ok(_) => {}
                    }
                    let Ok(v) = serde_json::from_str::<Json>(line.trim()) else {
                        continue;
                    };
                    server(v, &mut writer);
                }
            }
        });
        path
    }

    #[test]
    fn replies_are_matched_to_their_request_id_even_when_out_of_order() {
        let path = scripted_server("corr", |cmd, out| {
            let id = cmd["request_id"].as_i64().unwrap();
            let is_slow = cmd["command"].get(1).and_then(|v| v.as_str()) == Some("slow");
            if is_slow {
                std::thread::sleep(Duration::from_millis(300));
            }
            let reply = serde_json::json!({"error":"success","request_id":id,
                "data": format!("value-{id}")});
            let _ = writeln!(out, "{reply}");
            let _ = out.flush();
        });
        let rpc = RpcClient::connect(&path, Duration::from_secs(5)).unwrap();

        let slow_rpc = Arc::clone(&rpc);
        let slow = std::thread::spawn(move || {
            slow_rpc
                .request(serde_json::json!(["get_property", "slow"]), Duration::from_secs(5))
                .unwrap()
        });
        std::thread::sleep(Duration::from_millis(50));
        let fast = rpc
            .request(serde_json::json!(["get_property", "fast"]), Duration::from_secs(5))
            .unwrap();
        // The replies arrived in the *opposite* order (the fast request was
        // answered first) — each caller must still have received the body of
        // its own request. A FIFO reader that ignored ids would hand the fast
        // reply to the slow caller and fail both checks.
        let slow = slow.join().unwrap();
        let slow_id = slow["request_id"].as_i64().unwrap();
        let fast_id = fast["request_id"].as_i64().unwrap();
        assert_ne!(slow_id, fast_id, "distinct requests must get distinct ids");
        assert_eq!(fast["data"], format!("value-{fast_id}"));
        assert_eq!(slow["data"], format!("value-{slow_id}"));
    }

    #[test]
    fn a_reply_that_never_comes_is_a_timeout_not_a_hang() {
        let path = scripted_server("timeout", |_cmd, _out| {
            std::thread::sleep(Duration::from_secs(10));
        });
        let rpc = RpcClient::connect(&path, Duration::from_secs(5)).unwrap();
        let started = Instant::now();
        let err = rpc
            .request(serde_json::json!(["get_property", "time-pos"]), Duration::from_millis(200))
            .unwrap_err();
        assert!(matches!(err, PlayerError::Timeout(_)), "got {err:?}");
        assert!(started.elapsed() < Duration::from_secs(2));
    }

    /// mpv's stream is newline-delimited JSON, and anything unparseable must be
    /// skipped without disturbing the requests and events around it.
    #[test]
    fn malformed_lines_are_skipped_and_events_are_routed_to_the_queue() {
        let path = scripted_server("routing", |cmd, out| {
            let id = cmd["request_id"].as_i64().unwrap();
            let _ = writeln!(out, "this is not json");
            let _ = writeln!(out, "{{ truncated");
            let _ = writeln!(out, "{}", serde_json::json!({"event": "file-loaded"}));
            let _ = writeln!(out, "{}", serde_json::json!({"event": "end-file", "reason": "eof"}));
            let _ = writeln!(out, "{}", serde_json::json!({"error":"success","request_id":id,"data":7}));
            let _ = out.flush();
        });
        let rpc = RpcClient::connect(&path, Duration::from_secs(5)).unwrap();

        let reply = rpc
            .request(serde_json::json!(["get_property", "x"]), Duration::from_secs(5))
            .expect("the reply after the garbage");
        assert_eq!(reply["data"], 7);

        // Both events reached the queue, in order, and nothing else did.
        let first = rpc.wait_event(Duration::from_millis(500)).expect("first event");
        assert_eq!(first["event"], "file-loaded");
        let second = rpc.wait_event(Duration::from_millis(500)).expect("second event");
        assert_eq!(second["event"], "end-file");
        assert!(rpc.wait_event(Duration::from_millis(50)).is_none(), "no phantom events");
        assert!(!rpc.is_closed());
    }

    /// The socket dropping is the interesting failure: a caller already waiting
    /// must be released (as `NotRunning`), not left until its timeout, and the
    /// client must report itself closed so `ensure_alive` can restart mpv.
    #[test]
    fn a_dropped_connection_releases_waiters_and_marks_the_client_closed() {
        let path = sock_path("drop");
        let _ = std::fs::remove_file(&path);
        let listener = UnixListener::bind(&path).expect("bind test socket");
        let server = std::thread::spawn(move || {
            if let Ok((stream, _)) = listener.accept() {
                // Read the request, then close without answering.
                let mut reader = BufReader::new(stream);
                let mut line = String::new();
                let _ = reader.read_line(&mut line);
                assert!(line.contains("request_id"), "server never saw the request: {line:?}");
                drop(reader); // EOF for the client's reader thread
            }
        });

        let rpc = RpcClient::connect(&path, Duration::from_secs(5)).unwrap();
        let started = Instant::now();
        let err = rpc
            .request(serde_json::json!(["get_property", "time-pos"]), Duration::from_secs(5))
            .unwrap_err();
        assert!(matches!(err, PlayerError::NotRunning(_)), "got {err:?}");
        assert!(
            started.elapsed() < Duration::from_secs(2),
            "a dropped socket must not be waited out as a timeout"
        );

        let deadline = Instant::now() + Duration::from_secs(2);
        while !rpc.is_closed() && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(10));
        }
        assert!(rpc.is_closed(), "EOF must mark the client closed");

        // A closed client refuses further work instead of writing into a dead
        // socket.
        let err = rpc
            .request(serde_json::json!(["get_property", "time-pos"]), Duration::from_millis(100))
            .unwrap_err();
        assert!(matches!(err, PlayerError::NotRunning(_)), "got {err:?}");
        server.join().unwrap();
        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn an_error_reply_becomes_rejected_with_the_command_in_it() {
        let path = scripted_server("rejected", |cmd, out| {
            let id = cmd["request_id"].as_i64().unwrap();
            let reply = serde_json::json!({"error":"property not found","request_id":id});
            let _ = writeln!(out, "{reply}");
            let _ = out.flush();
        });
        let rpc = RpcClient::connect(&path, Duration::from_secs(5)).unwrap();
        let err = rpc
            .request(serde_json::json!(["set_property", "nope", 1]), Duration::from_secs(5))
            .unwrap_err();
        match err {
            PlayerError::Rejected { command, detail } => {
                assert!(command.contains("set_property"), "{command}");
                assert_eq!(detail, "property not found");
            }
            other => panic!("expected Rejected, got {other:?}"),
        }
    }

    #[test]
    fn requests_are_numbered_from_one_and_increase() {
        let path = scripted_server("ids", |cmd, out| {
            let id = cmd["request_id"].as_i64().unwrap();
            let _ = writeln!(out, "{}", serde_json::json!({"error":"success","request_id":id}));
            let _ = out.flush();
        });
        let rpc = RpcClient::connect(&path, Duration::from_secs(5)).unwrap();
        let a = rpc.request(serde_json::json!(["x"]), Duration::from_secs(5)).unwrap();
        let b = rpc.request(serde_json::json!(["y"]), Duration::from_secs(5)).unwrap();
        let (a, b) = (a["request_id"].as_i64().unwrap(), b["request_id"].as_i64().unwrap());
        assert_eq!(a, 1, "ids start at 1");
        assert_eq!(b, a + 1, "and increase by one");
    }

    /// Connecting before mpv has created the socket: the client waits for it,
    /// and reports a protocol error rather than hanging once the wait is spent.
    #[test]
    fn connect_waits_for_the_socket_then_fails_as_a_protocol_error() {
        let path = sock_path("missing");
        let _ = std::fs::remove_file(&path);
        let started = Instant::now();
        let err = match RpcClient::connect(&path, Duration::from_millis(300)) {
            Ok(_) => panic!("connect must fail when the socket never appears"),
            Err(e) => e,
        };
        assert!(matches!(err, PlayerError::Protocol(_)), "got {err:?}");
        assert!(started.elapsed() < Duration::from_secs(3), "must give up quickly");
    }
}
