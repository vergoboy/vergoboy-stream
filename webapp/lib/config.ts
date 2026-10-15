// In production this app is exported statically and served BY the Flask
// app itself under BASE_PATH, so a relative path is same-origin and
// API_ORIGIN stays empty. In dev, point it at wherever `python app.py` is
// actually running (see .env.local.example) — the Flask side already needs a
// small CORS allowance for this to work locally (see DEPLOYMENT_NOTES).
export const API_ORIGIN = process.env.NEXT_PUBLIC_API_ORIGIN ?? "";

// One codebase serves several instances under different URL prefixes: /stream
// (the original) and /w2g (the second instance). Next inlines basePath at
// build time (see next.config.ts), so this must equal the value that config
// receives — both read NEXT_PUBLIC_BASE_PATH with the same "/stream" default.
export const BASE_PATH =
  process.env.NEXT_PUBLIC_BUILD_TARGET === "tauri"
    ? ""
    : process.env.NEXT_PUBLIC_BASE_PATH || "/stream";

// Where the backend mounts its API, as opposed to where these assets are
// served from. The two differ only for the desktop ("tauri") target, which has
// no mount point of its own — its assets load from tauri://localhost, hence the
// empty BASE_PATH above — but can still talk to an instance served under a
// path. NEXT_PUBLIC_API_BASE_PATH names that instance's path (e.g. "/w2g");
// unset, it follows BASE_PATH, so the browser build is unchanged.
export const API_BASE_PATH = process.env.NEXT_PUBLIC_API_BASE_PATH ?? BASE_PATH;

// The backend serves every endpoint under API_BASE_PATH. Callers append the
// part after /api, e.g. `${API_PREFIX}/auth/login` or `${API_PREFIX}/upload`.
export const API_PREFIX = `${API_BASE_PATH}/api`;

export const SOCKET_PATH = `${API_BASE_PATH}/socket.io`;

export function apiUrl(path: string): string {
  return `${API_ORIGIN}${path}`;
}

export function mediaUrl(path: string): string {
  if (/^(?:https?:)?\/\//i.test(path) || /^(?:blob|data):/i.test(path)) return path;
  return apiUrl(path);
}
