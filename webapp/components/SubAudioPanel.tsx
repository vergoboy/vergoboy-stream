"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { PlaylistItem } from "@/lib/types";

const inputClass =
  "w-full rounded-xl border border-[color:var(--color-border)] bg-white/5 px-3.5 py-2.5 text-[13.5px] text-[color:var(--color-ink)] outline-none focus:border-[color:var(--color-amber)]";

export function SubAudioPanel({ playlist, myName }: { playlist: PlaylistItem[]; myName: string }) {
  const [targetId, setTargetId] = useState(playlist[0]?.id ?? "");
  const [subMode, setSubMode] = useState<"file" | "url">("file");

  const [subLabel, setSubLabel] = useState("");
  const [subFile, setSubFile] = useState<File | null>(null);
  const [subUrl, setSubUrl] = useState("");

  const [audioLabel, setAudioLabel] = useState("");
  const [audioUrl, setAudioUrl] = useState("");

  const effectiveTarget = playlist.some((p) => p.id === targetId) ? targetId : playlist[0]?.id ?? "";

  if (!playlist.length) {
    return <p className="text-[13.5px] text-[color:var(--color-ink-dim)]">ابتدا یک ویدیو به پلی‌لیست اضافه کن</p>;
  }

  return (
    <div>
      <label className="mb-2 block text-xs text-[color:var(--color-ink-muted)]">برای کدام ویدیوی پلی‌لیست اضافه شود؟</label>
      <select value={effectiveTarget} onChange={(e) => setTargetId(e.target.value)} className={`${inputClass} mb-5`}>
        {playlist.map((p) => (
          <option key={p.id} value={p.id}>
            {p.title}
          </option>
        ))}
      </select>

      <div className="grid gap-6 md:grid-cols-2">
        <div>
          <h4 className="mb-1.5 text-[14.5px] font-bold text-[color:var(--color-ink)]">💬 افزودن زیرنویس</h4>
          <p className="mb-3 text-[12.5px] leading-relaxed text-[color:var(--color-ink-dim)]">
            برای فایل‌های MKV با زیرنویس داخلی، مرورگر نمی‌تواند زیرنویس داخل فایل را بخواند — فایل جدا (vtt/srt) آپلود یا لینکش را وارد کن.
          </p>
          <div className="mb-3 flex gap-1.5">
            {(["file", "url"] as const).map((m) => (
              <button
                key={m}
                onClick={() => setSubMode(m)}
                className={`rounded-xl px-3.5 py-1.5 text-xs font-semibold ${subMode === m ? "bg-[color:var(--color-plum)]/25 text-white" : "bg-white/5 text-[color:var(--color-ink-muted)]"}`}
              >
                {m === "file" ? "📁 آپلود فایل" : "🔗 لینک خارجی"}
              </button>
            ))}
          </div>

          {subMode === "file" ? (
            <form
              onSubmit={async (e) => {
                e.preventDefault();
                if (!subFile || !effectiveTarget) return;
                try {
                  await api.subtitleUpload(subFile, effectiveTarget, subLabel || "زیرنویس", myName);
                  setSubLabel("");
                  setSubFile(null);
                } catch (err) {
                  alert(err instanceof Error ? err.message : "خطا در آپلود زیرنویس");
                }
              }}
              className="flex flex-col gap-2.5"
            >
              <input className={inputClass} value={subLabel} onChange={(e) => setSubLabel(e.target.value)} placeholder="برچسب (مثلا: فارسی)" />
              <input type="file" accept=".vtt,.srt" onChange={(e) => setSubFile(e.target.files?.[0] ?? null)} className="text-xs text-[color:var(--color-ink-muted)]" />
              <button type="submit" className="self-start rounded-xl px-4 py-2 text-[13px] font-bold text-white" style={{ background: "linear-gradient(135deg, var(--color-amber), var(--color-plum))" }}>
                افزودن زیرنویس
              </button>
            </form>
          ) : (
            <form
              onSubmit={async (e) => {
                e.preventDefault();
                if (!subUrl.trim() || !effectiveTarget) return;
                try {
                  await api.subtitleUrl(effectiveTarget, subUrl.trim(), subLabel || "زیرنویس", myName);
                  setSubLabel("");
                  setSubUrl("");
                } catch (err) {
                  alert(err instanceof Error ? err.message : "خطا در افزودن زیرنویس");
                }
              }}
              className="flex flex-col gap-2.5"
            >
              <input className={inputClass} value={subLabel} onChange={(e) => setSubLabel(e.target.value)} placeholder="برچسب (مثلا: فارسی)" />
              <input className={inputClass} dir="ltr" value={subUrl} onChange={(e) => setSubUrl(e.target.value)} placeholder="https://example.com/sub.srt" />
              <button type="submit" className="self-start rounded-xl px-4 py-2 text-[13px] font-bold text-white" style={{ background: "linear-gradient(135deg, var(--color-amber), var(--color-plum))" }}>
                افزودن زیرنویس
              </button>
            </form>
          )}
        </div>

        <div>
          <h4 className="mb-1.5 text-[14.5px] font-bold text-[color:var(--color-ink)]">🎧 افزودن کانال صدا (دوبله)</h4>
          <p className="mb-3 text-[12.5px] leading-relaxed text-[color:var(--color-ink-dim)]">یک فایل صوتی جدا که به‌صورت خودکار با تصویر سینک می‌شود.</p>
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              if (!audioUrl.trim() || !effectiveTarget) return;
              await api.addAudioTrack(effectiveTarget, audioUrl.trim(), audioLabel || "دوبله", myName);
              setAudioLabel("");
              setAudioUrl("");
            }}
            className="flex flex-col gap-2.5"
          >
            <input className={inputClass} value={audioLabel} onChange={(e) => setAudioLabel(e.target.value)} placeholder="برچسب (مثلا: دوبله فارسی)" />
            <input className={inputClass} dir="ltr" value={audioUrl} onChange={(e) => setAudioUrl(e.target.value)} placeholder="https://example.com/dub-fa.mp3" />
            <button type="submit" className="self-start rounded-xl px-4 py-2 text-[13px] font-bold text-white" style={{ background: "linear-gradient(135deg, var(--color-amber), var(--color-plum))" }}>
              افزودن کانال صدا
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
