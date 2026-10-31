/**
 * Native mpv player (desktop app only). The web UI keeps owning login, room and media state and hands
 * the player what it needs; the Tauri side (src-tauri/src/native_player.rs) only spawns and watches mpv.
 * Everything here is a no-op outside the Tauri build, so callers can use it freely.
 */
import { API_PREFIX, mediaUrl } from "./config";
import { authFetch } from "./auth";
import { isTauri } from "./tauri";

type SyncInfo = { url: string; token: string; room: string; role: "controller" | "watcher" };

/**
 * The two calls the player needs are tiny and idempotent, so a link that drops one of them must not
 * cost the user a play. Only network-level failures (a rejected fetch, which WebKit reports as
 * `TypeError: Load failed`) are retried; a real HTTP answer is handed to the caller unchanged.
 * On mobile links here a single reset used to surface as «ارتباط با سرور برقرار نشد» with no video.
 */
async function withRetry<T>(call: () => Promise<T>, attempts = 3): Promise<T> {
  let last: unknown;
  for (let i = 0; i < attempts; i++) {
    try {
      return await call();
    } catch (e) {
      last = e;
      if (!(e instanceof TypeError)) throw e;
      if (i + 1 < attempts) await new Promise((r) => setTimeout(r, 300 * (i + 1)));
    }
  }
  throw last;
}

function unreachable(): Error {
  return new Error("ارتباط با سرور برقرار نشد؛ اتصال اینترنت را بررسی کن");
}

async function call<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<T>(cmd, args);
}

/**
 * Where the picture goes. `embedded` draws libmpv into this window; `window` is the spawned player
 * (its own window), which is the only option an install without libmpv has.
 */
export type PlayerSurface = "embedded" | "window";

/** True when running in the desktop app AND the mpv fork + sync plugin are installed next to it. */
export async function nativePlayerAvailable(): Promise<boolean> {
  if (!isTauri()) return false;
  try {
    return await call<boolean>("native_player_available");
  } catch {
    return false;
  }
}

/** True when this install can draw video inside the app window (the package ships libmpv). */
export async function embeddedPlayerAvailable(): Promise<boolean> {
  if (!isTauri()) return false;
  try {
    return await call<boolean>("player_embed_available");
  } catch {
    return false;
  }
}

/**
 * Where the video box is, in CSS pixels relative to the web view. The native side converts to
 * device pixels with the widget's own scale factor, so this is correct on HiDPI and fractional
 * scaling alike. Safe to call when nothing is playing (it is ignored).
 */
export async function setPlayerSurfaceRect(x: number, y: number, w: number, h: number): Promise<void> {
  if (!isTauri()) return;
  await call<void>("player_embed_rect", { x, y, w, h });
}

/** Short-lived token for the room sync service. Role comes from the user's real room permissions. */
export async function fetchSyncInfo(): Promise<SyncInfo> {
  let res: Response;
  try {
    res = await withRetry(() => authFetch(`${API_PREFIX}/room/sync-token`, { method: "POST" }));
  } catch (e) {
    if (e instanceof TypeError) throw unreachable();
    throw e;
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data?.error === "string" ? data.error : `خطای سرور (${res.status})`);
  return data as SyncInfo;
}

export type NativeSource = { kind: "original" | "remote" | "live"; url: string; expires_at: number | null };

/** The original file (signed URL) or the item's own source link (opt-in). Nothing is transcoded. */
export async function fetchNativeSource(itemId: string): Promise<NativeSource> {
  let res: Response;
  try {
    res = await withRetry(() => authFetch(`${API_PREFIX}/items/${encodeURIComponent(itemId)}/native-source`));
  } catch (e) {
    if (e instanceof TypeError) throw unreachable();
    throw e;
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data?.error === "string" ? data.error : `خطای سرور (${res.status})`);
  return data as NativeSource;
}

/** Best-effort line in the app's log (app.log); never throws. */
export function trace(message: string, level: "debug" | "info" | "warn" | "error" = "info"): void {
  if (!isTauri()) return;
  call<void>("client_log", { level, message }).catch(() => {});
}

export async function startNativePlayer(opts: { itemId: string; title: string }): Promise<PlayerSurface> {
  trace(`native player: requested item=${opts.itemId}`);
  const sync = await fetchSyncInfo().catch((e: unknown) => {
    trace(`native player: sync token failed: ${e instanceof Error ? e.message : String(e)}`, "warn");
    throw e;
  });
  trace(`native player: sync token ok role=${sync.role}`);
  // The original file is the only source. There is no transcoded fallback: an item with no
  // playable source reports that instead of handing mpv a URL that cannot work.
  const src = await fetchNativeSource(opts.itemId).catch((e: unknown) => {
    trace(`native player: no source: ${e instanceof Error ? e.message : String(e)}`, "warn");
    throw e;
  });
  trace(`native player: source kind=${src.kind}`);
  const args = {
    syncUrl: sync.url,
    syncToken: sync.token,
    room: sync.room,
    role: sync.role,
    mediaUrl: mediaUrl(src.url),
    title: opts.title,
    // The signed URL is the credential; a remote source is a third-party host.
    // Never send our JWT to either.
  };
  // Prefer drawing into this window. The spawned player is the fallback for installs that ship no
  // libmpv, so an old package keeps working instead of refusing to play.
  if (await embeddedPlayerAvailable()) {
    try {
      await call<void>("player_embed_start", { args });
      trace("native player: embedded surface started");
      return "embedded";
    } catch (e) {
      trace(`native player: embedded surface failed: ${e instanceof Error ? e.message : String(e)}`, "warn");
    }
  }
  await call<void>("native_player_start", { args });
  return "window";
}

export async function stopNativePlayer(): Promise<void> {
  if (!isTauri()) return;
  // Both surfaces are stopped unconditionally: whichever one this build used, the room screen must
  // never leave a film playing behind a closed room.
  await call<void>("player_embed_stop").catch(() => {});
  await call<void>("native_player_stop").catch(() => {});
}

/** Calls `cb` when the player window closes. Resolves to an unsubscribe function. */
export async function onNativePlayerExit(cb: () => void): Promise<() => void> {
  if (!isTauri()) return () => {};
  const { listen } = await import("@tauri-apps/api/event");
  return listen("native-player-exit", () => cb());
}
