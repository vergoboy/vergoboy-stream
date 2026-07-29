"use client";

import { AnimatePresence, motion } from "framer-motion";
import { Avatar } from "./Avatar";
import type { PresenceUser } from "@/lib/types";

// A little deterministic per-seat variance (based on the name) so people
// don't look robotically identical sitting there — but stable across
// re-renders, not randomized every frame.
function seatWobble(name: string) {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 17 + name.charCodeAt(i)) % 7;
  return (h - 3) * 0.6; // -1.8deg .. +1.8deg
}

export function Sofa({ users }: { users: PresenceUser[] }) {
  const width = Math.min(880, Math.max(220, users.length * 96 + 80));

  return (
    <section className="relative mx-auto mt-2 mb-6 flex flex-col items-center">
      <div className="relative" style={{ width, maxWidth: "100%", transition: "width .4s cubic-bezier(.22,1,.36,1)" }}>
        {/* lamp glow */}
        <div
          className="pointer-events-none absolute -top-10 left-1/2 -translate-x-1/2 h-32 w-64 rounded-full blur-3xl opacity-60"
          style={{ background: "radial-gradient(ellipse, var(--color-amber) 0%, transparent 70%)" }}
        />

        {/* couch */}
        <svg viewBox="0 0 400 130" className="w-full h-auto relative z-10" preserveAspectRatio="none">
          <defs>
            <linearGradient id="cushionGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--color-plum-soft)" />
              <stop offset="100%" stopColor="var(--color-plum-deep)" />
            </linearGradient>
            <linearGradient id="baseGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--color-plum)" />
              <stop offset="100%" stopColor="var(--color-plum-deep)" />
            </linearGradient>
          </defs>
          {/* backrest */}
          <rect x="6" y="14" width="388" height="46" rx="18" fill="url(#cushionGrad)" opacity="0.9" />
          {/* seat base */}
          <rect x="0" y="52" width="400" height="60" rx="20" fill="url(#baseGrad)" />
          {/* armrests */}
          <rect x="0" y="34" width="26" height="78" rx="13" fill="var(--color-plum-deep)" />
          <rect x="374" y="34" width="26" height="78" rx="13" fill="var(--color-plum-deep)" />
          {/* seat cushion seams */}
          {Array.from({ length: Math.max(3, Math.min(6, users.length || 3)) }).map((_, i, arr) => (
            <line
              key={i}
              x1={(400 / arr.length) * (i + 1)}
              y1="58"
              x2={(400 / arr.length) * (i + 1)}
              y2="106"
              stroke="rgba(0,0,0,0.18)"
              strokeWidth="2"
            />
          ))}
        </svg>

        {/* people, sitting on top of the cushions */}
        <div className="relative z-20 -mt-16 flex items-end justify-center gap-1 px-6 pb-3 min-h-[64px] flex-wrap">
          <AnimatePresence mode="popLayout">
            {users.length === 0 && (
              <motion.p
                key="empty"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="text-xs text-[color:var(--color-ink-dim)] pb-2"
              >
                فعلاً کسی روی مبل نیست…
              </motion.p>
            )}
            {users.map((u) => (
              <motion.div
                key={u.name}
                layout
                initial={{ opacity: 0, y: 18, scale: 0.85 }}
                animate={{ opacity: 1, y: 0, scale: 1, rotate: seatWobble(u.name) }}
                exit={{ opacity: 0, y: 14, scale: 0.85 }}
                transition={{ type: "spring", stiffness: 340, damping: 22 }}
                className="flex flex-col items-center gap-1 w-16"
              >
                <Avatar name={u.name} url={u.avatar_url} size={40} className="ring-2 ring-[color:var(--color-bg)] shadow-[var(--shadow-soft)]" />
                <span className="text-[10.5px] text-[color:var(--color-ink-muted)] max-w-16 truncate">{u.name}</span>
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      </div>
      <p className="mt-2 text-[11.5px] text-[color:var(--color-ink-dim)]">
        👥 همه‌ی کسایی که الان توی اتاق‌ان، رو مبل نشستن
      </p>
    </section>
  );
}
