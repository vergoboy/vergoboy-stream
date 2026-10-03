"use client";

import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Mic, Users } from "lucide-react";
import { Avatar } from "./Avatar";
import type { PresenceUser } from "@/lib/types";

/** Avatars shown on the couch itself; everyone else is reachable in the popover.
 *  The couch must not grow with the crowd — it is a fixed metaphor, not a
 *  user limit — so overflow is folded into one compact "+N" chip instead of
 *  stretching the furniture or hiding people. */
const SEATS = 6;

// A little deterministic per-seat variance (based on the name) so people
// don't look robotically identical sitting there — but stable across
// re-renders, not randomized every frame.
function seatWobble(name: string) {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 17 + name.charCodeAt(i)) % 7;
  return (h - 3) * 0.6; // -1.8deg .. +1.8deg
}

/**
 * One of the lounge's exactly two couches.
 *
 * The couches are a visual metaphor for "these people are doing this together",
 * NOT a capacity limit: any number of people can be on either couch, and the
 * same person can be on both at once (watching while talking). Because a
 * person's presence entry carries every state they hold simultaneously, the
 * same avatar shows up on both couches with the matching badges rather than
 * appearing as two unrelated strangers.
 */
export function Sofa({
  title,
  description,
  users,
  voiceSpeaking = [],
  kind = "watching",
  emptyLabel,
}: {
  title: string;
  description: string;
  users: PresenceUser[];
  voiceSpeaking?: { name: string; avatarUrl: string | null; speaking: boolean }[];
  kind?: "watching" | "voice";
  emptyLabel?: string;
}) {
  const [overflowOpen, setOverflowOpen] = useState(false);
  const popRef = useRef<HTMLDivElement>(null);
  const speakingMap = new Map(voiceSpeaking.map((s) => [s.name, s.speaking]));
  const accent = kind === "voice" ? "var(--color-teal)" : "var(--color-amber)";

  const seated = users.slice(0, SEATS);
  const overflow = users.slice(SEATS);

  // Dismiss the overflow popover on Escape or an outside click, so it never
  // traps the user open.
  useEffect(() => {
    if (!overflowOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOverflowOpen(false);
    };
    const onDown = (e: MouseEvent) => {
      if (popRef.current && !popRef.current.contains(e.target as Node)) setOverflowOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
    };
  }, [overflowOpen]);

  const renderAvatar = (u: PresenceUser, index: number) => {
    const speaking = kind === "voice" && speakingMap.get(u.name);
    const voiceAvatar = voiceSpeaking.find((s) => s.name === u.name);
    return (
      <motion.div
        key={`${u.id ?? u.name}-${index}`}
        layout
        initial={{ opacity: 0, y: 18, scale: 0.85 }}
        animate={{ opacity: 1, y: 0, scale: 1, rotate: seatWobble(u.name) }}
        exit={{ opacity: 0, y: 14, scale: 0.85 }}
        transition={{ type: "spring", stiffness: 340, damping: 22 }}
        className="flex w-14 flex-col items-center gap-1"
        data-context-kind={kind === "voice" ? "voice" : "user"}
        data-context-name={u.name}
        title={stateLabel(u, kind, speaking)}
      >
        <div className="relative">
          <Avatar
            name={u.name}
            url={voiceAvatar?.avatarUrl ?? u.avatar_url}
            size={38}
            className={`ring-2 shadow-[var(--shadow-soft)] ${
              speaking
                ? "ring-[color:var(--color-teal)] shadow-[0_0_16px_-2px_rgba(64,205,160,0.7)]"
                : u.in_voice
                  ? "ring-[color:var(--color-teal)]/60"
                  : "ring-[color:var(--color-bg)]"
            }`}
          />
          {speaking ? (
            <span
              className="absolute -bottom-1 -left-1 rounded-full bg-[color:var(--color-teal)] p-1"
              style={{ animation: "pulse-live 1.4s infinite" }}
            >
              <MicMini />
            </span>
          ) : u.in_voice ? (
            // On the WATCHING couch, this badge is what tells you the person
            // you can see here is also the one talking on the other couch.
            <span className="absolute -bottom-1 -left-1 rounded-full bg-[color:var(--color-teal)]/80 p-1">
              <MicMini />
            </span>
          ) : null}
        </div>
        <span className="max-w-14 truncate text-[10.5px] text-[color:var(--color-ink-muted)]">{u.name}</span>
      </motion.div>
    );
  };

  return (
    <section className="relative mx-auto mt-2 mb-4 w-full max-w-[520px]">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="h-2.5 w-2.5 rounded-full" style={{ background: accent, boxShadow: `0 0 18px ${accent}` }} />
          <p className="text-[10px] font-semibold tracking-[0.22em] text-[color:var(--color-ink-dim)] uppercase">{title}</p>
        </div>
        <span className="rounded-full border border-[color:var(--color-border)] bg-white/5 px-2.5 py-1 text-[10px] font-medium text-[color:var(--color-ink-muted)]">
          {users.length} نفر
        </span>
      </div>

      {/* Fixed width: the couch is a metaphor, not a meter of capacity. */}
      <div className="relative w-full max-w-[420px]">
        <div
          className="pointer-events-none absolute -top-9 left-1/2 -translate-x-1/2 h-24 w-48 rounded-full blur-3xl opacity-70"
          style={{ background: `radial-gradient(ellipse, ${accent} 0%, transparent 70%)` }}
        />

        <svg viewBox="0 0 400 130" className="relative z-10 h-auto w-full" preserveAspectRatio="none">
          <defs>
            <linearGradient id={`cushionGrad-${kind}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={kind === "voice" ? "var(--color-plum-soft)" : "rgba(232,161,92,0.85)"} />
              <stop offset="100%" stopColor={kind === "voice" ? "var(--color-plum-deep)" : "rgba(120,77,44,0.8)"} />
            </linearGradient>
            <linearGradient id={`baseGrad-${kind}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={kind === "voice" ? "var(--color-plum)" : "rgba(201,128,62,0.95)"} />
              <stop offset="100%" stopColor={kind === "voice" ? "var(--color-plum-deep)" : "rgba(74,49,26,0.9)"} />
            </linearGradient>
          </defs>
          <rect x="6" y="14" width="388" height="46" rx="18" fill={`url(#cushionGrad-${kind})`} opacity="0.9" />
          <rect x="0" y="52" width="400" height="60" rx="20" fill={`url(#baseGrad-${kind})`} />
          <rect x="0" y="34" width="26" height="78" rx="13" fill={kind === "voice" ? "var(--color-plum-deep)" : "rgba(110,70,30,0.9)"} />
          <rect x="374" y="34" width="26" height="78" rx="13" fill={kind === "voice" ? "var(--color-plum-deep)" : "rgba(110,70,30,0.9)"} />
          {/* Three cushion seams: a hint of seats, never a hard cap. */}
          {[1, 2, 3].map((i) => (
            <line key={i} x1={(400 / 4) * i} y1="58" x2={(400 / 4) * i} y2="106" stroke="rgba(0,0,0,0.18)" strokeWidth="2" />
          ))}
        </svg>

        <div className="relative z-20 -mt-16 flex min-h-[64px] items-end justify-center gap-1 px-4 pb-3 flex-wrap">
          <AnimatePresence mode="popLayout">
            {users.length === 0 && (
              <motion.p
                key="empty"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="pb-2 text-xs text-[color:var(--color-ink-dim)]"
              >
                {emptyLabel ?? "فعلاً کسی این‌جا نیست…"}
              </motion.p>
            )}
            {seated.map(renderAvatar)}
            {overflow.length > 0 && (
              <motion.div
                key="overflow"
                layout
                initial={{ opacity: 0, scale: 0.85 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.85 }}
                className="relative flex w-14 flex-col items-center gap-1"
              >
                <button
                  type="button"
                  onClick={() => setOverflowOpen((o) => !o)}
                  aria-expanded={overflowOpen}
                  aria-label={`${overflow.length} نفر دیگر را ببین`}
                  className="flex h-[38px] w-[38px] items-center justify-center rounded-full border border-[color:var(--color-border)] bg-white/5 text-[11px] font-bold text-[color:var(--color-ink-muted)] transition-colors hover:border-[color:var(--color-amber)]/50 hover:text-[color:var(--color-ink)]"
                >
                  +{overflow.length}
                </button>
                <span className="text-[10.5px] text-[color:var(--color-ink-dim)]">بیشتر</span>

                <AnimatePresence>
                  {overflowOpen && (
                    <motion.div
                      ref={popRef}
                      role="dialog"
                      aria-label={`${title} — بقیه`}
                      initial={{ opacity: 0, y: 6, scale: 0.97 }}
                      animate={{ opacity: 1, y: 0, scale: 1 }}
                      exit={{ opacity: 0, y: 6, scale: 0.97 }}
                      className="absolute bottom-full right-0 z-50 mb-2 w-56 rounded-2xl border border-[color:var(--color-border)] bg-[color:var(--color-bg-soft)]/95 p-2 shadow-[var(--shadow-soft)] backdrop-blur-md"
                    >
                      <p className="px-1.5 pb-1.5 text-[10px] tracking-[0.18em] text-[color:var(--color-ink-dim)] uppercase">
                        {title}
                      </p>
                      <ul className="flex max-h-56 flex-col gap-0.5 overflow-y-auto">
                        {overflow.map((u, i) => {
                          const speaking = kind === "voice" && speakingMap.get(u.name);
                          return (
                            <li
                              key={`${u.id ?? u.name}-ov-${i}`}
                              data-context-kind={kind === "voice" ? "voice" : "user"}
                              data-context-name={u.name}
                              className="flex items-center gap-2 rounded-xl px-1.5 py-1.5 hover:bg-white/5"
                            >
                              <Avatar name={u.name} url={u.avatar_url} size={24} />
                              <span className="min-w-0 flex-1 truncate text-[11.5px] text-[color:var(--color-ink)]">
                                {u.name}
                              </span>
                              {u.in_voice && (
                                <Mic className="h-3.5 w-3.5 shrink-0 text-[color:var(--color-teal)]" aria-label="در اتاق صوتی" />
                              )}
                              {speaking && (
                                <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-[color:var(--color-teal)]" style={{ animation: "pulse-live 1.4s infinite" }} />
                              )}
                            </li>
                          );
                        })}
                      </ul>
                    </motion.div>
                  )}
                </AnimatePresence>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>

      <p className="mt-2 text-[11.5px] text-[color:var(--color-ink-dim)]">{description}</p>
    </section>
  );
}

/** Honest, specific label for what this person is doing right now. */
function stateLabel(u: PresenceUser, kind: "watching" | "voice", speaking?: boolean): string {
  const bits: string[] = [];
  if (kind === "watching") bits.push(u.watching ? "در حال تماشا" : "حاضر در اتاق");
  else bits.push("در اتاق صوتی");
  if (speaking) bits.push("در حال صحبت");
  else if (u.in_voice) bits.push("میکروفون خاموش");
  if (u.is_owner) bits.push("صاحب اتاق");
  return `${u.name} — ${bits.join("، ")}`;
}

/** Compact list of everyone present, used by the lounge summary. */
export function CouchSummary({ users }: { users: PresenceUser[] }) {
  if (users.length === 0) return null;
  return (
    <span className="inline-flex items-center gap-1 text-[11px] text-[color:var(--color-ink-muted)]">
      <Users className="h-3 w-3" />
      {users.map((u) => u.name).join("، ")}
    </span>
  );
}

function MicMini() {
  return (
    <svg viewBox="0 0 24 24" className="block h-3 w-3 text-white" fill="currentColor">
      <path d="M12 14a3 3 0 0 0 3-3V6a3 3 0 1 0-6 0v5a3 3 0 0 0 3 3Zm5.27-3a.75.75 0 0 1 .73.75 6 6 0 0 1-5.25 5.94v1.56h1.75a.75.75 0 0 1 0 1.5H8.5a.75.75 0 0 1 0-1.5h1.75v-1.56A6 6 0 0 1 5.25 11.75a.75.75 0 1 1 1.5 0 4.5 4.5 0 0 0 9 0 .75.75 0 0 1 .52-.75Z" />
    </svg>
  );
}