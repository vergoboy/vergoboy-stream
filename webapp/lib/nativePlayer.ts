/**
 * Native mpv player (desktop app only). The web UI keeps owning login, room and media state and hands
 * the player what it needs; the Tauri side (src-tauri/src/native_player.rs) only spawns and watches mpv.
 * Everything here is a no-op outside the Tauri build, so callers can use it freely.
 */
import { API_PREFIX, mediaUrl } from "./config";
import { authFetch } from "./auth";
import { isTauri } from "./tauri";

type SyncInfo = { url: string; token: string; room: string; role: "controller" | "watcher" };

async function call<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<T>(cmd, args);
}

/** True when running in the desktop app AND the mpv fork + sync plugin are installed next to it. */
export async function nativePlayerAvailable(): Promise<boolean> {
  if (!isTauri()) return false;
  try {
    return await call<boolean>("native_player_available");
  } catch {
    return false;
  }
}

/** Short-lived token for the room sync service. Role comes from the user's real room permissions. */
export async function fetchSyncInfo(): Promise<SyncInfo> {
  const res = await authFetch(`${API_PREFIX}/room/sync-token`, { method: "POST" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data?.error === "string" ? data.error : `خطای سرور (${res.status})`);
  return data as SyncInfo;
}

export type NativeSource = { kind: "original" | "remote" | "live"; url: string; expires_at: number | null };

/** The original file (signed URL) or the item's own source link (opt-in). Nothing is transcoded. */
export async function fetchNativeSource(itemId: string): Promise<NativeSource> {
  const res = await authFetch(`${API_PREFIX}/items/${encodeURIComponent(itemId)}/native-source`);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data?.error === "string" ? data.error : `خطای سرور (${res.status})`);
  return data as NativeSource;
}

/** Best-effort line in the app's log (app.log); never throws. */
export function trace(message: string, level: "debug" | "info" | "warn" | "error" = "info"): void {
  if (!isTauri()) return;
  call<void>("client_log", { level, message }).catch(() => {});
}

export async function startNativePlayer(opts: { itemId: string; title: string }): Promise<void> {
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
  await call<void>("native_player_start", {
    args: {
      syncUrl: sync.url,
      syncToken: sync.token,
      room: sync.room,
      role: sync.role,
      mediaUrl: mediaUrl(src.url),
      title: opts.title,
      // The signed URL is the credential; a remote source is a third-party host.
      // Never send our JWT to either.
    },
  });
}

export async function stopNativePlayer(): Promise<void> {
  if (isTauri()) await call<void>("native_player_stop");
}

/** Calls `cb` when the player window closes. Resolves to an unsubscribe function. */
export async function onNativePlayerExit(cb: () => void): Promise<() => void> {
  if (!isTauri()) return () => {};
  const { listen } = await import("@tauri-apps/api/event");
  return listen("native-player-exit", () => cb());
}
