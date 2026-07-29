"use client";

import { useEffect, useState } from "react";

// SSR-safe localStorage-backed state. Reads the stored value only after
// mount (avoids hydration mismatches in a statically-exported app that
// still runs an initial server-rendered pass for the shell).
export function useLocalStorage<T>(key: string, initial: T) {
  const [value, setValue] = useState<T>(initial);
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(key);
      // eslint-disable-next-line react-hooks/set-state-in-effect -- one-time read of an external store (localStorage) on mount; this is the standard SSR-safe hydration pattern, not a cascading-render risk
      if (raw !== null) setValue(JSON.parse(raw));
    } catch {
      /* ignore corrupt storage */
    }
    setHydrated(true);
  }, [key]);

  useEffect(() => {
    if (!hydrated) return;
    try {
      window.localStorage.setItem(key, JSON.stringify(value));
    } catch {
      /* storage full/unavailable — non-fatal */
    }
  }, [key, value, hydrated]);

  return [value, setValue, hydrated] as const;
}
