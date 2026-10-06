//! The mpv-agnostic surface of the player.
//!
//! Everything a caller (Tauri command, UI, later phases) may ask of a player
//! lives here and mentions mpv nowhere: [`PlayerPort`] is the seam the webview
//! wiring talks to, [`FakePlayer`] implements it with virtual time, and
//! `MpvPlayer` implements it over a spawned process. Swapping one for the other
//! must not change a single line above this module.

use std::fmt;
use std::time::Duration;

/// Cache/buffering profile, changeable at runtime.
///
/// The *values* below are mpv properties, but the choice of profile is a
/// product decision (normal / data-saver / metered) and belongs with the port.
/// `hr-seek=no` on the constrained profiles means mpv picks the nearest
/// keyframe instead of decoding up to the exact target while the network is
/// already the bottleneck.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CacheProfile {
    Normal,
    DataSaver,
    Metered,
}

impl CacheProfile {
    /// `(property, value)` pairs to push to the player.
    pub fn properties(&self) -> Vec<(&'static str, serde_json::Value)> {
        use serde_json::json;
        match self {
            CacheProfile::Normal => vec![
                ("demuxer-max-bytes", json!(150 * 1024 * 1024)),
                ("demuxer-readahead-secs", json!(1.0)),
                ("demuxer-max-back-bytes", json!(50 * 1024 * 1024)),
                ("hr-seek", json!("yes")),
            ],
            CacheProfile::DataSaver => vec![
                ("demuxer-max-bytes", json!(20 * 1024 * 1024)),
                ("demuxer-readahead-secs", json!(0.5)),
                ("demuxer-max-back-bytes", json!(10 * 1024 * 1024)),
                ("hr-seek", json!("no")),
            ],
            CacheProfile::Metered => vec![
                ("demuxer-max-bytes", json!(8 * 1024 * 1024)),
                ("demuxer-readahead-secs", json!(0.2)),
                ("demuxer-max-back-bytes", json!(4 * 1024 * 1024)),
                ("hr-seek", json!("no")),
            ],
        }
    }
}

/// Something the player wants the UI to know about.
#[derive(Debug, Clone, PartialEq)]
pub enum PlayerEvent {
    /// A file is loaded and playback can start (or resume after a restart).
    Ready,
    /// `true` while the buffer is starving, `false` once it refilled.
    Buffering(bool),
    /// Playback reached the end of the file.
    Ended,
    /// The player failed at something the caller should see (bad URL, crash,
    /// restart). Fatal for the current attempt, not necessarily for the port.
    Error(String),
    /// A seek the *user* started (drag/keyboard/click), as opposed to one we
    /// requested — the sync layer must broadcast these, not swallow them.
    UserSeek { pos: f64 },
    /// A pause toggle that came from the player itself (media key, mpv input
    /// script), not from our own `set_paused`.
    UserPause { paused: bool },
}

impl fmt::Display for PlayerEvent {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            PlayerEvent::Ready => write!(f, "ready"),
            PlayerEvent::Buffering(b) => write!(f, "buffering({b})"),
            PlayerEvent::Ended => write!(f, "ended"),
            PlayerEvent::Error(e) => write!(f, "error: {e}"),
            PlayerEvent::UserSeek { pos } => write!(f, "user-seek({pos})"),
            PlayerEvent::UserPause { paused } => write!(f, "user-pause({paused})"),
        }
    }
}

/// Everything that can go wrong driving a player.
#[derive(Debug)]
pub enum PlayerError {
    /// The player process/binary is gone or never started.
    NotRunning(String),
    /// A command timed out waiting for a reply.
    Timeout(String),
    /// The player answered with an error (bad URL, invalid argument, …).
    Rejected { command: String, detail: String },
    /// Transport/protocol failure (socket dropped, malformed line).
    Protocol(String),
    /// The implementation refuses this operation right now.
    Unsupported(String),
}

impl fmt::Display for PlayerError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            PlayerError::NotRunning(m) => write!(f, "player not running: {m}"),
            PlayerError::Timeout(m) => write!(f, "player timed out: {m}"),
            PlayerError::Rejected { command, detail } => {
                write!(f, "player rejected {command}: {detail}")
            }
            PlayerError::Protocol(m) => write!(f, "player protocol error: {m}"),
            PlayerError::Unsupported(m) => write!(f, "unsupported: {m}"),
        }
    }
}

impl std::error::Error for PlayerError {}

pub type PlayerResult<T> = Result<T, PlayerError>;

/// The player surface every layer above this crate codes against.
///
/// Synchronous and deliberately small: each call either reaches the player or
/// returns a [`PlayerError`]. Events are pulled, never pushed into a callback,
/// so a caller can drive the port from one thread (Tauri command handler, test
/// loop, future sync layer).
pub trait PlayerPort {
    /// Open `url` (file path or http(s) URL). `start` is the position in
    /// seconds, `paused` whether to start without playing.
    fn load(&mut self, url: &str, start: f64, paused: bool) -> PlayerResult<()>;
    /// Seek to an absolute position in seconds.
    fn seek(&mut self, pos: f64) -> PlayerResult<()>;
    fn set_paused(&mut self, paused: bool) -> PlayerResult<()>;
    /// Playback rate (1.0 = normal).
    fn set_rate(&mut self, rate: f64) -> PlayerResult<()>;
    /// Select an audio track by id (`None` = automatic).
    fn set_aid(&mut self, id: Option<i64>) -> PlayerResult<()>;
    /// Select a subtitle track by id (`None` = disabled).
    fn set_sid(&mut self, id: Option<i64>) -> PlayerResult<()>;
    /// Add an external audio file to the current playback.
    fn audio_add(&mut self, url: &str, title: &str) -> PlayerResult<()>;
    /// Switch buffering profile at runtime.
    fn set_cache_profile(&mut self, profile: CacheProfile) -> PlayerResult<()>;
    /// Current position in seconds.
    fn get_pos(&mut self) -> PlayerResult<f64>;
    /// Drain events that arrived since the last call, waiting up to `timeout`
    /// for the first one. Returns an empty vec on timeout.
    fn poll_events(&mut self, timeout: Duration) -> Vec<PlayerEvent>;
}
