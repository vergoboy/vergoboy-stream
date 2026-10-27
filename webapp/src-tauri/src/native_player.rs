//! Starts the w2g mpv player (the mpv fork + sync plugin) in its own window on behalf of the web UI.
//!
//! The web UI already owns login, room and media state, so it hands over everything the player
//! needs (sync URL/token, media URL). This module only spawns and watches the process; mpv and the
//! plugin are explicit paths (`W2G_MPV` / `W2G_PLUGIN`, else the package's own directory
//! `/usr/lib/<executable name>/`, else next to this executable) — never a PATH lookup.
use serde::Deserialize;
use std::path::{Path, PathBuf};
use std::process::{Child, Command};
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tauri::{AppHandle, Emitter, State};

#[derive(Default, Clone)]
pub struct NativePlayer(Arc<Mutex<Option<Child>>>);

#[derive(Deserialize, Debug)]
#[serde(rename_all = "camelCase")]
pub struct StartArgs {
    pub sync_url: String,
    pub sync_token: String,
    pub room: String,
    /// "controller" | "watcher" (the server enforces it; this only gates the local UI behaviour)
    pub role: String,
    pub media_url: String,
    pub title: String,
    #[serde(default)]
    pub access_token: Option<String>,
}

fn sibling(name: &str) -> PathBuf {
    std::env::current_exe().ok().and_then(|p| p.parent().map(|d| d.join(name))).unwrap_or_else(|| name.into())
}

/// Where the player is looked for, in order, for an executable at `exe`.
///
/// The package's own directory comes first: the client installs as
/// `/usr/bin/vergoboy-<instance>` and its mpv + `w2g.so` as
/// `/usr/lib/vergoboy-<instance>/`, so one pacman package provides the whole
/// player and two instances (or the distro's own `/usr/bin/mpv`) never fight
/// over a path. The executable's own directory stays as the fallback, which is
/// how a working-tree build (`target/release/`) and a system mpv are found.
fn player_dirs(exe: &Path) -> Vec<PathBuf> {
    let mut dirs = Vec::new();
    if let (Some(bin), Some(stem)) = (exe.parent(), exe.file_name()) {
        if let Some(prefix) = bin.parent() {
            dirs.push(prefix.join("lib").join(stem));
        }
        dirs.push(bin.to_path_buf());
    }
    dirs
}

/// Pure: what `resolve` decides for an executable at `exe`. Split out so the
/// lookup order can be tested without moving the test binary around.
fn resolve_at(exe: &Path, name: &str) -> PathBuf {
    let dirs = player_dirs(exe);
    for dir in &dirs {
        let candidate = dir.join(name);
        if candidate.is_file() {
            return candidate;
        }
    }
    // Nothing there yet: name the path a packaged install would use, so the
    // failure message points at the file that is actually missing.
    dirs.last().map(|d| d.join(name)).unwrap_or_else(|| PathBuf::from(name))
}

/// `W2G_MPV` / `W2G_PLUGIN` win when set and non-blank (a blank override is a
/// mistake, not a path); otherwise the first of [`player_dirs`] that exists.
fn resolve(var: &str, unix: &str, windows: &str) -> PathBuf {
    let name = if cfg!(windows) { windows } else { unix };
    match std::env::var(var) {
        Ok(explicit) if !explicit.trim().is_empty() => PathBuf::from(explicit),
        _ => {
            let exe = std::env::current_exe().unwrap_or_else(|_| sibling(name));
            resolve_at(&exe, name)
        }
    }
}

/// The player this app may spawn: the mpv fork and the sync plugin that talks to
/// the room's sync server (both inside this app's package).
pub fn paths() -> (PathBuf, PathBuf) {
    (resolve("W2G_MPV", "mpv", "mpv.exe"), resolve("W2G_PLUGIN", "w2g.so", "w2g.dll"))
}

/// Pure: the exact command line and environment for the player.
pub fn command_line(plugin: &Path, a: &StartArgs) -> (Vec<String>, Vec<(String, String)>) {
    let mut args = vec![
        "--no-config".to_string(), // never depend on a user's mpv.conf
        format!("--script={}", plugin.display()),
        format!("--force-media-title={}", a.title),
        "--keep-open=yes".into(),
        "--pause".into(), // start paused; the room decides when to play
    ];
    if let Some(t) = a.access_token.as_deref().filter(|t| !t.is_empty()) {
        args.push(format!("--http-header-fields=Authorization: Bearer {t}"));
    }
    args.push("--".into()); // the URL can never be parsed as an option
    args.push(a.media_url.clone());
    let role = if a.role == "controller" { "controller" } else { "watcher" };
    let env = vec![
        ("W2G_URL".into(), a.sync_url.clone()),
        ("W2G_TOKEN".into(), a.sync_token.clone()),
        ("W2G_ROOM".into(), a.room.clone()),
        ("W2G_ROLE".into(), role.into()),
    ];
    (args, env)
}

/// Lets the web UI write into the app's own log (`app.log`), so what happens in the webview can be
/// diagnosed from the same file as everything else. Level and size are bounded; newlines are removed.
#[tauri::command]
pub fn client_log(level: String, message: String) {
    let level = match level.as_str() { "debug" | "info" | "warn" | "error" => level.as_str(), _ => "info" };
    let one_line: String = message.chars().filter(|c| !c.is_control()).take(500).collect();
    crate::log(level, &format!("[web] {one_line}"));
}

#[tauri::command]
pub fn native_player_available() -> bool {
    let (mpv, plugin) = paths();
    mpv.is_file() && plugin.is_file()
}

#[tauri::command]
pub fn native_player_start(app: AppHandle, state: State<'_, NativePlayer>, args: StartArgs) -> Result<(), String> {
    // Every refusal is logged here, not only returned: the UI shows it as a short toast that is easy to miss.
    let fail = |msg: String| -> Result<(), String> {
        crate::log("warn", &format!("native player refused: {msg}"));
        Err(msg)
    };
    crate::log("info", &format!("native player: start requested room={} role={} media={}", args.room, args.role,
                                args.media_url.split('?').next().unwrap_or("")));
    let (mpv, plugin) = paths();
    for (what, p) in [("mpv", &mpv), ("plugin", &plugin)] {
        if !p.is_file() {
            return fail(format!("{what} not found at {} (set W2G_{})", p.display(), what.to_uppercase()));
        }
    }
    let slot = state.0.clone();
    let mut guard = slot.lock().map_err(|_| "player state poisoned".to_string())?;
    if let Some(child) = guard.as_mut() {
        if matches!(child.try_wait(), Ok(None)) {
            return fail("the player is already running".into());
        }
    }
    let (argv, env) = command_line(&plugin, &args);
    let child = match Command::new(&mpv).args(&argv).envs(env).spawn() {
        Ok(c) => c,
        Err(e) => return fail(format!("cannot start {}: {e}", mpv.display())),
    };
    crate::log("info", &format!("native player started: room={} role={}", args.room, args.role));
    *guard = Some(child);
    drop(guard);

    // Watcher: clears the slot and tells the UI when the player closes (however it closes).
    std::thread::spawn(move || loop {
        std::thread::sleep(Duration::from_millis(500));
        let Ok(mut g) = slot.lock() else { return };
        let done = match g.as_mut() {
            None => return, // stopped elsewhere
            Some(c) => c.try_wait().ok().flatten(),
        };
        if let Some(status) = done {
            *g = None;
            drop(g);
            crate::log("info", &format!("native player exited: {status}"));
            let _ = app.emit("native-player-exit", status.success());
            return;
        }
    });
    Ok(())
}

#[tauri::command]
pub fn native_player_stop(state: State<'_, NativePlayer>) -> Result<(), String> {
    let mut g = state.0.lock().map_err(|_| "player state poisoned".to_string())?;
    if let Some(mut c) = g.take() {
        let _ = c.kill();
        let _ = c.wait();
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(role: &str, token: Option<&str>) -> StartArgs {
        StartArgs {
            sync_url: "wss://vergoboy.ir/w2g-sync/".into(), sync_token: "v1.p.s".into(), room: "R1".into(),
            role: role.into(), media_url: "https://vergoboy.ir/w2g/media/hls/a/master.m3u8".into(),
            title: "Film".into(), access_token: token.map(String::from),
        }
    }

    #[test]
    fn url_is_last_and_after_a_double_dash() {
        let (argv, _) = command_line(Path::new("/opt/w2g.so"), &args("controller", None));
        assert_eq!(argv[argv.len() - 2], "--");
        assert_eq!(argv.last().unwrap(), "https://vergoboy.ir/w2g/media/hls/a/master.m3u8");
        assert!(argv.contains(&"--no-config".to_string()) && argv.contains(&"--pause".to_string()));
        assert!(argv.contains(&"--script=/opt/w2g.so".to_string()));
    }

    #[test]
    fn a_hostile_title_or_url_cannot_become_an_option() {
        let mut a = args("controller", None);
        a.media_url = "--script=/tmp/evil.lua".into();
        a.title = "--vo=null".into();
        let (argv, _) = command_line(Path::new("p"), &a);
        assert_eq!(argv.last().unwrap(), "--script=/tmp/evil.lua");
        assert_eq!(argv[argv.len() - 2], "--"); // everything after `--` is a file/URL, not an option
        assert!(argv.contains(&"--force-media-title=--vo=null".to_string())); // one argv element, value only
    }

    #[test]
    fn role_is_normalised_and_token_header_is_optional() {
        let (a1, e1) = command_line(Path::new("p"), &args("controller", Some("ACC")));
        assert!(e1.contains(&("W2G_ROLE".into(), "controller".into())));
        assert!(a1.contains(&"--http-header-fields=Authorization: Bearer ACC".to_string()));
        let (a2, e2) = command_line(Path::new("p"), &args("anything", Some("")));
        assert!(e2.contains(&("W2G_ROLE".into(), "watcher".into())));
        assert!(!a2.iter().any(|x| x.starts_with("--http-header-fields")));
    }

    #[test]
    fn client_log_accepts_any_input_without_panicking() {
        client_log("info".into(), "a\nb\x1b[31m".repeat(1000));
        client_log("nonsense".into(), String::new());
    }

    #[test]
    fn the_package_directory_is_searched_before_the_executable_directory() {
        let dirs = player_dirs(Path::new("/usr/bin/vergoboy-stream"));
        assert_eq!(dirs, vec![PathBuf::from("/usr/lib/vergoboy-stream"), PathBuf::from("/usr/bin")]);
        // An executable with no directory in it: the bare name is all that is left.
        assert_eq!(resolve_at(Path::new("vergoboy-stream"), "mpv"), PathBuf::from("mpv"));
    }

    #[test]
    fn the_packaged_player_wins_over_a_sibling_one() {
        let root = std::env::temp_dir().join(format!("w2g-paths-{}", std::process::id()));
        let exe = root.join("bin/vergoboy-stream");
        let packaged = root.join("lib/vergoboy-stream/w2g.so");
        let sibling = root.join("bin/w2g.so");
        for p in [&packaged, &sibling] {
            std::fs::create_dir_all(p.parent().unwrap()).unwrap();
            std::fs::write(p, b"").unwrap();
        }
        assert_eq!(resolve_at(&exe, "w2g.so"), packaged);
        std::fs::remove_file(&packaged).unwrap();
        assert_eq!(resolve_at(&exe, "w2g.so"), sibling, "the sibling is the fallback");
        std::fs::remove_file(&sibling).unwrap();
        assert_eq!(resolve_at(&exe, "w2g.so"), sibling, "a missing player is reported by path");
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn camel_case_json_from_the_web_ui_parses() {
        let a: StartArgs = serde_json::from_str(
            r#"{"syncUrl":"wss://h/s","syncToken":"t","room":"R","role":"watcher","mediaUrl":"u","title":"T"}"#).unwrap();
        assert_eq!((a.sync_url.as_str(), a.role.as_str(), a.access_token), ("wss://h/s", "watcher", None));
    }
}
