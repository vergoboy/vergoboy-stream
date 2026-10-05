//! mpv process lifecycle and the mpv-agnostic player port.
//!
//! Layering, downward only:
//!
//! * [`port`] — the trait, events, errors, cache profiles. No mpv, no I/O.
//! * [`fake`] — `FakePlayer`, a [`port::PlayerPort`] with virtual time.
//! * [`ipc`] — JSON request/response over the mpv socket; command-agnostic.
//! * [`process`] — spawn/kill/reap the mpv binary; command-agnostic.
//! * [`mpv`] — `MpvPlayer`: the only module that knows mpv's protocol.
//!
//! Nothing here depends on Tauri: the webview wiring in `src-tauri` sits on
//! top of [`port::PlayerPort`] like every other caller.

pub mod fake;
pub mod ipc;
pub mod mpv;
pub mod port;
pub mod process;

pub use fake::FakePlayer;
pub use mpv::MpvPlayer;
pub use port::{CacheProfile, PlayerError, PlayerEvent, PlayerPort, PlayerResult};
pub use process::{locate_mpv, MpvOptions, MpvProcess};
