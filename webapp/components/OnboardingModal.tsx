"use client";

import { useState } from "react";
import { motion, AnimatePresence } from "./anim";
import { KeyRound, Users, UserRoundCog, X } from "lucide-react";

const ONBOARD_KEY = "stream_onboarded_v2";

export function OnboardingModal() {
  const [open, setOpen] = useState(() => {
    if (typeof window === "undefined") return false;
    try {
      return window.localStorage.getItem(ONBOARD_KEY) !== "1";
    } catch {
      return true;
    }
  });
  const [dontShow, setDontShow] = useState(false);

  function close() {
    try {
      if (dontShow) window.localStorage.setItem(ONBOARD_KEY, "1");
    } catch {
      /* storage unavailable */
    }
    setOpen(false);
  }

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="fixed inset-0 z-[400] flex items-center justify-center bg-black/70 p-5 backdrop-blur-sm"
        >
          <motion.div
            initial={{ opacity: 0, y: 16, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            transition={{ type: "spring", stiffness: 260, damping: 24 }}
            className="w-full max-w-md rounded-3xl border border-[color:var(--color-border)] bg-[color:var(--color-bg-soft)] p-7 shadow-[var(--shadow-soft)]"
          >
            <div className="mb-4 flex items-start justify-between gap-3">
              <h3 className="text-lg font-bold text-[color:var(--color-ink)]">اینجا چطور کار می‌کند؟</h3>
              <button
                onClick={close}
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl text-[color:var(--color-ink-muted)] hover:bg-white/5"
                title="بستن"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <ul className="flex flex-col gap-3.5 text-[13.5px] leading-relaxed text-[color:var(--color-ink-muted)]">
              <li className="flex items-start gap-3">
                <KeyRound className="mt-0.5 h-4 w-4 shrink-0 text-[color:var(--color-amber)]" />
                <span>
                  هر کاربر <b className="text-[color:var(--color-ink)]">یک اتاق مخصوص خودش</b> دارد. کد اتاق و لینک دعوت را برای دوستت بفرست تا
                  هم‌زمان تماشا کنید.
                </span>
              </li>
              <li className="flex items-start gap-3">
                <Users className="mt-0.5 h-4 w-4 shrink-0 text-[color:var(--color-teal)]" />
                <span>
                  با کادر «کد اتاق…» می‌توانی وارد اتاق هرکس بشوی؛ دکمه‌ی <b className="text-[color:var(--color-ink)]">«اتاق من»</b> همیشه تو را
                  برمی‌گرداند.
                </span>
              </li>
              <li className="flex items-start gap-3">
                <UserRoundCog className="mt-0.5 h-4 w-4 shrink-0 text-[color:var(--color-plum-soft)]" />
                <span>
                  نقش کاربر جدید <b className="text-[color:var(--color-ink)]">«تماشاگر»</b> است: فقط می‌تواند تماشا و چت کند. برای پخش/توقف و افزودن
                  ویدیو باید با ادمین هماهنگ کنی تا دسترسی بدهد.
                </span>
              </li>
            </ul>

            <label className="mt-5 flex cursor-pointer items-center gap-2 text-[12.5px] text-[color:var(--color-ink-muted)]">
              <input
                type="checkbox"
                checked={dontShow}
                onChange={(e) => setDontShow(e.target.checked)}
                className="accent-[color:var(--color-amber)]"
              />
              دیگر این راهنما را نشان نده
            </label>

            <button
              onClick={close}
              className="mt-4 w-full rounded-2xl px-6 py-3 text-[14.5px] font-bold text-white transition-transform active:scale-[0.98]"
              style={{ background: "linear-gradient(135deg, var(--color-amber-soft), var(--color-amber))", boxShadow: "var(--shadow-lamp)" }}
            >
              متوجه شدم، شروع کن
            </button>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
