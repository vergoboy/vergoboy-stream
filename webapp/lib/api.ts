import { apiUrl, API_PREFIX } from "./config";
import { authFetch, friendlyError, getAccessToken } from "./auth";
import type { AdminUser, AuthRoom, AuthUser } from "./types";

const FETCH_TIMEOUT_MS = 45000;
// Archive calls reach digimoviez through the backend, and when Cloudflare has
// invalidated the clearance the backend spends one ~80 s solve minting a new
// one before it can answer. The default budget aborted those requests at 45 s
// and reported «سرور پاسخ نداد؛ اتصال را بررسی کن…» for a request that was in
// fact working (live 2026-10-26), so these get room for one solve.
const ARCHIVE_TIMEOUT_MS = 120000;

export class ArchiveApiError extends Error {
  kind?: string;
  manualSessionHelpful?: boolean;
  detail?: string;
  constructor(message: string, kind?: string, manualSessionHelpful?: boolean, detail?: string) {
    super(message);
    this.name = "ArchiveApiError";
    this.kind = kind;
    this.manualSessionHelpful = manualSessionHelpful;
    this.detail = detail;
  }
}

export type ArchiveAuthStatus = {
  enabled: boolean;
  state: string;
  kind: string;
  message: string;
  proxy_configured?: boolean;
};

async function postJson<T>(path: string, body: unknown, timeoutMs: number = FETCH_TIMEOUT_MS): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await authFetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    const data = (await res.json().catch(() => ({}))) as T & { error?: string; kind?: string; manual_session_helpful?: boolean; detail?: string };
    if (!res.ok) throw new ArchiveApiError(data.error || `${path} failed (${res.status})`, data.kind, data.manual_session_helpful, data.detail);
    return data;
  } catch (e) {
    if (e instanceof ArchiveApiError) throw e;
    throw friendlyError(e);
  } finally {
    clearTimeout(timer);
  }
}

async function postForm<T>(path: string, form: FormData): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const res = await authFetch(path, { method: "POST", body: form, signal: controller.signal });
    const data = (await res.json().catch(() => ({}))) as T & { error?: string };
    if (!res.ok) throw new Error(data.error || `${path} failed (${res.status})`);
    return data;
  } catch (e) {
    throw friendlyError(e);
  } finally {
    clearTimeout(timer);
  }
}

export interface UploadResult {
  id: string;
  [key: string]: unknown;
}

export function uploadVideo(
  file: File,
  title: string,
  name: string,
  onProgress?: (pct: number) => void
): Promise<UploadResult> {
  return new Promise((resolve, reject) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("title", title);
    fd.append("name", name);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", apiUrl(API_PREFIX + "/upload"));
    const token = getAccessToken();
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    xhr.upload.addEventListener("progress", (ev) => {
      if (ev.lengthComputable && onProgress) onProgress((ev.loaded / ev.total) * 100);
    });
    xhr.onload = () => {
      try {
        const data = JSON.parse(xhr.responseText);
        if (xhr.status >= 200 && xhr.status < 300) resolve(data);
        else reject(new Error(data.error || "آپلود ناموفق بود"));
      } catch {
        reject(new Error("پاسخ سرور قابل‌فهم نبود"));
      }
    };
    xhr.onerror = () => reject(new Error("خطا در ارتباط با سرور"));
    xhr.send(fd);
  });
}

export type YoutubeSubtitle = { code: string; name: string };

export type YoutubeFormat = {
  format_id: string;
  height: number;
  ext: string;
  tbr: number;
  label: string;
};

export type YoutubeScanVideo = {
  type: "video";
  title: string;
  thumbnail: string;
  duration?: number;
  duration_string?: string;
  channel?: string;
  view_count?: number;
  upload_date?: string;
  subtitles: YoutubeSubtitle[];
  auto_captions: YoutubeSubtitle[];
  formats: YoutubeFormat[];
};

export type YoutubeScanPlaylist = {
  type: "playlist";
  playlist_title: string;
  playlist_count: number;
  entries: {
    id: string;
    title: string;
    duration?: number;
    duration_string?: string;
    thumbnail?: string;
    url: string;
  }[];
};

export const api = {
  addUrl: (url: string, title: string, name: string) =>
    postJson<UploadResult>(API_PREFIX + "/add-url", { url, title, name }),

  youtubeFormats: (url: string) =>
    postJson<YoutubeScanVideo | YoutubeScanPlaylist>(
      API_PREFIX + "/youtube-formats",
      { url },
      120000
    ),

  addYoutube: (url: string, format_id: string, title: string, name: string, sub_lang = "") =>
    postJson<UploadResult>(API_PREFIX + "/add-youtube", { url, format_id, title, name, sub_lang }),

  addYoutubePlaylist: (url: string, title: string, name: string) =>
    postJson<{ added: number }>(API_PREFIX + "/add-youtube-playlist", { url, title, name }),

  newLiveKey: () =>
    authFetch(API_PREFIX + "/live/new-key", { method: "POST" }).then((r) => r.json()) as Promise<{
      key: string;
      push_url: string;
      server_url?: string;
      push_urls?: string[];
      playback_url: string;
    }>,

  addLive: (playback_url: string, title: string, name: string, key = "") =>
    postJson<UploadResult>(API_PREFIX + "/add-live", { playback_url, title, name, key }),

  /** Lazy encoding: asks the server to build the HLS for an `idle` item (idempotent). */
  ensureHls: (item_id: string) =>
    postJson<{ ok: boolean; message: string }>(API_PREFIX + `/items/${encodeURIComponent(item_id)}/ensure-hls`, {}),
  requestQuality: (item_id: string, label: string, name: string) =>
    postJson<{ ok: true }>(API_PREFIX + "/request-quality", { item_id, label, name }),

  removeItem: (itemId: string, name: string) =>
    authFetch(API_PREFIX + `/playlist/${itemId}?name=${encodeURIComponent(name)}`, { method: "DELETE" }),

  cleanup: () =>
    authFetch(API_PREFIX + "/cleanup", { method: "POST" }).then((r) => r.json()) as Promise<{
      total: number;
      removed: Record<string, string[]>;
    }>,

  subtitleUpload: (file: File, item_id: string, label: string, name: string) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("item_id", item_id);
    fd.append("label", label);
    fd.append("lang", "fa");
    fd.append("name", name);
    return postForm<{ id: string; url: string }>(API_PREFIX + "/subtitle-upload", fd);
  },

  subtitleUrl: (item_id: string, url: string, label: string, name: string) =>
    postJson<{ id: string; url: string }>(API_PREFIX + "/subtitle-url", { item_id, url, label, lang: "fa", name }),

  addAudioTrack: (item_id: string, url: string, label: string, name: string) =>
    postJson<{ ok: true }>(API_PREFIX + "/audio-track", { item_id, url, label, name }),

  chatHistory: () =>
    authFetch(API_PREFIX + "/chat/history").then((r) => r.json()) as Promise<{ messages: import("./types").ChatMessage[] }>,

  chatClear: (name: string) =>
    authFetch(API_PREFIX + `/chat/clear?name=${encodeURIComponent(name)}`, { method: "POST" }),

  chatImage: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return postForm<{ url: string }>(API_PREFIX + "/chat/image", fd);
  },

  avatarUpload: (file: File, name: string) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("name", name);
    return postForm<{ url: string }>(API_PREFIX + "/avatar", fd);
  },

  voiceToken: (name: string, avatarUrl: string | null) =>
    postJson<{ url: string; token: string; room: string; identity: string }>(
      API_PREFIX + "/voice/token",
      { name, avatar_url: avatarUrl }
    ),

  archiveAuthStatus: () =>
    authFetch(API_PREFIX + "/archive/auth/status").then(async (r) => {
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new ArchiveApiError(data.error || `archive/auth/status failed (${r.status})`, data.kind, data.manual_session_helpful, data.detail);
      return data as ArchiveAuthStatus;
    }),
  archiveAuthCheck: () =>
    postJson<ArchiveAuthStatus>(API_PREFIX + "/archive/auth/check", {}, ARCHIVE_TIMEOUT_MS),
  archiveSearch: (filters: import("./types").ArchiveSearchFilters) =>
    postJson<{ results: import("./types").ArchiveResult[] }>(API_PREFIX + "/archive/search", { filters }, ARCHIVE_TIMEOUT_MS),

  archiveFilters: () =>
    authFetch(API_PREFIX + "/archive/filters").then(async (r) => {
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || "خطا در دریافت فیلترها");
      return data as import("./types").ArchiveFilterOptions;
    }),

  archiveTitle: (source: string, url: string) =>
    postJson<import("./types").ArchiveTitle>(API_PREFIX + "/archive/title", { source, url }, ARCHIVE_TIMEOUT_MS),

  archiveFiles: (url: string) =>
    postJson<{ files: import("./types").ArchiveFile[] }>(API_PREFIX + "/archive/files", { url }, ARCHIVE_TIMEOUT_MS),

  addMany: (items: { title: string; url: string }[], name: string) =>
    postJson<{ added: number; skipped: number; dead: string[] }>(API_PREFIX + "/add-many", { items, name }),

  // ── Auth / rooms / admin ──────────────────────────────────────────────

  me: async () => {
    const res = await authFetch(API_PREFIX + "/auth/me");
    const data = (await res.json().catch(() => ({}))) as {
      user?: AuthUser;
      room?: AuthRoom | null;
      own_room?: AuthRoom;
      error?: string;
    };
    if (!res.ok) throw new Error(data.error || "نشست نامعتبر است");
    return data;
  },

  roomJoin: (code: string) =>
    postJson<{ room: AuthRoom; user: AuthUser }>(API_PREFIX + "/room/join", { code }),

  roomMine: () => postJson<{ room: AuthRoom; user: AuthUser }>(API_PREFIX + "/room/mine", {}),

  roomMembers: () =>
    postJson<{ owner_id: string; online: import("./types").RoomMember[]; banned: import("./types").RoomBannedUser[] }>(
      API_PREFIX + "/room/members",
      {}
    ),

  roomPromote: (userId: string) => postJson<{ ok: true; can_control: boolean }>(API_PREFIX + "/room/members/promote", { user_id: userId }),

  roomDemote: (userId: string) => postJson<{ ok: true; can_control: boolean }>(API_PREFIX + "/room/members/demote", { user_id: userId }),

  roomBan: (userId: string) => postJson<{ ok: true; already?: boolean }>(API_PREFIX + "/room/members/ban", { user_id: userId }),

  roomUnban: (userId: string) => postJson<{ ok: true }>(API_PREFIX + "/room/members/unban", { user_id: userId }),


  roomInfo: (code: string) =>
    authFetch(API_PREFIX + `/room/info?code=${encodeURIComponent(code)}`).then(async (r) => {
      const data = (await r.json().catch(() => ({}))) as { room?: AuthRoom; error?: string };
      if (!r.ok) throw new Error(data.error || "اتاق پیدا نشد");
      return data;
    }),

  adminStats: async () => {
    const res = await authFetch(API_PREFIX + "/admin/stats");
    const data = (await res.json().catch(() => ({}))) as { users?: number; rooms?: number; online?: number; media?: number; error?: string };
    if (!res.ok) throw new Error(data.error || "دسترسی ادمین لازم است");
    return {
      users: data.users ?? 0,
      rooms: data.rooms ?? 0,
      online: data.online ?? 0,
      media: data.media ?? 0,
    };
  },

  adminUsers: async () => {
    const res = await authFetch(API_PREFIX + "/admin/users");
    const data = (await res.json().catch(() => ({}))) as { users?: AdminUser[]; error?: string };
    if (!res.ok) throw new Error(data.error || "دسترسی ادمین لازم است");
    return { users: data.users ?? [] };
  },

  adminUpdateUser: (userId: string, patch: { role?: string; can_control?: boolean; youtube_allowed?: boolean; upload_quota?: number; is_active?: boolean }) =>
    postJson<{ user: AdminUser }>(API_PREFIX + `/admin/users/${userId}`, patch),

  adminDeleteUser: async (userId: string): Promise<void> => {
    const res = await authFetch(API_PREFIX + `/admin/users/${userId}`, { method: "DELETE" });
    const data = (await res.json().catch(() => ({}))) as { error?: string };
    if (!res.ok) throw new Error(data.error || "حذف کاربر ناموفق بود");
  },
};
