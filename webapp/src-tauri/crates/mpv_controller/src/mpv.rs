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
use std::time::{Duration, Instant};

use crate::ipc::RpcClient;
use crate::port::{
    CacheProfile, PlayerError, PlayerEvent, PlayerPort, PlayerResult, PlayerState, SubStyle,
    TrackInfo,
};
use crate::process::{locate_mpv, MpvOptions, MpvProcess};

const REQUEST_TIMEOUT: Duration = Duration::from_secs(3);
const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
/// A process that dies this many times in a row is not a flaky driver —
/// stop, report, let the UI show something useful.
const DEFAULT_MAX_RESTARTS: u32 = 3;
/// `time-pos` changes far more often than a UI needs it; observed positions
/// are throttled to this interval in `map_event` (the raw event queue is a
/// drop-oldest 512, and flooding it would evict real events).
const POSITION_INTERVAL: Duration = Duration::from_millis(500);
/// `demuxer-cache-duration` ticks continuously as the buffer fills; only a
/// change of at least this many seconds is worth an event.
const CACHE_CHANGE_EPSILON: f64 = 0.25;
/// The properties the port observes. `time-pos` is what feeds `Position`;
/// everything else the UI asks for on demand (`get_state`).
const OBSERVED: [(i64, &str); 7] = [
    (1, "pause"),
    (2, "paused-for-cache"),
    (3, "eof-reached"),
    (4, "time-pos"),
    (5, "demuxer-cache-duration"),
    (6, "aid"),
    (7, "sid"),
];

pub struct MpvPlayer {
    exe: PathBuf,
    opts: MpvOptions,
    process: Option<MpvProcess>,
    rpc: Option<Arc<RpcClient>>,
    profile: CacheProfile,
    /// Events produced but not yet handed out by `poll_events`.
    pending: VecDeque<PlayerEvent>,
    /// Last known position: `UserSeek` needs a number and mpv's `seek` event
    /// does not carry one. Kept fresh by load/seek/`get_pos` and by every
    /// observed `time-pos`; the *events* built from `time-pos` are throttled
    /// (see `POSITION_INTERVAL`).
    last_pos: f64,
    /// When the last `Position` event was emitted, for the throttle. `None`
    /// until the first one, which is therefore never suppressed.
    last_position_at: Option<Instant>,
    /// Buffered seconds at the last `CacheState`, for the epsilon dedupe.
    last_cache_secs: Option<f64>,
    /// The selected audio/subtitle tracks, learned from the observed `aid`/`sid`
    /// (including their on-subscription emission, so a change can report both).
    last_aid: Option<i64>,
    last_sid: Option<i64>,
    /// Output state, remembered so a restart can restore it. The live values
    /// always come from mpv (`get_state`); these are only for `respawn`.
    volume: u8,
    mute: bool,
    fullscreen: bool,
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
            last_position_at: None,
            last_cache_secs: None,
            last_aid: None,
            last_sid: None,
            volume: 100, // mpv's default
            mute: false,
            fullscreen: false,
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
        for (id, name) in OBSERVED {
            rpc.request(
                serde_json::json!(["observe_property", id, name]),
                REQUEST_TIMEOUT,
            )?;
        }
        let profile = self.profile;
        self.apply_profile(&rpc, profile)?;
        // Put the output state back the way the caller left it. On a first
        // start these are mpv's own defaults (a harmless re-set); after a
        // crash they are the state the user was actually in.
        rpc.set_property("volume", serde_json::json!(self.volume as f64), REQUEST_TIMEOUT)?;
        rpc.set_property("mute", serde_json::json!(self.mute), REQUEST_TIMEOUT)?;
        rpc.set_property("fullscreen", serde_json::json!(self.fullscreen), REQUEST_TIMEOUT)?;
        // Fresh connection: any seek/pause in flight died with the old one,
        // and the first emission per property is the current value, not news.
        self.own_pause = Some(self.opts.paused);
        self.own_seeks = 0;
        self.ended = false;
        self.initial_props = OBSERVED.iter().map(|(_, name)| *name).collect();
        self.last_position_at = None; // the new subscription's first value is not news
        self.last_cache_secs = None;
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
        self.pending
            .push_back(PlayerEvent::ProcessCrashed { restarts: self.restarts });
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
        let loaded = self.loaded_event();
        self.pending.push_back(loaded);
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
        let deadline = Instant::now() + timeout;
        while let Some(raw) = rpc.wait_event(deadline.saturating_duration_since(Instant::now())) {
            if raw.get("event").and_then(|e| e.as_str()) == Some(name) {
                return true;
            }
            for ev in self.map_event(raw) {
                self.pending.push_back(ev);
            }
        }
        false
    }

    /// mpv message → port events. An empty vector means "not interesting to
    /// the UI". Returning a `Vec` (rather than an `Option`) is what lets one
    /// message legitimately be two events — a user pause is `Paused` *and*
    /// `UserPause`, and a load is `Loaded` *and* `Ready` — in a defined order.
    fn map_event(&mut self, raw: serde_json::Value) -> Vec<PlayerEvent> {
        let Some(kind) = raw.get("event").and_then(|e| e.as_str()) else {
            return vec![];
        };
        match kind {
            "file-loaded" => vec![self.loaded_event(), PlayerEvent::Ready],
            "end-file" => {
                let reason = raw.get("reason").and_then(|r| r.as_str()).unwrap_or("");
                match reason {
                    "eof" => self.take_ended().into_iter().collect(),
                    "error" => vec![PlayerEvent::Error(format!(
                        "playback failed: {}",
                        raw.get("file_error").and_then(|e| e.as_str()).unwrap_or("unknown")
                    ))],
                    // stop/redirect/quit: our own doing, not a failure.
                    _ => vec![],
                }
            }
            "seek" => {
                // No `reason` field exists (mpv v0.41) — ours by counter,
                // the user's if unmatched.
                if self.own_seeks > 0 {
                    self.own_seeks -= 1;
                    let pos = self.time_pos_now();
                    return vec![PlayerEvent::Seeked { pos }];
                }
                vec![PlayerEvent::UserSeek { pos: self.time_pos_now() }]
            }
            "property-change" => {
                let Some(name) = raw.get("name").and_then(|n| n.as_str()) else {
                    return vec![];
                };
                let data = raw.get("data").cloned().unwrap_or(serde_json::Value::Null);
                if self.initial_props.remove(name) {
                    // The on-subscription emission is the current value, not
                    // news — but for the track selections it *is* the starting
                    // point a later change has to report both halves of.
                    match name {
                        "aid" => self.last_aid = parse_track_id(&data),
                        "sid" => self.last_sid = parse_track_id(&data),
                        _ => {}
                    }
                    return vec![];
                }
                match name {
                    "paused-for-cache" => {
                        data.as_bool().map(PlayerEvent::Buffering).into_iter().collect()
                    }
                    "eof-reached" => {
                        if data.as_bool() == Some(true) {
                            self.take_ended().into_iter().collect()
                        } else {
                            vec![]
                        }
                    }
                    "pause" => {
                        let Some(paused) = data.as_bool() else { return vec![] };
                        if self.own_pause == Some(paused) {
                            // Echo of our own `set_paused`: real state news,
                            // but not something the user did.
                            vec![PlayerEvent::Paused { paused }]
                        } else {
                            self.own_pause = Some(paused);
                            vec![PlayerEvent::Paused { paused }, PlayerEvent::UserPause { paused }]
                        }
                    }
                    "time-pos" => self.maybe_position(data.as_f64()),
                    "demuxer-cache-duration" => self.maybe_cache(data.as_f64()),
                    "aid" => {
                        self.last_aid = parse_track_id(&data);
                        vec![PlayerEvent::TrackChanged { aid: self.last_aid, sid: self.last_sid }]
                    }
                    "sid" => {
                        self.last_sid = parse_track_id(&data);
                        vec![PlayerEvent::TrackChanged { aid: self.last_aid, sid: self.last_sid }]
                    }
                    _ => vec![],
                }
            }
            _ => vec![],
        }
    }

    /// `Loaded` for the file mpv just opened. The metadata is *fetched*: the
    /// `file-loaded` event carries none, and the UI wants the duration and the
    /// track list the moment it can play.
    fn loaded_event(&mut self) -> PlayerEvent {
        let duration = self
            .fetch("duration")
            .and_then(|v| v.as_f64())
            .unwrap_or(0.0);
        let tracks = self
            .fetch("track-list")
            .and_then(|v| v.as_array().cloned())
            .map(|a| a.iter().filter_map(track_info).collect())
            .unwrap_or_default();
        PlayerEvent::Loaded { duration, tracks }
    }

    /// One property, best effort: a dead connection or an unset property is
    /// simply absent, never a panic — `loaded_event`/`get_state` default.
    fn fetch(&self, name: &str) -> Option<serde_json::Value> {
        self.rpc.clone()?.get_property(name, REQUEST_TIMEOUT).ok()
    }

    /// `time-pos` → at most one `Position` per `POSITION_INTERVAL`. `last_pos`
    /// always follows the newest value, so a crash-resume is accurate even
    /// when the events were throttled away.
    fn maybe_position(&mut self, pos: Option<f64>) -> Vec<PlayerEvent> {
        let Some(pos) = pos else { return vec![] };
        self.last_pos = pos;
        let now = Instant::now();
        let due = self
            .last_position_at
            .map(|at| now.duration_since(at) >= POSITION_INTERVAL)
            .unwrap_or(true);
        if !due {
            return vec![];
        }
        self.last_position_at = Some(now);
        vec![PlayerEvent::Position { pos }]
    }

    /// `demuxer-cache-duration` → `CacheState`, but only for a real change:
    /// the value drifts continuously and would otherwise flood the queue.
    fn maybe_cache(&mut self, secs: Option<f64>) -> Vec<PlayerEvent> {
        let Some(secs) = secs else { return vec![] };
        let changed = self
            .last_cache_secs
            .map(|prev| (secs - prev).abs() >= CACHE_CHANGE_EPSILON)
            .unwrap_or(true);
        if !changed {
            return vec![];
        }
        self.last_cache_secs = Some(secs);
        vec![PlayerEvent::CacheState { buffered_secs: secs }]
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

/// One entry of mpv's `track-list` → a [`TrackInfo`]. An entry without an id
/// or a type is not a track we can address, so it is dropped.
fn track_info(v: &serde_json::Value) -> Option<TrackInfo> {
    Some(TrackInfo {
        id: v.get("id")?.as_i64()?,
        kind: v.get("type")?.as_str()?.to_string(),
        title: v.get("title").and_then(|t| t.as_str()).map(str::to_string),
        lang: v.get("lang").and_then(|t| t.as_str()).map(str::to_string),
    })
}

/// mpv spells "no track" `false` and a selection mode `"auto"`/`"no"`; only a
/// real id (or a numeric string) is a selection.
fn parse_track_id(v: &serde_json::Value) -> Option<i64> {
    if let Some(id) = v.as_i64() {
        return Some(id);
    }
    v.as_str()?.parse::<i64>().ok()
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

    fn add_subtitle(&mut self, url: &str) -> PlayerResult<()> {
        self.ensure_alive(true)?;
        // `select` also shows it — matching FakePlayer and the UI's intent.
        self.rpc()?
            .request(
                serde_json::json!(["sub-add", url, "select"]),
                REQUEST_TIMEOUT,
            )
            .map(|_| ())
    }

    fn set_sub_style(&mut self, style: SubStyle) -> PlayerResult<()> {
        self.ensure_alive(true)?;
        let rpc = self.rpc()?;
        if let Some(scale) = style.scale {
            rpc.set_property("sub-scale", serde_json::json!(scale), REQUEST_TIMEOUT)?;
        }
        for (prop, value) in [
            ("sub-color", style.color),
            ("sub-border-color", style.border_color),
            ("sub-back-color", style.back_color),
        ] {
            if let Some(value) = value {
                rpc.set_property(prop, serde_json::json!(value), REQUEST_TIMEOUT)?;
            }
        }
        Ok(())
    }

    fn set_volume(&mut self, level: u8) -> PlayerResult<()> {
        self.ensure_alive(true)?;
        let level = level.min(100);
        self.rpc()?
            .set_property("volume", serde_json::json!(level as f64), REQUEST_TIMEOUT)?;
        self.volume = level; // remembered for the next respawn
        Ok(())
    }

    fn set_mute(&mut self, on: bool) -> PlayerResult<()> {
        self.ensure_alive(true)?;
        self.rpc()?
            .set_property("mute", serde_json::json!(on), REQUEST_TIMEOUT)?;
        self.mute = on;
        Ok(())
    }

    fn set_fullscreen(&mut self, on: bool) -> PlayerResult<()> {
        self.ensure_alive(true)?;
        self.rpc()?
            .set_property("fullscreen", serde_json::json!(on), REQUEST_TIMEOUT)?;
        self.fullscreen = on;
        Ok(())
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

    fn get_state(&mut self) -> PlayerResult<PlayerState> {
        self.ensure_alive(true)?;
        let rpc = self.rpc()?;
        // A paint, not a command: anything mpv cannot answer for defaults.
        let raw = |name: &str| {
            rpc.get_property(name, REQUEST_TIMEOUT)
                .unwrap_or(serde_json::Value::Null)
        };
        let pos = raw("time-pos").as_f64().unwrap_or(self.last_pos);
        self.last_pos = pos;
        let aid = parse_track_id(&raw("aid"));
        let sid = parse_track_id(&raw("sid"));
        // Keep the observed-selection bookkeeping in step with what we read.
        self.last_aid = aid;
        self.last_sid = sid;
        Ok(PlayerState {
            pos,
            duration: raw("duration").as_f64().unwrap_or(0.0),
            paused: raw("pause").as_bool().unwrap_or(false),
            rate: raw("speed").as_f64().unwrap_or(1.0),
            aid,
            sid,
            volume: raw("volume").as_f64().unwrap_or(100.0).clamp(0.0, 100.0).round() as u8,
            mute: raw("mute").as_bool().unwrap_or(false),
            fullscreen: raw("fullscreen").as_bool().unwrap_or(false),
            profile: self.profile,
            buffered_secs: raw("demuxer-cache-duration").as_f64().unwrap_or(0.0),
            tracks: raw("track-list")
                .as_array()
                .map(|a| a.iter().filter_map(track_info).collect())
                .unwrap_or_default(),
            loaded: self.opts.media.clone(),
        })
    }

    fn poll_events(&mut self, timeout: Duration) -> Vec<PlayerEvent> {
        if let Err(e) = self.ensure_alive(true) {
            // One clear line when the restart budget is spent, never a stream
            // of them: transient failures below the budget already surfaced as
            // the `ProcessCrashed` event `ensure_alive` queued before failing.
            if self.restarts >= self.max_restarts && !self.fatal_reported {
                self.fatal_reported = true;
                self.pending.push_back(PlayerEvent::Error(e.to_string()));
            }
            return self.pending.drain(..).collect();
        }
        if self.pending.is_empty() {
            // Wait for the first *port* event; mpv noise (unobserved
            // properties, stray events) maps to nothing and is skipped while
            // there is time left on the clock.
            let deadline = Instant::now() + timeout;
            while self.pending.is_empty() {
                let now = Instant::now();
                if now >= deadline {
                    break;
                }
                let Some(rpc) = self.rpc.clone() else { break };
                let Some(raw) = rpc.wait_event(deadline - now) else { break };
                for ev in self.map_event(raw) {
                    self.pending.push_back(ev);
                }
            }
            // Anything that queued up behind the first one, without blocking.
            if let Some(rpc) = self.rpc.clone() {
                for raw in rpc.drain_events() {
                    for ev in self.map_event(raw) {
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
            last_position_at: None,
            last_cache_secs: None,
            last_aid: None,
            last_sid: None,
            volume: 100,
            mute: false,
            fullscreen: false,
            restarts: 0,
            max_restarts: DEFAULT_MAX_RESTARTS,
            own_pause: Some(false),
            own_seeks: 0,
            initial_props: OBSERVED.iter().map(|(_, name)| *name).collect(),
            ended: false,
            fatal_reported: false,
        }
    }

    fn prop(name: &str, data: serde_json::Value) -> serde_json::Value {
        serde_json::json!({ "event": "property-change", "name": name, "data": data })
    }

    /// A player with no file and a fresh subscription: the state `respawn`
    /// leaves behind, for tests that want to drive a change directly.
    fn observing_player() -> MpvPlayer {
        let mut p = bare_player();
        p.initial_props.clear();
        p
    }

    #[test]
    fn initial_property_emissions_are_consumed_not_surfaced() {
        let mut p = bare_player();
        // observe_property emits the current value once, on subscription …
        assert!(p.map_event(prop("pause", serde_json::json!(false))).is_empty());
        assert!(p.map_event(prop("paused-for-cache", serde_json::json!(false))).is_empty());
        assert!(p.map_event(prop("eof-reached", serde_json::json!(false))).is_empty());
        assert!(p.map_event(prop("time-pos", serde_json::json!(3.0))).is_empty());
        assert!(p.map_event(prop("demuxer-cache-duration", serde_json::json!(1.0))).is_empty());
        assert!(p.map_event(prop("aid", serde_json::json!(1))).is_empty());
        assert!(p.map_event(prop("sid", serde_json::json!("no"))).is_empty());
        assert!(p.initial_props.is_empty(), "one consumption per observed property");
        // The initial `aid` emission is still *learned*: a later `sid` change
        // reports both halves of the selection.
        assert_eq!(p.last_aid, Some(1), "the starting selection is recorded");
        // … and only the *next* change is news.
        assert_eq!(
            p.map_event(prop("pause", serde_json::json!(true))),
            vec![PlayerEvent::Paused { paused: true }, PlayerEvent::UserPause { paused: true }]
        );
    }

    #[test]
    fn our_own_pause_write_is_a_paused_event_but_not_a_user_pause() {
        let mut p = observing_player();
        p.own_pause = Some(true); // we just called set_paused(true)
        assert_eq!(
            p.map_event(prop("pause", serde_json::json!(true))),
            vec![PlayerEvent::Paused { paused: true }],
            "the echo updates state, but is not a user action"
        );
        // The user toggles back — that is both.
        assert_eq!(
            p.map_event(prop("pause", serde_json::json!(false))),
            vec![PlayerEvent::Paused { paused: false }, PlayerEvent::UserPause { paused: false }]
        );
        // A further same-value emission is the echo of *that* change: state
        // news (`Paused`), never another user action. mpv only emits on a real
        // change, so this only happens for our own write.
        assert_eq!(
            p.map_event(prop("pause", serde_json::json!(false))),
            vec![PlayerEvent::Paused { paused: false }],
            "state recorded"
        );
    }

    #[test]
    fn command_seeks_are_seeked_and_unmatched_seeks_are_the_users() {
        let mut p = observing_player();
        p.last_pos = 7.5;
        p.own_seeks = 1;
        // Verbatim shape from mpv v0.41: no `reason` field exists.
        let seek = serde_json::json!({ "event": "seek" });
        assert_eq!(
            p.map_event(seek.clone()),
            vec![PlayerEvent::Seeked { pos: 7.5 }],
            "our own seek"
        );
        assert_eq!(p.own_seeks, 0);
        // No rpc in a unit test: the position falls back to `last_pos`.
        assert_eq!(p.map_event(seek), vec![PlayerEvent::UserSeek { pos: 7.5 }]);
    }

    #[test]
    fn buffering_edges_flow_through_and_the_initial_false_does_not() {
        let mut p = bare_player();
        // On-subscription emission: NOT a "refilled" edge.
        assert!(p.map_event(prop("paused-for-cache", serde_json::json!(false))).is_empty());
        assert_eq!(
            p.map_event(prop("paused-for-cache", serde_json::json!(true))),
            vec![PlayerEvent::Buffering(true)]
        );
        assert_eq!(
            p.map_event(prop("paused-for-cache", serde_json::json!(false))),
            vec![PlayerEvent::Buffering(false)]
        );
    }

    #[test]
    fn eof_is_reported_once_even_though_mpv_can_signal_it_twice() {
        let mut p = observing_player();
        assert_eq!(
            p.map_event(prop("eof-reached", serde_json::json!(true))),
            vec![PlayerEvent::Ended]
        );
        let end_file = serde_json::json!({ "event": "end-file", "reason": "eof" });
        assert!(p.map_event(end_file.clone()).is_empty(), "second EOF signal suppressed");
        // Seeking back rewinds: EOF can legitimately happen again.
        p.ended = false;
        assert_eq!(p.map_event(end_file), vec![PlayerEvent::Ended]);
    }

    #[test]
    fn error_end_files_and_our_own_stops_map_correctly() {
        let mut p = observing_player();
        let err = serde_json::json!({ "event": "end-file", "reason": "error", "file_error": "unreadable" });
        match p.map_event(err).as_slice() {
            [PlayerEvent::Error(msg)] => assert!(msg.contains("unreadable"), "{msg}"),
            other => panic!("expected one Error, got {other:?}"),
        }
        // stop/redirect: we replaced the file ourselves, not a failure.
        assert!(p
            .map_event(serde_json::json!({ "event": "end-file", "reason": "stop" }))
            .is_empty());
        // file-loaded announces the metadata and then readiness. There is no
        // rpc in a unit test, so the metadata is the default.
        assert_eq!(
            p.map_event(serde_json::json!({ "event": "file-loaded" })),
            vec![
                PlayerEvent::Loaded { duration: 0.0, tracks: vec![] },
                PlayerEvent::Ready
            ]
        );
    }

    /// The observed set is everything `respawn` subscribes to; the rest of the
    /// port's surface is fetched on demand (`get_state`).
    #[test]
    fn a_fresh_player_observes_the_seven_properties() {
        let p = bare_player();
        assert_eq!(p.initial_props.len(), 7, "observed: {:?}", p.initial_props);
        for name in [
            "pause",
            "paused-for-cache",
            "eof-reached",
            "time-pos",
            "demuxer-cache-duration",
            "aid",
            "sid",
        ] {
            assert!(p.initial_props.contains(name), "missing {name}");
        }
        // Restart budget, remembered output state and defaults the
        // crash-recovery path relies on.
        assert_eq!(p.max_restarts, 3);
        assert_eq!(p.own_pause, Some(false));
        assert_eq!(p.own_seeks, 0);
        assert_eq!(p.last_pos, 0.0);
        assert_eq!(p.volume, 100);
        assert!(!p.mute);
        assert!(!p.fullscreen);
        assert!(p.last_position_at.is_none());
        assert!(p.last_cache_secs.is_none());
        assert!(!p.ended);
        assert!(!p.fatal_reported);
        assert!(p.process.is_none() && p.rpc.is_none());
    }

    /// Properties the port does not observe never become events, however hot
    /// they are on the wire. The UI reads them through `get_state` instead.
    #[test]
    fn unobserved_properties_are_ignored() {
        let mut p = observing_player();
        for (name, data) in [
            ("duration", serde_json::json!(300.0)),
            ("volume", serde_json::json!(55.0)),
            ("mute", serde_json::json!(true)),
            ("fullscreen", serde_json::json!(true)),
            ("speed", serde_json::json!(1.5)),
            ("track-list", serde_json::json!([])),
            ("aid-list", serde_json::json!([])),
        ] {
            assert!(
                p.map_event(prop(name, data)).is_empty(),
                "property {name} is not surfaced as an event"
            );
        }
    }

    /// `time-pos` is observed, but throttled: the first change after a
    /// subscription is news, an immediate second one is not, and time passing
    /// releases the next. `last_pos` always follows the newest value, so a
    /// suppressed sample still informs a crash-resume.
    #[test]
    fn position_events_are_throttled_but_last_pos_is_not() {
        let mut p = observing_player();
        assert_eq!(
            p.map_event(prop("time-pos", serde_json::json!(1.0))),
            vec![PlayerEvent::Position { pos: 1.0 }]
        );
        assert!(p
            .map_event(prop("time-pos", serde_json::json!(1.2)))
            .is_empty(), "too soon");
        assert_eq!(p.last_pos, 1.2, "the newest value is still recorded");
        // Pretend the interval elapsed, as it will in production.
        p.last_position_at = Some(Instant::now() - POSITION_INTERVAL);
        assert_eq!(
            p.map_event(prop("time-pos", serde_json::json!(1.5))),
            vec![PlayerEvent::Position { pos: 1.5 }]
        );
        // A non-numeric payload is not a position.
        assert!(p.map_event(prop("time-pos", serde_json::json!("n/a"))).is_empty());
    }

    /// Cache telemetry only speaks up for a real change: the value drifts
    /// continuously and a sub-epsilon wobble is noise.
    #[test]
    fn cache_state_is_deduped_by_a_quarter_second() {
        let mut p = observing_player();
        assert_eq!(
            p.map_event(prop("demuxer-cache-duration", serde_json::json!(2.0))),
            vec![PlayerEvent::CacheState { buffered_secs: 2.0 }]
        );
        assert!(p
            .map_event(prop("demuxer-cache-duration", serde_json::json!(2.1)))
            .is_empty(), "within the epsilon");
        assert_eq!(
            p.map_event(prop("demuxer-cache-duration", serde_json::json!(2.4))),
            vec![PlayerEvent::CacheState { buffered_secs: 2.4 }]
        );
    }

    /// A track selection change reports *both* selections, so the UI can
    /// re-render without asking again.
    #[test]
    fn track_changes_report_both_selections() {
        let mut p = observing_player();
        p.last_aid = Some(1);
        p.last_sid = None;
        assert_eq!(
            p.map_event(prop("sid", serde_json::json!(2))),
            vec![PlayerEvent::TrackChanged { aid: Some(1), sid: Some(2) }]
        );
        assert_eq!(
            p.map_event(prop("aid", serde_json::json!(3))),
            vec![PlayerEvent::TrackChanged { aid: Some(3), sid: Some(2) }]
        );
        // `false` is mpv's "no track".
        assert_eq!(
            p.map_event(prop("aid", serde_json::json!(false))),
            vec![PlayerEvent::TrackChanged { aid: None, sid: Some(2) }]
        );
    }

    /// A crash below the restart budget is `ProcessCrashed`, not `Error`; the
    /// fatal path (budget spent) is still an `Error`. This unit test has no mpv
    /// to respawn, so the restart itself fails — but the event is queued first.
    #[test]
    fn a_restart_queues_process_crashed_not_error() {
        let mut p = observing_player();
        assert!(p.ensure_alive(false).is_err(), "no mpv to respawn");
        assert_eq!(p.restarts, 1);
        assert!(
            p.pending.iter().any(|e| *e == PlayerEvent::ProcessCrashed { restarts: 1 }),
            "queued: {:?}",
            p.pending
        );
        assert!(
            !p.pending.iter().any(|e| matches!(e, PlayerEvent::Error(_))),
            "a recoverable crash is not an Error"
        );
    }

    /// Malformed or merely unknown traffic on the socket must never become a
    /// player event — an unparseable line upstream leaves `raw` as whatever
    /// parsed, and mpv emits plenty of events this port does not care about.
    #[test]
    fn malformed_unknown_and_non_boolean_payloads_are_ignored() {
        let mut p = observing_player();

        // No / unknown event kind.
        assert!(p.map_event(serde_json::json!({})).is_empty(), "no event field");
        assert!(p.map_event(serde_json::json!({ "event": "idle" })).is_empty());
        assert!(p.map_event(serde_json::json!({ "event": "startup-complete" })).is_empty());

        // property-change without a usable name or payload.
        assert!(
            p.map_event(serde_json::json!({ "event": "property-change" }))
                .is_empty(),
            "no name"
        );
        assert!(
            p.map_event(serde_json::json!({ "event": "property-change", "name": "pause" }))
                .is_empty(),
            "missing data is Null, not false"
        );
        assert!(p.map_event(prop("pause", serde_json::json!("yes"))).is_empty(), "non-bool pause");
        assert!(
            p.map_event(prop("paused-for-cache", serde_json::json!("maybe")))
                .is_empty(),
            "non-bool buffering"
        );
        assert!(p.map_event(prop("eof-reached", serde_json::json!(false))).is_empty());
        assert!(
            p.map_event(prop("demuxer-cache-duration", serde_json::json!("soon")))
                .is_empty(),
            "non-numeric cache"
        );

        // end-file reasons that are ours or inert, plus the missing file_error
        // fallback ("playback failed: unknown" rather than a panic or "").
        for reason in ["", "quit", "redirect", "delete", "stop"] {
            assert!(
                p.map_event(serde_json::json!({ "event": "end-file", "reason": reason }))
                    .is_empty(),
                "reason {reason:?} is not a failure"
            );
        }
        match p
            .map_event(serde_json::json!({ "event": "end-file", "reason": "error" }))
            .as_slice()
        {
            [PlayerEvent::Error(msg)] => assert!(msg.contains("unknown"), "{msg}"),
            other => panic!("expected one Error, got {other:?}"),
        }
    }
}
