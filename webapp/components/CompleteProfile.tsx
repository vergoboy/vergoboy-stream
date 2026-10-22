"use client";

import { useState } from "react";
import { motion } from "./anim";
import { Eye, EyeOff } from "lucide-react";
import { completeProfile, logout } from "@/lib/auth";
import type { AuthUser } from "@/lib/types";

const FIELD =
  "mb-3 w-full rounded-2xl border border-[color:var(--color-border)] bg-white/5 px-4 py-3 text-center text-[14.5px] text-[color:var(--color-ink)] outline-none focus:border-[color:var(--color-amber)]";

/** Shown once, right after a first Google/GitHub sign-in: the account exists, the person picks the real credentials. */
export function CompleteProfile({ user }: { user: AuthUser }) {
  const [username, setUsername] = useState(user.username);
  const [password, setPassword] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (busy) return;
    const u = username.trim();
    if (u.length < 3 || u.length > 24) return setError("نام کاربری باید ۳ تا ۲۴ حرف باشد");
    if (!/^[A-Za-z0-9_.\-]+$/.test(u)) return setError("نام کاربری فقط حروف انگلیسی، عدد، _ و - می‌تواند باشد");
    if (password.length < 6) return setError("رمز عبور باید حداقل ۶ کاراکتر باشد");
    setBusy(true);
    setError(null);
    try {
      await completeProfile(u, password); // the stored user changes, the app takes over
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className="fixed inset-0 z-[300] flex items-center justify-center overflow-y-auto bg-black/70 p-5 backdrop-blur-sm"
    >
      <motion.div
        initial={{ opacity: 0, y: 16, scale: 0.97 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ type: "spring", stiffness: 260, damping: 24 }}
        className="w-full max-w-sm rounded-3xl border border-[color:var(--color-border)] bg-[color:var(--color-bg-soft)] p-7 text-center shadow-[var(--shadow-soft)]"
      >
        <div
          className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl text-xl font-black text-white"
          style={{ background: "linear-gradient(135deg, var(--color-amber-soft), var(--color-amber))" }}
        >
          V
        </div>
        <h3 className="mb-2 text-lg font-bold text-[color:var(--color-ink)]">یک قدم دیگر</h3>
        <p className="mb-5 text-[13px] leading-relaxed text-[color:var(--color-ink-muted)]">
          ایمیلت تأیید شد{user.email ? <> (<span dir="ltr">{user.email}</span>)</> : null}. نام کاربری و رمز عبور خودت را انتخاب کن تا بتوانی بعداً هم با آن‌ها وارد شوی.
        </p>

        <input
          autoFocus
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
          maxLength={24}
          placeholder="نام کاربری"
          autoCapitalize="none"
          autoComplete="username"
          dir="ltr"
          className={FIELD}
        />
        <div className="relative">
          <input
            type={showPw ? "text" : "password"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
            placeholder="رمز عبور (حداقل ۶ کاراکتر)"
            autoComplete="new-password"
            dir="ltr"
            className={FIELD}
          />
          <button
            type="button"
            onClick={() => setShowPw((v) => !v)}
            aria-label={showPw ? "پنهان‌کردن رمز" : "نمایش رمز"}
            className="absolute left-3 top-3 text-[color:var(--color-ink-muted)] hover:text-[color:var(--color-ink)]"
          >
            {showPw ? <EyeOff className="h-5 w-5" /> : <Eye className="h-5 w-5" />}
          </button>
        </div>

        {error && <p className="mb-3 text-[13px] text-[color:var(--color-coral)]">{error}</p>}

        <button
          onClick={submit}
          disabled={busy}
          className="w-full rounded-2xl px-6 py-3 text-[14.5px] font-bold text-white transition-transform active:scale-[0.98] disabled:opacity-60"
          style={{ background: "linear-gradient(135deg, var(--color-amber-soft), var(--color-amber))", boxShadow: "var(--shadow-lamp)" }}
        >
          {busy ? "لطفاً صبر کن…" : "ذخیره و ادامه"}
        </button>
        <button
          onClick={() => void logout()}
          className="mt-3 w-full rounded-2xl px-6 py-3 text-[13.5px] font-bold text-[color:var(--color-ink-muted)] transition-colors hover:text-[color:var(--color-ink)]"
        >
          خروج از حساب
        </button>
      </motion.div>
    </motion.div>
  );
}
