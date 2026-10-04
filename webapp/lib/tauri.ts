/**
 * Thin, defensive wrapper around the Tauri window API.
 *
 * Everything here is a no-op in the plain web build, so components can call
 * these freely. The Tauri JS API is imported lazily so the web bundle never
 * pays for it.
 */
export const IS_TAURI_BUILD = process.env.NEXT_PUBLIC_BUILD_TARGET === "tauri";

export function isTauri(): boolean {
  return IS_TAURI_BUILD && typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

export function isMobileOS(): boolean {
  if (typeof navigator === "undefined") return false;
  return /android|iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.maxTouchPoints > 1 && /Macintosh/i.test(navigator.userAgent));
}

/** True when we can genuinely shrink the app window into an always-on-top mini player. */
export function canNativeFloat(): boolean {
  return isTauri() && !isMobileOS();
}

type Win = Awaited<ReturnType<typeof getWin>>;
async function getWin() {
  const mod = await import("@tauri-apps/api/window");
  return { mod, win: mod.getCurrentWindow() };
}

const MINI_W = 420;
const MINI_H = 236; // 16:9 + a hair for the drag strip

/**
 * Turn the app window into a small, frameless, always-on-top video window parked
 * in the bottom-right corner of the current monitor. Returns a function that
 * restores the exact previous size, position and chrome.
 */
export async function enterNativeMini(): Promise<() => Promise<void>> {
  const { mod, win } = await getWin();
  const { LogicalSize, LogicalPosition } = mod;

  const scale = await win.scaleFactor();
  const [inner, pos, deco, onTop, maximized, fullscreen] = await Promise.all([
    win.innerSize(),
    win.outerPosition(),
    win.isDecorated(),
    win.isAlwaysOnTop(),
    win.isMaximized(),
    win.isFullscreen(),
  ]);

  if (fullscreen) await win.setFullscreen(false);
  if (maximized) await win.unmaximize();

  await win.setMinSize(new LogicalSize(240, 135));
  await win.setDecorations(false);
  await win.setSize(new LogicalSize(MINI_W, MINI_H));
  try {
    const mon = await mod.currentMonitor();
    if (mon) {
      const ms = mon.scaleFactor || scale;
      const x = (mon.position.x + mon.size.width) / ms - MINI_W - 24;
      const y = (mon.position.y + mon.size.height) / ms - MINI_H - 64;
      await win.setPosition(new LogicalPosition(Math.max(0, x), Math.max(0, y)));
    }
  } catch {
    /* positioning is best-effort */
  }
  await win.setAlwaysOnTop(true);

  return async () => {
    await win.setAlwaysOnTop(onTop);
    await win.setDecorations(deco);
    await win.setMinSize(new LogicalSize(380, 600));
    await win.setSize(new LogicalSize(inner.width / scale, inner.height / scale));
    await win.setPosition(new LogicalPosition(pos.x / scale, pos.y / scale));
    if (maximized) await win.maximize();
  };
}

/** OS-level fullscreen for the desktop shell (element fullscreen is unreliable in some webviews). */
export async function setNativeFullscreen(on: boolean): Promise<void> {
  if (!isTauri() || isMobileOS()) return;
  const { win } = await getWin();
  await win.setFullscreen(on);
}

/**
 * Ask before the window closes. `shouldAsk` decides per attempt; `ask` shows
 * our own dialog and resolves true to really close. Returns an unsubscribe.
 */
export async function guardNativeClose(shouldAsk: () => boolean, ask: () => Promise<boolean>): Promise<() => void> {
  if (!isTauri()) return () => {};
  const { win } = await getWin();
  const unlisten = await win.onCloseRequested(async (event) => {
    if (!shouldAsk()) return;
    event.preventDefault();
    if (await ask()) await win.destroy();
  });
  return unlisten;
}

export type { Win };
