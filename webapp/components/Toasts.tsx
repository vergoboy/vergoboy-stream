"use client";

import { AnimatePresence, motion } from "./anim";

export interface Toast {
  id: string;
  text: string;
  warn?: boolean;
}

/** Small notes that float down from the top, never covering the screen's controls. */
export function Toasts({ toasts }: { toasts: Toast[] }) {
  return (
    <div
      className="pointer-events-none fixed left-1/2 z-[200] flex w-[min(92vw,360px)] -translate-x-1/2 flex-col items-center gap-2"
      style={{ top: "calc(10px + env(safe-area-inset-top, 0px))" }}
    >
      <AnimatePresence>
        {toasts.map((t) => (
          <motion.div
            key={t.id}
            initial={{ opacity: 0, y: -24, scale: 0.9 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -12, scale: 0.95 }}
            transition={{ type: "spring", stiffness: 340, damping: 20 }}
            className={`glass-strong rounded-full px-5 py-2.5 text-center text-[13px] text-white ${t.warn ? "!border-[color:var(--color-amber)]/60" : ""}`}
            dangerouslySetInnerHTML={{ __html: t.text }}
          />
        ))}
      </AnimatePresence>
    </div>
  );
}
