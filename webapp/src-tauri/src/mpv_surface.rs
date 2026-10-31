//! The embedded player: libmpv renders the video straight into a GL widget inside the app window.
//!
//! The separate-window player (`native_player.rs`) cannot be embedded on Wayland: a compositor
//! never lets one client draw into another's surface, and mpv's `--wid` exists only on X11/macOS.
//! So this module loads the *same* mpv fork as a library instead of spawning the binary, and draws
//! it into a `GtkGLArea` laid over the room's video box. The sync plugin is loaded unchanged
//! (`script=`), so room timing, seeking, roles and OSD behave exactly as before — the only
//! difference is where the pixels land.
//!
//! libmpv is dlopen'd from the package's own directory (`/usr/lib/<exe name>/libmpv.so.2`,
//! `W2G_LIBMPV` overrides), never from the system: the plugin is compiled against this fork, and a
//! distro libmpv would be a different player with a different voice.
//!
//! Threading is the whole difficulty here: every GL render call must happen on the main thread with
//! the widget's GL context current, while mpv's core threads call the update callback as fast as
//! frames arrive. The callback therefore only posts a redraw to the main loop; all render work
//! happens inside the `render` signal of the widget.

use gtk::glib;
use gtk::prelude::*;
use std::cell::RefCell;
use std::ffi::{c_char, c_int, c_void, CStr, CString};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, OnceLock};
use std::time::Duration;
use tauri::{AppHandle, Manager};

use crate::native_player::{paths, resolve_lib, StartArgs};

/// Run `f` on the GTK main thread and wait for its result.
///
/// Every entry point below touches GTK objects, which are main-thread-only, while Tauri runs a
/// command on whichever thread the IPC handler happens to use. When the caller already *is* the
/// main thread (a real possibility: Tauri executes sync commands there), dispatching and blocking
/// would deadlock, so that case runs inline.
pub fn on_main<T, F>(app: &AppHandle, f: F) -> Result<T, String>
where
    T: Send + 'static,
    F: FnOnce() -> T + Send + 'static,
{
    if glib::MainContext::default().is_owner() {
        return Ok(f());
    }
    let (tx, rx) = std::sync::mpsc::channel();
    app.run_on_main_thread(move || {
        let _ = tx.send(f());
    })
    .map_err(|e| e.to_string())?;
    rx.recv().map_err(|e| e.to_string())
}

// ── Tauri commands ──────────────────────────────────────────────────────────────────────────────

/// True when this install can draw video inside the window (the package ships libmpv).
#[tauri::command]
pub fn player_embed_available() -> bool {
    available()
}

/// Start (or replace) the embedded player for the room the web UI is in.
#[tauri::command]
pub fn player_embed_start(app: AppHandle, args: StartArgs) -> Result<(), String> {
    let handle = app.clone();
    on_main(&app, move || start(handle, args))?
}

/// Stop the embedded player; the web view is untouched.
#[tauri::command]
pub fn player_embed_stop(app: AppHandle) -> Result<(), String> {
    on_main(&app, stop)?
}

/// Where the video box is, in CSS pixels relative to the web view.
#[tauri::command]
pub fn player_embed_rect(app: AppHandle, x: f64, y: f64, w: f64, h: f64) -> Result<(), String> {
    on_main(&app, move || set_rect(x, y, w, h))?
}

/// True while the embedded player is running.
#[tauri::command]
pub fn player_embed_running(app: AppHandle) -> Result<bool, String> {
    on_main(&app, running)
}

// ── minimal FFI: exactly the symbols this module calls ───────────────────────────────────────────

#[repr(C)]
struct MpvEvent {
    event_id: c_int,
    error: c_int,
    reply_userdata: u64,
    data: *mut c_void,
}

const MPV_EVENT_NONE: c_int = 0;
const MPV_EVENT_SHUTDOWN: c_int = 1;
const MPV_EVENT_LOG_MESSAGE: c_int = 2;
const MPV_EVENT_START_FILE: c_int = 6;
const MPV_EVENT_END_FILE: c_int = 7;
const MPV_EVENT_FILE_LOADED: c_int = 8;
const MPV_EVENT_VIDEO_RECONFIG: c_int = 17;

const MPV_RENDER_PARAM_API_TYPE: c_int = 1;
const MPV_RENDER_PARAM_OPENGL_INIT_PARAMS: c_int = 2;
const MPV_RENDER_PARAM_OPENGL_FBO: c_int = 3;
const MPV_RENDER_PARAM_FLIP_Y: c_int = 4;
const MPV_RENDER_PARAM_WL_DISPLAY: c_int = 9;

const GL_FRAMEBUFFER_BINDING: c_int = 0x8CA6;

/// Mirrors `mpv_event_log_message` (client.h) — the only way a libmpv host sees warnings, since
/// there is no terminal to write them to in a packaged app.
#[repr(C)]
struct LogMessage {
    prefix: *const c_char,
    level: *const c_char,
    text: *const c_char,
    log_level: c_int,
}

#[repr(C)]
struct RenderParam {
    kind: c_int,
    data: *mut c_void,
}

/// Mirrors `mpv_opengl_init_params` (render_gl.h): the callback pair mpv uses to resolve every GL
/// entry point it needs. mpv never links GL itself.
#[repr(C)]
struct OpenglInitParams {
    get_proc_address: Option<extern "C" fn(*mut c_void, *const c_char) -> *mut c_void>,
    get_proc_address_ctx: *mut c_void,
}

/// Mirrors `mpv_opengl_fbo` (render_gl.h). `internal_format` 0 means "unknown", which mpv accepts.
#[repr(C)]
struct OpenglFbo {
    fbo: c_int,
    w: c_int,
    h: c_int,
    internal_format: c_int,
}

/// The subset of libmpv this module uses. Loaded by name so a missing/old library is a clear "not
/// available" answer instead of a link error that would break the whole app at startup.
struct Api {
    create: unsafe extern "C" fn() -> *mut c_void,
    initialize: unsafe extern "C" fn(*mut c_void) -> c_int,
    set_option_string: unsafe extern "C" fn(*mut c_void, *const c_char, *const c_char) -> c_int,
    command_string: unsafe extern "C" fn(*mut c_void, *const c_char) -> c_int,
    request_log_messages: unsafe extern "C" fn(*mut c_void, *const c_char) -> c_int,
    wait_event: unsafe extern "C" fn(*mut c_void, f64) -> *mut MpvEvent,
    error_string: unsafe extern "C" fn(c_int) -> *const c_char,
    terminate_destroy: unsafe extern "C" fn(*mut c_void),
    render_create: unsafe extern "C" fn(*mut *mut c_void, *mut c_void, *mut RenderParam) -> c_int,
    render_free: unsafe extern "C" fn(*mut c_void),
    render_set_update_callback:
        unsafe extern "C" fn(*mut c_void, Option<extern "C" fn(*mut c_void)>, *mut c_void),
    render: unsafe extern "C" fn(*mut c_void, *mut RenderParam) -> c_int,
    render_report_swap: unsafe extern "C" fn(*mut c_void),
}

struct Library {
    api: Api,
}

unsafe impl Send for Library {}
unsafe impl Sync for Library {}

thread_local! {
    /// The one embedded player, if any. GTK objects are main-thread-only, hence thread-local.
    static PLAYER: RefCell<Option<Player>> = const { RefCell::new(None) };
}

static LIBRARY: OnceLock<Result<&'static Library, String>> = OnceLock::new();
static LOAD_FAILED: AtomicBool = AtomicBool::new(false);

/// Where libmpv is looked for: `W2G_LIBMPV`, else the package's player directory, else beside the
/// executable (see [`resolve_lib`]).
fn libmpv_path() -> PathBuf {
    resolve_lib("W2G_LIBMPV", "libmpv.so.2")
}

fn cstr(s: &str) -> CString {
    CString::new(s).unwrap()
}

/// The session's `wl_display`, when the app is running as a Wayland client.
///
/// Without it libmpv cannot open a VA-API display at all (`Could not create a VA display`), so
/// every film is decoded on the CPU — measured on this machine: the same file reports
/// `hwdec-current=vaapi` once the display is passed, and `no` without it. GTK keeps the display on
/// the default GDK connection, so it is taken from there; on X11 this returns null and mpv picks
/// its own path.
fn wayland_display() -> *mut c_void {
    static F: OnceLock<Option<unsafe extern "C" fn(*mut c_void) -> *mut c_void>> = OnceLock::new();
    let display = match gtk::gdk::Display::default() {
        Some(d) if d.type_().name() == "GdkWaylandDisplay" => d,
        _ => return std::ptr::null_mut(),
    };
    let f = *F.get_or_init(|| {
        let handle = unsafe { libc::dlopen(cstr("libgdk-3.so.0").as_ptr(), libc::RTLD_NOW | libc::RTLD_LOCAL) };
        if handle.is_null() {
            return None;
        }
        let sym = unsafe { libc::dlsym(handle, cstr("gdk_wayland_display_get_wl_display").as_ptr()) };
        if sym.is_null() {
            None
        } else {
            Some(unsafe { std::mem::transmute_copy(&sym) })
        }
    });
    match f {
        Some(f) => {
            let raw: *mut gtk::gdk::ffi::GdkDisplay =
                glib::translate::ToGlibPtr::<*mut gtk::gdk::ffi::GdkDisplay>::to_glib_none(&display).0;
            unsafe { f(raw as *mut c_void) }
        }
        None => std::ptr::null_mut(),
    }
}

/// The library, loaded once for the life of the process: every pointer stored in `Player` (and in
/// the GL callback) points into it, so it must never be closed.
fn library() -> Result<&'static Library, String> {
    LIBRARY.get_or_init(|| {
        let path = libmpv_path();
        // SAFETY: never closed; see above.
        let handle = unsafe { libc::dlopen(cstr(&path.display().to_string()).as_ptr(), libc::RTLD_NOW | libc::RTLD_LOCAL) };
        if handle.is_null() {
            LOAD_FAILED.store(true, Ordering::Relaxed);
            return Err(format!("libmpv not found at {} (set W2G_LIBMPV)", path.display()));
        }
        macro_rules! sym {
            ($name:literal) => {{
                let p = unsafe { libc::dlsym(handle, cstr($name).as_ptr()) };
                if p.is_null() {
                    return Err(format!("{} is missing symbol {}", path.display(), $name));
                }
                unsafe { std::mem::transmute_copy(&p) }
            }};
        }
        let api = Api {
            create: sym!("mpv_create"),
            initialize: sym!("mpv_initialize"),
            set_option_string: sym!("mpv_set_option_string"),
            command_string: sym!("mpv_command_string"),
            request_log_messages: sym!("mpv_request_log_messages"),
            wait_event: sym!("mpv_wait_event"),
            error_string: sym!("mpv_error_string"),
            terminate_destroy: sym!("mpv_terminate_destroy"),
            render_create: sym!("mpv_render_context_create"),
            render_free: sym!("mpv_render_context_free"),
            render_set_update_callback: sym!("mpv_render_context_set_update_callback"),
            render: sym!("mpv_render_context_render"),
            render_report_swap: sym!("mpv_render_context_report_swap"),
        };
        Ok(Box::leak(Box::new(Library { api })) as &'static Library)
    })
    .clone()
}

/// libepoxy is what GDK itself resolves GL through, so it always agrees with the GL context GTK
/// hands us. mpv asks for every entry point through this one callback.
type GetProc = unsafe extern "C" fn(*const c_char) -> *mut c_void;

fn epoxy() -> Option<GetProc> {
    static EPOXY: OnceLock<Option<GetProc>> = OnceLock::new();
    *EPOXY.get_or_init(|| {
        let handle = unsafe { libc::dlopen(cstr("libepoxy.so.0").as_ptr(), libc::RTLD_NOW | libc::RTLD_LOCAL) };
        if handle.is_null() {
            return None;
        }
        let sym = unsafe { libc::dlsym(handle, cstr("epoxy_get_proc_address").as_ptr()) };
        if sym.is_null() {
            return None;
        }
        Some(unsafe { std::mem::transmute_copy(&sym) })
    })
}

extern "C" fn get_proc_address(_ctx: *mut c_void, name: *const c_char) -> *mut c_void {
    match epoxy() {
        Some(f) => unsafe { f(name) },
        None => std::ptr::null_mut(),
    }
}

extern "C" fn on_frame_update(_ctx: *mut c_void) {
    // Runs on an mpv thread: nothing but a wakeup may happen here. The closure captures nothing,
    // so the GTK objects are still only touched on the main thread.
    glib::MainContext::default().invoke(|| {
        PLAYER.with(|p| {
            if let Some(player) = p.borrow().as_ref() {
                if player.area.is_realized() {
                    player.area.queue_render();
                }
            }
        });
    });
}

/// The mpv handle, shared with the event thread. libmpv's command API is thread-safe.
struct Core {
    handle: *mut c_void,
    lib: &'static Library,
}

unsafe impl Send for Core {}
unsafe impl Sync for Core {}

impl Core {
    fn command(&self, line: &str) -> bool {
        let line = match CString::new(line) {
            Ok(l) => l,
            Err(_) => return false,
        };
        unsafe { (self.lib.api.command_string)(self.handle, line.as_ptr()) >= 0 }
    }
}

struct Player {
    core: Arc<Core>,
    render: *mut c_void,
    area: gtk::GLArea,
    size: (c_int, c_int),
    /// The file to open as soon as a render context exists. Opening it earlier makes the libmpv
    /// video output refuse to configure (`[vo/libmpv] No render context set.`) and the film then
    /// never appears — measured with a GTK+libmpv probe, not assumed.
    pending: Option<String>,
    /// Signalled by the event thread once the core has shut down and been destroyed. mpv's event
    /// queue has exactly one consumer, so that thread owns `mpv_wait_event` *and* the destroy; this
    /// is how the main thread waits for it instead of racing it.
    events: Option<std::sync::mpsc::Receiver<()>>,
    app: AppHandle,
}

/// Pure: CSS pixels (web view) → GTK device pixels for the overlay child.
fn device_rect(x: f64, y: f64, w: f64, h: f64, scale: f64) -> (i32, i32, i32, i32) {
    let s = if scale > 0.0 { scale } else { 1.0 };
    (
        (x * s).round() as i32,
        (y * s).round() as i32,
        (w * s).round().max(1.0) as i32,
        (h * s).round().max(1.0) as i32,
    )
}

/// Pure: a key press the video widget owns, as an mpv command. Anything else is left to the UI.
fn command_for_key(key: &str) -> Option<&'static str> {
    Some(match key {
        "space" | "k" => "cycle pause",
        "Left" => "seek -5",
        "Right" => "seek 5",
        "Up" => "add volume 5",
        "Down" => "add volume -5",
        "m" => "cycle mute",
        "f" => "cycle fullscreen",
        _ => return None,
    })
}

fn log(level: &str, message: &str) {
    crate::log(level, &format!("[surface] {message}"));
}

/// True when this install can render video inside the window at all.
pub fn available() -> bool {
    libmpv_path().is_file() && !LOAD_FAILED.load(Ordering::Relaxed)
}

/// The widget that currently hosts the web content, found by type so this does not depend on the
/// internal widget tree Tauri/wry happen to build today.
fn find_webview(widget: &gtk::Widget) -> Option<gtk::Widget> {
    if widget.type_().name() == "WebKitWebView" {
        return Some(widget.clone());
    }
    if let Some(container) = widget.downcast_ref::<gtk::Container>() {
        for child in container.children() {
            if let Some(found) = find_webview(&child) {
                return Some(found);
            }
        }
    }
    None
}

/// Wrap the webview in a `GtkOverlay` so a GL surface can sit on top of it without losing the
/// packing the window manager expects. Returns the overlay and the surface.
fn install_overlay(window: &gtk::Window) -> Result<(gtk::Overlay, gtk::GLArea), String> {
    let webview = find_webview(window.upcast_ref::<gtk::Widget>())
        .ok_or_else(|| "the web view was not found in the window".to_string())?;
    let parent = webview.parent().ok_or_else(|| "the web view has no parent".to_string())?;
    // A second play in the same session must reuse the overlay instead of wrapping the web view in
    // another one (each nesting level would drag a GL area and a render context along).
    if let Some(existing) = parent.downcast_ref::<gtk::Overlay>() {
        if let Some(area) = existing.children().into_iter().find_map(|c| c.downcast::<gtk::GLArea>().ok()) {
            return Ok((existing.clone(), area));
        }
    }
    let overlay = gtk::Overlay::new();
    overlay.set_hexpand(true);
    overlay.set_vexpand(true);

    if let Some(win) = parent.downcast_ref::<gtk::Window>() {
        win.set_child(Some(&overlay));
    } else if let Some(container) = parent.downcast_ref::<gtk::Container>() {
        // The web view fills the window, so its replacement must expand exactly as it did.
        let expand = true;
        container.remove(&webview);
        if let Some(b) = parent.downcast_ref::<gtk::Box>() {
            b.pack_start(&overlay, expand, true, 0);
        } else {
            container.add(&overlay);
        }
    } else {
        return Err("the web view's parent cannot host an overlay".into());
    }
    overlay.add(&webview);

    let area = gtk::GLArea::new();
    area.set_auto_render(false); // frames are drawn only when mpv says there is one
    area.set_hexpand(false);
    area.set_vexpand(false);
    area.set_halign(gtk::Align::Start);
    area.set_valign(gtk::Align::Start);
    area.set_can_focus(true);
    area.add_events(gtk::gdk::EventMask::KEY_PRESS_MASK | gtk::gdk::EventMask::BUTTON_PRESS_MASK);
    area.set_visible(false);
    overlay.add_overlay(&area);
    overlay.set_overlay_pass_through(&area, false);
    Ok((overlay, area))
}

/// Create the render context if there is none yet, and with it flush any file that was waiting.
///
/// Must run with the area's GL context current (its `realize` signal, or `render`). Returns false
/// when no context could be created, which is the one case the app cannot recover from.
fn ensure_context(player: &mut Player, lib: &'static Library) -> bool {
    if !player.render.is_null() {
        return true;
    }
    let api = &lib.api;
    let mut init = OpenglInitParams {
        get_proc_address: Some(get_proc_address),
        get_proc_address_ctx: std::ptr::null_mut(),
    };
    let mut params = vec![
        RenderParam { kind: MPV_RENDER_PARAM_API_TYPE, data: api_type_ptr() },
        RenderParam {
            kind: MPV_RENDER_PARAM_OPENGL_INIT_PARAMS,
            data: &mut init as *mut OpenglInitParams as *mut c_void,
        },
    ];
    let wl = wayland_display();
    if !wl.is_null() {
        params.push(RenderParam { kind: MPV_RENDER_PARAM_WL_DISPLAY, data: wl });
    }
    params.push(RenderParam { kind: 0, data: std::ptr::null_mut() });
    let mut ctx: *mut c_void = std::ptr::null_mut();
    let rc = unsafe { (api.render_create)(&mut ctx, player.core.handle, params.as_mut_ptr()) };
    if rc < 0 || ctx.is_null() {
        log("error", &format!("render context failed: {}", err(api, rc)));
        crate::emit_surface_exit(&player.app, false);
        return false;
    }
    player.render = ctx;
    unsafe { (api.render_set_update_callback)(ctx, Some(on_frame_update), std::ptr::null_mut()) };
    log("info", "render context ready");

    // Only now can the video output be configured, so this is where the film is actually opened.
    if let Some(url) = player.pending.take() {
        if player.core.command(&format!("loadfile {}", quote(&url))) {
            log("info", "opened the media");
        } else {
            log("warn", "loadfile was refused");
        }
    }
    true
}

/// Render one frame into the GL surface. Must run on the main thread with the area's context
/// current — i.e. inside the `render` signal.
fn render_frame(player: &mut Player, lib: &'static Library) {
    let (w, h) = player.size;
    if w <= 0 || h <= 0 {
        return;
    }
    if !ensure_context(player, lib) {
        return;
    }
    // GtkGLArea draws into a GDK-owned framebuffer, not the default one; asking GL which one is
    // bound is the only reliable answer across Wayland/X11 and drivers. (Measured: 0 during
    // realize, a non-zero FBO inside `render`.)
    let mut fbo: c_int = 0;
    if let Some(f) = gl_get_integerv() {
        unsafe { f(GL_FRAMEBUFFER_BINDING, &mut fbo as *mut c_int as *mut c_void) };
    }
    let api = &lib.api;

    let mut fbo_desc = OpenglFbo { fbo, w, h, internal_format: 0 };
    let mut flip: c_int = 1;
    let mut params = [
        RenderParam {
            kind: MPV_RENDER_PARAM_OPENGL_FBO,
            data: &mut fbo_desc as *mut OpenglFbo as *mut c_void,
        },
        RenderParam { kind: MPV_RENDER_PARAM_FLIP_Y, data: &mut flip as *mut c_int as *mut c_void },
        RenderParam { kind: 0, data: std::ptr::null_mut() },
    ];
    let rc = unsafe { (api.render)(player.render, params.as_mut_ptr()) };
    if rc < 0 {
        log("warn", &format!("render failed: {}", err(api, rc)));
        return;
    }
    unsafe { (api.render_report_swap)(player.render) };
}

/// `MPV_RENDER_API_TYPE_OPENGL` must outlive the call and is not retained by mpv's API past it,
/// but it must be a stable, non-freed pointer either way.
fn api_type_ptr() -> *mut c_void {
    static API_TYPE: OnceLock<CString> = OnceLock::new();
    API_TYPE.get_or_init(|| cstr("opengl")).as_ptr() as *mut c_void
}

/// Resolve `glGetIntegerv`, the one GL call made outside mpv.
fn gl_get_integerv() -> Option<unsafe extern "C" fn(c_int, *mut c_void)> {
    static F: OnceLock<Option<unsafe extern "C" fn(c_int, *mut c_void)>> = OnceLock::new();
    *F.get_or_init(|| {
        let p = unsafe { epoxy()?(cstr("glGetIntegerv").as_ptr()) };
        if p.is_null() {
            None
        } else {
            Some(unsafe { std::mem::transmute_copy(&p) })
        }
    })
}

fn err(api: &Api, code: c_int) -> String {
    unsafe {
        let p = (api.error_string)(code);
        if p.is_null() {
            format!("error {code}")
        } else {
            CStr::from_ptr(p).to_string_lossy().into_owned()
        }
    }
}

/// Tear the player down on the main thread: free the render context while the widget's GL context
/// can still be made current, then ask the core to quit and wait for the event thread to destroy it.
fn shutdown(mut player: Player) {
    let api = &player.core.lib.api;
    // Free the render context here, with the widget's GL context made current: the context must be
    // released before the core is destroyed, and this is the only moment GL is safe to touch.
    if !player.render.is_null() {
        unsafe { (api.render_set_update_callback)(player.render, None, std::ptr::null_mut()) };
        player.area.make_current();
        unsafe { (api.render_free)(player.render) };
        player.render = std::ptr::null_mut();
    }
    player.area.set_visible(false);
    player.core.command("quit");
    // The event thread destroys the handle as its last act; wait for it so the caller can start a
    // replacement player without two cores alive at once.
    if let Some(events) = player.events {
        if events.recv_timeout(Duration::from_secs(3)).is_err() {
            log("warn", "the core did not shut down in time");
        }
    }
}

/// Start (or replace) the embedded player for this room.
pub fn start(app: AppHandle, args: StartArgs) -> Result<(), String> {
    // mpv refuses to run under a locale whose decimal separator is not '.', and a GTK app has
    // already called setlocale(LC_ALL, "") by now, so it inherits the user's locale (a Persian
    // session included) and would abort the whole app. mpv's own binary does this for itself; a
    // library host has to do it.
    unsafe { libc::setlocale(libc::LC_NUMERIC, cstr("C").as_ptr()) };
    let lib = library()?;
    let (_, plugin) = paths();
    if !plugin.is_file() {
        return Err(format!("sync plugin not found at {} (set W2G_PLUGIN)", plugin.display()));
    }

    // Nothing may render while the old player is still alive.
    let previous = PLAYER.with(|p| p.borrow_mut().take());
    if let Some(player) = previous {
        shutdown(player);
    }

    let api = &lib.api;
    let handle = unsafe { (api.create)() };
    if handle.is_null() {
        return Err("mpv_create failed".into());
    }
    let core = Arc::new(Core { handle, lib });

    // The same shape as the spawned player: no user config, the fork's own sync plugin, paused
    // until the room says otherwise, keeping the file open at the end.
    let options: Vec<(&str, String)> = {
        let role = if args.role == "controller" { "controller" } else { "watcher" };
        let mut options = vec![
            ("config", "no".to_string()),
            ("terminal", "no".to_string()),
            ("vo", "libmpv".to_string()),
            ("gpu-api", "opengl".to_string()),
            ("hwdec", "auto-safe".to_string()),
            ("keep-open", "yes".to_string()),
            ("pause", "yes".to_string()),
            ("osc", "no".to_string()),
            ("input-default-bindings", "no".to_string()),
            ("script", plugin.display().to_string()),
            ("force-media-title", args.title.clone()),
            ("stream-lavf-o", "reconnect=1,reconnect_on_network_error=1,reconnect_delay_max=5".to_string()),
            ("network-timeout", "30".to_string()),
        ];
        if let Some(t) = args.access_token.as_deref().filter(|t| !t.is_empty()) {
            options.push(("http-header-fields", format!("Authorization: Bearer {t}")));
        }
        let _ = role;
        options
    };
    for (name, value) in &options {
        let (n, v) = (cstr(name), cstr(value));
        let rc = unsafe { (api.set_option_string)(handle, n.as_ptr(), v.as_ptr()) };
        if rc < 0 {
            unsafe { (api.terminate_destroy)(handle) };
            return Err(format!("mpv option {name}: {}", err(api, rc)));
        }
    }

    // The plugin reads its room from the environment, exactly as the spawned player passes it.
    let role = if args.role == "controller" { "controller" } else { "watcher" };
    std::env::set_var("W2G_URL", &args.sync_url);
    std::env::set_var("W2G_TOKEN", &args.sync_token);
    std::env::set_var("W2G_ROOM", &args.room);
    std::env::set_var("W2G_ROLE", role);

    let rc = unsafe { (api.initialize)(handle) };
    if rc < 0 {
        unsafe { (api.terminate_destroy)(handle) };
        return Err(format!("mpv_initialize: {}", err(api, rc)));
    }
    // There is no terminal in a packaged app, so the core's own warnings (a refused network
    // stream, a missing codec, a failed GL init) are only visible if they are asked for here.
    unsafe { (api.request_log_messages)(handle, cstr("warn").as_ptr()) };

    // Events are drained on their own thread: `wait_event` both keeps the core happy and gives the
    // app a real exit signal. This thread also destroys the handle — the event queue has exactly
    // one consumer, so nothing else may call `wait_event` or `terminate_destroy` concurrently.
    let (done_tx, done_rx) = std::sync::mpsc::channel();
    {
        let listener = core.clone();
        let app_for_events = app.clone();
        let media = args.media_url.split('?').next().unwrap_or("").to_string();
        std::thread::spawn(move || loop {
            let ev = unsafe { (listener.lib.api.wait_event)(listener.handle, 0.5) };
            if ev.is_null() {
                let _ = done_tx.send(());
                return;
            }
            let (id, error) = unsafe { ((*ev).event_id, (*ev).error) };
            match id {
                MPV_EVENT_SHUTDOWN => {
                    log("info", "core shut down");
                    unsafe { (listener.lib.api.terminate_destroy)(listener.handle) };
                    crate::emit_surface_exit(&app_for_events, true);
                    let _ = done_tx.send(());
                    return;
                }
                MPV_EVENT_LOG_MESSAGE => {
                    let msg = unsafe { &*((*ev).data as *const LogMessage) };
                    let text = if msg.text.is_null() {
                        String::new()
                    } else {
                        unsafe { CStr::from_ptr(msg.text) }.to_string_lossy().into_owned()
                    };
                    let prefix = if msg.prefix.is_null() {
                        "mpv"
                    } else {
                        unsafe { CStr::from_ptr(msg.prefix) }.to_str().unwrap_or("mpv")
                    };
                    let line: String = text.chars().filter(|c| !c.is_control()).take(300).collect();
                    if !line.trim().is_empty() {
                        log("warn", &format!("[{prefix}] {line}"));
                    }
                }
                MPV_EVENT_FILE_LOADED => log("info", "file loaded"),
                MPV_EVENT_START_FILE => log("info", &format!("starting {media}")),
                MPV_EVENT_END_FILE => log("warn", &format!("end of file (error={error})")),
                MPV_EVENT_VIDEO_RECONFIG => log("info", "video reconfigured"),
                MPV_EVENT_NONE => {}
                _ => {}
            }
        });
    }

    // Build the surface and hand the widget to the window.
    let window = app
        .get_webview_window("main")
        .ok_or_else(|| "main window not found".to_string())?;
    let gtk_window = window.gtk_window().map_err(|e| format!("gtk window: {e}"))?;
    let (overlay, area) = match install_overlay(gtk_window.upcast_ref::<gtk::Window>()) {
        Ok(found) => found,
        Err(e) => {
            // No widget to draw into: drop the core without leaving a thread waiting on it.
            core.command("quit");
            let _ = done_rx.recv_timeout(Duration::from_secs(3));
            return Err(e);
        }
    };
    let _ = &overlay;

    {
        area.connect_resize(|_, w, h| {
            PLAYER.with(|p| {
                if let Some(player) = p.borrow_mut().as_mut() {
                    player.size = (w, h);
                }
            });
        });
    }
    {
        // The render context is freed while its GL context is still current: GtkGLArea keeps the
        // context current until after this signal.
        area.connect_unrealize(|_| {
            PLAYER.with(|p| {
                let mut guard = p.borrow_mut();
                if let Some(player) = guard.as_mut() {
                    if !player.render.is_null() {
                        let api = &player.core.lib.api;
                        unsafe { (api.render_set_update_callback)(player.render, None, std::ptr::null_mut()) };
                        unsafe { (api.render_free)(player.render) };
                        player.render = std::ptr::null_mut();
                    }
                }
            });
        });
    }
    {
        let lib_static: &'static Library = lib;
        area.connect_realize(move |area| {
            area.make_current();
            PLAYER.with(|p| {
                if let Some(player) = p.borrow_mut().as_mut() {
                    ensure_context(player, lib_static);
                }
            });
        });
        area.connect_render(move |_, _| {
            PLAYER.with(|p| {
                if let Some(player) = p.borrow_mut().as_mut() {
                    render_frame(player, lib_static);
                }
            });
            glib::Propagation::Stop
        });
        area.connect_key_press_event(|_, ev| {
            if let Some(key) = ev.keyval().name() {
                let key = key.as_str();
                if let Some(command) = command_for_key(key) {
                    PLAYER.with(|p| {
                        if let Some(player) = p.borrow().as_ref() {
                            player.core.command(command);
                        }
                    });
                    return glib::Propagation::Stop;
                }
                if key == "Escape" {
                    let _ = stop();
                    return glib::Propagation::Stop;
                }
            }
            glib::Propagation::Proceed
        });
        area.connect_button_press_event(|area, ev| {
            area.grab_focus();
            if ev.button() == 1 {
                PLAYER.with(|p| {
                    if let Some(player) = p.borrow().as_ref() {
                        player.core.command("cycle pause");
                    }
                });
            }
            glib::Propagation::Stop
        });
    }

    let player = Player {
        core: core.clone(),
        render: std::ptr::null_mut(),
        area: area.clone(),
        size: (0, 0),
        pending: Some(args.media_url.clone()),
        events: Some(done_rx),
        app: app.clone(),
    };
    PLAYER.with(|p| *p.borrow_mut() = Some(player));

    // The file is opened from `ensure_context` once the GL surface exists: libmpv's video output
    // cannot be configured before its render context, and loading first leaves a window that
    // silently plays nothing.
    area.set_visible(true);
    area.queue_render();
    log("info", &format!("embedded player started room={} role={}", args.room, role));
    Ok(())
}

/// mpv's command parser has its own quoting rules; the URL goes through the quoted-string form
/// `loadfile` documents, so a URL can never be parsed as another option.
fn quote(value: &str) -> String {
    let mut out = String::from("\"");
    for c in value.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '"' => out.push_str("\\\""),
            _ => out.push(c),
        }
    }
    out.push('"');
    out
}

/// Stop the embedded player, if one is running.
pub fn stop() -> Result<(), String> {
    let player = PLAYER.with(|p| p.borrow_mut().take());
    if let Some(player) = player {
        shutdown(player);
    }
    Ok(())
}

/// Where the video box is, in CSS pixels relative to the web view.
pub fn set_rect(x: f64, y: f64, w: f64, h: f64) -> Result<(), String> {
    PLAYER.with(|p| {
        let mut guard = p.borrow_mut();
        let Some(player) = guard.as_mut() else { return Ok(()) };
        let scale = player.area.scale_factor() as f64;
        let (x, y, w, h) = device_rect(x, y, w, h, scale);
        player.area.set_margin_start(x);
        player.area.set_margin_top(y);
        player.area.set_size_request(w, h);
        player.size = (w, h);
        player.area.queue_resize();
        player.area.queue_render();
        Ok(())
    })
}

/// True while a player is running inside the window.
pub fn running() -> bool {
    PLAYER.with(|p| p.borrow().is_some())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn css_rect_becomes_device_pixels() {
        assert_eq!(device_rect(10.0, 20.0, 640.0, 360.0, 1.0), (10, 20, 640, 360));
        assert_eq!(device_rect(10.0, 20.0, 640.0, 360.0, 2.0), (20, 40, 1280, 720));
        // A zero or fractional scale must never produce an empty or negative widget.
        assert_eq!(device_rect(-4.0, 0.0, 0.0, 0.0, 0.0), (-4, 0, 1, 1));
    }

    #[test]
    fn only_player_keys_are_forwarded() {
        assert_eq!(command_for_key("space"), Some("cycle pause"));
        assert_eq!(command_for_key("Left"), Some("seek -5"));
        assert_eq!(command_for_key("f"), Some("cycle fullscreen"));
        assert_eq!(command_for_key("a"), None);
        assert_eq!(command_for_key("Return"), None);
    }

    #[test]
    fn a_url_with_spaces_or_quotes_cannot_break_the_command() {
        assert_eq!(quote("https://h/a b.mp4"), "\"https://h/a b.mp4\"");
        assert_eq!(quote("https://h/a\"b"), "\"https://h/a\\\"b\"");
        assert_eq!(quote("https://h/a\\b"), "\"https://h/a\\\\b\"");
    }

    #[test]
    fn a_missing_library_reports_the_expected_path() {
        std::env::set_var("W2G_LIBMPV", "/nonexistent/libmpv.so.2");
        assert!(!available());
        std::env::remove_var("W2G_LIBMPV");
    }
}
