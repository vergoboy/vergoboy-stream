"use client";

import { useEffect, useRef, useState } from "react";
import { Mic } from "lucide-react";
import { AnimatePresence, motion } from "./anim";
import { Avatar } from "./Avatar";
import { run, spring } from "@/lib/anim";
import type { PresenceUser } from "@/lib/types";

/**
 * The audience: everyone in the room, sitting in the front row facing the
 * screen. Watching people sit up and glow gold; the one currently talking
 * lights up teal. One row replaces the old two "couches" — the badges say
 * who is also on voice, so nobody appears twice.
 */
export function Audience({
  users,
  voiceSpeaking = [],
  compact = false,
  max = 9,
}: {
  users: PresenceUser[];
  voiceSpeaking?: { name: string; avatarUrl: string | null; speaking: boolean }[];
  compact?: boolean;
  max?: number;
}) {
  const speakingMap = new Map(voiceSpeaking.map((s) => [s.name, s.speaking]));
  const shown = users.slice(0, max);
  const extra = users.length - shown.length;
  const size = compact ? 30 : 42;

  // watching people first, so the front of the row is the people actually paying attention
  const ordered = [...shown].sort((a, b) => Number(!!b.watching) - Number(!!a.watching));

  return (
    <div className="relative flex min-h-[58px] items-end justify-center gap-1.5 px-2" aria-label="حاضرین سالن">
      <AnimatePresence initial={false}>
        {users.length === 0 && (
          <motion.p key="empty" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="pb-3 text-[12.5px] text-white/40">
            صندلی‌ها خالیه — لینک دعوت رو بفرست 🍿
          </motion.p>
        )}
        {ordered.map((u, i) => {
          const speaking = !!speakingMap.get(u.name);
          const avatar = voiceSpeaking.find((s) => s.name === u.name)?.avatarUrl ?? u.avatar_url;
          return (
            <motion.div
              key={`${u.id ?? u.name}`}
              initial={{ opacity: 0, y: 30, scale: 0.7 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 24, scale: 0.7 }}
              transition={{ type: "spring", stiffness: 300, damping: 17, delay: i * 0.04 }}
              className="flex flex-col items-center"
              data-context-kind={u.in_voice ? "voice" : "user"}
              data-context-name={u.name}
              title={stateLabel(u, speaking)}
            >
              <Seat size={size} watching={!!u.watching} speaking={speaking} voice={!!u.in_voice}>
                <Avatar name={u.name} url={avatar} size={size} />
              </Seat>
              {!compact && <span className="mt-0.5 max-w-[54px] truncate text-[10.5px] text-white/55">{u.name}</span>}
            </motion.div>
          );
        })}
      </AnimatePresence>
      {extra > 0 && <span className="mb-4 rounded-full bg-white/10 px-2 py-1 text-[11px] font-bold text-white/70">+{extra}</span>}
    </div>
  );
}

function Seat({ children, size, watching, speaking, voice }: { children: React.ReactNode; size: number; watching: boolean; speaking: boolean; voice: boolean }) {
  const ring = useRef<HTMLDivElement>(null);
  const [prev, setPrev] = useState(false);
  // a small hop when someone starts talking: it answers a real event, then stays still
  useEffect(() => {
    if (speaking && !prev) run(ring.current, { translateY: [0, -6, 0], duration: 420, ease: spring({ stiffness: 400, damping: 14 }) });
    // eslint-disable-next-line react-hooks/set-state-in-effect -- remembers the previous speaking flag to detect the rising edge
    setPrev(speaking);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [speaking]);
  return (
    <div ref={ring} className="relative" style={{ opacity: watching || voice ? 1 : 0.6 }}>
      <div
        className="overflow-hidden rounded-full"
        style={{
          boxShadow: speaking
            ? "0 0 0 2px var(--color-teal), 0 0 22px 2px color-mix(in oklab, var(--color-teal) 70%, transparent)"
            : watching
              ? "0 0 0 2px var(--color-amber), 0 0 16px -2px color-mix(in oklab, var(--color-amber) 70%, transparent)"
              : "0 0 0 2px rgba(255,255,255,.12)",
          width: size,
          height: size,
        }}
      >
        {children}
      </div>
      {voice && (
        <span className="absolute -bottom-1 -left-1 flex h-[17px] w-[17px] items-center justify-center rounded-full bg-[color:var(--color-teal)] text-black ring-2 ring-[#0b070d]">
          <Mic className="h-2.5 w-2.5" />
        </span>
      )}
      {/* the seat back */}
      <div className="absolute -bottom-2 left-1/2 -z-10 h-5 w-[calc(100%+14px)] -translate-x-1/2 rounded-t-xl bg-[color:var(--color-plum-deep)]" style={{ filter: "brightness(1.2)" }} />
    </div>
  );
}

function stateLabel(u: PresenceUser, speaking: boolean): string {
  const bits: string[] = [];
  bits.push(u.watching ? "در حال تماشا" : "حاضر در اتاق");
  if (u.in_voice) bits.push(speaking ? "در حال صحبت" : "میکروفون خاموش");
  if (u.is_owner) bits.push("صاحب اتاق");
  return `${u.name} — ${bits.join("، ")}`;
}
