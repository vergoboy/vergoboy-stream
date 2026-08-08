"use client";

import { useState } from "react";
import { Copy, Check, DoorOpen, Home, Users } from "lucide-react";
import { api } from "@/lib/api";
import { updateUser } from "@/lib/auth";

const basePath = process.env.NEXT_PUBLIC_BUILD_TARGET === "tauri" ? "" : "/stream";

export function RoomBar({
  roomCode,
  online,
  onRoomChange,
}: {
  roomCode: string;
  online: number;
  onRoomChange: (code: string) => void;
}) {
  const [joinCode, setJoinCode] = useState("");
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const inviteLink = `${window.location.origin}${basePath}/?join=${roomCode}`;

  function copyInvite() {
    navigator.clipboard
      .writeText(inviteLink)
      .then(() => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 2000);
      })
      .catch(() => setError("کپی کردن با خطا مواجه شد"));
  }

  async function join(code: string) {
    const c = code.trim().toUpperCase();
    if (!c) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const res = await api.roomJoin(c);
      updateUser(res.user);
      setJoinCode("");
      setNotice(`وارد اتاق ${c} شدی`);
      onRoomChange(res.room.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "ورود به اتاق ناموفق بود");
    } finally {
      setBusy(false);
    }
  }

  async function backToMine() {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const res = await api.roomMine();
      updateUser(res.user);
      setNotice("به اتاق خودت برگشتی");
      onRoomChange(res.room.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "بازگشت به اتاق ناموفق بود");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mb-4 flex flex-wrap items-center gap-2.5 rounded-3xl border border-[color:var(--color-border)] bg-[color:var(--color-bg-soft)]/70 px-4 py-3 backdrop-blur-md">
      <div className="flex items-center gap-2">
        <span className="text-[12.5px] text-[color:var(--color-ink-muted)]">کد اتاق:</span>
        <span className="rounded-lg border border-[color:var(--color-amber)]/40 bg-white/5 px-2.5 py-1 font-mono text-sm font-bold tracking-widest text-[color:var(--color-amber)]" dir="ltr">
          {roomCode}
        </span>
      </div>

      <button
        onClick={copyInvite}
        title="کپی لینک دعوت"
        className="flex items-center gap-1.5 rounded-xl border border-[color:var(--color-border)] bg-white/5 px-3 py-1.5 text-[12.5px] text-[color:var(--color-ink)] transition-colors hover:border-[color:var(--color-amber)]/50"
      >
        {copied ? <Check className="h-3.5 w-3.5 text-[color:var(--color-teal)]" /> : <Copy className="h-3.5 w-3.5" />}
        {copied ? "کپی شد!" : "کپی لینک دعوت"}
      </button>

      <div className="flex items-center gap-1.5 text-[12px] text-[color:var(--color-ink-muted)]">
        <Users className="h-3.5 w-3.5 text-[color:var(--color-teal)]" />
        {online} آنلاین
      </div>

      <div className="flex flex-1 items-center justify-end gap-2">
        <form
          className="flex items-center gap-1.5"
          onSubmit={(e) => {
            e.preventDefault();
            join(joinCode);
          }}
        >
          <input
            value={joinCode}
            onChange={(e) => setJoinCode(e.target.value.toUpperCase())}
            maxLength={6}
            placeholder="کد اتاق…"
            className="w-24 rounded-xl border border-[color:var(--color-border)] bg-white/5 px-3 py-1.5 text-center font-mono text-[12.5px] tracking-widest text-[color:var(--color-ink)] outline-none focus:border-[color:var(--color-amber)]"
            dir="ltr"
          />
          <button
            type="submit"
            disabled={busy || !joinCode.trim()}
            title="ورود با کد"
            className="flex items-center gap-1.5 rounded-xl border border-[color:var(--color-border)] bg-white/5 px-3 py-1.5 text-[12.5px] text-[color:var(--color-ink)] transition-colors hover:border-[color:var(--color-amber)]/50 disabled:opacity-40"
          >
            <DoorOpen className="h-3.5 w-3.5" />
            ورود
          </button>
        </form>

        <button
          onClick={backToMine}
          disabled={busy}
          title="بازگشت به اتاق خودت"
          className="flex items-center gap-1.5 rounded-xl border border-[color:var(--color-border)] bg-white/5 px-3 py-1.5 text-[12.5px] text-[color:var(--color-ink)] transition-colors hover:border-[color:var(--color-amber)]/50 disabled:opacity-40"
        >
          <Home className="h-3.5 w-3.5" />
          اتاق من
        </button>
      </div>

      {(error || notice) && (
        <p className={`w-full text-[12px] ${error ? "text-[color:var(--color-coral)]" : "text-[color:var(--color-teal)]"}`}>
          {error ?? notice}
        </p>
      )}
    </div>
  );
}
