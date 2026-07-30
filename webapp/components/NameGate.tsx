"use client";

import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";

export function NameGate({
  initialName,
  onJoin,
}: {
  initialName: string;
  onJoin: (name: string) => void;
}) {
  const [value, setValue] = useState(initialName);

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        className="fixed inset-0 z-[300] flex items-center justify-center bg-black/70 backdrop-blur-sm p-5"
      >
        <motion.div
          initial={{ opacity: 0, y: 16, scale: 0.97 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          transition={{ type: "spring", stiffness: 260, damping: 24 }}
          className="w-full max-w-sm rounded-3xl border border-[color:var(--color-border)] bg-[color:var(--color-bg-soft)] p-7 text-center shadow-[var(--shadow-soft)]"
        >
          <div
            className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl text-xl font-black text-white"
            style={{ background: "linear-gradient(135deg, var(--color-amber), var(--color-plum))" }}
          >
            V
          </div>
          <h3 className="mb-2 text-lg font-bold text-[color:var(--color-ink)]">به اتاق تماشای مشترک خوش اومدی</h3>
          <p className="mb-5 text-[13px] leading-relaxed text-[color:var(--color-ink-muted)]">
            یک اسم انتخاب کن تا بقیه بدونن کی پخش/توقف می‌کند
          </p>
          <input
            autoFocus
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && value.trim() && onJoin(value)}
            maxLength={24}
            placeholder="مثلا: آرمان"
            className="mb-4 w-full rounded-2xl border border-[color:var(--color-border)] bg-white/5 px-4 py-3 text-center text-[14.5px] text-[color:var(--color-ink)] outline-none focus:border-[color:var(--color-amber)]"
          />
          <button
            onClick={() => value.trim() && onJoin(value)}
            className="w-full rounded-2xl px-6 py-3 text-[14.5px] font-bold text-white transition-transform active:scale-[0.98]"
            style={{ background: "linear-gradient(135deg, var(--color-amber), var(--color-plum))", boxShadow: "var(--shadow-lamp)" }}
          >
            ورود به اتاق
          </button>
        </motion.div>
      </motion.div>
    </AnimatePresence>
  );
}