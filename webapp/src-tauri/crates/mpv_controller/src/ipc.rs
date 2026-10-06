//! JSON IPC client: request/response correlation by `request_id`, plus the
//! asynchronous event stream mpv pushes on the same socket.
//!
//! One reader thread owns the socket's read half and routes every line:
//! replies go to the waiting caller through a per-request channel, events go
//! into a queue drained by [`RpcClient::drain_events`] /
//! [`RpcClient::wait_event`]. Nothing here knows what the commands mean —
//! that belongs to `mpv.rs`.

use std::collections::{HashMap, VecDeque};
use std::io::{BufRead, BufReader, Write};
use std::path::Path;
use std::sync::atomic::{AtomicBool, AtomicI64, Ordering};
use std::sync::mpsc::{sync_channel, SyncSender};
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

use crate::port::PlayerError;

type Json = serde_json::Value;

const EVENT_QUEUE_LIMIT: usize = 512;

#[cfg(unix)]
pub struct RpcClient {
    writer: Mutex<std::os::unix::net::UnixStream>,
    inner: Arc<Inner>,
}

struct Inner {
    /// `request_id` → the caller waiting for that reply.
    pending: Mutex<HashMap<i64, SyncSender<Json>>>,
    events: Mutex<VecDeque<Json>>,
    cv: Condvar,
    next_id: AtomicI64,
    closed: AtomicBool,
}

#[cfg(unix)]
impl RpcClient {
    /// Connect to an already-listening IPC socket, waiting up to `timeout` for
    /// it to appear (mpv creates it asynchronously after spawn).
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

        let inner = Arc::new(Inner {
            pending: Mutex::new(HashMap::new()),
            events: Mutex::new(VecDeque::new()),
            cv: Condvar::new(),
            next_id: AtomicI64::new(1),
            closed: AtomicBool::new(false),
        });

        let reader = stream.try_clone().map_err(|e| {
            PlayerError::Protocol(format!("clone IPC stream: {e}"))
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
#[cfg(unix)]
fn reader_loop(stream: std::os::unix::net::UnixStream, inner: Arc<Inner>) {
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

/// Windows transport is not implemented yet: mpv's IPC there is a named pipe
/// (`\\.\pipe\mpv-...`), which std cannot open. The surface exists so the
/// rest of the crate compiles and reports the gap instead of pretending.
#[cfg(not(unix))]
pub struct RpcClient;

#[cfg(not(unix))]
impl RpcClient {
    fn unsupported() -> PlayerError {
        PlayerError::Unsupported("mpv IPC named-pipe transport is not implemented".into())
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
}
