"use client";

import { AnimatePresence, motion } from "framer-motion";

export interface Toast {
  id: string;
  text: string;
  warn?: boolean;
}

export function Toasts({ toasts }: { toasts: Toast[] }) {
  return (
    <div className="fixed top-20 left-4 z-[200] flex max-w-[300px] flex-col gap-2.5">
      <AnimatePresence>
        {toasts.map((t) => (
          <motion.div
            key={t.id}
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={{ duration: 0.22 }}
            className={`rounded-2xl border px-4 py-2.5 text-[13px] shadow-[var(--shadow-soft)] backdrop-blur-md ${
              t.warn
                ? "border-[color:var(--color-amber)]/40 bg-[color:var(--color-bg-elevated)]/95 text-[color:var(--color-ink)]"
                : "border-[color:var(--color-border)] bg-[color:var(--color-bg-elevated)]/95 text-[color:var(--color-ink)]"
            }`}
            dangerouslySetInnerHTML={{ __html: t.text }}
          />
        ))}
      </AnimatePresence>
    </div>
  );
}
