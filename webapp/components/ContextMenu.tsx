"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Check, type LucideIcon } from "lucide-react";
import { popIn, run } from "@/lib/anim";

export type MenuEntry =
  | { type: "item"; id: string; label: string; icon: LucideIcon; hint?: string; danger?: boolean; active?: boolean; disabled?: boolean }
  | { type: "chips"; label: string; icon: LucideIcon; options: { id: string; label: string; active?: boolean }[]; disabled?: boolean }
  | { type: "title"; label: string }
  | { type: "sep" };

/**
 * Right-click menu. Opens from the pointer with a spring, items cascade in,
 * closes on outside press / Escape, arrow keys move focus.
 */
export function ContextMenu({
  x,
  y,
  entries,
  onPick,
  onClose,
}: {
  x: number;
  y: number;
  entries: MenuEntry[];
  onPick: (id: string) => void;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ x, y, ox: "0% 0%" });

  // measure, then flip/clamp inside the viewport; the menu grows from the pointer's corner
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const w = el.offsetWidth;
    const h = el.offsetHeight;
    const flipX = x + w > window.innerWidth - 8;
    const flipY = y + h > window.innerHeight - 8;
    setPos({
      x: Math.max(8, flipX ? x - w : x),
      y: Math.max(8, flipY ? Math.max(8, y - h) : y),
      ox: `${flipX ? "100%" : "0%"} ${flipY ? "100%" : "0%"}`,
    });
  }, [x, y, entries.length]);

  useEffect(() => {
    popIn(ref.current, { from: pos.ox, y: 0 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const down = (e: PointerEvent) => {
      if (!(e.target instanceof Element) || !e.target.closest("[data-context-menu-root]")) onClose();
    };
    const key = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("pointerdown", down);
    window.addEventListener("keydown", key);
    window.addEventListener("blur", onClose);
    window.addEventListener("resize", onClose);
    return () => {
      window.removeEventListener("pointerdown", down);
      window.removeEventListener("keydown", key);
      window.removeEventListener("blur", onClose);
      window.removeEventListener("resize", onClose);
    };
  }, [onClose]);

  const close = (id: string) => {
    run(ref.current, { opacity: 0, scale: 0.96, duration: 110, ease: "inQuad", onComplete: () => onPick(id) });
  };

  const onKeys = (e: React.KeyboardEvent) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    const items = Array.from(ref.current?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)") ?? []);
    if (!items.length) return;
    e.preventDefault();
    const i = items.indexOf(document.activeElement as HTMLButtonElement);
    items[(i + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length]?.focus();
  };

  return (
    <div
      ref={ref}
      data-context-menu-root
      role="menu"
      dir="rtl"
      onKeyDown={onKeys}
      onContextMenu={(e) => e.preventDefault()}
      className="glass-strong fixed z-[10000] w-[236px] rounded-2xl p-1.5 text-white"
      style={{ left: pos.x, top: pos.y, transformOrigin: pos.ox }}
    >
      {entries.map((en, i) => {
        if (en.type === "sep") return <div key={i} className="my-1 h-px bg-white/10" />;
        if (en.type === "title")
          return (
            <div key={i} data-pop-item className="truncate px-3 pb-1 pt-1.5 text-[12px] font-bold text-[color:var(--color-amber)]">
              {en.label}
            </div>
          );
        if (en.type === "chips") {
          const Icon = en.icon;
          return (
            <div key={i} data-pop-item className="px-2 pb-1 pt-1">
              <div className="mb-1.5 flex items-center gap-2 px-1 text-[11.5px] text-white/55">
                <Icon className="h-3.5 w-3.5" />
                {en.label}
              </div>
              <div className="grid grid-cols-3 gap-1">
                {en.options.map((o) => (
                  <button
                    key={o.id}
                    type="button"
                    disabled={en.disabled}
                    onClick={() => close(o.id)}
                    className={`rounded-lg py-1.5 text-[12.5px] font-semibold transition-colors disabled:opacity-40 ${
                      o.active ? "bg-[color:var(--color-amber)] text-black" : "bg-white/8 hover:bg-white/15"
                    }`}
                  >
                    {o.label}
                  </button>
                ))}
              </div>
            </div>
          );
        }
        const Icon = en.icon;
        return (
          <button
            key={en.id}
            type="button"
            role="menuitem"
            data-pop-item
            disabled={en.disabled}
            autoFocus={i === 0}
            onClick={() => close(en.id)}
            className={`flex min-h-10 w-full items-center gap-3 rounded-xl px-3 text-right text-[13.5px] outline-none transition-colors hover:bg-white/10 focus-visible:bg-white/12 disabled:opacity-40 ${
              en.danger ? "text-[color:var(--color-coral)]" : en.active ? "text-[color:var(--color-amber)]" : "text-white"
            }`}
          >
            <Icon className="h-[18px] w-[18px] shrink-0 opacity-85" />
            <span className="flex-1 truncate">{en.label}</span>
            {en.active ? <Check className="h-4 w-4" /> : en.hint ? <kbd className="font-mono text-[10.5px] text-white/40" dir="ltr">{en.hint}</kbd> : null}
          </button>
        );
      })}
    </div>
  );
}
