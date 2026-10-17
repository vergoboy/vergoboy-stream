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

/// One entry of mpv's `track-list`: a track the current file (or an
/// `audio_add`/`add_subtitle`) provides.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TrackInfo {
    /// mpv's track id — what `set_aid`/`set_sid` take.
    pub id: i64,
    /// mpv's `type` field: `"video"`, `"audio"` or `"sub"`.
    pub kind: String,
    pub title: Option<String>,
    pub lang: Option<String>,
}

/// Subtitle appearance. Every field is optional and `None` means *leave that
/// property alone* — the port never resets a value the caller did not mention.
/// Colours are mpv colour strings (`#RRGGBB` / `#AARRGGBB`).
#[derive(Debug, Clone, PartialEq, Default)]
pub struct SubStyle {
    pub scale: Option<f64>,
    pub color: Option<String>,
    pub border_color: Option<String>,
    pub back_color: Option<String>,
}

/// A full snapshot of the player, for the UI's first paint and for a re-sync
/// after a gap in events. Reading it costs a handful of property round trips.
#[derive(Debug, Clone, PartialEq)]
pub struct PlayerState {
    pub pos: f64,
    /// `0.0` when the player is idle or the file does not report a duration.
    pub duration: f64,
    pub paused: bool,
    pub rate: f64,
    pub aid: Option<i64>,
    pub sid: Option<i64>,
    /// 0–100, the scale `set_volume` takes.
    pub volume: u8,
    pub mute: bool,
    pub fullscreen: bool,
    pub profile: CacheProfile,
    /// Seconds of media buffered ahead of the playhead.
    pub buffered_secs: f64,
    pub tracks: Vec<TrackInfo>,
    /// The URL currently open, if any.
    pub loaded: Option<String>,
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
    /// The player failed at something the caller should see (bad URL, a
    /// restart budget spent). Fatal for the current attempt; a *recoverable*
    /// process death is [`PlayerEvent::ProcessCrashed`] instead.
    Error(String),
    /// A seek the *user* started (drag/keyboard/click), as opposed to one we
    /// requested — the sync layer must broadcast these, not swallow them.
    UserSeek { pos: f64 },
    /// A pause toggle that came from the player itself (media key, mpv input
    /// script), not from our own `set_paused`.
    UserPause { paused: bool },

    // ── added for the mpv player task (S1) ──────────────────────────────────
    /// A file is open and its metadata is known. Sent before [`Ready`] for the
    /// same file, and again after a restart.
    ///
    /// [`Ready`]: PlayerEvent::Ready
    Loaded {
        duration: f64,
        tracks: Vec<TrackInfo>,
    },
    /// Steady-state position push. The observed `time-pos` is throttled, so
    /// this arrives at most every ~0.5 s — precise positions still come from
    /// `get_pos`/the event that carries one.
    Position { pos: f64 },
    /// *Every* pause change, whatever caused it. Player-originated ones also
    /// emit [`UserPause`], which is what the sync layer broadcasts.
    ///
    /// [`UserPause`]: PlayerEvent::UserPause
    Paused { paused: bool },
    /// Cache/buffer telemetry: seconds buffered ahead of the playhead.
    CacheState { buffered_secs: f64 },
    /// A seek *we* asked for completed (`UserSeek` is the user's).
    Seeked { pos: f64 },
    /// The selected audio/subtitle tracks changed — by our own selection, by
    /// an added track, or by mpv itself.
    TrackChanged { aid: Option<i64>, sid: Option<i64> },
    /// mpv died and was restarted; `restarts` counts the attempts in a row.
    /// Once the budget is spent the port reports [`Error`] and stops.
    ///
    /// [`Error`]: PlayerEvent::Error
    ProcessCrashed { restarts: u32 },
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
            PlayerEvent::Loaded { duration, tracks } => {
                write!(f, "loaded({duration}s, {} tracks)", tracks.len())
            }
            PlayerEvent::Position { pos } => write!(f, "position({pos})"),
            PlayerEvent::Paused { paused } => write!(f, "paused({paused})"),
            PlayerEvent::CacheState { buffered_secs } => write!(f, "cache({buffered_secs})"),
            PlayerEvent::Seeked { pos } => write!(f, "seeked({pos})"),
            PlayerEvent::TrackChanged { aid, sid } => write!(f, "tracks(aid={aid:?}, sid={sid:?})"),
            PlayerEvent::ProcessCrashed { restarts } => write!(f, "process-crashed({restarts})"),
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
    /// Add a subtitle file/URL and select it.
    fn add_subtitle(&mut self, url: &str) -> PlayerResult<()>;
    /// Set subtitle appearance; fields left `None` are untouched.
    fn set_sub_style(&mut self, style: SubStyle) -> PlayerResult<()>;
    /// Switch buffering profile at runtime.
    fn set_cache_profile(&mut self, profile: CacheProfile) -> PlayerResult<()>;
    /// Output volume, 0–100 (values above 100 are clamped).
    fn set_volume(&mut self, level: u8) -> PlayerResult<()>;
    fn set_mute(&mut self, on: bool) -> PlayerResult<()>;
    fn set_fullscreen(&mut self, on: bool) -> PlayerResult<()>;
    /// Current position in seconds.
    fn get_pos(&mut self) -> PlayerResult<f64>;
    /// A full snapshot: position, tracks, volume, profile, cache. Every field
    /// the player cannot answer for defaults (see [`PlayerState`]) rather than
    /// failing — this is a paint, not a command.
    fn get_state(&mut self) -> PlayerResult<PlayerState>;
    /// Drain events that arrived since the last call, waiting up to `timeout`
    /// for the first one. Returns an empty vec on timeout.
    fn poll_events(&mut self, timeout: Duration) -> Vec<PlayerEvent>;
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// The profiles are the product decision (how much RAM/bandwidth a viewer
    /// spends) and mpv receives them verbatim — so pin every number. A profile
    /// that silently drifts is a metered user's data plan.
    #[test]
    fn cache_profiles_are_exact_property_maps() {
        assert_eq!(
            CacheProfile::Normal.properties(),
            vec![
                ("demuxer-max-bytes", json!(150 * 1024 * 1024)),
                ("demuxer-readahead-secs", json!(1.0)),
                ("demuxer-max-back-bytes", json!(50 * 1024 * 1024)),
                ("hr-seek", json!("yes")),
            ]
        );
        assert_eq!(
            CacheProfile::DataSaver.properties(),
            vec![
                ("demuxer-max-bytes", json!(20 * 1024 * 1024)),
                ("demuxer-readahead-secs", json!(0.5)),
                ("demuxer-max-back-bytes", json!(10 * 1024 * 1024)),
                ("hr-seek", json!("no")),
            ]
        );
        assert_eq!(
            CacheProfile::Metered.properties(),
            vec![
                ("demuxer-max-bytes", json!(8 * 1024 * 1024)),
                ("demuxer-readahead-secs", json!(0.2)),
                ("demuxer-max-back-bytes", json!(4 * 1024 * 1024)),
                ("hr-seek", json!("no")),
            ]
        );
        // The ordering is constrained by design: only the constrained profiles
        // give up exact seeking.
        for (i, profile) in [CacheProfile::Normal, CacheProfile::DataSaver, CacheProfile::Metered]
            .into_iter()
            .enumerate()
        {
            let props = profile.properties();
            assert_eq!(props.len(), 4, "profile {i} changed shape");
            assert_eq!(props[0].0, "demuxer-max-bytes");
            assert_eq!(props[3].0, "hr-seek");
        }
    }

    /// These strings reach logs and the WebView's error report, so they are a
    /// contract, not a formatting detail.
    #[test]
    fn event_displays_are_stable() {
        assert_eq!(PlayerEvent::Ready.to_string(), "ready");
        assert_eq!(PlayerEvent::Buffering(true).to_string(), "buffering(true)");
        assert_eq!(PlayerEvent::Buffering(false).to_string(), "buffering(false)");
        assert_eq!(PlayerEvent::Ended.to_string(), "ended");
        assert_eq!(PlayerEvent::Error("boom".into()).to_string(), "error: boom");
        assert_eq!(PlayerEvent::UserSeek { pos: 12.5 }.to_string(), "user-seek(12.5)");
        assert_eq!(PlayerEvent::UserPause { paused: true }.to_string(), "user-pause(true)");
        assert_eq!(
            PlayerEvent::Loaded { duration: 300.0, tracks: vec![] }.to_string(),
            "loaded(300s, 0 tracks)"
        );
        assert_eq!(PlayerEvent::Position { pos: 4.5 }.to_string(), "position(4.5)");
        assert_eq!(PlayerEvent::Paused { paused: false }.to_string(), "paused(false)");
        assert_eq!(
            PlayerEvent::CacheState { buffered_secs: 2.25 }.to_string(),
            "cache(2.25)"
        );
        assert_eq!(PlayerEvent::Seeked { pos: 9.0 }.to_string(), "seeked(9)");
        assert_eq!(
            PlayerEvent::TrackChanged { aid: Some(2), sid: None }.to_string(),
            "tracks(aid=Some(2), sid=None)"
        );
        assert_eq!(
            PlayerEvent::ProcessCrashed { restarts: 2 }.to_string(),
            "process-crashed(2)"
        );
    }

    #[test]
    fn error_displays_are_stable() {
        assert_eq!(
            PlayerError::NotRunning("gone".into()).to_string(),
            "player not running: gone"
        );
        assert_eq!(
            PlayerError::Timeout("slow".into()).to_string(),
            "player timed out: slow"
        );
        assert_eq!(
            PlayerError::Rejected { command: "seek".into(), detail: "bad".into() }.to_string(),
            "player rejected seek: bad"
        );
        assert_eq!(
            PlayerError::Protocol("dropped".into()).to_string(),
            "player protocol error: dropped"
        );
        assert_eq!(
            PlayerError::Unsupported("nope".into()).to_string(),
            "unsupported: nope"
        );
        // Callers above the crate rely on this bound (the command layer maps it
        // into its own error type).
        fn is_std_error<E: std::error::Error>() {}
        is_std_error::<PlayerError>();
    }
}
