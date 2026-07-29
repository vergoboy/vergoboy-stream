export function formatTime(sec: number | null | undefined): string {
  const s = Math.max(0, Math.floor(sec || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(r)}` : `${m}:${pad(r)}`;
}

export function isHlsUrl(url: string | null | undefined): boolean {
  if (!url) return false;
  return /\.m3u8(\?.*)?$/i.test(url);
}

export function nameInitial(name: string | null | undefined): string {
  const s = (name || "?").trim();
  return s ? s[0].toUpperCase() : "?";
}

// Deterministic "initials + hue" default avatar — no upload required, and
// stable across sessions since it's derived purely from the name string.
export function nameHue(name: string | null | undefined): number {
  let h = 0;
  const s = String(name || "?");
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360;
  return h;
}

export function nameHueColor(name: string | null | undefined): string {
  return `hsl(${nameHue(name)}, 55%, 48%)`;
}

export function hexToRgba(hex: string, alpha: number): string {
  const m = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex || "#000000");
  if (!m) return `rgba(0,0,0,${alpha})`;
  const r = parseInt(m[1], 16);
  const g = parseInt(m[2], 16);
  const b = parseInt(m[3], 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

export function escapeHtml(s: string | null | undefined): string {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c] as string));
}

export function levelLabelFromUrl(url: string | string[] | null | undefined): string | null {
  const u = Array.isArray(url) ? url[0] : url;
  const m = /\/([^/]+)\/index\.m3u8/.exec(u || "");
  return m ? m[1] : null;
}
