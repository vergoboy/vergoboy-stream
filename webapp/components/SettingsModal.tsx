"use client";

import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  Keyboard,
  X,
  Palette,
  Mic,
  Volume2,
  AudioLines,
} from "lucide-react";
import {
  THEMES,
  SHORTCUT_ACTIONS,
  SHORTCUT_LABELS,
  QUALITY_OPTIONS,
  formatKey,
  useAppSettings,
  type ShortcutAction,
} from "@/lib/settings";
import type { useVoiceRoom } from "@/lib/useVoiceRoom";

const inputClass =
  "w-full rounded-xl border border-[color:var(--color-border)] bg-white/5 px-3.5 py-2.5 text-[13px] text-[color:var(--color-ink)] outline-none focus:border-[color:var(--color-amber)]/60";

function Toggle({
  checked,
  onChange,
  label,
  hint,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  hint?: string;
}) {
  return (
    <label className="flex cursor-pointer items-center justify-between gap-3 rounded-xl px-1 py-1.5">
      <span className="text-[12.5px] leading-snug text-[color:var(--color-ink)]">
        {label}
        {hint && <span className="block text-[10.5px] text-[color:var(--color-ink-dim)]">{hint}</span>}
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={(e) => {
          e.preventDefault();
          onChange(!checked);
        }}
        className={`relative h-5 w-9 shrink-0 rounded-full transition-colors ${
          checked ? "bg-[color:var(--color-teal)]" : "bg-white/15"
        }`}
      >
        <span className="absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all" style={{ left: checked ? 2 : 18 }} />
      </button>
    </label>
  );
}

function SectionTitle({ icon, children }: { icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <h4 className="mb-2.5 flex items-center gap-1.5 text-[13.5px] font-bold text-[color:var(--color-ink)]">
      {icon}
      {children}
    </h4>
  );
}

export function SettingsModal({
  open,
  onClose,
  voice,
}: {
  open: boolean;
  onClose: () => void;
  voice: ReturnType<typeof useVoiceRoom>;
}) {
  const { settings, setTheme, setShortcut, resetShortcuts, setPushToTalk } = useAppSettings();
  const [recording, setRecording] = useState<ShortcutAction | null>(null);
  const recordRef = useRef<HTMLInputElement>(null);

  // Capture the next key while a shortcut is being re-bound.
  useEffect(() => {
    if (!open) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- cancel an in-progress key capture when the modal closes
      setRecording(null);
      return;
    }
    if (!recording) return;
    const onKey = (e: KeyboardEvent) => {
      e.preventDefault();
      e.stopPropagation();
      if (e.key === "Escape") {
        setRecording(null);
        return;
      }
      const key = e.key === " " ? " " : e.key;
      setShortcut(recording, key);
      setRecording(null);
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [recording, open, setShortcut]);

  // Keep the hidden capture input focused while recording.
  useEffect(() => {
    if (recording) {
      const t = setTimeout(() => recordRef.current?.focus(), 30);
      return () => clearTimeout(t);
    }
  }, [recording]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onClick={(e) => e.target === e.currentTarget && onClose()}
          className="fixed inset-0 z-[300] flex items-center justify-center bg-black/70 p-5 backdrop-blur-sm"
        >
          <motion.div
            initial={{ opacity: 0, scale: 0.96 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.96 }}
            transition={{ type: "spring", stiffness: 300, damping: 26 }}
            className="max-h-[86vh] w-full max-w-xl overflow-y-auto rounded-3xl border border-[color:var(--color-border)] bg-[color:var(--color-bg-soft)] p-6 text-right"
          >
            <div className="mb-4 flex items-center justify-between border-b border-[color:var(--color-border)] pb-3.5">
              <h3 className="m-0 text-base font-bold text-[color:var(--color-ink)]">
                <Keyboard className="w-5 h-5 text-[color:var(--color-amber)]" /> تنظیمات
              </h3>
              <button
                onClick={onClose}
                className="flex h-8 w-8 items-center justify-center rounded-lg border border-[color:var(--color-border)] bg-white/5 text-[color:var(--color-ink-muted)] hover:text-white"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="flex flex-col gap-6">
              {/* ── Appearance / themes ───────────────────────────── */}
              <section>
                <SectionTitle icon={<Palette className="h-4 w-4 text-[color:var(--color-amber)]" />}>
                  ظاهر — پوسته (تم)
                </SectionTitle>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                  {THEMES.map((t) => {
                    const active = settings.theme === t.id;
                    return (
                      <button
                        key={t.id}
                        onClick={() => setTheme(t.id)}
                        className={`flex items-center gap-2.5 rounded-2xl border p-2.5 text-right transition-colors ${
                          active
                            ? "border-[color:var(--color-amber)] bg-[color:var(--color-amber)]/10"
                            : "border-[color:var(--color-border)] bg-white/[0.03] hover:border-[color:var(--color-amber)]/50"
                        }`}
                      >
                        <span className="flex h-8 w-8 shrink-0 items-center justify-center overflow-hidden rounded-lg border border-white/10" dir="ltr">
                          {t.swatch.map((c, i) => (
                            <span key={i} className="h-full flex-1" style={{ background: c }} />
                          ))}
                        </span>
                        <span className="min-w-0">
                          <span className="block truncate text-[12.5px] font-semibold text-[color:var(--color-ink)]">{t.label}</span>
                          <span className="block truncate text-[10.5px] text-[color:var(--color-ink-dim)]">{t.id}</span>
                        </span>
                      </button>
                    );
                  })}
                </div>
              </section>

              {/* ── Shortcuts ─────────────────────────────────────── */}
              <section>
                <SectionTitle icon={<Keyboard className="h-4 w-4 text-[color:var(--color-amber)]" />}>
                  میانبرهای صفحه‌کلید
                </SectionTitle>
                <div className="flex flex-col gap-1.5">
                  {SHORTCUT_ACTIONS.map((a) => (
                    <div key={a} className="flex items-center justify-between gap-2 rounded-xl bg-white/[0.03] px-3 py-2">
                      <span className="min-w-0 flex-1 text-[12.5px] text-[color:var(--color-ink)]">{SHORTCUT_LABELS[a]}</span>
                      <input
                        ref={recording === a ? recordRef : undefined}
                        className="sr-only"
                        tabIndex={-1}
                        aria-hidden="true"
                        readOnly
                      />
                      <button
                        onClick={() => setRecording(recording === a ? null : a)}
                        className={`min-w-[76px] shrink-0 rounded-lg border px-2.5 py-1 text-center font-mono text-[11.5px] transition-colors ${
                          recording === a
                            ? "border-[color:var(--color-amber)] bg-[color:var(--color-amber)]/20 text-[color:var(--color-amber)]"
                            : "border-[color:var(--color-border)] bg-white/10 text-[color:var(--color-ink)] hover:border-[color:var(--color-amber)]/50"
                        }`}
                        dir="ltr"
                      >
                        {recording === a ? "فشردن…" : formatKey(settings.shortcuts[a])}
                      </button>
                    </div>
                  ))}
                </div>
                <button
                  onClick={resetShortcuts}
                  className="mt-2.5 rounded-xl border border-[color:var(--color-border)] bg-white/5 px-3.5 py-2 text-xs text-[color:var(--color-ink)] hover:border-[color:var(--color-amber)]/50"
                >
                  بازنشانی میانبرها به پیش‌فرض
                </button>
              </section>

              {/* ── Voice ─────────────────────────────────────────── */}
              <section>
                <SectionTitle icon={<AudioLines className="h-4 w-4 text-[color:var(--color-teal)]" />}>
                  صدا
                </SectionTitle>
                <div className="flex flex-col gap-2 rounded-2xl border border-[color:var(--color-border)] bg-black/20 p-3">
                  <Toggle
                    checked={settings.pushToTalk}
                    onChange={setPushToTalk}
                    label="فشار دهید تا صحبت کنید (Push-to-Talk)"
                    hint={
                      settings.pushToTalk
                        ? `با نگه داشتن کلید «${formatKey(settings.shortcuts.pushToTalk)}» صحبت می‌کنی؛ با رها کردن قطع می‌شه`
                        : "با صدا فعال (مثل دیسکورد) — میکروفون همیشه روشنه"
                    }
                  />

                  <label className="block">
                    <span className="mb-1 block text-[11.5px] text-[color:var(--color-ink-muted)]">میکروفون</span>
                    <select value={voice.selectedDevice} onChange={(e) => voice.changeDevice(e.target.value)} className={inputClass}>
                      {voice.devices.length === 0 && <option value="">میکروفون پیش‌فرض</option>}
                      {voice.devices.map((d) => (
                        <option key={d.deviceId} value={d.deviceId}>
                          {d.label || "میکروفون"}
                        </option>
                      ))}
                    </select>
                  </label>

                  <label className="block">
                    <span className="mb-1 block text-[11.5px] text-[color:var(--color-ink-muted)]">بلندگو / خروجی صدا</span>
                    <select value={voice.selectedOutputDevice} onChange={(e) => voice.changeOutputDevice(e.target.value)} className={inputClass}>
                      {voice.outputDevices.length === 0 && <option value="">بلندگوی پیش‌فرض سیستم</option>}
                      {voice.outputDevices.map((d) => (
                        <option key={d.deviceId} value={d.deviceId}>
                          {d.label || "بلندگو"}
                        </option>
                      ))}
                    </select>
                  </label>

                  <label className="block">
                    <span className="mb-1 block text-[11.5px] text-[color:var(--color-ink-muted)]">کیفیت صدا</span>
                    <select
                      value={voice.settings.quality}
                      onChange={(e) => voice.setSettings({ ...voice.settings, quality: e.target.value as typeof voice.settings.quality })}
                      className={inputClass}
                    >
                      {QUALITY_OPTIONS.map((o) => (
                        <option key={o.id} value={o.id}>
                          {o.label}
                        </option>
                      ))}
                    </select>
                  </label>

                  <Toggle
                    checked={voice.settings.echoCancellation}
                    onChange={(x) => voice.setSettings({ ...voice.settings, echoCancellation: x })}
                    label="حذف پژواک (AEC)"
                    hint="پژواک صدای خودتان در اسپیکر را حذف می‌کند"
                  />
                  <Toggle
                    checked={voice.settings.noiseSuppression}
                    onChange={(x) => voice.setSettings({ ...voice.settings, noiseSuppression: x })}
                    label="حذف نویز (NS)"
                    hint="نویز پس‌زمینه مثل فن و کیبورد را کم می‌کند"
                  />
                  <Toggle
                    checked={voice.settings.autoGainControl}
                    onChange={(x) => voice.setSettings({ ...voice.settings, autoGainControl: x })}
                    label="تنظیم خودکار بلندی صدا (AGC)"
                    hint="صدای بلند/آهسته را متعادل می‌کند"
                  />
                  <Toggle
                    checked={voice.settings.autoQuality}
                    onChange={(x) => voice.setSettings({ ...voice.settings, autoQuality: x })}
                    label="کیفیت خودکار بر اساس اتصال"
                    hint="در شبکه ضعیف خودش کیفیت را کم می‌کند"
                  />

                  <label className="flex items-center gap-2 pt-1">
                    <Mic className="h-4 w-4 text-[color:var(--color-ink-muted)]" />
                    <span className="text-[11.5px] text-[color:var(--color-ink-muted)]">بلندی صدای همه</span>
                    <input
                      type="range"
                      min={0}
                      max={2}
                      step={0.01}
                      value={voice.settings.masterVolume}
                      onChange={(e) => voice.setMasterVolume(parseFloat(e.target.value))}
                      className="flex-1 accent-[color:var(--color-amber)]"
                    />
                    <span className="w-8 text-right font-mono text-[10.5px] text-[color:var(--color-ink-muted)]">
                      {Math.round(voice.settings.masterVolume * 100)}%
                    </span>
                  </label>
                </div>
              </section>
            </div>

            <p className="mt-4 text-[11.5px] leading-relaxed text-[color:var(--color-ink-dim)]">
              <Volume2 className="w-3.5 h-3.5 inline ml-1" />
              کلیدها فقط وقتی فوکوس روی ویدیو یا صفحه‌ی اصلیه (نه روی فیلدهای متنی) کار می‌کنن؛ میانبرهای چت صوتی
              (میکروفون، ناشنوا، PTT) در هر جای صفحه کار می‌کنن مگر روی فیلد متنی.
            </p>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
