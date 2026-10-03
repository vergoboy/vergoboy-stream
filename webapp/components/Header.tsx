"use client";

import { ChevronDown, LogOut, LayoutDashboard, PlayCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import type { AuthUser } from "@/lib/auth";

const basePath = process.env.NEXT_PUBLIC_BUILD_TARGET === "tauri" ? "" : "/stream";

/**
 * The lounge is the only destination in this app — `app.py` registers `/stream/`
 * and nothing else, so `/`, `/donate`, and the `/#services` / `/#contact`
 * anchors that used to sit here all returned 404. There is deliberately no
 * second nav item rather than a row of dead links; account actions live in the
 * account menu on the left.
 */
const HOME = "/stream/";
const LINKS = [{ href: HOME, label: "تماشا", icon: PlayCircle }] as const;

export function Header({ user, onLogout }: { user?: AuthUser | null; onLogout?: () => void }) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);
  const accountRef = useRef<HTMLDivElement>(null);
  const pathname = usePathname();
  const isAdmin = user?.role === "admin";

  // This Next app is mounted at /stream/, so its own pathname reads as "/".
  // Being rendered at all means we are on the lounge.
  const isActive = (href: string) => href === HOME && (pathname === "/" || pathname === "/stream" || pathname === "/stream/");

  // Dismiss the account menu on an outside click or Escape.
  useEffect(() => {
    if (!accountOpen) return;
    const onPointer = (e: PointerEvent) => {
      if (!(e.target instanceof Element) || !accountRef.current?.contains(e.target)) setAccountOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setAccountOpen(false);
    };
    window.addEventListener("pointerdown", onPointer);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("pointerdown", onPointer);
      window.removeEventListener("keydown", onKey);
    };
  }, [accountOpen]);

  const navLinks = (onNavigate?: () => void) =>
    LINKS.map(({ href, label, icon: Icon }) => (
      <a
        key={href}
        href={href}
        onClick={onNavigate}
        aria-current={isActive(href) ? "page" : undefined}
        className={`flex items-center gap-2 rounded-xl px-3.5 py-2 text-[14px] font-medium transition-colors ${
          isActive(href)
            ? "bg-[color:var(--color-plum)]/20 text-white"
            : "text-[color:var(--color-ink-muted)] hover:bg-white/5 hover:text-white"
        }`}
      >
        <Icon className="h-4 w-4" />
        {label}
      </a>
    ));

  return (
    <header className="sticky top-0 z-[100] border-b border-[color:var(--color-border)] bg-[color:var(--color-bg)]/85 backdrop-blur-xl">
      <div className="mx-auto flex h-[64px] max-w-[1120px] items-center justify-between gap-4 px-5 md:px-7">
        <a href={HOME} aria-label="برو به لانج" className="flex items-center gap-2.5">
          <div
            className="flex h-9 w-9 items-center justify-center rounded-xl text-lg font-black text-white"
            style={{ background: "linear-gradient(135deg, var(--color-amber), var(--color-plum))" }}
          >
            V
          </div>
          <span className="font-mono text-[17px]" dir="ltr">
            <span
              className="font-extrabold"
              style={{
                background: "linear-gradient(135deg, var(--color-amber), var(--color-plum))",
                WebkitBackgroundClip: "text",
                WebkitTextFillColor: "transparent",
              }}
            >
              vergoboy
            </span>
            <span className="text-[color:var(--color-ink-dim)]">.ir</span>
          </span>
        </a>

        <nav aria-label="ناوبری اصلی" className="hidden items-center gap-1 md:flex">
          {navLinks()}
        </nav>

        <div className="flex items-center gap-2">
          {user ? (
            <div className="relative" ref={accountRef}>
              <button
                onClick={() => setAccountOpen((o) => !o)}
                aria-haspopup="menu"
                aria-expanded={accountOpen}
                className="flex items-center gap-1.5 rounded-xl border border-[color:var(--color-border)] bg-white/5 px-3 py-2 text-[13px] text-[color:var(--color-ink)] transition-colors hover:bg-white/10"
              >
                <span className="max-w-[92px] truncate">{user.username}</span>
                <ChevronDown className={`h-3.5 w-3.5 transition-transform ${accountOpen ? "rotate-180" : ""}`} />
              </button>

              {accountOpen && (
                <div
                  role="menu"
                  className="absolute left-0 top-[calc(100%+6px)] z-[110] w-52 overflow-hidden rounded-2xl border border-[color:var(--color-border)] bg-[color:var(--color-bg-soft)] py-1 shadow-2xl"
                >
                  {isAdmin && (
                    <a
                      href={`${basePath}/admin/`}
                      role="menuitem"
                      className="flex items-center gap-2 px-3.5 py-2.5 text-[13px] text-[color:var(--color-amber)] transition-colors hover:bg-white/5"
                    >
                      <LayoutDashboard className="h-4 w-4" />
                      پنل ادمین
                    </a>
                  )}
                  <button
                    role="menuitem"
                    onClick={() => {
                      setAccountOpen(false);
                      onLogout?.();
                    }}
                    className="flex w-full items-center gap-2 px-3.5 py-2.5 text-right text-[13px] text-[color:var(--color-coral)] transition-colors hover:bg-white/5"
                  >
                    <LogOut className="h-4 w-4" />
                    خروج از حساب
                  </button>
                </div>
              )}
            </div>
          ) : (
            <span className="text-[13px] text-[color:var(--color-ink-dim)]">مهمان</span>
          )}

          <button
            onClick={() => setMobileOpen((o) => !o)}
            className="flex h-9 w-9 flex-col items-center justify-center gap-1.5 md:hidden"
            aria-label="منو"
            aria-expanded={mobileOpen}
          >
            <span className="h-0.5 w-5 rounded bg-[color:var(--color-ink)]" />
            <span className="h-0.5 w-5 rounded bg-[color:var(--color-ink)]" />
            <span className="h-0.5 w-5 rounded bg-[color:var(--color-ink)]" />
          </button>
        </div>
      </div>

      {mobileOpen && (
        <nav aria-label="ناوبری موبایل" className="flex flex-col gap-1 border-t border-[color:var(--color-border)] bg-[color:var(--color-bg)] px-4 pb-4 pt-2 md:hidden">
          {navLinks(() => setMobileOpen(false))}
          {isAdmin && (
            <a
              href={`${basePath}/admin/`}
              onClick={() => setMobileOpen(false)}
              className="flex items-center gap-2 rounded-xl px-3.5 py-3 text-[15px] text-[color:var(--color-amber)] hover:bg-white/5"
            >
              <LayoutDashboard className="h-4 w-4" />
              پنل ادمین
            </a>
          )}
          {user && (
            <button
              onClick={() => {
                setMobileOpen(false);
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