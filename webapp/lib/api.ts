import { apiUrl } from "./config";
import { authFetch, friendlyError, getAccessToken } from "./auth";
import type { AdminUser, AuthRoom, AuthUser } from "./types";

const FETCH_TIMEOUT_MS = 45000;

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const res = await authFetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    const data = (await res.json().catch(() => ({}))) as T & { error?: string };
    if (!res.ok) throw new Error(data.error || `${path} failed (${res.status})`);
    return data;
  } catch (e) {
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
    xhr.open("POST", apiUrl("/stream/api/upload"));
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

export const api = {
  addUrl: (url: string, title: string, name: string) =>
    postJson<UploadResult>("/stream/api/add-url", { url, title, name }),

  youtubeFormats: (url: string) =>
    postJson<{ title: string; thumbnail: string; formats: { format_id: string; height: number; ext: string; tbr: number; label: string }[] }>(
      "/stream/api/youtube-formats",
      { url }
    ),

  addYoutube: (url: string, format_id: string, title: string, name: string) =>
    postJson<UploadResult>("/stream/api/add-youtube", { url, format_id, title, name }),

  newLiveKey: () =>
    authFetch("/stream/api/live/new-key", { method: "POST" }).then((r) => r.json()) as Promise<{
      key: string;
      push_url: string;
      playback_url: string;
    }>,

  addLive: (playback_url: string, title: string, name: string, key = "") =>
    postJson<UploadResult>("/stream/api/add-live", { playback_url, title, name, key }),

  requestQuality: (item_id: string, label: string, name: string) =>
    postJson<{ ok: true }>("/stream/api/request-quality", { item_id, label, name }),

  removeItem: (itemId: string, name: string) =>
    authFetch(`/stream/api/playlist/${itemId}?name=${encodeURIComponent(name)}`, { method: "DELETE" }),

  cleanup: () =>
    authFetch("/stream/api/cleanup", { method: "POST" }).then((r) => r.json()) as Promise<{
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
    return postForm<{ id: string; url: string }>("/stream/api/subtitle-upload", fd);
  },

  subtitleUrl: (item_id: string, url: string, label: string, name: string) =>
    postJson<{ id: string; url: string }>("/stream/api/subtitle-url", { item_id, url, label, lang: "fa", name }),

  addAudioTrack: (item_id: string, url: string, label: string, name: string) =>
    postJson<{ ok: true }>("/stream/api/audio-track", { item_id, url, label, name }),

  chatHistory: () =>
    authFetch("/stream/api/chat/history").then((r) => r.json()) as Promise<{ messages: import("./types").ChatMessage[] }>,

  chatClear: (name: string) =>
    authFetch(`/stream/api/chat/clear?name=${encodeURIComponent(name)}`, { method: "POST" }),

  chatImage: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return postForm<{ url: string }>("/stream/api/chat/image", fd);
  },

  avatarUpload: (file: File, name: string) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("name", name);
    return postForm<{ url: string }>("/stream/api/avatar", fd);
  },

  voiceToken: (name: string, avatarUrl: string | null) =>
    postJson<{ url: string; token: string; room: string; identity: string }>(
      "/stream/api/voice/token",
      { name, avatar_url: avatarUrl }
    ),

  archiveSearch: (q: string, sources: string[]) =>
    postJson<{ results: import("./types").ArchiveResult[] }>("/stream/api/archive/search", { q, sources }),

  archiveTitle: (source: string, url: string) =>
    postJson<import("./types").ArchiveTitle>("/stream/api/archive/title", { source, url }),

  archiveFiles: (url: string) =>
    postJson<{ files: import("./types").ArchiveFile[] }>("/stream/api/archive/files", { url }),

  addMany: (items: { title: string; url: string }[], name: string) =>
    postJson<{ added: number; skipped: number; dead: string[] }>("/stream/api/add-many", { items, name }),

  // ── Auth / rooms / admin ──────────────────────────────────────────────

  me: async () => {
    const res = await authFetch("/stream/api/auth/me");
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
    postJson<{ room: AuthRoom; user: AuthUser }>("/stream/api/room/join", { code }),

  roomMine: () => postJson<{ room: AuthRoom; user: AuthUser }>("/stream/api/room/mine", {}),

  roomInfo: (code: string) =>
    authFetch(`/stream/api/room/info?code=${encodeURIComponent(code)}`).then(async (r) => {
      const data = (await r.json().catch(() => ({}))) as { room?: AuthRoom; error?: string };
      if (!r.ok) throw new Error(data.error || "اتاق پیدا نشد");
      return data;
    }),

  adminStats: async () => {
    const res = await authFetch("/stream/api/admin/stats");
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
    const res = await authFetch("/stream/api/admin/users");
    const data = (await res.json().catch(() => ({}))) as { users?: AdminUser[]; error?: string };
    if (!res.ok) throw new Error(data.error || "دسترسی ادمین لازم است");
    return { users: data.users ?? [] };
  },

  adminUpdateUser: (userId: string, patch: { role?: string; can_control?: boolean; youtube_allowed?: boolean; upload_quota?: number; is_active?: boolean }) =>
    postJson<{ user: AdminUser }>(`/stream/api/admin/users/${userId}`, patch),

  adminDeleteUser: async (userId: string): Promise<void> => {
    const res = await authFetch(`/stream/api/admin/users/${userId}`, { method: "DELETE" });
    const data = (await res.json().catch(() => ({}))) as { error?: string };
    if (!res.ok) throw new Error(data.error || "حذف کاربر ناموفق بود");
  },
};
