"use client";

import { useEffect, useRef, useState } from "react";

/**
 * "Lights down" detector: becomes true after `ms` without pointer / touch / key
 * activity, but only while `armed` (e.g. a video is playing and nothing is open).
 * Any activity brings it straight back to false.
 */
export function useIdle(ms: number, armed: boolean) {
  const [idle, setIdle] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const clear = () => {
      if (timer.current) clearTimeout(timer.current);
      timer.current = null;
    };
    if (!armed) {
      clear();
      // eslint-disable-next-line react-hooks/set-state-in-effect -- disarming must bring the lights back up immediately
      setIdle(false);
      return;
    }
    const wake = () => {
      setIdle(false);
      clear();
      timer.current = setTimeout(() => setIdle(true), ms);
    };
    wake();
    const evs = ["pointermove", "pointerdown", "keydown", "wheel", "touchstart"] as const;
    evs.forEach((e) => window.addEventListener(e, wake, { passive: true }));
    return () => {
      clear();
      evs.forEach((e) => window.removeEventListener(e, wake));
    };
  }, [armed, ms]);

  return idle;
}

/** Tracks viewport shape so layout can switch between phone-portrait, landscape and desktop. */
export function useViewport() {
  const [v, setV] = useState({ w: 1280, h: 800, portrait: false, touch: false });
  useEffect(() => {
    const read = () =>
      setV({
        w: window.innerWidth,
        h: window.innerHeight,
        portrait: window.innerHeight > window.innerWidth,
        touch: window.matchMedia("(pointer: coarse)").matches,
      });
    read();
    window.addEventListener("resize", read);
    window.addEventListener("orientationchange", read);
    return () => {
      window.removeEventListener("resize", read);
      window.removeEventListener("orientationchange", read);
    };
  }, []);
  return { ...v, phone: v.w < 640, compact: v.w < 900 || v.h < 520 };
}
