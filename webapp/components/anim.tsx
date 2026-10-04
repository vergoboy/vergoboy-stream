"use client";
/* eslint-disable react-hooks/refs */

/**
 * `motion` / `AnimatePresence` for the panels and modals, powered by anime.js.
 *
 * It keeps the small declarative surface the components were written against
 * (`initial` / `animate` / `exit` / `transition`) so existing screens get
 * smooth enter and exit motion without each one hand-rolling timelines.
 * Supported values: opacity, x, y, scale, rotate, and width/height (the
 * latter two accept "auto" and "NN%").
 */
import {
  Children,
  createContext,
  createElement,
  isValidElement,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
  type Ref,
} from "react";
import { spring, run } from "@/lib/anim";

type Vals = { opacity?: number; x?: number; y?: number; scale?: number; rotate?: number; width?: number | string; height?: number | string };
type Transition = { duration?: number; delay?: number; type?: string; stiffness?: number; damping?: number };

const PresenceCtx = createContext<{ present: boolean; done: () => void; skipInitial: boolean } | null>(null);

function toStyle(v: Vals | undefined | false): React.CSSProperties {
  if (!v) return {};
  const t: string[] = [];
  if (v.x !== undefined || v.y !== undefined) t.push(`translate(${v.x ?? 0}px, ${v.y ?? 0}px)`);
  if (v.scale !== undefined) t.push(`scale(${v.scale})`);
  if (v.rotate !== undefined) t.push(`rotate(${v.rotate}deg)`);
  const s: React.CSSProperties = {};
  if (v.opacity !== undefined) s.opacity = v.opacity;
  if (t.length) s.transform = t.join(" ");
  if (v.width !== undefined) s.width = v.width;
  if (v.height !== undefined && v.height !== "auto") s.height = v.height;
  return s;
}

function easeFor(t?: Transition) {
  if (t?.type === "spring") return spring({ stiffness: t.stiffness ?? 300, damping: t.damping ?? 24 });
  return "outQuart";
}

function play(el: HTMLElement, from: Vals | undefined, to: Vals, tr: Transition | undefined, done?: () => void) {
  const p: Record<string, unknown> = {
    duration: Math.round((tr?.duration ?? 0.32) * 1000),
    delay: Math.round((tr?.delay ?? 0) * 1000),
    ease: easeFor(tr),
    onComplete: () => {
      // never leave a transform behind: a lingering transform makes this node
      // the containing block for fixed-position descendants
      if (to.x === 0 && to.y === 0 && (to.scale === 1 || to.scale === undefined) && (to.rotate === undefined || to.rotate === 0)) {
        el.style.transform = "";
      }
      if (to.height === "auto") el.style.height = "auto";
      done?.();
    },
  };
  const pair = (a: number | undefined, b: number | undefined, fallback: number) => (b === undefined ? undefined : [a ?? fallback, b]);
  const set = (k: string, v: unknown) => v !== undefined && (p[k] = v);
  set("opacity", pair(from?.opacity, to.opacity, 1));
  set("translateX", pair(from?.x, to.x, 0));
  set("translateY", pair(from?.y, to.y, 0));
  set("scale", pair(from?.scale, to.scale, 1));
  set("rotate", pair(from?.rotate, to.rotate, 0));
  if (to.width !== undefined) p.width = from?.width !== undefined ? [from.width, to.width] : to.width;
  if (to.height !== undefined) {
    const target = to.height === "auto" ? el.scrollHeight : to.height;
    const start = from?.height !== undefined && from.height !== "auto" ? from.height : to.height === "auto" ? 0 : el.offsetHeight;
    el.style.overflow = "hidden";
    p.height = [start, target];
  }
  run(el, p);
}

function mergeRefs<T>(...refs: (Ref<T> | undefined)[]) {
  return (node: T | null) => {
    for (const r of refs) {
      if (typeof r === "function") r(node);
      else if (r && typeof r === "object") (r as { current: T | null }).current = node;
    }
  };
}

function make(tag: string) {
  function MotionEl({
    initial,
    animate: anim,
    exit,
    transition,
    layout: _layout,
    ref,
    style,
    children,
    ...rest
  }: Omit<React.HTMLAttributes<HTMLElement>, "style" | "children"> & {
    initial?: Vals | false;
    animate?: Vals;
    exit?: Vals;
    transition?: Transition;
    layout?: unknown;
    ref?: Ref<HTMLElement>;
    style?: React.CSSProperties;
    children?: ReactNode;
  }) {
    void _layout;
    const own = useRef<HTMLElement | null>(null);
    const presence = useContext(PresenceCtx);
    const prevAnim = useRef<Vals | undefined>(undefined);
    const mounted = useRef(false);
    const skip = initial === false || presence?.skipInitial;
    const animKey = JSON.stringify(anim ?? null);

    // first paint carries the `initial` look so there is no flash of the end state
    const [startStyle] = useState(() => (skip ? {} : toStyle(initial)));

    useLayoutEffect(() => {
      const el = own.current;
      if (!el || !anim) return;
      if (!mounted.current) {
        mounted.current = true;
        prevAnim.current = anim;
        if (skip) {
          Object.assign(el.style, toStyle(anim));
          return;
        }
        play(el, initial || undefined, anim, transition);
        return;
      }
      play(el, prevAnim.current, anim, transition);
      prevAnim.current = anim;
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [animKey]);

    useEffect(() => {
      if (!presence || presence.present) return;
      const el = own.current;
      if (!el || !exit) {
        presence.done();
        return;
      }
      play(el, anim, exit, { duration: 0.2, ...transition }, presence.done);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [presence?.present]);

    return createElement(tag, { ...rest, ref: mergeRefs(own as Ref<HTMLElement>, ref), style: { ...startStyle, ...style } } as Record<string, unknown>, children);
  }
  return MotionEl;
}

const cache = new Map<string, ReturnType<typeof make>>();
export const motion = new Proxy({} as Record<string, ReturnType<typeof make>>, {
  get(_t, tag: string) {
    if (!cache.has(tag)) cache.set(tag, make(tag));
    return cache.get(tag)!;
  },
});

export function AnimatePresence({ children, initial = true }: { children?: ReactNode; initial?: boolean; mode?: string }) {
  const current = Children.toArray(children).filter(isValidElement) as ReactElement[];
  const [, bump] = useState(0);
  const lastRendered = useRef<ReactElement[]>([]);
  const exiting = useRef<Map<string | number, { el: ReactElement; index: number }>>(new Map());
  const firstRender = useRef(true);

  const currentKeys = new Set(current.map((c) => c.key as string));
  // anything that was on screen and is now gone starts exiting
  lastRendered.current.forEach((el, i) => {
    const k = el.key as string;
    if (!currentKeys.has(k) && !exiting.current.has(k)) exiting.current.set(k, { el, index: i });
  });
  // anything that came back cancels its exit
  currentKeys.forEach((k) => exiting.current.delete(k));

  const out: { el: ReactElement; leaving: boolean }[] = current.map((el) => ({ el, leaving: false }));
  [...exiting.current.values()]
    .sort((a, b) => a.index - b.index)
    .forEach(({ el, index }) => out.splice(Math.min(index, out.length), 0, { el, leaving: true }));

  useEffect(() => {
    lastRendered.current = current;
    firstRender.current = false;
  });

  const finish = useCallback((k: string) => {
    if (exiting.current.delete(k)) bump((n) => n + 1);
  }, []);

  const skipInitial = firstRender.current && !initial;
  return (
    <>
      {out.map(({ el, leaving }) => (
        <PresenceCtx.Provider key={el.key} value={{ present: !leaving, done: () => finish(el.key as string), skipInitial }}>
          {el}
        </PresenceCtx.Provider>
      ))}
    </>
  );
}
