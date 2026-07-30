"use client";

import { Tv, Users, Radio, Sparkles } from "lucide-react";


import { useState } from "react";

const LINKS = [
  ["/", "🏠 خانه"],
  ["/#services", "⚡ سرویس‌ها"],
  ["/stream/", "🎬 استریم"],
  ["/donate", "💜 دونیت"],
  ["/#contact", "✉️ تماس"],
] as const;

export function Header() {
  const [open, setOpen] = useState(false);
  return (
    <header className="sticky top-0 z-[100] border-b border-[color:var(--color-border)] bg-[color:var(--color-bg)]/85 backdrop-blur-xl">
      <div className="mx-auto flex h-[64px] max-w-[1120px] items-center justify-between gap-4 px-5 md:px-7">
        {/* eslint-disable-next-line @next/next/no-html-link-for-pages -- "/" is vergoboy.ir's main Flask-rendered site, not a route inside this Next app (which is mounted at /stream/) */}
        <a href="/" className="flex items-center gap-2.5">
          <div
            className="flex h-9 w-9 items-center justify-center rounded-xl text-lg font-black text-white"
            style={{ background: "linear-gradient(135deg, var(--color-amber), var(--color-plum))" }}
          >
            V
          </div>
          <span className="font-mono text-[17px]" dir="ltr">
            <span className="font-extrabold" style={{ background: "linear-gradient(135deg, var(--color-amber), var(--color-plum))", WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent" }}>
              vergoboy
            </span>
            <span className="text-[color:var(--color-ink-dim)]">.ir</span>
          </span>
        </a>

        <nav className="hidden items-center gap-1 md:flex">
          {LINKS.map(([href, label]) => (
            <a
              key={href}
              href={href}
              className={`rounded-lg px-3.5 py-2 text-[14px] font-medium transition-colors ${
                href === "/stream/" ? "bg-[color:var(--color-plum)]/20 text-white" : "text-[color:var(--color-ink-muted)] hover:bg-white/5 hover:text-white"
              }`}
            >
              {label}
            </a>
          ))}
        </nav>

        <button onClick={() => setOpen((o) => !o)} className="flex h-9 w-9 flex-col items-center justify-center gap-1.5 md:hidden" aria-label="منو">
          <span className="h-0.5 w-5 rounded bg-[color:var(--color-ink)]" />
          <span className="h-0.5 w-5 rounded bg-[color:var(--color-ink)]" />
          <span className="h-0.5 w-5 rounded bg-[color:var(--color-ink)]" />
        </button>
      </div>

      {open && (
        <nav className="flex flex-col gap-1 border-t border-[color:var(--color-border)] bg-[color:var(--color-bg)] px-4 pb-4 pt-2 md:hidden">
          {LINKS.map(([href, label]) => (
            <a key={href} href={href} onClick={() => setOpen(false)} className="rounded-xl px-3.5 py-3 text-[15px] text-[color:var(--color-ink)] hover:bg-white/5">
              {label}
            </a>
          ))}
        </nav>
      )}
    </header>
  );
}