/**
 * Motion for the whole app lives here, built on anime.js v4.
 *
 * One rule shapes every preset below: animations answer something the person
 * just did (open, close, confirm) or draw the eye to one thing. Nothing loops
 * forever except the two ambient details of the room itself (dust in the
 * projector beam and the marquee bulbs), and those stop for reduced-motion.
 */
import { animate, spring, stagger, utils, createTimeline, type JSAnimation } from "animejs";

export { animate, spring, stagger, utils, createTimeline };
export type { JSAnimation };

export function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}

/** Soft, slightly overshooting spring — used for things that "land" (menus, seats). */
export const pop = () => spring({ stiffness: 380, damping: 26, mass: 1 });
/** Calmer spring for panels that glide in. */
export const glide = () => spring({ stiffness: 240, damping: 30, mass: 1 });

type El = Element | null | undefined;

/** Run an animation, or jump straight to its end state when motion is reduced. */
export function run(targets: El | El[] | NodeListOf<Element>, params: Record<string, unknown>): JSAnimation | null {
  if (!targets) return null;
  const list = Array.isArray(targets) ? targets.filter(Boolean) : targets;
  if (Array.isArray(list) && list.length === 0) return null;
  if (prefersReducedMotion()) {
    // collapse to the final value of every from→to pair
    const flat: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(params)) {
      if (["duration", "delay", "ease", "onComplete", "onBegin", "onUpdate", "loop", "alternate"].includes(k)) continue;
      flat[k] = Array.isArray(v) ? v[v.length - 1] : v;
    }
    const a = animate(list as never, { ...flat, duration: 1, onComplete: params.onComplete as never });
    return a;
  }
  return animate(list as never, params as never);
}

/** Menu / popover opening: scales up from the point it grew from. */
export function popIn(el: El, opts: { from?: string; y?: number } = {}) {
  if (!el) return;
  (el as HTMLElement).style.transformOrigin = opts.from ?? "50% 100%";
  run(el, {
    opacity: [0, 1],
    scale: [0.9, 1],
    translateY: [opts.y ?? 8, 0],
    duration: 420,
    ease: pop(),
  });
  const items = el.querySelectorAll("[data-pop-item]");
  if (items.length) {
    run(items, { opacity: [0, 1], translateY: [6, 0], delay: stagger(18, { start: 60 }), duration: 320, ease: "outQuad" });
  }
}

export function popOut(el: El, done?: () => void) {
  if (!el) return done?.();
  run(el, { opacity: 0, scale: 0.94, translateY: 6, duration: 140, ease: "inQuad", onComplete: () => done?.() });
}

/** Tactile press feedback for any button-like thing. */
export function press(el: El) {
  run(el, { scale: [0.88, 1], duration: 380, ease: spring({ stiffness: 520, damping: 14 }) });
}

/** Expanding ring from a point — double-tap seek and play/pause confirmations. */
export function ripple(host: HTMLElement | null, x: number, y: number, color = "rgba(255,255,255,.22)") {
  if (!host) return;
  const dot = document.createElement("span");
  const size = 160;
  Object.assign(dot.style, {
    position: "absolute",
    left: `${x - size / 2}px`,
    top: `${y - size / 2}px`,
    width: `${size}px`,
    height: `${size}px`,
    borderRadius: "50%",
    background: color,
    pointerEvents: "none",
    zIndex: "8",
  });
  host.appendChild(dot);
  run(dot, {
    scale: [0.2, 1.5],
    opacity: [0.9, 0],
    duration: 650,
    ease: "outQuart",
    onComplete: () => dot.remove(),
  });
}
