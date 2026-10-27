/** What this server has switched on (GET /api/capabilities). Cached for the page's lifetime. */
import { API_PREFIX } from "./config";
import { authFetch } from "./auth";

export type ServerCapabilities = {
  version: number;
  server: {
    native_original: boolean; // signed original-file URLs work
    native_remote: boolean; // item source links may be played directly
    sync: boolean; // the room sync service is configured
  };
};

let cached: Promise<ServerCapabilities> | null = null;

export function fetchCapabilities(): Promise<ServerCapabilities> {
  if (!cached) {
    cached = authFetch(`${API_PREFIX}/capabilities`)
      .then((res) => {
        if (!res.ok) throw new Error(`capabilities ${res.status}`);
        return res.json() as Promise<ServerCapabilities>;
      })
      .catch((e) => {
        cached = null; // do not cache a failure
        throw e;
      });
  }
  return cached;
}
