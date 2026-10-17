//! `FakePlayer` — a [`PlayerPort`] with virtual time.
//!
//! Later phases (sync, cache profiles, UI) need a player that can be paused,
//! stalled, made slow and made to fail *on demand*, without a process or a
//! codec. Virtual time only moves when a test calls [`FakePlayer::tick`], so
//! tests are deterministic and take microseconds instead of seconds.

use std::collections::VecDeque;
use std::time::Duration;

use crate::port::{
    CacheProfile, PlayerError, PlayerEvent, PlayerPort, PlayerResult, PlayerState, SubStyle,
    TrackInfo,
};

#[derive(Debug, Clone, PartialEq)]
pub struct FakeLoad {
    pub url: String,
    pub start: f64,
    pub paused: bool,
}

/// A [`PlayerPort`] that plays nothing and lies on purpose.
#[derive(Debug)]
pub struct FakePlayer {
    /// Position in seconds — only moved by `tick`/`seek`.
    pub pos: f64,
    pub duration: f64,
    pub paused: bool,
    pub rate: f64,
    pub aid: Option<i64>,
    pub sid: Option<i64>,
    pub profile: CacheProfile,
    pub loaded: Option<FakeLoad>,
    /// External audio files handed to `audio_add`, in order.
    pub audio_tracks: Vec<(String, String)>,
    /// External subtitle files handed to `add_subtitle`, in order.
    pub subs: Vec<String>,
    /// The current file's track list (synthesised on `load`, appended to by
    /// `audio_add`/`add_subtitle`).
    pub tracks: Vec<TrackInfo>,
    pub volume: u8,
    pub mute: bool,
    pub fullscreen: bool,
    pub sub_style: SubStyle,
    /// `true` after [`FakePlayer::crash`] until [`FakePlayer::recover`]: every
    /// command fails with `NotRunning`, as with a dead mpv.
    pub crashed: bool,
    /// Restarts simulated by `crash()`, for asserting `ProcessCrashed`.
    pub restarts: u32,
    /// Every command seen, for assertions on call order.
    pub calls: Vec<String>,

    /// Artificial round-trip latency applied *before* a command takes effect:
    /// the call still returns after `latency`, i.e. it is synchronous, but the
    /// observable state changes at the end of it. Default zero, so tests that
    /// do not ask for it stay instant.
    pub latency: Duration,
    /// Commands still owed a failure (popped front-to-back).
    fail_next: VecDeque<String>,
    /// Seconds still left in a simulated stall: time does not advance and
    /// `Buffering(true)` stays latched.
    stall_for: f64,
    buffering: bool,
    ended: bool,
    events: VecDeque<PlayerEvent>,
}

impl Default for FakePlayer {
    fn default() -> Self {
        Self::new(60.0)
    }
}

impl FakePlayer {
    pub fn new(duration: f64) -> Self {
        Self {
            pos: 0.0,
            duration,
            paused: true,
            rate: 1.0,
            aid: None,
            sid: None,
            profile: CacheProfile::Normal,
            loaded: None,
            audio_tracks: Vec::new(),
            subs: Vec::new(),
            tracks: Vec::new(),
            volume: 100,
            mute: false,
            fullscreen: false,
            sub_style: SubStyle::default(),
            crashed: false,
            restarts: 0,
            calls: Vec::new(),
            latency: Duration::ZERO,
            fail_next: VecDeque::new(),
            stall_for: 0.0,
            buffering: false,
            ended: false,
            events: VecDeque::new(),
        }
    }

    // ── test controls (not part of PlayerPort) ──────────────────────────────

    /// Make the next command named `op` fail with `detail`.
    pub fn fail_next(&mut self, op: &str, detail: &str) {
        self.fail_next.push_back(format!("{op}:{detail}"));
    }

    /// Freeze playback for `seconds` of virtual time, emitting the buffering
    /// edges a real player would.
    pub fn stall_for(&mut self, seconds: f64) {
        self.stall_for = seconds;
        self.events.push_back(PlayerEvent::Buffering(true));
        self.buffering = true;
    }

    /// Advance virtual time; un-stalls, plays (unless paused) and emits
    /// `Buffering(false)` / `Ended` as a real player would.
    pub fn tick(&mut self, seconds: f64) {
        if self.stall_for > 0.0 {
            self.stall_for = (self.stall_for - seconds).max(0.0);
            if self.stall_for == 0.0 && self.buffering {
                self.buffering = false;
                self.events.push_back(PlayerEvent::Buffering(false));
            }
            return;
        }
        if self.paused || self.loaded.is_none() || self.ended {
            return;
        }
        self.pos = (self.pos + seconds * self.rate).min(self.duration);
        if self.pos >= self.duration && !self.ended {
            self.ended = true;
            self.events.push_back(PlayerEvent::Ended);
        }
    }

    /// Queue an event as if the player produced it.
    pub fn emit(&mut self, event: PlayerEvent) {
        self.events.push_back(event);
    }

    /// Simulate mpv dying: every command now fails with `NotRunning` until
    /// [`recover`](Self::recover), and a `ProcessCrashed` event is queued.
    pub fn crash(&mut self) {
        self.crashed = true;
        self.restarts += 1;
        self.events
            .push_back(PlayerEvent::ProcessCrashed { restarts: self.restarts });
    }

    /// Simulate the restart `ensure_alive` performs after a crash. Position,
    /// pause, rate, volume, mute, fullscreen and tracks survive — that is the
    /// state restore the real player does — and the file announces itself
    /// again as `Loaded` followed by `Ready`, in that order.
    pub fn recover(&mut self) {
        self.crashed = false;
        if self.loaded.is_some() {
            self.events.push_back(PlayerEvent::Loaded {
                duration: self.duration,
                tracks: self.tracks.clone(),
            });
            self.events.push_back(PlayerEvent::Ready);
        }
    }

    /// The next free track id of `kind` for the current file, as mpv numbers
    /// them: per file, per kind, from 1.
    fn next_track_id(&self, kind: &str) -> i64 {
        self.tracks
            .iter()
            .filter(|t| t.kind == kind)
            .map(|t| t.id)
            .max()
            .map(|m| m + 1)
            .unwrap_or(1)
    }

    fn guard(&mut self, op: &str) -> PlayerResult<()> {
        // Injected latency lands before anything else happens: the caller
        // waits, then the command either takes effect or fails.
        if !self.latency.is_zero() {
            std::thread::sleep(self.latency);
        }
        if self.crashed {
            return Err(PlayerError::NotRunning(format!("mpv crashed (during {op})")));
        }
        let key = format!("{}:*", op);
        let fail = self
            .fail_next
            .front()
            .map(|k| k == &key || k.starts_with(&format!("{op}:")))
            .unwrap_or(false);
        if fail {
            let entry = self.fail_next.pop_front().unwrap_or_default();
            let detail = entry.split_once(':').map(|(_, d)| d.to_string()).unwrap_or_default();
            return Err(PlayerError::Rejected {
                command: op.to_string(),
                detail,
            });
        }
        if self.loaded.is_none() && op != "load" {
            return Err(PlayerError::NotRunning(format!("no file loaded for {op}")));
        }
        Ok(())
    }
}

impl PlayerPort for FakePlayer {
    fn load(&mut self, url: &str, start: f64, paused: bool) -> PlayerResult<()> {
        self.guard("load")?;
        self.calls.push(format!("load {url} @{start} paused={paused}"));
        // Events queued for the previous file would be delivered as if they
        // belonged to this one — mirror MpvPlayer::load, which clears too.
        self.events.clear();
        // Per-file state, as a real load replaces it: the external tracks
        // belonged to the old file. `calls` accumulates on purpose.
        self.audio_tracks.clear();
        self.subs.clear();
        self.tracks = vec![
            TrackInfo { id: 1, kind: "video".into(), title: None, lang: None },
            TrackInfo { id: 1, kind: "audio".into(), title: None, lang: None },
        ];
        self.aid = Some(1); // mpv auto-selects the first audio track
        self.sid = None;
        self.loaded = Some(FakeLoad { url: url.to_string(), start, paused });
        self.pos = start;
        self.paused = paused;
        self.ended = false;
        // What mpv actually emits on a load: metadata, then "you can play".
        self.events.push_back(PlayerEvent::Loaded {
            duration: self.duration,
            tracks: self.tracks.clone(),
        });
        self.events.push_back(PlayerEvent::Ready);
        // Documented asymmetry (ADR 0004): the real MpvPlayer credits a
        // `start > 0` load as its own seek and therefore emits `Seeked`. The
        // fake does not — a load already announces its target as `start`, so
        // there is no transition left for a consumer to wait for. If a Sync
        // engine ever needs to react to a load-position seek, close this gap
        // deliberately rather than by accident.
        Ok(())
    }

    fn seek(&mut self, pos: f64) -> PlayerResult<()> {
        self.guard("seek")?;
        self.calls.push(format!("seek {pos}"));
        self.pos = pos.clamp(0.0, self.duration);
        self.ended = false;
        // Faithful to mpv (ADR 0004): our own seek does produce an event. The
        // fake previously emitted nothing because it has no socket, which
        // meant a `PlayerPort` consumer — the sync engine — could never see
        // its own seek complete when testing against the fake. `load(start>0)`
        // is the deliberate exception; see `load`.
        self.events.push_back(PlayerEvent::Seeked { pos: self.pos });
        Ok(())
    }

    fn set_paused(&mut self, paused: bool) -> PlayerResult<()> {
        self.guard("set_paused")?;
        self.calls.push(format!("set_paused {paused}"));
        self.paused = paused;
        // Our own write still changes the state the UI shows, so it is a
        // `Paused`; it is *not* a `UserPause` (that is for mpv-originated
        // toggles, which a test injects with `emit`).
        self.events.push_back(PlayerEvent::Paused { paused });
        Ok(())
    }

    fn set_rate(&mut self, rate: f64) -> PlayerResult<()> {
        self.guard("set_rate")?;
        self.calls.push(format!("set_rate {rate}"));
        self.rate = rate;
        Ok(())
    }

    fn set_aid(&mut self, id: Option<i64>) -> PlayerResult<()> {
        self.guard("set_aid")?;
        self.calls.push(format!("set_aid {id:?}"));
        self.aid = id;
        // mpv echoes the selection as an `aid` property change.
        self.events.push_back(PlayerEvent::TrackChanged { aid: self.aid, sid: self.sid });
        Ok(())
    }

    fn set_sid(&mut self, id: Option<i64>) -> PlayerResult<()> {
        self.guard("set_sid")?;
        self.calls.push(format!("set_sid {id:?}"));
        self.sid = id;
        self.events.push_back(PlayerEvent::TrackChanged { aid: self.aid, sid: self.sid });
        Ok(())
    }

    fn audio_add(&mut self, url: &str, title: &str) -> PlayerResult<()> {
        self.guard("audio_add")?;
        self.calls.push(format!("audio_add {url}"));
        self.audio_tracks.push((url.to_string(), title.to_string()));
        // mpv's `audio-add … auto` appends a track but does not switch to it;
        // the caller selects it with `set_aid`, which emits the TrackChanged.
        let id = self.next_track_id("audio");
        self.tracks.push(TrackInfo {
            id,
            kind: "audio".into(),
            title: Some(title.to_string()),
            lang: None,
        });
        Ok(())
    }

    fn add_subtitle(&mut self, url: &str) -> PlayerResult<()> {
        self.guard("add_subtitle")?;
        self.calls.push(format!("add_subtitle {url}"));
        self.subs.push(url.to_string());
        let id = self.next_track_id("sub");
        self.tracks.push(TrackInfo {
            id,
            kind: "sub".into(),
            title: Some(url.to_string()),
            lang: None,
        });
        // `sub-add … select`: the new subtitle becomes the current one.
        self.sid = Some(id);
        self.events.push_back(PlayerEvent::TrackChanged { aid: self.aid, sid: self.sid });
        Ok(())
    }

    fn set_sub_style(&mut self, style: SubStyle) -> PlayerResult<()> {
        self.guard("set_sub_style")?;
        self.calls.push(format!("set_sub_style {style:?}"));
        // Only the fields that were given change; `None` leaves the old value.
        if style.scale.is_some() {
            self.sub_style.scale = style.scale;
        }
        if style.color.is_some() {
            self.sub_style.color = style.color;
        }
        if style.border_color.is_some() {
            self.sub_style.border_color = style.border_color;
        }
        if style.back_color.is_some() {
            self.sub_style.back_color = style.back_color;
        }
        Ok(())
    }

    fn set_cache_profile(&mut self, profile: CacheProfile) -> PlayerResult<()> {
        self.guard("set_cache_profile")?;
        self.calls.push(format!("set_cache_profile {profile:?}"));
        self.profile = profile;
        Ok(())
    }

    fn set_volume(&mut self, level: u8) -> PlayerResult<()> {
        self.guard("set_volume")?;
        let level = level.min(100);
        self.calls.push(format!("set_volume {level}"));
        self.volume = level;
        Ok(())
    }

    fn set_mute(&mut self, on: bool) -> PlayerResult<()> {
        self.guard("set_mute")?;
        self.calls.push(format!("set_mute {on}"));
        self.mute = on;
        Ok(())
    }

    fn set_fullscreen(&mut self, on: bool) -> PlayerResult<()> {
        self.guard("set_fullscreen")?;
        self.calls.push(format!("set_fullscreen {on}"));
        self.fullscreen = on;
        Ok(())
    }

    fn get_pos(&mut self) -> PlayerResult<f64> {
        self.guard("get_pos")?;
        Ok(self.pos)
    }

    /// A snapshot, not a command: like the real player on an idle file, it
    /// answers rather than failing, so it is deliberately not gated on `load`.
    fn get_state(&mut self) -> PlayerResult<PlayerState> {
        Ok(PlayerState {
            pos: self.pos,
            duration: self.duration,
            paused: self.paused,
            rate: self.rate,
            aid: self.aid,
            sid: self.sid,
            volume: self.volume,
            mute: self.mute,
            fullscreen: self.fullscreen,
            profile: self.profile,
            // A local file is "buffered" to the end.
            buffered_secs: (self.duration - self.pos).max(0.0),
            tracks: self.tracks.clone(),
            loaded: self.loaded.as_ref().map(|l| l.url.clone()),
        })
    }

    fn poll_events(&mut self, _timeout: Duration) -> Vec<PlayerEvent> {
        self.events.drain(..).collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The two events `load` and `recover` produce, in order: the file
    /// announces its metadata, then says it can play. `duration` is the
    /// fake's own, and the tracks are the synthetic video+audio a load creates.
    fn loaded(duration: f64) -> Vec<PlayerEvent> {
        vec![
            PlayerEvent::Loaded {
                duration,
                tracks: vec![
                    TrackInfo { id: 1, kind: "video".into(), title: None, lang: None },
                    TrackInfo { id: 1, kind: "audio".into(), title: None, lang: None },
                ],
            },
            PlayerEvent::Ready,
        ]
    }

    #[test]
    fn load_reports_loaded_then_ready_and_starts_paused() {
        let mut f = FakePlayer::new(100.0);
        f.load("file:///m.mkv", 12.0, true).unwrap();
        assert_eq!(f.poll_events(Duration::ZERO), loaded(100.0));
        assert_eq!(f.get_pos().unwrap(), 12.0);
        assert!(f.paused);
        assert_eq!(f.aid, Some(1), "a load selects the first audio track");
        assert_eq!(f.loaded.as_ref().map(|l| l.url.as_str()), Some("file:///m.mkv"));
    }

    #[test]
    fn virtual_time_only_moves_when_ticked_and_not_paused() {
        let mut f = FakePlayer::new(10.0);
        f.load("m", 0.0, true).unwrap();
        f.tick(5.0);
        assert_eq!(f.get_pos().unwrap(), 0.0);
        f.set_paused(false).unwrap();
        f.tick(3.0);
        assert_eq!(f.get_pos().unwrap(), 3.0);
        f.set_rate(2.0).unwrap();
        f.tick(1.0);
        assert_eq!(f.get_pos().unwrap(), 5.0);
    }

    #[test]
    fn stall_freezes_time_and_brackets_it_with_buffering_edges() {
        let mut f = FakePlayer::new(60.0);
        f.load("m", 0.0, false).unwrap();
        f.stall_for(2.0);
        f.tick(1.0); // 1s into the 2s stall
        f.tick(1.0); // stall completes exactly here; this tick does not play
        assert_eq!(f.get_pos().unwrap(), 0.0, "time must not advance while stalled");
        let mut expected = loaded(60.0);
        expected.push(PlayerEvent::Buffering(true));
        expected.push(PlayerEvent::Buffering(false));
        assert_eq!(f.poll_events(Duration::ZERO), expected);
        f.tick(1.0);
        assert_eq!(f.get_pos().unwrap(), 1.0, "play resumes after the stall");
    }

    #[test]
    fn end_of_file_emits_ended_once() {
        let mut f = FakePlayer::new(2.0);
        f.load("m", 0.0, false).unwrap();
        f.tick(5.0);
        f.tick(5.0);
        let evs = f.poll_events(Duration::ZERO);
        assert_eq!(evs.iter().filter(|e| **e == PlayerEvent::Ended).count(), 1);
        assert_eq!(f.get_pos().unwrap(), 2.0);
    }

    #[test]
    fn injected_failures_are_reported_and_consumed_once() {
        let mut f = FakePlayer::new(60.0);
        f.load("m", 0.0, false).unwrap();
        f.fail_next("seek", "denied");
        let err = f.seek(10.0).unwrap_err();
        assert!(matches!(err, PlayerError::Rejected { ref command, .. } if command == "seek"));
        f.seek(10.0).unwrap();
        assert_eq!(f.get_pos().unwrap(), 10.0);
    }

    #[test]
    fn port_calls_before_load_are_rejected() {
        let mut f = FakePlayer::default();
        assert!(matches!(
            f.set_rate(1.5),
            Err(PlayerError::NotRunning(_))
        ));
    }

    #[test]
    fn cache_profile_is_stored() {
        let mut f = FakePlayer::default();
        f.load("m", 0.0, false).unwrap();
        f.set_cache_profile(CacheProfile::Metered).unwrap();
        assert_eq!(f.profile, CacheProfile::Metered);
    }

    #[test]
    fn every_port_call_before_load_is_rejected() {
        // The port contract: a player with nothing loaded refuses work rather
        // than pretending. `load` is the only exception, and `poll_events`
        // only drains whatever is already queued.
        let mut f = FakePlayer::default();
        assert!(matches!(f.seek(1.0), Err(PlayerError::NotRunning(_))));
        assert!(matches!(f.set_paused(false), Err(PlayerError::NotRunning(_))));
        assert!(matches!(f.set_rate(1.5), Err(PlayerError::NotRunning(_))));
        assert!(matches!(f.set_aid(Some(2)), Err(PlayerError::NotRunning(_))));
        assert!(matches!(f.set_sid(None), Err(PlayerError::NotRunning(_))));
        assert!(matches!(f.audio_add("a.mp3", "t"), Err(PlayerError::NotRunning(_))));
        assert!(matches!(f.add_subtitle("s.srt"), Err(PlayerError::NotRunning(_))));
        assert!(matches!(f.set_sub_style(SubStyle::default()), Err(PlayerError::NotRunning(_))));
        assert!(matches!(f.set_cache_profile(CacheProfile::Metered), Err(PlayerError::NotRunning(_))));
        assert!(matches!(f.set_volume(50), Err(PlayerError::NotRunning(_))));
        assert!(matches!(f.set_mute(true), Err(PlayerError::NotRunning(_))));
        assert!(matches!(f.set_fullscreen(true), Err(PlayerError::NotRunning(_))));
        assert!(matches!(f.get_pos(), Err(PlayerError::NotRunning(_))));
        assert_eq!(f.poll_events(Duration::ZERO), vec![]);
        // A snapshot is not a command: like an idle mpv, it answers.
        let st = f.get_state().expect("get_state works without a load");
        assert_eq!(st.loaded, None);
        assert_eq!(st.duration, f.duration);
        assert!(f.load("m", 0.0, true).is_ok(), "load is allowed on a fresh player");
    }

    #[test]
    fn ready_is_delivered_exactly_once_per_load() {
        let mut f = FakePlayer::new(5.0);
        f.load("m", 0.0, false).unwrap();
        assert_eq!(f.poll_events(Duration::ZERO), loaded(5.0));
        assert_eq!(f.poll_events(Duration::ZERO), vec![], "Ready is not re-delivered");
        // A fresh load arms it again — the restart path depends on this.
        f.load("m2", 1.0, true).unwrap();
        assert_eq!(f.poll_events(Duration::ZERO), loaded(5.0));
    }

    #[test]
    fn emit_queues_events_in_order_without_a_load() {
        let mut f = FakePlayer::default();
        f.emit(PlayerEvent::Error("a".into()));
        f.emit(PlayerEvent::Ended);
        assert_eq!(
            f.poll_events(Duration::ZERO),
            vec![PlayerEvent::Error("a".into()), PlayerEvent::Ended]
        );
    }

    #[test]
    fn seek_clamps_to_the_file_and_re_arms_playback() {
        let mut f = FakePlayer::new(10.0);
        f.load("m", 0.0, false).unwrap();
        let _ = f.poll_events(Duration::ZERO);

        f.tick(20.0); // runs off the end
        assert_eq!(f.poll_events(Duration::ZERO), vec![PlayerEvent::Ended]);
        assert_eq!(f.get_pos().unwrap(), 10.0);

        f.seek(-5.0).unwrap();
        assert_eq!(f.get_pos().unwrap(), 0.0, "negative seeks clamp to 0");
        f.seek(999.0).unwrap();
        assert_eq!(f.get_pos().unwrap(), 10.0, "seeks past the end clamp to duration");
        // Our own seek reports `Seeked` carrying the *clamped* position, so a
        // consumer learns where it actually landed (ADR 0004; this is new —
        // the fake used to emit nothing here).
        assert_eq!(
            f.poll_events(Duration::ZERO),
            vec![
                PlayerEvent::Seeked { pos: 0.0 },
                PlayerEvent::Seeked { pos: 10.0 },
            ]
        );

        // Seeking back from EOF makes playback live again: time moves and a
        // second Ended is legitimate.
        f.seek(3.0).unwrap();
        assert_eq!(f.get_pos().unwrap(), 3.0);
        f.tick(20.0);
        assert_eq!(
            f.poll_events(Duration::ZERO),
            vec![PlayerEvent::Seeked { pos: 3.0 }, PlayerEvent::Ended]
        );
    }

    #[test]
    fn an_injected_user_seek_is_not_swallowed_and_keeps_its_order() {
        // The fake itself has no "user", so a `UserSeek` can only come from a
        // test driving the port directly — it must pass through untouched and
        // keep its place in line with the fake's own events. (`UserSeek` means
        // "not ours", whoever issued it — ADR 0004.)
        let mut f = FakePlayer::new(10.0);
        f.load("m", 0.0, false).unwrap();
        f.emit(PlayerEvent::UserSeek { pos: 4.0 });
        f.seek(6.0).unwrap();
        let mut expected = loaded(10.0);
        expected.push(PlayerEvent::UserSeek { pos: 4.0 });
        expected.push(PlayerEvent::Seeked { pos: 6.0 });
        assert_eq!(f.poll_events(Duration::ZERO), expected);
        assert_eq!(f.get_pos().unwrap(), 6.0, "our own seek still moved playback");
    }

    #[test]
    fn the_fake_does_not_model_a_load_position_seek() {
        // Deliberate, documented gap (ADR 0004): the real MpvPlayer credits a
        // `start > 0` load as its own seek and emits `Seeked`; the fake
        // announces the position inside `Loaded` instead, so there is no
        // transition left to report. Asserted here so the asymmetry stays
        // visible: if SyncEngine ever needs `Seeked` after a load, close this
        // gap on purpose rather than by accident.
        let mut f = FakePlayer::new(100.0);
        f.load("file:///m.mkv", 12.0, true).unwrap();
        assert_eq!(
            f.poll_events(Duration::ZERO),
            loaded(100.0),
            "a start>0 load is not reported as a seek"
        );
        // A genuine own seek right after the load *is* reported.
        f.seek(30.0).unwrap();
        assert_eq!(f.poll_events(Duration::ZERO), vec![PlayerEvent::Seeked { pos: 30.0 }]);
    }

    #[test]
    fn tick_does_nothing_until_a_file_is_loaded() {
        let mut f = FakePlayer::new(4.0);
        f.paused = false; // playing, but with nothing loaded: time still must not move
        f.tick(2.0);
        assert_eq!(f.pos, 0.0, "unloaded players do not advance");
        assert_eq!(f.poll_events(Duration::ZERO), vec![]);
    }

    /// A stall keeps running while paused: it models a network condition, not
    /// playback, and the buffering edge must still be reported.
    #[test]
    fn a_stall_completes_even_while_paused() {
        let mut f = FakePlayer::new(60.0);
        f.load("m", 0.0, true).unwrap(); // paused
        f.stall_for(1.0);
        f.tick(1.0);
        let mut expected = loaded(60.0);
        expected.push(PlayerEvent::Buffering(true));
        expected.push(PlayerEvent::Buffering(false));
        assert_eq!(f.poll_events(Duration::ZERO), expected);
        assert_eq!(f.pos, 0.0, "paused: the stall must not move time");
    }

    /// `calls` is what later phases assert call *order* on, so its exact shape
    /// is a contract.
    #[test]
    fn the_call_log_and_audio_tracks_record_what_was_asked() {
        let mut f = FakePlayer::new(10.0);
        f.load("m", 1.0, true).unwrap();
        f.audio_add("a.mp3", "Commentary").unwrap();
        f.audio_add("b.mp3", "Dub").unwrap();
        f.set_aid(Some(3)).unwrap();
        f.set_sid(None).unwrap();

        assert_eq!(
            f.audio_tracks,
            vec![
                ("a.mp3".to_string(), "Commentary".to_string()),
                ("b.mp3".to_string(), "Dub".to_string()),
            ]
        );
        assert_eq!(f.aid, Some(3));
        assert_eq!(f.sid, None);
        assert_eq!(
            f.calls,
            vec![
                "load m @1 paused=true".to_string(),
                "audio_add a.mp3".to_string(),
                "audio_add b.mp3".to_string(),
                "set_aid Some(3)".to_string(),
                "set_sid None".to_string(),
            ]
        );
    }

    #[test]
    fn a_new_fake_starts_paused_at_zero_with_a_default_duration() {
        let f = FakePlayer::default();
        assert_eq!(f.duration, 60.0);
        assert_eq!(f.pos, 0.0);
        assert!(f.paused);
        assert_eq!(f.rate, 1.0);
        assert_eq!(f.aid, None);
        assert_eq!(f.sid, None);
        assert_eq!(f.profile, CacheProfile::Normal);
        assert!(f.loaded.is_none());
        assert_eq!(f.latency, Duration::ZERO);
        assert_eq!(f.volume, 100, "mpv's default output volume");
        assert!(!f.mute);
        assert!(!f.fullscreen);
        assert!(f.tracks.is_empty());
        assert!(f.subs.is_empty());
        assert!(!f.crashed);
        assert_eq!(f.restarts, 0);
        assert_eq!(f.sub_style, SubStyle::default());
    }

    #[test]
    fn set_paused_emits_paused_but_not_user_pause() {
        let mut f = FakePlayer::new(10.0);
        f.load("m", 0.0, false).unwrap();
        let _ = f.poll_events(Duration::ZERO);
        f.set_paused(true).unwrap();
        // Our own write is state news (`Paused`), not a user action.
        assert_eq!(f.poll_events(Duration::ZERO), vec![PlayerEvent::Paused { paused: true }]);
    }

    #[test]
    fn selecting_a_track_emits_track_changed() {
        let mut f = FakePlayer::new(10.0);
        f.load("m", 0.0, false).unwrap();
        let _ = f.poll_events(Duration::ZERO);
        f.set_aid(Some(2)).unwrap();
        assert_eq!(
            f.poll_events(Duration::ZERO),
            vec![PlayerEvent::TrackChanged { aid: Some(2), sid: None }]
        );
        f.set_sid(Some(4)).unwrap();
        assert_eq!(
            f.poll_events(Duration::ZERO),
            vec![PlayerEvent::TrackChanged { aid: Some(2), sid: Some(4) }]
        );
    }

    #[test]
    fn adding_a_subtitle_selects_it_and_reports_the_track() {
        let mut f = FakePlayer::new(10.0);
        f.load("m", 0.0, false).unwrap();
        let _ = f.poll_events(Duration::ZERO);
        f.add_subtitle("s.srt").unwrap();
        assert_eq!(f.sid, Some(1), "`sub-add … select` makes it current");
        assert_eq!(f.subs, vec!["s.srt".to_string()]);
        assert!(f.tracks.iter().any(|t| t.kind == "sub" && t.id == 1));
        assert_eq!(
            f.poll_events(Duration::ZERO),
            vec![PlayerEvent::TrackChanged { aid: Some(1), sid: Some(1) }]
        );
    }

    #[test]
    fn audio_add_appends_a_track_without_selecting_it() {
        let mut f = FakePlayer::new(10.0);
        f.load("m", 0.0, false).unwrap();
        let _ = f.poll_events(Duration::ZERO);
        f.audio_add("a.mp3", "Commentary").unwrap();
        assert_eq!(f.aid, Some(1), "the current track is unchanged");
        assert!(f
            .tracks
            .iter()
            .any(|t| t.kind == "audio" && t.title.as_deref() == Some("Commentary")));
        assert_eq!(f.poll_events(Duration::ZERO), vec![], "no selection moved");
    }

    #[test]
    fn output_controls_are_recorded_and_volume_is_clamped() {
        let mut f = FakePlayer::new(10.0);
        f.load("m", 0.0, false).unwrap();
        f.set_volume(255).unwrap(); // clamped to the 0–100 scale
        f.set_mute(true).unwrap();
        f.set_fullscreen(true).unwrap();
        assert_eq!(f.volume, 100);
        assert!(f.mute);
        assert!(f.fullscreen);
        assert_eq!(f.calls.iter().filter(|c| c.starts_with("set_volume")).count(), 1);
        assert!(f.calls.iter().any(|c| c == "set_volume 100"), "{:?}", f.calls);
    }

    #[test]
    fn get_state_reports_the_whole_snapshot() {
        let mut f = FakePlayer::new(30.0);
        f.load("file:///m.mkv", 5.0, true).unwrap();
        f.set_rate(1.5).unwrap();
        f.set_volume(60).unwrap();
        f.set_mute(true).unwrap();
        f.set_cache_profile(CacheProfile::DataSaver).unwrap();
        f.add_subtitle("s.srt").unwrap();

        let st = f.get_state().unwrap();
        assert_eq!(st.pos, 5.0);
        assert_eq!(st.duration, 30.0);
        assert!(st.paused);
        assert_eq!(st.rate, 1.5);
        assert_eq!(st.aid, Some(1));
        assert_eq!(st.sid, Some(1));
        assert_eq!(st.volume, 60);
        assert!(st.mute);
        assert!(!st.fullscreen);
        assert_eq!(st.profile, CacheProfile::DataSaver);
        assert_eq!(st.buffered_secs, 25.0, "a local file is buffered to the end");
        assert_eq!(st.tracks.len(), 3, "video + audio + the added sub");
        assert_eq!(st.loaded.as_deref(), Some("file:///m.mkv"));
    }

    #[test]
    fn a_crash_fails_commands_and_queues_process_crashed() {
        let mut f = FakePlayer::new(10.0);
        f.load("m", 2.0, false).unwrap();
        let _ = f.poll_events(Duration::ZERO);

        f.crash();
        assert_eq!(
            f.poll_events(Duration::ZERO),
            vec![PlayerEvent::ProcessCrashed { restarts: 1 }]
        );
        assert!(matches!(f.seek(3.0), Err(PlayerError::NotRunning(_))));
        assert!(matches!(f.set_volume(10), Err(PlayerError::NotRunning(_))));
        assert_eq!(f.restarts, 1);
    }

    #[test]
    fn recover_restores_the_state_and_reannounces_the_file() {
        let mut f = FakePlayer::new(10.0);
        f.load("m", 4.0, false).unwrap();
        f.set_volume(30).unwrap();
        f.set_rate(2.0).unwrap();
        let _ = f.poll_events(Duration::ZERO);

        f.crash();
        let _ = f.poll_events(Duration::ZERO);
        f.recover();

        // The file announces itself again, metadata before Ready, with the
        // tracks it already had.
        assert_eq!(f.poll_events(Duration::ZERO), loaded(10.0));

        let st = f.get_state().unwrap();
        assert_eq!(st.pos, 4.0, "position survives");
        assert_eq!(st.volume, 30, "volume survives");
        assert_eq!(st.rate, 2.0, "rate survives");
        assert!(f.load("m", 4.0, false).is_ok(), "commands work again");
    }

    #[test]
    fn latency_delays_a_command_without_changing_its_result() {
        let mut f = FakePlayer::new(10.0);
        f.load("m", 0.0, false).unwrap();
        f.latency = Duration::from_millis(60);
        let started = std::time::Instant::now();
        f.seek(3.0).unwrap();
        assert!(
            started.elapsed() >= Duration::from_millis(60),
            "the call must wait out the injected latency, took {:?}",
            started.elapsed()
        );
        assert_eq!(f.get_pos().unwrap(), 3.0, "the result is unchanged");
    }

    #[test]
    fn load_clears_events_from_the_previous_file() {
        let mut f = FakePlayer::new(10.0);
        f.load("m", 0.0, false).unwrap();
        f.emit(PlayerEvent::Error("stale".into()));
        f.load("m2", 0.0, false).unwrap();
        // The stale event belonged to the old file and must not leak through.
        assert_eq!(f.poll_events(Duration::ZERO), loaded(10.0));
    }
}
