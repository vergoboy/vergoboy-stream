"use client";

import { Keyboard, X } from "lucide-react";


import { AnimatePresence, motion } from "framer-motion";

const ROWS: [string[], string][] = [
  [["Space"], "پخش / مکث"],
  [["←", "J"], "۱۰ ثانیه عقب"],
  [["→", "L"], "۱۰ ثانیه جلو"],
  [["↑"], "صدا بیشتر (+۵٪)"],
  [["↓"], "صدا کمتر (−۵٪)"],
  [["M"], "بی‌صدا / باصدا"],
  [["F"], "تمام‌صفحه"],
  [["P"], "آیتم قبلی"],
  [["N"], "آیتم بعدی"],
  [["1", "…", "9"], "رفتن به ۱۰٪–۹۰٪ فیلم"],
  [["0"], "رفتن به ابتدای فیلم"],
  [["?"], "نمایش/بستن این پنجره"],
  [["Esc"], "بستن این پنجره / خروج از تمام‌صفحه"],
];

export function ShortcutsModal({ open, onClose }: { open: boolean; onClose: () => void }) {
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
              {ROWS.map(([keys, desc], i) => (
                <div key={i} className="flex items-center justify-between rounded-xl bg-white/[0.03] px-3 py-2">
                  <span className="text-[13px] text-[color:var(--color-ink)]">{desc}</span>
                  <span className="flex gap-1" dir="ltr">
                    {keys.map((k, j) => (
                      <kbd key={j} className="min-w-[26px] rounded-md border border-b-2 border-[color:var(--color-border)] border-b-black/30 bg-white/10 px-2 py-0.5 text-center font-mono text-[11.5px] text-[color:var(--color-ink)]">
                        {k}
                      </kbd>
                    ))}
                  </span>
                </div>
              ))}
            </div>
            <p className="mt-3.5 text-xs leading-relaxed text-[color:var(--color-ink-dim)]">کلیدها فقط وقتی فوکوس روی ویدیو یا صفحه‌ی اصلیه (نه روی فیلدهای متنی) کار می‌کنن.</p>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}