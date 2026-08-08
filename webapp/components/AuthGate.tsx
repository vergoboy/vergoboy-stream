"use client";

import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { login, register } from "@/lib/auth";

type Mode = "login" | "register";

export function AuthGate() {
  const [mode, setMode] = useState<Mode>("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (busy) return;
    const u = username.trim();
    if (!u || !password) {
      setError("نام کاربری و رمز عبور را وارد کن");
      return;
    }
    setError(null);
    setBusy(true);
    try {
      if (mode === "login") await login(u, password);
      else await register(u, password);
      // Successful auth flips `useAuth()` — the page unmounts this gate.
    } catch (e) {
      setError(e instanceof Error ? e.message : "خطای ناشناخته");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        className="fixed inset-0 z-[300] flex items-center justify-center bg-black/70 p-5 backdrop-blur-sm"
      >
        <motion.div
          initial={{ opacity: 0, y: 16, scale: 0.97 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          transition={{ type: "spring", stiffness: 260, damping: 24 }}
          className="w-full max-w-sm rounded-3xl border border-[color:var(--color-border)] bg-[color:var(--color-bg-soft)] p-7 text-center shadow-[var(--shadow-soft)]"
        >
          <div
            className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl text-xl font-black text-white"
            style={{ background: "linear-gradient(135deg, var(--color-amber), var(--color-plum))" }}
          >
            V
          </div>
          <h3 className="mb-2 text-lg font-bold text-[color:var(--color-ink)]">
            {mode === "login" ? "ورود به حساب" : "ساخت حساب جدید"}
          </h3>
          <p className="mb-5 text-[13px] leading-relaxed text-[color:var(--color-ink-muted)]">
            هر کاربر یک اتاق مخصوص به خودش دارد؛ با کد اتاق دوستت را دعوت کن.
          </p>

          <div className="mb-4 flex rounded-2xl border border-[color:var(--color-border)] bg-white/5 p-1">
            {(["login", "register"] as Mode[]).map((m) => (
              <button
                key={m}
                onClick={() => {
                  setMode(m);
                  setError(null);
                }}
                className={`flex-1 rounded-xl py-2 text-[13.5px] font-bold transition-colors ${
                  mode === m ? "bg-white/10 text-[color:var(--color-ink)]" : "text-[color:var(--color-ink-muted)]"
                }`}
              >
                {m === "login" ? "ورود" : "ثبت‌نام"}
              </button>
            ))}
          </div>

          <input
            autoFocus
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
            maxLength={24}
            placeholder="نام کاربری"
            autoCapitalize="none"
            className="mb-3 w-full rounded-2xl border border-[color:var(--color-border)] bg-white/5 px-4 py-3 text-center text-[14.5px] text-[color:var(--color-ink)] outline-none focus:border-[color:var(--color-amber)]"
          />
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
            maxLength={128}
            placeholder="رمز عبور (حداقل ۶ کاراکتر)"
            className="mb-4 w-full rounded-2xl border border-[color:var(--color-border)] bg-white/5 px-4 py-3 text-center text-[14.5px] text-[color:var(--color-ink)] outline-none focus:border-[color:var(--color-amber)]"
          />

          {error && (
            <p className="mb-4 rounded-xl border border-[color:var(--color-coral)]/30 bg-[color:var(--color-coral)]/10 px-3 py-2 text-[12.5px] text-[color:var(--color-coral)]">
              {error}
            </p>
          )}

          <button
            onClick={submit}
            disabled={busy}
            className="w-full rounded-2xl px-6 py-3 text-[14.5px] font-bold text-white transition-transform active:scale-[0.98] disabled:opacity-60"
            style={{ background: "linear-gradient(135deg, var(--color-amber), var(--color-plum))", boxShadow: "var(--shadow-lamp)" }}
          >
            {busy ? "لطفاً صبر کن…" : mode === "login" ? "ورود" : "ساخت حساب"}
          </button>
        </motion.div>
      </motion.div>
    </AnimatePresence>
  );
}
