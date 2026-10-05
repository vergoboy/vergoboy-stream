//! `MpvPlayer` — a [`PlayerPort`] over a spawned mpv process.
//!
//! Everything mpv-specific lives here: which properties to observe, how a
//! `property-change` becomes a [`PlayerEvent`], how a dead process is noticed
//! and restarted. Callers only ever see the trait.
//!
//! Three behaviours were *measured* against mpv v0.41.0 rather than assumed
//! (see `scripts/mpv_spike.py` and the phase 1B notes in `docs/DEPENDENCIES.md`):
//!
//! * `observe_property` immediately emits the property's current value, so the
//!   first `pause` / `paused-for-cache` / `eof-reached` change is discarded
//!   (`initial_props`). Without this the port would start with a phantom
//!   `Buffering(false)` and a phantom `UserPause`.
//! * the `seek` event carries **no** `reason` field — a command seek and a
//!   keypress seek both arrive as plain `{"event":"seek"}` — so our own seeks
//!   are tracked with a counter (`own_seeks`) and only unmatched seeks become
//!   `UserSeek`.
//! * the `pause` echo to `set_property` races its reply (it can arrive before
//!   *or* after), so echoes are recognised by comparing against the last value
//!   we wrote (`own_pause`), never by timing.

use std::collections::{HashSet, VecDeque};
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use crate::ipc::RpcClient;
use crate::port::{CacheProfile, PlayerError, PlayerEvent, PlayerPort, PlayerResult};
use crate::process::{locate_mpv, MpvOptions, MpvProcess};

const REQUEST_TIMEOUT: Duration = Duration::from_secs(3);
const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
/// A process that dies this many times in a row is not a flaky driver —
/// stop, report, let the UI show something useful.
const DEFAULT_MAX_RESTARTS: u32 = 3;

pub struct MpvPlayer {
    exe: PathBuf,
    opts: MpvOptions,
    process: Option<MpvProcess>,
    rpc: Option<Arc<RpcClient>>,
    profile: CacheProfile,
    /// Events produced but not yet handed out by `poll_events`.
    pending: VecDeque<PlayerEvent>,
    /// Last known position: `UserSeek` needs a number and mpv's `seek` event
    /// does not carry one. Kept fresh by load/seek/`get_pos` (the UI polls
    /// `get_pos`); `time-pos` is deliberately not observed — see `map_event`.
    last_pos: f64,
    restarts: u32,
    max_restarts: u32,
    /// The `pause` value we last wrote. A property-change matching it is the
    /// echo of our own `set_paused`, not a `UserPause`. Updated on every
    /// observed change, so a user toggling away from and back to our value is
    /// still seen.
    own_pause: Option<bool>,
    /// Command seeks sent but not yet matched by a `seek` event. mpv gives no
    /// way to tell the two apart on the event itself.
    own_seeks: u32,
    /// Observed properties whose initial (on-subscription) emission has not
    /// been discarded yet. Refilled on every respawn.
    initial_props: HashSet<&'static str>,
    /// `Ended` was already reported for this run of the file: mpv can signal
    /// EOF twice (`eof-reached` and `end-file/eof`) and a UI must not advance
    /// the playlist twice. Reset by load/seek.
    ended: bool,
    /// The fatal "giving up" error was already queued; do not spam it.
    fatal_reported: bool,
}

impl MpvPlayer {
    /// Locate mpv, spawn it, connect the IPC socket. The process is alive and
    /// controllable by the time this returns — idle, with no file loaded;
    /// [`PlayerPort::load`] is what opens media.
    pub fn start(opts: MpvOptions) -> PlayerResult<Self> {
        let exe = locate_mpv().ok_or_else(|| {
            PlayerError::NotRunning("mpv not found (set STREAM_MPV_BIN or install mpv)".into())
        })?;
        let mut player = Self {
            exe,
            opts,
            process: None,
            rpc: None,
            profile: CacheProfile::Normal,
            pending: VecDeque::new(),
            last_pos: 0.0,
            restarts: 0,
            max_restarts: DEFAULT_MAX_RESTARTS,
            own_pause: None,
            own_seeks: 0,
            initial_props: HashSet::new(),
            ended: false,
            fatal_reported: false,
        };
        player.respawn()?;
        Ok(player)
    }

    /// Spawn a fresh mpv, connect, re-observe the properties the port needs
    /// and re-apply the cache profile. Used at start-up and after a crash.
    fn respawn(&mut self) -> PlayerResult<()> {
        if let Some(mut old) = self.process.take() {
            let _ = old.kill();
        }
        self.rpc = None;
        // Always spawn *idle*: `load_inner` is the single place that opens
        // files, so neither a positional load nor a CLI `--start` seek can race
        // it. (`--start` would matter because its seek also arrives as a
        // bare `seek` event with no `reason` field.)
        let mut spawn_opts = self.opts.clone();
        spawn_opts.media = None;
        spawn_opts.start = 0.0;
        let process = MpvProcess::spawn(self.exe.clone(), spawn_opts)?;
        let rpc = RpcClient::connect(&self.opts.ipc_path, CONNECT_TIMEOUT)?;
        for (id, name) in [(1i64, "pause"), (2, "paused-for-cache"), (3, "eof-reached")] {
            rpc.request(
                serde_json::json!(["observe_property", id, name]),
                REQUEST_TIMEOUT,
            )?;
        }
        let profile = self.profile;
        self.apply_profile(&rpc, profile)?;
        // Fresh connection: any seek/pause in flight died with the old one,
        // and the first emission per property is the current value, not news.
        self.own_pause = Some(self.opts.paused);
        self.own_seeks = 0;
        self.ended = false;
        self.initial_props = ["pause", "paused-for-cache", "eof-reached"].into_iter().collect();
        self.process = Some(process);
        self.rpc = Some(rpc);
        Ok(())
    }

    fn apply_profile(&self, rpc: &Arc<RpcClient>, profile: CacheProfile) -> PlayerResult<()> {
        for (name, value) in profile.properties() {
            rpc.set_property(name, value, REQUEST_TIMEOUT)?;
        }
        Ok(())
    }

    /// The live IPC connection, or [`PlayerError::NotRunning`].
    fn rpc(&self) -> PlayerResult<Arc<RpcClient>> {
        self.rpc
            .clone()
            .ok_or_else(|| PlayerError::NotRunning("no IPC connection".into()))
    }

    /// `Ok(())` when both the child and the socket are usable; restarts the
    /// player otherwise (bounded), so a crash mid-play recovers on the next
    /// call instead of wedging the UI. With `reload`, the file that was
    /// playing is opened again and resumed at the last known position.
    fn ensure_alive(&mut self, reload: bool) -> PlayerResult<()> {
        let proc_alive = self.process.as_mut().map(|p| p.is_running()).unwrap_or(false);
        let rpc_alive = self.rpc.as_ref().map(|r| !r.is_closed()).unwrap_or(false);
        if proc_alive && rpc_alive {
            return Ok(());
        }
        if self.restarts >= self.max_restarts {
            // We've given up driving it — but not before making sure nothing
            // is left behind. The IPC socket usually dies before the process
            // does, so at this point the child may still be exiting (or
            // already a zombie nobody has reaped). SIGKILL + wait handles
            // every state, and the UI only ever sees the error.
            if let Some(mut p) = self.process.take() {
                let _ = p.kill();
            }
            self.rpc = None;
            return Err(PlayerError::NotRunning(format!(
                "mpv died {} times; giving up",
                self.restarts
            )));
        }
        self.restarts += 1;
        self.pending.push_back(PlayerEvent::Error(format!(
            "mpv exited; restarting (attempt {})",
            self.restarts
        )));
        // Best effort: what the user was watching is where they want to come
        // back. `last_pos` (load/seek/get_pos) is our best known position.
        let resume_at = self.last_pos;
        let media = self.opts.media.clone();
        let paused = self.opts.paused;
        self.respawn()?;
        if reload {
            if let Some(url) = media {
                self.load_inner(&url, resume_at, paused)?;
            }
        }
        Ok(())
    }

    fn load_inner(&mut self, url: &str, start: f64, paused: bool) -> PlayerResult<()> {
        let rpc = self.rpc()?;
        rpc.request(
            serde_json::json!(["loadfile", url, "replace"]),
            REQUEST_TIMEOUT,
        )?;
        // Wait for the file to actually be loaded, otherwise a `seek` below
        // would land on the previous file. Other events keep flowing into the
        // pending queue so nothing is lost.
        if !self.await_raw_event("file-loaded", Duration::from_secs(15)) {
            return Err(PlayerError::Timeout(format!("mpv never loaded {url}")));
        }
        self.ended = false;
        self.pending.push_back(PlayerEvent::Ready);
        if start > 0.0 {
            self.own_seeks += 1;
            if let Err(e) = rpc.request(
                serde_json::json!(["seek", start, "absolute+exact"]),
                REQUEST_TIMEOUT,
            ) {
                self.own_seeks -= 1; // no `seek` event will arrive
                return Err(e);
            }
        }
        self.own_pause = Some(paused);
        rpc.set_property("pause", serde_json::json!(paused), REQUEST_TIMEOUT)?;
        self.last_pos = start;
        Ok(())
    }

    /// Pump raw mpv messages until `event` shows up (or timeout), mapping
    /// everything else into `pending` so the caller never misses an event.
    fn await_raw_event(&mut self, name: &str, timeout: Duration) -> bool {
        let Some(rpc) = self.rpc.clone() else { return false };
        let deadline = std::time::Instant::now() + timeout;
        while let Some(raw) = rpc.wait_event(deadline.saturating_duration_since(std::time::Instant::now())) {
            if raw.get("event").and_then(|e| e.as_str()) == Some(name) {
                return true;
            }
            if let Some(ev) = self.map_event(raw) {
                self.pending.push_back(ev);
            }
        }
        false
    }

    /// mpv message → port event. `None` means "not interesting to the UI".
    fn map_event(&mut self, raw: serde_json::Value) -> Option<PlayerEvent> {
        match raw.get("event").and_then(|e| e.as_str())? {
            "file-loaded" => Some(PlayerEvent::Ready),
            "end-file" => {
                let reason = raw.get("reason").and_then(|r| r.as_str()).unwrap_or("");
                match reason {
                    "eof" => self.take_ended(),
                    "error" => Some(PlayerEvent::Error(format!(
                        "playback failed: {}",
                        raw.get("file_error").and_then(|e| e.as_str()).unwrap_or("unknown")
                    ))),
                    // stop/redirect/quit: our own doing, not a failure.
                    _ => None,
                }
            }
            "seek" => {
                // No `reason` field exists (mpv v0.41) — ours by counter,
                // the user's if unmatched.
                if self.own_seeks > 0 {
                    self.own_seeks -= 1;
                    return None;
                }
                Some(PlayerEvent::UserSeek { pos: self.time_pos_now() })
            }
            "property-change" => {
                let name = raw.get("name")?.as_str()?;
                if self.initial_props.remove(name) {
                    return None; // the on-subscription emission, not a change
                }
                let data = raw.get("data").cloned().unwrap_or(serde_json::Value::Null);
                match name {
                    "paused-for-cache" => data.as_bool().map(PlayerEvent::Buffering),
                    "eof-reached" => {
                        if data.as_bool() == Some(true) {
                            self.take_ended()
                        } else {
                            None
                        }
                    }
                    "pause" => {
                        let paused = data.as_bool()?;
                        if self.own_pause == Some(paused) {
                            None // echo of our own `set_paused`
                        } else {
                            self.own_pause = Some(paused);
                            Some(PlayerEvent::UserPause { paused })
                        }
                    }
                    // `time-pos` is deliberately not observed: at frame rate it
                    // would drown the event queue. Positions come from
                    // `get_pos` and the synchronous fetch in `UserSeek`.
                    _ => None,
                }
            }
            _ => None,
        }
    }

    /// First EOF wins; mpv may signal it twice for one file.
    fn take_ended(&mut self) -> Option<PlayerEvent> {
        if self.ended {
            None
        } else {
            self.ended = true;
            Some(PlayerEvent::Ended)
        }
    }

    /// Current position, fetched synchronously. Falls back to `last_pos` when
    /// mpv cannot answer (idle, right between files).
    fn time_pos_now(&mut self) -> f64 {
        if let Some(rpc) = self.rpc.clone() {
            if let Ok(v) = rpc.get_property("time-pos", REQUEST_TIMEOUT) {
                if let Some(p) = v.as_f64() {
                    self.last_pos = p;
                }
            }
        }
        self.last_pos
    }

    pub fn restarts(&self) -> u32 {
        self.restarts
    }

    pub fn is_running(&mut self) -> bool {
        let proc_alive = self.process.as_mut().map(|p| p.is_running()).unwrap_or(false);
        let rpc_alive = self.rpc.as_ref().map(|r| !r.is_closed()).unwrap_or(false);
        proc_alive && rpc_alive
    }

    /// The live mpv child's pid, if any. Tests use it to prove crashes are
    /// noticed and the old process is reaped; useful in diagnostics elsewhere.
    pub fn pid(&self) -> Option<u32> {
        self.process.as_ref().map(|p| p.pid())
    }

    /// Kill mpv now (no restart, no zombies).
    pub fn shutdown(&mut self) {
        self.max_restarts = 0;
        if let Some(mut p) = self.process.take() {
            let _ = p.kill();
        }
        self.rpc = None;
    }
}

impl PlayerPort for MpvPlayer {
    fn load(&mut self, url: &str, start: f64, paused: bool) -> PlayerResult<()> {
        // Stale events from the previous file would otherwise be delivered as
        // if they belonged to this one. If the player is dead, `ensure_alive`
        // re-opens the *old* file's record below this clear — hence the order:
        // clear first, then report a restart if there was one, then load.
        self.pending.clear();
        self.ensure_alive(false)?;
        self.opts.media = Some(url.to_string());
        self.opts.start = start;
        self.opts.paused = paused;
        self.load_inner(url, start, paused)
    }

    fn seek(&mut self, pos: f64) -> PlayerResult<()> {
        self.ensure_alive(true)?;
        let rpc = self.rpc()?;
        self.own_seeks += 1;
        if let Err(e) = rpc.request(
            serde_json::json!(["seek", pos, "absolute+exact"]),
            REQUEST_TIMEOUT,
        ) {
            self.own_seeks -= 1; // rejected: no `seek` event will arrive
            return Err(e);
        }
        self.last_pos = pos;
        self.ended = false; // away from EOF: it can end again
        Ok(())
    }

    fn set_paused(&mut self, paused: bool) -> PlayerResult<()> {
        self.ensure_alive(true)?;
        let rpc = self.rpc()?;
        let previous = self.own_pause.replace(paused);
        if let Err(e) = rpc.set_property("pause", serde_json::json!(paused), REQUEST_TIMEOUT) {
            self.own_pause = previous;
            return Err(e);
        }
        self.opts.paused = paused; // a restart must come back as we left it
        Ok(())
    }

    fn set_rate(&mut self, rate: f64) -> PlayerResult<()> {
        self.ensure_alive(true)?;
        self.rpc()?
            .set_property("speed", serde_json::json!(rate), REQUEST_TIMEOUT)
    }

    fn set_aid(&mut self, id: Option<i64>) -> PlayerResult<()> {
        self.ensure_alive(true)?;
        // None = automatic: mpv's `auto` keeps its own default choice.
        let value = match id {
            Some(n) => serde_json::json!(n),
            None => serde_json::json!("auto"),
        };
        self.rpc()?.set_property("aid", value, REQUEST_TIMEOUT)
    }

    fn set_sid(&mut self, id: Option<i64>) -> PlayerResult<()> {
        self.ensure_alive(true)?;
        // None = disabled: mpv spells "no track" `no`.
        let value = match id {
            Some(n) => serde_json::json!(n),
            None => serde_json::json!("no"),
        };
        self.rpc()?.set_property("sid", value, REQUEST_TIMEOUT)
    }

    fn audio_add(&mut self, url: &str, title: &str) -> PlayerResult<()> {
        self.ensure_alive(true)?;
        self.rpc()?
            .request(
                serde_json::json!(["audio-add", url, "auto", title]),
                REQUEST_TIMEOUT,
            )
            .map(|_| ())
    }

    fn set_cache_profile(&mut self, profile: CacheProfile) -> PlayerResult<()> {
        self.ensure_alive(true)?;
        self.profile = profile; // a respawn must re-apply the new one
        let rpc = self.rpc()?;
        self.apply_profile(&rpc, profile)
    }

    fn get_pos(&mut self) -> PlayerResult<f64> {
        self.ensure_alive(true)?;
        let rpc = self.rpc()?;
        let v = rpc.get_property("time-pos", REQUEST_TIMEOUT)?;
        match v.as_f64() {
            Some(p) => {
                self.last_pos = p;
                Ok(p)
            }
            None => Err(PlayerError::Protocol("mpv reported no time-pos".into())),
        }
    }

    fn poll_events(&mut self, timeout: Duration) -> Vec<PlayerEvent> {
        if let Err(e) = self.ensure_alive(true) {
            // One clear line when the restart budget is spent, never a stream
            // of them: transient failures below the budget already surfaced as
            // the "restarting" event `ensure_alive` queued before failing.
            if self.restarts >= self.max_restarts && !self.fatal_reported {
                self.fatal_reported = true;
                self.pending.push_back(PlayerEvent::Error(e.to_string()));
            }
            return self.pending.drain(..).collect();
        }
        if self.pending.is_empty() {
            // Wait for the first *port* event; mpv noise (unobserved
            // properties, stray events) is mapped to None and skipped while
            // there is time left on the clock.
            let deadline = std::time::Instant::now() + timeout;
            while self.pending.is_empty() {
                let now = std::time::Instant::now();
                if now >= deadline {
                    break;
                }
                let Some(rpc) = self.rpc.clone() else { break };
                let Some(raw) = rpc.wait_event(deadline - now) else { break };
                if let Some(ev) = self.map_event(raw) {
                    self.pending.push_back(ev);
                }
            }
            // Anything that queued up behind the first one, without blocking.
            if let Some(rpc) = self.rpc.clone() {
                for raw in rpc.drain_events() {
                    if let Some(ev) = self.map_event(raw) {
                        self.pending.push_back(ev);
                    }
                }
            }
        }
        self.pending.drain(..).collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A player with no process behind it: enough to drive `map_event`, which
    /// is where every mpv-protocol decision lives.
    fn bare_player() -> MpvPlayer {
        MpvPlayer {
            exe: PathBuf::from("/nonexistent/mpv"),
            opts: MpvOptions::new(PathBuf::from("/tmp/mpv-controller-unit.sock")),
            process: None,
            rpc: None,
            profile: CacheProfile::Normal,
            pending: VecDeque::new(),
            last_pos: 0.0,
            restarts: 0,
            max_restarts: DEFAULT_MAX_RESTARTS,
            own_pause: Some(false),
            own_seeks: 0,
            initial_props: ["pause", "paused-for-cache", "eof-reached"].into_iter().collect(),
            ended: false,
            fatal_reported: false,
        }
    }

    fn prop(name: &str, data: serde_json::Value) -> serde_json::Value {
        serde_json::json!({ "event": "property-change", "name": name, "data": data })
    }

    #[test]
    fn initial_property_emissions_are_consumed_not_surfaced() {
        let mut p = bare_player();
        // observe_property emits the current value once, on subscription …
        assert_eq!(p.map_event(prop("pause", serde_json::json!(false))), None);
        assert_eq!(p.map_event(prop("paused-for-cache", serde_json::json!(false))), None);
        assert_eq!(p.map_event(prop("eof-reached", serde_json::json!(false))), None);
        assert!(p.initial_props.is_empty(), "one consumption per property");
        // … and only the *next* change is news.
        assert_eq!(
            p.map_event(prop("pause", serde_json::json!(true))),
            Some(PlayerEvent::UserPause { paused: true })
        );
    }

    #[test]
    fn our_own_pause_write_is_not_a_user_pause() {
        let mut p = bare_player();
        p.initial_props.clear();
        p.own_pause = Some(true); // we just called set_paused(true)
        assert_eq!(p.map_event(prop("pause", serde_json::json!(true))), None, "echo");
        // The user toggles back — and a second, unchanged emission is not news.
        assert_eq!(
            p.map_event(prop("pause", serde_json::json!(false))),
            Some(PlayerEvent::UserPause { paused: false })
        );
        assert_eq!(p.map_event(prop("pause", serde_json::json!(false))), None, "state recorded");
    }

    #[test]
    fn command_seeks_are_ours_and_unmatched_seeks_are_the_users() {
        let mut p = bare_player();
        p.last_pos = 7.5;
        p.own_seeks = 1;
        // Verbatim shape from mpv v0.41: no `reason` field exists.
        let seek = serde_json::json!({ "event": "seek" });
        assert_eq!(p.map_event(seek.clone()), None, "the event for our own seek");
        assert_eq!(p.own_seeks, 0);
        // No rpc in a unit test: the position falls back to `last_pos`.
        assert_eq!(p.map_event(seek), Some(PlayerEvent::UserSeek { pos: 7.5 }));
    }

    #[test]
    fn buffering_edges_flow_through_and_the_initial_false_does_not() {
        let mut p = bare_player();
        // On-subscription emission: NOT a "refilled" edge.
        assert_eq!(p.map_event(prop("paused-for-cache", serde_json::json!(false))), None);
        assert_eq!(
            p.map_event(prop("paused-for-cache", serde_json::json!(true))),
            Some(PlayerEvent::Buffering(true))
        );
        assert_eq!(
            p.map_event(prop("paused-for-cache", serde_json::json!(false))),
            Some(PlayerEvent::Buffering(false))
        );
    }

    #[test]
    fn eof_is_reported_once_even_though_mpv_can_signal_it_twice() {
        let mut p = bare_player();
        p.initial_props.clear();
        assert_eq!(
            p.map_event(prop("eof-reached", serde_json::json!(true))),
            Some(PlayerEvent::Ended)
        );
        let end_file = serde_json::json!({ "event": "end-file", "reason": "eof" });
        assert_eq!(p.map_event(end_file.clone()), None, "second EOF signal suppressed");
        // Seeking back rewinds: EOF can legitimately happen again.
        p.ended = false;
        assert_eq!(p.map_event(end_file), Some(PlayerEvent::Ended));
    }

    #[test]
    fn error_end_files_and_our_own_stops_map_correctly() {
        let mut p = bare_player();
        let err = serde_json::json!({ "event": "end-file", "reason": "error", "file_error": "unreadable" });
        match p.map_event(err) {
            Some(PlayerEvent::Error(msg)) => assert!(msg.contains("unreadable"), "{msg}"),
            other => panic!("expected Error, got {other:?}"),
        }
        // stop/redirect: we replaced the file ourselves, not a failure.
        assert_eq!(
            p.map_event(serde_json::json!({ "event": "end-file", "reason": "stop" })),
            None
        );
        assert_eq!(
            p.map_event(serde_json::json!({ "event": "file-loaded" })),
            Some(PlayerEvent::Ready)
        );
    }
}
