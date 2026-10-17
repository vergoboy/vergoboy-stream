# Dependencies — runtime and licensing notes

Short inventory of the non-Python/non-npm things the app depends on, why, and under what
license. Only dependencies that exist today are listed (ADR 0001 rule: nothing for modules
that do not exist yet).

## mpv (native app playback) — phase 1

| | |
| --- | --- |
| **What** | `mpv` executable, driven as a **separate process** over JSON IPC (`--input-ipc-server`) by the `mpv_controller` crate. |
| **Why** | Play the original file (any codec/container, 10-bit, HDR, MKV, multi-track, ASS) in the native app without transcoding. |
| **License** | Package: `GPL-2.0-or-later AND LGPL-2.1-or-later` (`pacman -Qi mpv`). We spawn the **binary** — no linking, no LGPL obligations in our code. |
| **Not used** | `libmpv.so` (LGPL-2.1+) is *not* linked; see ADR 0002. |
| **Distribution** | User/distro-installed, like ffmpeg. Absent → native client shows "install mpv"; web client unaffected. |
| **Detection** | `mpv_controller` resolves `$STREAM_MPV_BIN`, then `PATH` (`mpv`, or `mpv.exe` on Windows); spawn failure is an `Error` port event, never a crash. |
| **Transport** | unix socket (`--input-ipc-server=/path.sock`) on unix; Windows named pipe (`--input-ipc-server=\\.\pipe\mpv-…`) opened with `std::fs::OpenOptions`. **std only — no new crate, no `libmpv`, no `unsafe`.** Windows support is compile-verified (`cargo check -p mpv_controller --target x86_64-pc-windows-msvc`), not run (no Windows host). |
| **Evidence** | `docs/spike/mpv-ipc-observations.json`, ADR 0002 (incl. part-B wire-behaviour record), `cargo test -p mpv_controller` (59 unit + 7 against a real headless mpv). |
