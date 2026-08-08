"use client";

import { LayoutDashboard, LogOut } from "lucide-react";


import { useState } from "react";
import type { AuthUser } from "@/lib/auth";

const basePath = process.env.NEXT_PUBLIC_BUILD_TARGET === "tauri" ? "" : "/stream";

const LINKS = [
  ["/", "🏠 خانه"],
  ["/#services", "⚡ سرویس‌ها"],
  ["/stream/", "🎬 استریم"],
  ["/donate", "💜 دونیت"],
  ["/#contact", "✉️ تماس"],
] as const;

export function Header({ user, onLogout }: { user?: AuthUser | null; onLogout?: () => void }) {
  const [open, setOpen] = useState(false);
  const isAdmin = user?.role === "admin";
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
          {isAdmin && (
            <a
              href={`${basePath}/admin/`}
              className="flex items-center gap-1.5 rounded-lg px-3.5 py-2 text-[14px] font-medium text-[color:var(--color-amber)] transition-colors hover:bg-white/5"
            >
              <LayoutDashboard className="h-4 w-4" />
              ادمین
            </a>
          )}
          {user && (
            <button
              onClick={onLogout}
              title="خروج از حساب"
              className="flex items-center gap-1.5 rounded-lg px-3 py-2 text-[13.5px] text-[color:var(--color-ink-muted)] transition-colors hover:bg-white/5 hover:text-[color:var(--color-coral)]"
            >
              <LogOut className="h-4 w-4" />
              <span className="max-w-[90px] truncate">{user.username}</span>
            </button>
          )}
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
          {isAdmin && (
            <a
              href={`${basePath}/admin/`}
              onClick={() => setOpen(false)}
              className="flex items-center gap-2 rounded-xl px-3.5 py-3 text-[15px] text-[color:var(--color-amber)] hover:bg-white/5"
            >
              <LayoutDashboard className="h-4 w-4" />
              پنل ادمین
            </a>
          )}
          {user && (
            <button
              onClick={() => {
                setOpen(false);
                onLogout?.();
              }}
              className="flex items-center gap-2 rounded-xl px-3.5 py-3 text-[15px] text-[color:var(--color-coral)] hover:bg-white/5"
            >
              <LogOut className="h-4 w-4" />
              خروج ({user.username})
            </button>
          )}
        </nav>
      )}
    </header>
  );
}
