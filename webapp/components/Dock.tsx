"use client";

import { useEffect, useRef } from "react";
import { ListVideo, Plus, MessageCircle, Users, Captions, CircleUserRound, type LucideIcon } from "lucide-react";
import { run, spring, press } from "@/lib/anim";

export type PanelId = "queue" | "add" | "chat" | "people" | "subs" | "me";

type Item = { id: PanelId; label: string; icon: LucideIcon; badge?: number };

export function Dock({
  active,
  onPick,
  canAdd,
  unreadChat,
  online,
  voiceLive,
  layout,
  chromeHidden,
}: {
  active: PanelId | null;
  onPick: (id: PanelId) => void;
  canAdd: boolean;
  unreadChat: number;
  online: number;
  voiceLive: boolean;
  layout: "rail" | "bar";
  chromeHidden: boolean;
}) {
  const items: Item[] = [
    { id: "queue", label: "صف پخش", icon: ListVideo },
    ...(canAdd ? [{ id: "add" as const, label: "افزودن", icon: Plus }] : []),
    { id: "chat", label: "چت", icon: MessageCircle, badge: unreadChat },
    { id: "people", label: "حاضرین", icon: Users, badge: online > 1 ? online : 0 },
    { id: "subs", label: "زیرنویس", icon: Captions },
    { id: "me", label: "من", icon: CircleUserRound },
  ];
  const root = useRef<HTMLElement>(null);

  // arrival: buttons pop in one after another, once
  useEffect(() => {
    const btns = root.current?.querySelectorAll("[data-dock-btn]");
    if (btns?.length) run(btns, { scale: [0.4, 1], opacity: [0, 1], delay: (_: unknown, i: number) => 120 + i * 60, duration: 560, ease: spring({ stiffness: 360, damping: 15 }) });
  }, []);

  const rail = layout === "rail";
  return (
    <nav
      ref={root}
      aria-label="منوی سالن"
      className={`glass z-40 flex ${
        rail
          ? "absolute right-3 top-1/2 -translate-y-1/2 flex-col gap-1 rounded-[26px] p-1.5"
          : "absolute inset-x-2 bottom-2 justify-between rounded-[26px] p-1.5"
      } transition-opacity duration-500 ${chromeHidden ? "pointer-events-none opacity-0" : "opacity-100"}`}
      style={rail ? { marginRight: "env(safe-area-inset-right, 0px)" } : { marginBottom: "env(safe-area-inset-bottom, 0px)" }}
    >
      {items.map((it) => {
        const on = active === it.id;
        const Icon = it.icon;
        return (
          <button
            key={it.id}
            data-dock-btn
            type="button"
            aria-label={it.label}
            aria-pressed={on}
            onClick={(e) => {
              press(e.currentTarget.querySelector("svg"));
              onPick(it.id);
            }}
            className={`relative flex flex-col items-center justify-center gap-0.5 rounded-[20px] transition-colors ${
              rail ? "h-[58px] w-[58px]" : "h-[54px] flex-1"
            } ${on ? "bg-[color:var(--color-amber)] text-black" : "text-white/75 hover:bg-white/10 hover:text-white"}`}
          >
            <Icon className="h-[22px] w-[22px]" strokeWidth={on ? 2.4 : 2} />
            <span className="text-[10.5px] font-semibold leading-none">{it.label}</span>
            {!!it.badge && (
              <span
                className={`absolute right-1.5 top-1 min-w-[17px] rounded-full px-1 text-center text-[10px] font-extrabold leading-[17px] ${
                  it.id === "chat" ? "bg-[color:var(--color-coral)] text-white" : "bg-white/20 text-white"
                }`}
              >
                {it.badge > 99 ? "99+" : it.badge}
              </span>
            )}
            {it.id === "people" && voiceLive && (
              <span className="absolute left-2 top-2 h-2 w-2 rounded-full bg-[color:var(--color-teal)] shadow-[0_0_8px_var(--color-teal)]" />
            )}
          </button>
        );
      })}
    </nav>
  );
}
