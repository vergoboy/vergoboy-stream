// In production this app is exported statically and served BY the Flask
// app itself at /stream/, so a relative path is same-origin and this stays
// empty. In dev, point it at wherever `python app.py` is actually running
// (see .env.local.example) — the Flask side already needs a small CORS
// allowance for this to work locally (see DEPLOYMENT_NOTES).
export const API_ORIGIN = process.env.NEXT_PUBLIC_API_ORIGIN ?? "";

export const SOCKET_PATH = "/stream/socket.io";

export function apiUrl(path: string): string {
  return `${API_ORIGIN}${path}`;
}

export function mediaUrl(path: string): string {
  if (/^(?:https?:)?\/\//i.test(path) || /^(?:blob|data):/i.test(path)) return path;
  return apiUrl(path);
}
