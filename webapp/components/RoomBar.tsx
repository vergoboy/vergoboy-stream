"use client";

import { useState } from "react";
import { Copy, Check, DoorOpen, Home, Ticket } from "lucide-react";
import { api } from "@/lib/api";
import { updateUser } from "@/lib/auth";

const basePath = process.env.NEXT_PUBLIC_BUILD_TARGET === "tauri" ? "" : "/stream";

/** Your ticket: the room code, a one-tap invite, and the way into someone else's room. */
export function RoomBar({ roomCode, online, onRoomChange }: { roomCode: string; online: number; onRoomChange: (code: string) => void }) {
  const [joinCode, setJoinCode] = useState("");
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ text: string; error?: boolean } | null>(null);

  const origin = process.env.NEXT_PUBLIC_API_ORIGIN || window.location.origin;
  const inviteLink = `${origin}${basePath}/?join=${roomCode}`;

  function copyInvite() {
    navigator.clipboard
      .writeText(inviteLink)
      .then(() => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 2000);
      })
      .catch(() => setMsg({ text: "کپی نشد؛ لینک رو دستی کپی کن", error: true }));
  }

  async function go(fn: () => Promise<{ user: Parameters<typeof updateUser>[0]; room: { id: string } }>, ok: string) {
    setBusy(true);
    setMsg(null);
    try {
      const res = await fn();
      updateUser(res.user);
      setJoinCode("");
      setMsg({ text: ok });
      onRoomChange(res.room.id);
    } catch (e) {
      setMsg({ text: e instanceof Error ? e.message : "نشد", error: true });
    }
    setBusy(false);
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="ticket relative overflow-hidden rounded-2xl bg-[color:var(--color-amber)] p-4 text-[#1b1005]">
        <div className="flex items-center gap-2 text-[12px] font-bold opacity-70">
          <Ticket className="h-4 w-4" /> بلیت اتاق تو · {online} نفر آنلاین
        </div>
        <div className="my-2 border-y-2 border-dashed border-black/25 py-2 text-center font-mono text-[34px] font-black tracking-[0.3em]" dir="ltr">
          {roomCode}
        </div>
        <button
          onClick={copyInvite}
          className="flex w-full items-center justify-center gap-2 rounded-xl bg-[#1b1005] py-2.5 text-[13.5px] font-bold text-[color:var(--color-amber)] active:scale-[0.98]"
        >
          {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
          {copied ? "لینک دعوت کپی شد!" : "کپی لینک دعوت"}
        </button>
      </div>

      <form
        className="flex items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          const c = joinCode.trim().toUpperCase();
          if (c) void go(() => api.roomJoin(c), `وارد اتاق ${c} شدی 🎉`);
        }}
      >
        <input
          value={joinCode}
          onChange={(e) => setJoinCode(e.target.value.toUpperCase())}
          maxLength={6}
          placeholder="کد اتاق دوستت"
          dir="ltr"
          className="min-w-0 flex-1 rounded-xl border border-white/12 bg-white/5 px-3 py-2.5 text-center font-mono text-[14px] tracking-widest text-white outline-none focus:border-[color:var(--color-amber)]"
        />
        <button
          type="submit"
          disabled={busy || !joinCode.trim()}
          className="flex shrink-0 items-center gap-1.5 rounded-xl bg-white/10 px-4 py-2.5 text-[13px] font-bold text-white hover:bg-white/15 disabled:opacity-40"
        >
          <DoorOpen className="h-4 w-4" />
          ورود
        </button>
      </form>
      <button
        onClick={() => void go(() => api.roomMine(), "برگشتی تو اتاق خودت")}
        disabled={busy}
        className="flex items-center justify-center gap-2 rounded-xl border border-white/12 py-2.5 text-[13px] text-white/75 hover:bg-white/8 disabled:opacity-40"
      >
        <Home className="h-4 w-4" />
        برگشت به اتاق خودم
      </button>
      {msg && <p className={`text-[12.5px] ${msg.error ? "text-[color:var(--color-coral)]" : "text-[color:var(--color-teal)]"}`}>{msg.text}</p>}
    </div>
  );
}
