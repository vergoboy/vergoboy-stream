/**
 * Native mpv player (desktop app only). The web UI keeps owning login, room and media state and hands
 * the player what it needs; the Tauri side (src-tauri/src/native_player.rs) only spawns and watches mpv.
 * Everything here is a no-op outside the Tauri build, so callers can use it freely.
 */
import { API_PREFIX } from "./config";
import { authFetch, getAccessToken } from "./auth";
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

export async function startNativePlayer(opts: { mediaUrl: string; title: string }): Promise<void> {
  const sync = await fetchSyncInfo();
  await call<void>("native_player_start", {
    args: {
      syncUrl: sync.url,
      syncToken: sync.token,
      room: sync.room,
      role: sync.role,
      mediaUrl: opts.mediaUrl,
      title: opts.title,
      accessToken: getAccessToken() ?? undefined,
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
