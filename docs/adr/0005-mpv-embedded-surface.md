# ADR 0005 — Draw the video inside the app window (libmpv render API)

- **Status:** Accepted — 2026-10-31
- **Supersedes:** ADR 0002 §4 "Display strategy" (the separate-window-first choice only)
- **Evidence:** measured on the target machine (Hyprland/Wayland, Intel Arc); see below

## Context

ADR 0002 chose a separate mpv window and deferred embedding with the note "revisit only if the
separate window is rejected in use". It has been rejected in use: clicking play opened a second
top-level window titled `mpv`, and the room screen then showed a black placeholder while the film
played somewhere else.

ADR 0002's objection was correct as far as it went: on Wayland a client cannot draw into another
client's surface, and mpv's `--wid` exists only on X11/macOS. That rules out *spawning* mpv into
the window. It does not rule out *linking* it.

## Decision

Load the fork's `libmpv` (not the system one) at runtime and render it into a `GtkGLArea` that
overlays the room's video box inside the existing Tauri window (`src/mpv_surface.rs`).

- **Library location:** `/usr/lib/vergoboy-<instance>/libmpv.so.2`, built from the same mpv tree as
  the sync plugin, resolved exactly like the player binary (`W2G_LIBMPV` overrides). A distro
  libmpv would be a different player than the one the plugin was written against.
- **Surface:** a `GtkGLArea` added as an overlay child of a `GtkOverlay` that wraps the web view.
  The web UI reports the video box's rect (CSS px) and the host converts it with the widget's own
  scale factor, so HiDPI and fractional scaling need no special case.
- **Sync:** unchanged. The plugin is loaded with `script=`, reads `W2G_URL`/`W2G_TOKEN`/`W2G_ROOM`/
  `W2G_ROLE` from the environment, and drives the room exactly as before; mpv's OSD (script
  messages, subtitles, seek feedback) is drawn into the same surface.
- **Controls:** the room transport bar still works (it commands the room, and the plugin applies
  it); the surface itself takes a few local keys (space/arrows/m/f, Esc to stop) and a click to
  pause, because there is no mpv window to hold them any more.
- **Fallback:** an install without libmpv (an older package) still spawns the player window, so
  `native_player.rs` is kept rather than deleted.

## Measured facts this depends on

| Fact | How it was measured |
| --- | --- |
| libmpv renders into a `GtkGLArea` on this Wayland session and the surface composites **above** the WebKit view | GTK+libmpv probe window with a web view below: video rendered, `vo-configured=yes`, 101 frames in 4 s |
| The file must be opened **after** the render context exists | loading first logs `[vo/libmpv] No render context set.` and the film never appears; the context is therefore created in `realize` |
| The FBO is not 0 inside `render` | `glGetIntegerv(GL_FRAMEBUFFER_BINDING)` returns 0 during `realize` and a GDK-owned FBO inside the render signal |
| VA-API only initialises when the Wayland display is passed | `hwdec-current` is `vaapi` with `MPV_RENDER_PARAM_WL_DISPLAY`, `no` without it |
| mpv aborts under a non-C numeric locale | the probe died with `Non-C locale detected`; the host calls `setlocale(LC_NUMERIC, "C")` before creating the core |
| mpv's event queue has one consumer | teardown is owned by the event thread (it calls `mpv_terminate_destroy`); the main thread waits on a channel |

## Consequences

- The package now ships `libmpv.so.2` alongside `mpv` and `w2g.so` (still one `pacman -U`).
- The app links `gtk` directly (the version Tauri/wry already build) — the widget pointers must be
  the same types, so the gtk-rs version is pinned to theirs.
- ADR 0002's licence reasoning still holds for the *binary* path, but libmpv is now loaded in
  process: it is the fork's own LGPL library, dlopen'd from our package, not linked into the
  distribution's broader system.
- Reparenting the web view into a `GtkOverlay` assumes Tauri/wry add it to the window's child
  container; `install_overlay` finds the widget by GType and handles both a `GtkWindow` and a
  generic `GtkContainer` parent, and reuses an overlay that is already installed.
