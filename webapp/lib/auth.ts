"use client";

import { useSyncExternalStore } from "react";
import { apiUrl } from "./config";
import type { AuthRoom, AuthTokens, AuthUser, UserRole } from "./types";

export type { AuthRoom, AuthTokens, AuthUser, UserRole };
export type { AdminUser } from "./types";

interface AuthState {
  user: AuthUser | null;
  access: string | null;
  refresh: string | null;
  hydrated: boolean;
}

const ACCESS_KEY = "stream_access_token";
const REFRESH_KEY = "stream_refresh_token";
const USER_KEY = "stream_user";

const SERVER_SNAPSHOT: AuthState = { user: null, access: null, refresh: null, hydrated: false };

let state: AuthState = SERVER_SNAPSHOT;
const listeners = new Set<() => void>();

function readStorage(): AuthState {
  try {
    const access = window.localStorage.getItem(ACCESS_KEY);
    const refresh = window.localStorage.getItem(REFRESH_KEY);
    const rawUser = window.localStorage.getItem(USER_KEY);
    return {
      user: rawUser ? (JSON.parse(rawUser) as AuthUser) : null,
      access,
      refresh,
      hydrated: true,
    };
  } catch {
    return { user: null, access: null, refresh: null, hydrated: true };
  }
}

function setState(next: AuthState) {
  state = next;
  listeners.forEach((l) => l());
}

if (typeof window !== "undefined") {
  state = readStorage();
}

function subscribe(cb: () => void) {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

const getSnapshot = () => state;
const getServerSnapshot = () => SERVER_SNAPSHOT;

export function useAuth(): AuthState {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

export function getAccessToken(): string | null {
  return state.access;
}

export function getRefreshToken(): string | null {
  return state.refresh;
}

export function saveAuth(tokens: AuthTokens, user: AuthUser) {
  try {
    window.localStorage.setItem(ACCESS_KEY, tokens.access_token);
    window.localStorage.setItem(REFRESH_KEY, tokens.refresh_token);
    window.localStorage.setItem(USER_KEY, JSON.stringify(user));
  } catch {
    /* storage unavailable — non-fatal */
  }
  setState({ user, access: tokens.access_token, refresh: tokens.refresh_token, hydrated: true });
}

export function updateUser(user: AuthUser) {
  try {
    window.localStorage.setItem(USER_KEY, JSON.stringify(user));
  } catch {
    /* storage unavailable — non-fatal */
  }
  setState({ ...state, user });
}

export function clearAuth() {
  try {
    window.localStorage.removeItem(ACCESS_KEY);
    window.localStorage.removeItem(REFRESH_KEY);
    window.localStorage.removeItem(USER_KEY);
  } catch {
    /* storage unavailable — non-fatal */
  }
  setState({ user: null, access: null, refresh: null, hydrated: true });
}

// ── Network helpers ─────────────────────────────────────────────────────────

export function friendlyError(e: unknown): Error {
  const msg = e instanceof Error ? e.message : String(e);
  const name = e instanceof Error ? e.name : "";
  if (name === "AbortError" || (typeof DOMException !== "undefined" && e instanceof DOMException && e.name === "AbortError")) {
    return new Error("سرور پاسخ نداد؛ اتصال را بررسی کن و دوباره تلاش کن");
  }
  if (name === "TypeError" || /failed to fetch|networkerror|load failed|network request failed/i.test(msg)) {
    return new Error("ارتباط با سرور برقرار نشد؛ اتصال اینترنت را بررسی کن");
  }
  return e instanceof Error ? e : new Error(msg);
}

export async function refreshAccessToken(): Promise<boolean> {
  const refresh = state.refresh;
  if (!refresh) {
    clearAuth();
    return false;
  }
  try {
    const res = await fetch(apiUrl("/stream/api/auth/refresh"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refresh }),
    });
    if (!res.ok) {
      clearAuth();
      return false;
    }
    const data = await res.json();
    saveAuth(data, data.user);
    return true;
  } catch {
    clearAuth();
    return false;
  }
}

/** fetch() that attaches the bearer token and retries once after a token refresh on 401. */
export async function authFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const doFetch = (token: string | null): Promise<Response> => {
    const headers = new Headers(init.headers);
    if (token) headers.set("Authorization", `Bearer ${token}`);
    return fetch(apiUrl(path), { ...init, headers });
  };

  let res = await doFetch(state.access);
  if (res.status === 401) {
    const refreshed = await refreshAccessToken();
    if (refreshed) res = await doFetch(state.access);
  }
  return res;
}

// ── Auth API calls ──────────────────────────────────────────────────────────

export async function login(username: string, password: string): Promise<AuthUser> {
  let res: Response;
  try {
    res = await fetch(apiUrl("/stream/api/auth/login"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
  } catch (e) {
    throw friendlyError(e);
  }
  const data = (await res.json().catch(() => ({}))) as Partial<AuthTokens> & { user?: AuthUser; error?: string };
  if (!res.ok) throw new Error(data.error || "خطا در ورود");
  if (!data.access_token || !data.refresh_token || !data.user) throw new Error("پاسخ سرور ناقص بود");
  saveAuth({ access_token: data.access_token, refresh_token: data.refresh_token }, data.user);
  return data.user;
}

export async function register(username: string, password: string, display_name = ""): Promise<AuthUser> {
  let res: Response;
  try {
    res = await fetch(apiUrl("/stream/api/auth/register"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password, display_name }),
    });
  } catch (e) {
    throw friendlyError(e);
  }
  const data = (await res.json().catch(() => ({}))) as Partial<AuthTokens> & { user?: AuthUser; error?: string };
  if (!res.ok) throw new Error(data.error || "خطا در ثبت‌نام");
  if (!data.access_token || !data.refresh_token || !data.user) throw new Error("پاسخ سرور ناقص بود");
  saveAuth({ access_token: data.access_token, refresh_token: data.refresh_token }, data.user);
  return data.user;
}

export async function logout(): Promise<void> {
  const refresh = state.refresh;
  try {
    if (refresh) {
      await fetch(apiUrl("/stream/api/auth/logout"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refresh }),
      });
    }
  } catch {
    /* best effort — still clear local state */
  }
  clearAuth();
}

// ── Permissions (mirror of the backend's rules) ─────────────────────────────

export function isAdmin(user: AuthUser | null): boolean {
  return user?.role === "admin";
}

export function mayControl(user: AuthUser | null): boolean {
  return user?.role === "admin" || user?.can_control === true;
}

export function mayYoutube(user: AuthUser | null): boolean {
  return user?.role === "admin" || user?.youtube_allowed === true;
}

export function mayAdd(user: AuthUser | null): boolean {
  if (!user) return false;
  if (user.role === "admin") return true;
  return user.upload_quota === -1 || user.uploads_used < user.upload_quota;
}
