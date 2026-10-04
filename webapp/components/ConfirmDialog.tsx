"use client";

import { useEffect, useRef } from "react";
import { run, spring } from "@/lib/anim";

/** Friendly "are you sure?" — used for every way out of the room. */
export function ConfirmDialog({
  open,
  title,
  body,
  stay,
  leave,
  onStay,
  onLeave,
}: {
  open: boolean;
  title: string;
  body: string;
  stay: string;
  leave: string;
  onStay: () => void;
  onLeave: () => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    run(box.current, { scale: [0.8, 1], opacity: [0, 1], rotate: [-2, 0], duration: 520, ease: spring({ stiffness: 340, damping: 16 }) });
    const k = (e: KeyboardEvent) => e.key === "Escape" && onStay();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [open, onStay]);
  if (!open) return null;
  return (
    <div className="absolute inset-0 z-[300] flex items-center justify-center bg-black/70 p-5 backdrop-blur-sm" role="alertdialog" aria-label={title}>
      <div ref={box} className="glass-strong w-full max-w-[340px] rounded-[28px] p-6 text-center">
        <div className="mb-2 text-[40px]" aria-hidden>🎬</div>
        <h3 className="display mb-1.5 text-[24px] text-white">{title}</h3>
        <p className="mb-5 text-[13.5px] leading-relaxed text-white/65">{body}</p>
        <div className="flex flex-col gap-2">
          <button
            autoFocus
            onClick={onStay}
            className="rounded-full bg-[color:var(--color-amber)] py-3 text-[14.5px] font-bold text-black shadow-[var(--shadow-lamp)] active:scale-[0.97]"
          >
            {stay}
          </button>
          <button onClick={onLeave} className="rounded-full py-2.5 text-[13.5px] text-white/60 hover:bg-white/10 hover:text-white">
            {leave}
          </button>
        </div>
      </div>
    </div>
  );
}
