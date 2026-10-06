//! `FakePlayer` — a [`PlayerPort`] with virtual time.
//!
//! Later phases (sync, cache profiles, UI) need a player that can be paused,
//! stalled, made slow and made to fail *on demand*, without a process or a
//! codec. Virtual time only moves when a test calls [`FakePlayer::tick`], so
//! tests are deterministic and take microseconds instead of seconds.

use std::collections::VecDeque;
use std::time::Duration;

use crate::port::{CacheProfile, PlayerError, PlayerEvent, PlayerPort, PlayerResult};

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
    /// Every command seen, for assertions on call order.
    pub calls: Vec<String>,

    /// Artificial round-trip latency applied *before* a command takes effect:
    /// the call still returns after `latency`, i.e. it is synchronous, but the
    /// observable state changes at the end of it.
    pub latency: Duration,
    /// Commands still owed a failure (popped front-to-back).
    fail_next: VecDeque<String>,
    /// Seconds still left in a simulated stall: time does not advance and
    /// `Buffering(true)` stays latched.
    stall_for: f64,
    buffering: bool,
    ended: bool,
    events: VecDeque<PlayerEvent>,
    ready_pending: bool,
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
            calls: Vec::new(),
            latency: Duration::ZERO,
            fail_next: VecDeque::new(),
            stall_for: 0.0,
            buffering: false,
            ended: false,
            events: VecDeque::new(),
            ready_pending: false,
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

    fn guard(&mut self, op: &str) -> PlayerResult<()> {
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
        self.loaded = Some(FakeLoad { url: url.to_string(), start, paused });
        self.pos = start;
        self.paused = paused;
        self.ended = false;
        self.ready_pending = true;
        Ok(())
    }

    fn seek(&mut self, pos: f64) -> PlayerResult<()> {
        self.guard("seek")?;
        self.calls.push(format!("seek {pos}"));
        self.pos = pos.clamp(0.0, self.duration);
        self.ended = false;
        Ok(())
    }

    fn set_paused(&mut self, paused: bool) -> PlayerResult<()> {
        self.guard("set_paused")?;
        self.calls.push(format!("set_paused {paused}"));
        self.paused = paused;
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
        Ok(())
    }

    fn set_sid(&mut self, id: Option<i64>) -> PlayerResult<()> {
        self.guard("set_sid")?;
        self.calls.push(format!("set_sid {id:?}"));
        self.sid = id;
        Ok(())
    }

    fn audio_add(&mut self, url: &str, title: &str) -> PlayerResult<()> {
        self.guard("audio_add")?;
        self.calls.push(format!("audio_add {url}"));
        self.audio_tracks.push((url.to_string(), title.to_string()));
        Ok(())
    }

    fn set_cache_profile(&mut self, profile: CacheProfile) -> PlayerResult<()> {
        self.guard("set_cache_profile")?;
        self.calls.push(format!("set_cache_profile {profile:?}"));
        self.profile = profile;
        Ok(())
    }

    fn get_pos(&mut self) -> PlayerResult<f64> {
        self.guard("get_pos")?;
        Ok(self.pos)
    }

    fn poll_events(&mut self, _timeout: Duration) -> Vec<PlayerEvent> {
        if self.ready_pending {
            self.ready_pending = false;
            self.events.push_front(PlayerEvent::Ready);
        }
        self.events.drain(..).collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn load_reports_ready_and_starts_paused() {
        let mut f = FakePlayer::new(100.0);
        f.load("file:///m.mkv", 12.0, true).unwrap();
        assert_eq!(f.poll_events(Duration::ZERO), vec![PlayerEvent::Ready]);
        assert_eq!(f.get_pos().unwrap(), 12.0);
        assert!(f.paused);
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
        let evs = f.poll_events(Duration::ZERO);
        assert_eq!(
            evs,
            vec![
                PlayerEvent::Ready,
                PlayerEvent::Buffering(true),
                PlayerEvent::Buffering(false)
            ]
        );
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
}
