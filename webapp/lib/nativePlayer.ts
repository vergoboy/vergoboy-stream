/**
 * Native mpv player (desktop app only). The web UI keeps owning login, room and media state and hands
 * the player what it needs; the Tauri side (src-tauri/src/native_player.rs) only spawns and watches mpv.
 * Everything here is a no-op outside the Tauri build, so callers can use it freely.
 */
import { API_PREFIX, mediaUrl } from "./config";
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

export type NativeSource = { kind: "original" | "remote" | "hls"; url: string; expires_at: number | null };

/** Original file (signed URL), the item's own source link (opt-in), or the HLS fallback. */
export async function fetchNativeSource(itemId: string): Promise<NativeSource> {
  const res = await authFetch(`${API_PREFIX}/items/${encodeURIComponent(itemId)}/native-source`);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data?.error === "string" ? data.error : `خطای سرور (${res.status})`);
  return data as NativeSource;
}

export async function startNativePlayer(opts: { itemId: string; fallbackUrl: string; title: string }): Promise<void> {
  const sync = await fetchSyncInfo();
  // Prefer the original file. Any failure (feature not configured on the server yet, file already
  // cleaned up, ...) falls back to the HLS the web player uses, so the button keeps working.
  let url = opts.fallbackUrl;
  let bearer: string | null = getAccessToken();
  try {
    const src = await fetchNativeSource(opts.itemId);
    url = mediaUrl(src.url);
    // original: the signed URL is the credential; remote: a third-party host. Never send our JWT to either.
    if (src.kind !== "hls") bearer = null;
  } catch {
    /* keep the HLS fallback */
  }
  await call<void>("native_player_start", {
    args: {
      syncUrl: sync.url,
      syncToken: sync.token,
      room: sync.room,
      role: sync.role,
      mediaUrl: url,
      title: opts.title,
      accessToken: bearer ?? undefined,
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
