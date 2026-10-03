"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useLocalStorage } from "./useLocalStorage";
import type { VoiceSettings } from "./types";
import { DEFAULT_VOICE_SETTINGS } from "./types";

export type ThemeId = "violet" | "dracula" | "nord" | "tokyo" | "light";

export const THEMES: { id: ThemeId; label: string; swatch: string[] }[] = [
  { id: "violet", label: "بنفش شب", swatch: ["#131019", "#e8a15c", "#6e2c55"] },
  { id: "dracula", label: "دراکولا", swatch: ["#282a36", "#f1fa8c", "#bd93f9"] },
  { id: "nord", label: "نورد", swatch: ["#2e3440", "#d08770", "#5e81ac"] },
  { id: "tokyo", label: "توکیو نایت", swatch: ["#1a1b26", "#e0af68", "#bb9af7"] },
  { id: "light", label: "روشن", swatch: ["#f4f0e9", "#c07f3c", "#a24f86"] },
];

export type ShortcutAction =
  | "playPause"
  | "seekBack"
  | "seekForward"
  | "volumeUp"
  | "volumeDown"
  | "mute"
  | "fullscreen"
  | "prev"
  | "next"
  | "pip"
  | "shortcuts"
  | "settings"
  | "pushToTalk"
  | "toggleMic"
  | "toggleDeafen";

export const SHORTCUT_ACTIONS: ShortcutAction[] = [
  "playPause",
  "seekBack",
  "seekForward",
  "volumeUp",
  "volumeDown",
  "mute",
  "fullscreen",
  "prev",
  "next",
  "pip",
  "shortcuts",
  "settings",
  "toggleMic",
  "toggleDeafen",
  "pushToTalk",
];

export const SHORTCUT_LABELS: Record<ShortcutAction, string> = {
  playPause: "پخش / مکث",
  seekBack: "۱۰ ثانیه عقب",
  seekForward: "۱۰ ثانیه جلو",
  volumeUp: "صدا بیشتر",
  volumeDown: "صدا کمتر",
  mute: "بی‌صدا / باصدا",
  fullscreen: "تمام‌صفحه",
  prev: "آیتم قبلی",
  next: "آیتم بعدی",
  pip: "تصویر در تصویر",
  shortcuts: "نمایش راهنمای میانبرها",
  settings: "باز کردن تنظیمات",
  pushToTalk: "فشار دهید تا صحبت کنید",
  toggleMic: "قطع / وصل میکروفون",
  toggleDeafen: "قطع کامل صدا",
};

export const DEFAULT_SHORTCUTS: Record<ShortcutAction, string> = {
  playPause: " ",
  seekBack: "ArrowLeft",
  seekForward: "ArrowRight",
  volumeUp: "ArrowUp",
  volumeDown: "ArrowDown",
  mute: "m",
  fullscreen: "f",
  prev: "p",
  next: "n",
  pip: "i",
  shortcuts: "?",
  settings: "s",
  pushToTalk: "v",
  toggleMic: "x",
  toggleDeafen: "z",
};

export const QUALITY_OPTIONS: { id: VoiceSettings["quality"]; label: string }[] = [
  { id: "auto", label: "خودکار (بر اساس اتصال)" },
  { id: "high", label: "بالا — استریو 48kHz" },
  { id: "medium", label: "متوسط — مونو 48kHz" },
  { id: "low", label: "کم — مونو 16kHz (پهنای باند کم)" },
];

export interface PlaybackSettings {
  /** Player volume, 0..1 — persisted so the room starts at the listener's level. */
  volume: number;
  muted: boolean;
}

export interface AppSettings {
  theme: ThemeId;
  playback: PlaybackSettings;
  shortcuts: Record<ShortcutAction, string>;
  voice: VoiceSettings;
  pushToTalk: boolean;
}

export const DEFAULT_SETTINGS: AppSettings = {
  theme: "violet",
  playback: { volume: 1, muted: false },
  shortcuts: DEFAULT_SHORTCUTS,
  voice: DEFAULT_VOICE_SETTINGS,
  pushToTalk: false,
};

interface SettingsContextValue {
  settings: AppSettings;
  setSettings: (s: AppSettings | ((prev: AppSettings) => AppSettings)) => void;
  setTheme: (t: ThemeId) => void;
  setShortcut: (action: ShortcutAction, key: string) => void;
  resetShortcuts: () => void;
  setVoice: (v: VoiceSettings | ((prev: VoiceSettings) => VoiceSettings)) => void;
  setPushToTalk: (on: boolean) => void;
}

const SettingsContext = createContext<SettingsContextValue | null>(null);

export function SettingsProvider({ children }: { children: React.ReactNode }) {
  const [settings, setSettings, hydrated] = useLocalStorage<AppSettings>("stream_settings", DEFAULT_SETTINGS);

  // Apply the chosen theme to <html>.
  useEffect(() => {
    if (!hydrated) return;
    document.documentElement.setAttribute("data-theme", settings.theme);
  }, [settings.theme, hydrated]);

  // One-time migration from the old "stream_voice_settings" key so nobody
  // loses their per-user voice prefs when the settings model moved.
  useEffect(() => {
    if (!hydrated) return;
    try {
      const raw = window.localStorage.getItem("stream_settings");
      if (raw) {
        const parsed = JSON.parse(raw) as Partial<AppSettings>;
        if (parsed && !parsed.voice) {
          const old = window.localStorage.getItem("stream_voice_settings");
          if (old) {
            const oldVoice = { ...DEFAULT_VOICE_SETTINGS, ...(JSON.parse(old) as Partial<VoiceSettings>) };
            setSettings((s) => (s.voice === DEFAULT_VOICE_SETTINGS ? { ...s, voice: oldVoice } : s));
          }
        }
      }
    } catch {
      /* ignore corrupt storage */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hydrated]);

  // `useLocalStorage` replaces the stored object wholesale instead of merging,
  // so settings saved before `playback` existed have no `playback` key at all.
  // Fill it in once; without this the player would read `undefined.volume`.
  useEffect(() => {
    if (!hydrated || settings.playback) return;
    setSettings((s) => ({ ...s, playback: s.playback ?? DEFAULT_SETTINGS.playback }));
  }, [hydrated, settings.playback, setSettings]);

  const setTheme = useCallback((theme: ThemeId) => setSettings((s) => ({ ...s, theme })), [setSettings]);
  const setShortcut = useCallback(
    (action: ShortcutAction, key: string) => setSettings((s) => ({ ...s, shortcuts: { ...s.shortcuts, [action]: key } })),
    [setSettings]
  );
  const resetShortcuts = useCallback(() => setSettings((s) => ({ ...s, shortcuts: DEFAULT_SHORTCUTS })), [setSettings]);
  const setVoice = useCallback(
    (voice: VoiceSettings | ((prev: VoiceSettings) => VoiceSettings)) =>
      setSettings((s) => ({ ...s, voice: typeof voice === "function" ? voice(s.voice) : voice })),
    [setSettings]
  );
  const setPushToTalk = useCallback((pushToTalk: boolean) => setSettings((s) => ({ ...s, pushToTalk })), [setSettings]);

  const value = useMemo(
    () => ({ settings, setSettings, setTheme, setShortcut, resetShortcuts, setVoice, setPushToTalk }),
    [settings, setSettings, setTheme, setShortcut, resetShortcuts, setVoice, setPushToTalk]
  );

  return <SettingsContext.Provider value={value}>{children}</SettingsContext.Provider>;
}

export function useAppSettings(): SettingsContextValue {
  const ctx = useContext(SettingsContext);
  if (!ctx) throw new Error("useAppSettings must be used within SettingsProvider");
  return ctx;
}

/** Formats a physical key for display inside a <kbd> (ltr). */
export function formatKey(key: string): string {
  const map: Record<string, string> = {
    " ": "Space",
    ArrowLeft: "←",
    ArrowRight: "→",
    ArrowUp: "↑",
    ArrowDown: "↓",
    ArrowUpRight: "↗",
    Enter: "Enter",
    Escape: "Esc",
  };
  return map[key] ?? key.toUpperCase();
}
