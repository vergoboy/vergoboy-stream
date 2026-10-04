"use client";

import { useEffect, useRef } from "react";
import { X } from "lucide-react";
import { run, spring } from "@/lib/anim";

/**
 * One panel at a time. On wide screens it glides in beside the dock; on phones
 * it is a bottom sheet you can pull down to dismiss.
 */
export function Drawer({
  open,
  title,
  icon,
  sheet,
  onClose,
  children,
}: {
  open: boolean;
  title: string;
  icon: React.ReactNode;
  sheet: boolean;
  onClose: () => void;
  children: React.ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const drag = useRef<{ y: number; dy: number } | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (open) {
      run(el, sheet ? { translateY: ["100%", "0%"], opacity: [0.6, 1], duration: 520, ease: spring({ stiffness: 260, damping: 28 }) } : { translateX: [40, 0], opacity: [0, 1], duration: 480, ease: spring({ stiffness: 300, damping: 28 }) });
      const kids = el.querySelectorAll("[data-drawer-head]");
      if (kids.length) run(kids, { opacity: [0, 1], translateY: [-6, 0], duration: 400, delay: 120 });
    }
  }, [open, sheet]);

  useEffect(() => {
    if (!open) return;
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [open, onClose]);

  const down = (e: React.PointerEvent) => {
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { y: e.clientY, dy: 0 };
  };
  const move = (e: React.PointerEvent) => {
    if (!drag.current || !ref.current) return;
    const dy = Math.max(0, e.clientY - drag.current.y);
    drag.current.dy = dy;
    ref.current.style.transform = `translateY(${dy}px)`;
  };
  const up = () => {
    const d = drag.current;
    drag.current = null;
    if (!d || !ref.current) return;
    if (d.dy > 110) {
      run(ref.current, { translateY: [d.dy, ref.current.offsetHeight], duration: 220, ease: "inQuad", onComplete: onClose });
    } else {
      run(ref.current, { translateY: [d.dy, 0], duration: 420, ease: spring({ stiffness: 400, damping: 24 }) });
    }
  };

  if (!open) return null;

  return (
    <>
      {sheet && <div className="absolute inset-0 z-40 bg-black/50" onClick={onClose} aria-hidden />}
      <aside
        ref={ref}
        role="dialog"
        aria-label={title}
        className={`glass-strong z-50 flex flex-col overflow-hidden ${
          sheet
            ? "absolute inset-x-0 bottom-0 h-[78dvh] rounded-t-[28px]"
            : "absolute bottom-3 top-3 w-[min(400px,calc(100vw-100px))] rounded-[28px]"
        }`}
        style={sheet ? { paddingBottom: "env(safe-area-inset-bottom, 0px)" } : { right: "calc(76px + env(safe-area-inset-right, 0px))" }}
      >
        {sheet && (
          <div className="flex h-7 shrink-0 cursor-grab touch-none items-center justify-center" onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up}>
            <span className="h-1.5 w-11 rounded-full bg-white/25" />
          </div>
        )}
        <header data-drawer-head className="flex shrink-0 items-center gap-2.5 px-5 pb-3 pt-2">
          <span className="text-[color:var(--color-amber)]">{icon}</span>
          <h2 className="display flex-1 text-[21px] leading-none text-white">{title}</h2>
          <button type="button" aria-label="بستن" onClick={onClose} className="hit flex h-10 w-10 items-center justify-center rounded-full bg-white/8 text-white/80 hover:bg-white/15">
            <X className="h-5 w-5" />
          </button>
        </header>
        <div className="drawer-body min-h-0 flex-1 overflow-y-auto px-5 pb-6">{children}</div>
      </aside>
    </>
  );
}
