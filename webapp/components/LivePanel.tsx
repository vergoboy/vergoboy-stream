"use client";

import { useState } from "react";
import { api } from "@/lib/api";

const inputClass =
  "w-full rounded-xl border border-[color:var(--color-border)] bg-white/5 px-3.5 py-2.5 text-[13.5px] text-[color:var(--color-ink)] outline-none focus:border-[color:var(--color-amber)]";

function CopyField({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-xs text-[color:var(--color-ink-muted)]">{label}</label>
      <div className="flex items-center gap-2 rounded-xl border border-[color:var(--color-border)] bg-white/5 px-3 py-2">
        <code className="flex-1 overflow-x-auto whitespace-nowrap font-mono text-xs text-[color:var(--color-amber)]" dir="ltr">
          {value}
        </code>
        <button
          onClick={() => {
            navigator.clipboard.writeText(value);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          }}
          className="shrink-0 rounded-lg border border-[color:var(--color-border)] px-2 py-1 text-[11.5px] text-[color:var(--color-ink)] hover:border-[color:var(--color-amber)]/50"
        >
          {copied ? "کپی شد ✓" : "کپی"}
        </button>
      </div>
    </div>
  );
}

export function LivePanel({ myName }: { myName: string }) {
  const [key, setKey] = useState<{ key: string; push_url: string; playback_url: string } | null>(null);
  const [manualTitle, setManualTitle] = useState("");
  const [manualUrl, setManualUrl] = useState("");

  return (
    <div className="grid gap-6 md:grid-cols-2">
      <div>
        <h4 className="mb-1.5 text-[14.5px] font-bold text-[color:var(--color-ink)]">۱) ساخت کلید استریم</h4>
        <p className="mb-3 text-[12.5px] leading-relaxed text-[color:var(--color-ink-dim)]">یک کلید بساز و آدرس Push رو در نرم‌افزار پخشت (OBS، اپ موبایل و ...) بذار.</p>
        <button
          onClick={async () => setKey(await api.newLiveKey())}
          className="rounded-xl border border-[color:var(--color-border)] bg-white/5 px-4 py-2.5 text-[13px] text-[color:var(--color-ink)] hover:border-[color:var(--color-amber)]/50"
        >
          🔑 ساخت کلید جدید
        </button>

        {key && (
          <div className="mt-4 flex flex-col gap-3">
            <CopyField label="آدرس Push (Server)" value={key.push_url} />
            <CopyField label="کلید استریم (Stream Key)" value={key.key} />
            <CopyField label="آدرس پخش (HLS Playback)" value={key.playback_url} />
            <button
              onClick={async () => {
                await api.addLive(key.playback_url, "پخش زنده", myName, key.key);
                setKey(null);
              }}
              className="mt-1 rounded-xl px-5 py-2.5 text-[14px] font-bold text-white"
              style={{ background: "linear-gradient(135deg, var(--color-amber), var(--color-plum))" }}
            >
              ➕ افزودن این پخش زنده به پلی‌لیست
            </button>
          </div>
        )}
      </div>

      <div>
        <h4 className="mb-1.5 text-[14.5px] font-bold text-[color:var(--color-ink)]">۲) یا وارد کردن دستی</h4>
        <p className="mb-3 text-[12.5px] leading-relaxed text-[color:var(--color-ink-dim)]">اگه از قبل آدرس HLS پخش زنده رو داری، مستقیم وارد کن.</p>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            if (!manualUrl.trim()) return;
            await api.addLive(manualUrl.trim(), manualTitle || "پخش زنده", myName);
            setManualTitle("");
            setManualUrl("");
          }}
          className="flex flex-col gap-2.5"
        >
          <label className="text-xs text-[color:var(--color-ink-muted)]">عنوان</label>
          <input className={inputClass} value={manualTitle} onChange={(e) => setManualTitle(e.target.value)} placeholder="مثلا: پخش زنده دوربین" />
          <label className="text-xs text-[color:var(--color-ink-muted)]">آدرس پخش HLS (m3u8)</label>
          <input className={inputClass} dir="ltr" value={manualUrl} onChange={(e) => setManualUrl(e.target.value)} placeholder="https://vergoboy.ir/hls/xxxx/index.m3u8" />
          <button
            type="submit"
            className="mt-1 rounded-xl px-5 py-2.5 text-[14px] font-bold text-white"
            style={{ background: "linear-gradient(135deg, var(--color-amber), var(--color-plum))" }}
          >
            افزودن به پلی‌لیست
          </button>
        </form>
      </div>
    </div>
  );
}