"use client";

import { Keyboard, X } from "lucide-react";


import { AnimatePresence, motion } from "framer-motion";
import { useAppSettings, SHORTCUT_LABELS, formatKey } from "@/lib/settings";

export function ShortcutsModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { settings } = useAppSettings();
  const sc = settings.shortcuts;

  const ROWS: { keys: string[]; desc: string }[] = [
    { keys: [formatKey(sc.playPause)], desc: SHORTCUT_LABELS.playPause },
    { keys: [formatKey(sc.seekBack), "J"], desc: SHORTCUT_LABELS.seekBack },
    { keys: [formatKey(sc.seekForward), "L"], desc: SHORTCUT_LABELS.seekForward },
    { keys: [formatKey(sc.volumeUp)], desc: SHORTCUT_LABELS.volumeUp },
    { keys: [formatKey(sc.volumeDown)], desc: SHORTCUT_LABELS.volumeDown },
    { keys: [formatKey(sc.mute)], desc: SHORTCUT_LABELS.mute },
    { keys: [formatKey(sc.fullscreen)], desc: SHORTCUT_LABELS.fullscreen },
    { keys: [formatKey(sc.prev)], desc: SHORTCUT_LABELS.prev },
    { keys: [formatKey(sc.next)], desc: SHORTCUT_LABELS.next },
    { keys: [formatKey(sc.pip)], desc: SHORTCUT_LABELS.pip },
    { keys: [formatKey(sc.shortcuts)], desc: SHORTCUT_LABELS.shortcuts },
    { keys: [formatKey(sc.settings)], desc: SHORTCUT_LABELS.settings },
    { keys: ["1", "…", "9"], desc: "رفتن به ۱۰٪–۹۰٪ فیلم" },
    { keys: ["0"], desc: "رفتن به ابتدای فیلم" },
    { keys: ["Esc"], desc: "بستن این پنجره / خروج از تمام‌صفحه" },
  ];

  const VOICE_ROWS: { keys: string[]; desc: string }[] = [
    { keys: [formatKey(sc.pushToTalk)], desc: SHORTCUT_LABELS.pushToTalk },
    { keys: [formatKey(sc.toggleMic)], desc: SHORTCUT_LABELS.toggleMic },
    { keys: [formatKey(sc.toggleDeafen)], desc: SHORTCUT_LABELS.toggleDeafen },
  ];

  const Row = ({ keys, desc }: { keys: string[]; desc: string }) => (
    <div className="flex items-center justify-between rounded-xl bg-white/[0.03] px-3 py-2">
      <span className="text-[13px] text-[color:var(--color-ink)]">{desc}</span>
      <span className="flex gap-1" dir="ltr">
        {keys.map((k, j) => (
          <kbd key={j} className="min-w-[26px] rounded-md border border-b-2 border-[color:var(--color-border)] border-b-black/30 bg-white/10 px-2 py-0.5 text-center font-mono text-[11.5px] text-[color:var(--color-ink)]">
            {k}
          </kbd>
        ))}
      </span>
    </div>
  );

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
            className="max-h-[85vh] w-full max-w-md overflow-y-auto rounded-3xl border border-[color:var(--color-border)] bg-[color:var(--color-bg-soft)] p-6 text-right"
          >
            <div className="mb-4 flex items-center justify-between border-b border-[color:var(--color-border)] pb-3.5">
              <h3 className="m-0 text-base font-bold text-[color:var(--color-ink)]"><Keyboard className="w-5 h-5 text-[color:var(--color-amber)]" /> کلیدهای میانبر</h3>
              <button onClick={onClose} className="flex h-8 w-8 items-center justify-center rounded-lg border border-[color:var(--color-border)] bg-white/5 text-[color:var(--color-ink-muted)] hover:text-white">
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="flex flex-col gap-2">
              {ROWS.map((r, i) => (
                <Row key={i} {...r} />
              ))}
            </div>
            <h4 className="mb-2 mt-5 text-[12.5px] font-bold text-[color:var(--color-ink)]">چت صوتی</h4>
            <div className="flex flex-col gap-2">
              {VOICE_ROWS.map((r, i) => (
                <Row key={i} {...r} />
              ))}
            </div>
            <p className="mt-3.5 text-xs leading-relaxed text-[color:var(--color-ink-dim)]">
              کلیدها فقط وقتی فوکوس روی ویدیو یا صفحه‌ی اصلیه (نه روی فیلدهای متنی) کار می‌کنن؛ میانبرهای چت صوتی در
              هر جای صفحه کار می‌کنن مگر روی فیلد متنی. همه‌ی این کلیدها در «تنظیمات» قابل تغییرن.
            </p>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
