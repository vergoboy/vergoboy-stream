"use client";

import { useState } from "react";
import { motion, AnimatePresence } from "./anim";
import { Eye, EyeOff, Mail, RefreshCw, ArrowRight } from "lucide-react";
import { login, register, resendVerification, forgotPassword, oauthLoginUrl, VerifyRequiredError } from "@/lib/auth";

type Mode = "login" | "register";

function readOAuthError(): string | null {
  if (typeof window === "undefined") return null;
  const p = new URLSearchParams(window.location.search);
  const err = p.get("oauth_error");
  if (err) {
    p.delete("oauth_error");
    const q = p.toString();
    window.history.replaceState(null, "", `${window.location.pathname}${q ? `?${q}` : ""}`);
  }
  return err;
}

export function AuthGate() {
  const [mode, setMode] = useState<Mode>("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [email, setEmail] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [verifyEmail, setVerifyEmail] = useState<string | null>(null);
  const [verifySent, setVerifySent] = useState(false);
  const [showForgot, setShowForgot] = useState(false);
  const [forgotSent, setForgotSent] = useState(false);
  const [error, setError] = useState<string | null>(readOAuthError());
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (busy) return;
    const u = username.trim();
    const e = email.trim().toLowerCase();
    if (mode === "login" && (!u || !password)) {
      setError("نام کاربری یا ایمیل و رمز عبور را وارد کن");
      return;
    }
    if (mode === "register") {
      if (!u || !password || !e) {
        setError("نام کاربری، ایمیل و رمز عبور را وارد کن");
        return;
      }
    }
    setError(null);
    setVerifySent(false);
    setBusy(true);
    try {
      if (mode === "login") await login(u, password);
      else await register(u, password, "", e);
      // Successful auth flips `useAuth()` — the page unmounts this gate.
    } catch (e) {
      if (e instanceof VerifyRequiredError) {
        setVerifyEmail(e.email);
        setError(e.message);
      } else {
        setError(e instanceof Error ? e.message : "خطای ناشناخته");
      }
    } finally {
      setBusy(false);
    }
  }

  async function resend() {
    if (!verifyEmail || busy) return;
    setBusy(true);
    setVerifySent(false);
    setError(null);
    try {
      await resendVerification(verifyEmail);
      setVerifySent(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "ارسال دوباره ناموفق بود");
    } finally {
      setBusy(false);
    }
  }

  async function sendForgot() {
    if (busy) return;
    const e = email.trim().toLowerCase();
    if (!e) {
      setError("ایمیل را وارد کن");
      return;
    }
    setError(null);
    setForgotSent(false);
    setBusy(true);
    try {
      await forgotPassword(e);
      setForgotSent(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "درخواست بازنشانی ناموفق بود");
    } finally {
      setBusy(false);
    }
  }

  function switchMode(m: Mode) {
    setMode(m);
    setError(null);
    setVerifySent(false);
    setShowForgot(false);
    setForgotSent(false);
  }

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
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

          {verifyEmail ? (
            <>
              <h3 className="mb-2 text-lg font-bold text-[color:var(--color-ink)]">
                <Mail className="mx-auto mb-2 h-6 w-6 text-[color:var(--color-amber)]" />
                ایمیلت را تأیید کن
              </h3>
              <p className="mb-5 text-[13px] leading-relaxed text-[color:var(--color-ink-muted)]">
                یک لینک تأیید به <span className="font-bold text-[color:var(--color-ink)]" dir="ltr">{verifyEmail}</span>{" "}
                فرستادیم. روی لینک داخل ایمیل بزن تا وارد شوی.
              </p>

              {verifySent && (
                <p className="mb-4 rounded-xl border border-[color:var(--color-teal)]/30 bg-[color:var(--color-teal)]/10 px-3 py-2 text-[12.5px] text-[color:var(--color-teal)]">
                  ایمیل جدید ارسال شد؛ صندوق ورودی را چک کن.
                </p>
              )}
              {error && !verifySent && (
                <p className="mb-4 rounded-xl border border-[color:var(--color-coral)]/30 bg-[color:var(--color-coral)]/10 px-3 py-2 text-[12.5px] text-[color:var(--color-coral)]">
                  {error}
                </p>
              )}

              <button
                onClick={resend}
                disabled={busy}
                className="mb-3 flex w-full items-center justify-center gap-2 rounded-2xl border border-[color:var(--color-border)] bg-white/5 px-6 py-3 text-[14px] font-bold text-[color:var(--color-ink)] transition-colors hover:border-[color:var(--color-amber)]/50 disabled:opacity-60"
              >
                <RefreshCw className="h-4 w-4" />
                {busy ? "لطفاً صبر کن…" : "ارسال دوباره لینک"}
              </button>
              <button
                onClick={() => {
                  setVerifyEmail(null);
                  setError(null);
                  switchMode("login");
                }}
                className="flex w-full items-center justify-center gap-2 rounded-2xl px-6 py-3 text-[13.5px] font-bold text-[color:var(--color-ink-muted)] transition-colors hover:text-[color:var(--color-ink)]"
              >
                <ArrowRight className="h-4 w-4 rotate-180" />
                بازگشت به ورود
              </button>
            </>
          ) : showForgot ? (
            <>
              <h3 className="mb-2 text-lg font-bold text-[color:var(--color-ink)]">
                فراموشی رمز عبور
              </h3>
              <p className="mb-5 text-[13px] leading-relaxed text-[color:var(--color-ink-muted)]">
                ایمیل حساب‌ات را بنویس؛ یک لینک برای تعیین رمز جدید برایت می‌فرستیم.
              </p>

              {forgotSent && (
                <p className="mb-4 rounded-xl border border-[color:var(--color-teal)]/30 bg-[color:var(--color-teal)]/10 px-3 py-2 text-[12.5px] text-[color:var(--color-teal)]">
                  لینک بازنشانی فرستاده شد؛ اگر حسابی با این ایمیل باشد، صندوق ورودی را چک کن.
                </p>
              )}
              {error && !forgotSent && (
                <p className="mb-4 rounded-xl border border-[color:var(--color-coral)]/30 bg-[color:var(--color-coral)]/10 px-3 py-2 text-[12.5px] text-[color:var(--color-coral)]">
                  {error}
                </p>
              )}

              <input
                type="email"
                autoFocus
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && sendForgot()}
                maxLength={255}
                placeholder="ایمیل حساب‌ات"
                autoComplete="email"
                className="mb-4 w-full rounded-2xl border border-[color:var(--color-border)] bg-white/5 px-4 py-3 text-center text-[14.5px] text-[color:var(--color-ink)] outline-none focus:border-[color:var(--color-amber)]"
              />

              <button
                onClick={sendForgot}
                disabled={busy || forgotSent}
                className="w-full rounded-2xl px-6 py-3 text-[14.5px] font-bold text-white transition-transform active:scale-[0.98] disabled:opacity-60"
                style={{ background: "linear-gradient(135deg, var(--color-amber-soft), var(--color-amber))", boxShadow: "var(--shadow-lamp)" }}
              >
                {busy ? "لطفاً صبر کن…" : "ارسال لینک بازنشانی"}
              </button>
              <button
                onClick={() => {
                  setShowForgot(false);
                  setForgotSent(false);
                  setError(null);
                }}
                className="mt-3 flex w-full items-center justify-center gap-2 rounded-2xl px-6 py-3 text-[13.5px] font-bold text-[color:var(--color-ink-muted)] transition-colors hover:text-[color:var(--color-ink)]"
              >
                <ArrowRight className="h-4 w-4 rotate-180" />
                بازگشت به ورود
              </button>
            </>
          ) : (
            <>
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
                    onClick={() => switchMode(m)}
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
                placeholder={mode === "login" ? "نام کاربری یا ایمیل" : "نام کاربری"}
                autoCapitalize="none"
                autoComplete="username"
                className="mb-3 w-full rounded-2xl border border-[color:var(--color-border)] bg-white/5 px-4 py-3 text-center text-[14.5px] text-[color:var(--color-ink)] outline-none focus:border-[color:var(--color-amber)]"
              />

              {mode === "register" && (
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && submit()}
                  maxLength={255}
                  placeholder="ایمیل (برای تأیید حساب)"
                  autoComplete="email"
                  className="mb-3 w-full rounded-2xl border border-[color:var(--color-border)] bg-white/5 px-4 py-3 text-center text-[14.5px] text-[color:var(--color-ink)] outline-none focus:border-[color:var(--color-amber)]"
                />
              )}

              <div className="relative mb-4">
                <input
                  type={showPw ? "text" : "password"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && submit()}
                  maxLength={128}
                  placeholder="رمز عبور (حداقل ۶ کاراکتر)"
                  autoComplete={mode === "login" ? "current-password" : "new-password"}
                  className="w-full rounded-2xl border border-[color:var(--color-border)] bg-white/5 px-4 py-3 text-center text-[14.5px] text-[color:var(--color-ink)] outline-none focus:border-[color:var(--color-amber)]"
                />
                <button
                  type="button"
                  onClick={() => setShowPw(!showPw)}
                  aria-label={showPw ? "پنهان کردن رمز" : "نمایش رمز"}
                  title={showPw ? "پنهان کردن رمز" : "نمایش رمز"}
                  className="absolute left-2 top-1/2 -translate-y-1/2 rounded-lg p-1.5 text-[color:var(--color-ink-muted)] transition-colors hover:text-[color:var(--color-ink)]"
                >
                  {showPw ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>

              {mode === "login" && (
                <button
                  type="button"
                  onClick={() => {
                    setShowForgot(true);
                    setForgotSent(false);
                    setError(null);
                  }}
                  className="mb-4 -mt-1 text-[12px] font-bold text-[color:var(--color-ink-muted)] transition-colors hover:text-[color:var(--color-amber)]"
                >
                  رمز عبور را فراموش کرده‌ای؟
                </button>
              )}

              {error && (
                <p className="mb-4 rounded-xl border border-[color:var(--color-coral)]/30 bg-[color:var(--color-coral)]/10 px-3 py-2 text-[12.5px] text-[color:var(--color-coral)]">
                  {error}
                </p>
              )}

              <button
                onClick={submit}
                disabled={busy}
                className="w-full rounded-2xl px-6 py-3 text-[14.5px] font-bold text-white transition-transform active:scale-[0.98] disabled:opacity-60"
                style={{ background: "linear-gradient(135deg, var(--color-amber-soft), var(--color-amber))", boxShadow: "var(--shadow-lamp)" }}
              >
                {busy ? "لطفاً صبر کن…" : mode === "login" ? "ورود" : "ساخت حساب"}
              </button>

              <div className="my-4 flex items-center gap-3 text-[11.5px] text-[color:var(--color-ink-muted)]">
                <span className="h-px flex-1 bg-[color:var(--color-border)]" />
                یا با حساب خارجی
                <span className="h-px flex-1 bg-[color:var(--color-border)]" />
              </div>

              <div className="flex gap-2.5">
                <a
                  href={oauthLoginUrl("google")}
                  className="flex flex-1 items-center justify-center gap-2 rounded-2xl border border-[color:var(--color-border)] bg-white/5 px-3 py-2.5 text-[13px] font-bold text-[color:var(--color-ink)] transition-colors hover:border-[color:var(--color-amber)]/50"
                >
                  <GoogleIcon />
                  گوگل
                </a>
                <a
                  href={oauthLoginUrl("github")}
                  className="flex flex-1 items-center justify-center gap-2 rounded-2xl border border-[color:var(--color-border)] bg-white/5 px-3 py-2.5 text-[13px] font-bold text-[color:var(--color-ink)] transition-colors hover:border-[color:var(--color-amber)]/50"
                >
                  <GitHubIcon />
                  گیت‌هاب
                </a>
              </div>
            </>
          )}
        </motion.div>
      </motion.div>
    </AnimatePresence>
  );
}

function GoogleIcon() {
  return (
    <svg className="h-4 w-4" viewBox="0 0 24 24" aria-hidden="true">
      <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.27-4.74 3.27-8.1z" />
      <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
      <path fill="#FBBC05" d="M5.84 14.1c-.22-.66-.35-1.36-.35-2.1s.13-1.44.35-2.1V7.06H2.18A10.96 10.96 0 0 0 1 12c0 1.77.43 3.45 1.18 4.94l3.66-2.84z" />
      <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z" />
    </svg>
  );
}

function GitHubIcon() {
  return (
    <svg className="h-4 w-4" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M12 .5C5.65.5.5 5.65.5 12c0 5.08 3.29 9.39 7.86 10.91.58.11.79-.25.79-.55v-2.17c-3.2.7-3.87-1.36-3.87-1.36-.52-1.33-1.28-1.68-1.28-1.68-1.04-.72.08-.7.08-.7 1.16.08 1.77 1.19 1.77 1.19 1.02 1.75 2.68 1.25 3.34.95.1-.74.4-1.25.73-1.54-2.55-.29-5.23-1.28-5.23-5.68 0-1.26.45-2.28 1.19-3.09-.12-.29-.52-1.46.11-3.05 0 0 .97-.31 3.17 1.18a11 11 0 0 1 5.78 0c2.2-1.49 3.17-1.18 3.17-1.18.63 1.59.23 2.76.11 3.05.74.81 1.19 1.83 1.19 3.09 0 4.41-2.68 5.38-5.24 5.67.41.35.77 1.05.77 2.11v3.13c0 .3.2.67.8.55A11.5 11.5 0 0 0 23.5 12C23.5 5.65 18.35.5 12 .5z" />
    </svg>
  );
}
