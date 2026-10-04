"use client";

import { useRef, useState } from "react";
import { Captions, Headphones, FileUp, Link2, UploadCloud, CheckCircle, AlertCircle, Loader2, X } from "lucide-react";
import { api } from "@/lib/api";
import { prettyTitle } from "@/lib/format";
import type { PlaylistItem } from "@/lib/types";

const inputClass =
  "w-full rounded-xl border border-[color:var(--color-border)] bg-white/5 px-3.5 py-2.5 text-[13.5px] text-[color:var(--color-ink)] outline-none focus:border-[color:var(--color-amber)]";
const primaryBtn =
  "flex items-center justify-center gap-1.5 rounded-xl px-4 py-2.5 text-[13px] font-bold text-white transition-transform active:scale-[0.98] disabled:opacity-50";

function SubtitleDropZone({ file, onFile, disabled }: { file: File | null; onFile: (f: File | null) => void; disabled: boolean }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragActive, setDragActive] = useState(false);

  const handleDrag = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === "dragenter" || e.type === "dragover") setDragActive(true);
    else if (e.type === "dragleave") setDragActive(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    const f = e.dataTransfer.files?.[0];
    if (f) onFile(f);
  };

  return (
    <div
      onDragEnter={handleDrag}
      onDragOver={handleDrag}
      onDragLeave={handleDrag}
      onDrop={handleDrop}
      onClick={() => !disabled && inputRef.current?.click()}
      className={`relative flex cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed p-6 transition-all sm:p-8 ${
        dragActive
          ? "scale-[1.01] border-[color:var(--color-amber)] bg-[color:var(--color-amber)]/10"
          : file
          ? "border-emerald-500/50 bg-emerald-500/5"
          : "border-[color:var(--color-border)] bg-white/5 hover:border-[color:var(--color-amber)]/60 hover:bg-white/10"
      } ${disabled ? "pointer-events-none opacity-60" : ""}`}
    >
      <input
        ref={inputRef}
        type="file"
        accept=".vtt,.srt"
        onChange={(e) => onFile(e.target.files?.[0] ?? null)}
        hidden
      />
      {file ? (
        <div className="flex w-full items-center gap-3 text-emerald-400">
          <CheckCircle className="h-8 w-8 shrink-0" />
          <div className="min-w-0 flex-1 text-right">
            <p className="truncate text-sm font-bold text-white">{file.name}</p>
            <p className="text-xs text-[color:var(--color-ink-muted)]">
              {(file.size / 1024).toFixed(1)} KB — برای تغییر کلیک کن
            </p>
          </div>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onFile(null);
            }}
            className="rounded-lg p-1.5 text-[color:var(--color-ink-dim)] hover:bg-white/10 hover:text-white"
            aria-label="حذف فایل"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      ) : (
        <>
          <UploadCloud className="mb-2 h-9 w-9 animate-bounce text-[color:var(--color-amber)] sm:h-10 sm:w-10" />
          <p className="mb-1 text-[13px] font-semibold text-[color:var(--color-ink)] sm:text-sm">
            فایل زیرنویس را بکشید و اینجا رها کنید
          </p>
          <p className="text-center text-xs text-[color:var(--color-ink-muted)]">
            یا برای انتخاب فایل کلیک کنید (SRT یا VTT)
          </p>
        </>
      )}
    </div>
  );
}

export function SubAudioPanel({ playlist, myName, canUpload = true }: { playlist: PlaylistItem[]; myName: string; canUpload?: boolean }) {
  const [targetId, setTargetId] = useState(playlist[0]?.id ?? "");
  const [subMode, setSubMode] = useState<"file" | "url">("file");

  const [subLabel, setSubLabel] = useState("");
  const [subFile, setSubFile] = useState<File | null>(null);
  const [subUrl, setSubUrl] = useState("");
  const [subBusy, setSubBusy] = useState(false);
  const [subStatus, setSubStatus] = useState<{ text: string; error?: boolean } | null>(null);

  const [audioLabel, setAudioLabel] = useState("");
  const [audioUrl, setAudioUrl] = useState("");
  const [audioBusy, setAudioBusy] = useState(false);
  const [audioStatus, setAudioStatus] = useState<{ text: string; error?: boolean } | null>(null);

  const effectiveTarget = playlist.some((p) => p.id === targetId) ? targetId : playlist[0]?.id ?? "";

  if (!playlist.length) {
    return <p className="text-[13.5px] text-[color:var(--color-ink-dim)]">ابتدا یک ویدیو به پلی‌لیست اضافه کن</p>;
  }

  if (!canUpload) {
    return <p className="text-[13.5px] text-[color:var(--color-ink-dim)]">فقط مدیریت اتاق می‌تونه زیرنویس و صدا اضافه کنه</p>;
  }

  async function uploadSub(e: React.FormEvent) {
    e.preventDefault();
    if (!subFile || !effectiveTarget || subBusy) return;
    setSubBusy(true);
    setSubStatus(null);
    try {
      await api.subtitleUpload(subFile, effectiveTarget, subLabel || "زیرنویس", myName);
      setSubLabel("");
      setSubFile(null);
      setSubStatus({ text: "زیرنویس با موفقیت اضافه شد" });
      setTimeout(() => setSubStatus(null), 4000);
    } catch (err) {
      setSubStatus({ text: err instanceof Error ? err.message : "خطا در آپلود زیرنویس", error: true });
    }
    setSubBusy(false);
  }

  async function addSubUrl(e: React.FormEvent) {
    e.preventDefault();
    if (!subUrl.trim() || !effectiveTarget || subBusy) return;
    setSubBusy(true);
    setSubStatus(null);
    try {
      await api.subtitleUrl(effectiveTarget, subUrl.trim(), subLabel || "زیرنویس", myName);
      setSubLabel("");
      setSubUrl("");
      setSubStatus({ text: "زیرنویس با موفقیت اضافه شد" });
      setTimeout(() => setSubStatus(null), 4000);
    } catch (err) {
      setSubStatus({ text: err instanceof Error ? err.message : "خطا در افزودن زیرنویس", error: true });
    }
    setSubBusy(false);
  }

  async function addAudio(e: React.FormEvent) {
    e.preventDefault();
    if (!audioUrl.trim() || !effectiveTarget || audioBusy) return;
    setAudioBusy(true);
    setAudioStatus(null);
    try {
      await api.addAudioTrack(effectiveTarget, audioUrl.trim(), audioLabel || "دوبله", myName);
      setAudioLabel("");
      setAudioUrl("");
      setAudioStatus({ text: "کانال صدا اضافه شد" });
      setTimeout(() => setAudioStatus(null), 4000);
    } catch (err) {
      setAudioStatus({ text: err instanceof Error ? err.message : "خطا در افزودن کانال صدا", error: true });
    }
    setAudioBusy(false);
  }

  return (
    <div>
      <label className="mb-2 block text-xs text-[color:var(--color-ink-muted)]">برای کدام ویدیوی پلی‌لیست اضافه شود؟</label>
      <select value={effectiveTarget} onChange={(e) => setTargetId(e.target.value)} className={`${inputClass} mb-5`}>
        {playlist.map((p) => (
          <option key={p.id} value={p.id}>
            {prettyTitle(p.title)}
          </option>
        ))}
      </select>

      <div className="grid gap-6 md:grid-cols-2">
        <div>
          <h4 className="mb-1.5 flex items-center gap-1.5 text-[14.5px] font-bold text-[color:var(--color-ink)]"><Captions className="h-4 w-4 text-[color:var(--color-teal)]" /> افزودن زیرنویس</h4>
          <p className="mb-3 text-[12.5px] leading-relaxed text-[color:var(--color-ink-dim)]">
            برای فایل‌های MKV با زیرنویس داخلی، مرورگر نمی‌تواند زیرنویس داخل فایل را بخواند — فایل جدا (vtt/srt) آپلود یا لینکش را وارد کن.
          </p>
          <div className="mb-3 flex gap-1.5">
            {(["file", "url"] as const).map((m) => (
              <button
                key={m}
                onClick={() => setSubMode(m)}
                className={`flex items-center gap-1 rounded-xl px-3.5 py-1.5 text-xs font-semibold ${subMode === m ? "bg-[color:var(--color-plum)]/25 text-white" : "bg-white/5 text-[color:var(--color-ink-muted)]"}`}
              >
                {m === "file" ? <FileUp className="h-3.5 w-3.5" /> : <Link2 className="h-3.5 w-3.5" />}
                {m === "file" ? "آپلود فایل" : "لینک خارجی"}
              </button>
            ))}
          </div>

          {subMode === "file" ? (
            <form onSubmit={uploadSub} className="flex flex-col gap-2.5">
              <input className={inputClass} value={subLabel} onChange={(e) => setSubLabel(e.target.value)} placeholder="برچسب (مثلا: فارسی)" />
              <SubtitleDropZone file={subFile} onFile={setSubFile} disabled={subBusy} />
              <button type="submit" disabled={!subFile || subBusy} className={primaryBtn} style={{ background: "linear-gradient(135deg, var(--color-amber-soft), var(--color-amber))", boxShadow: "var(--shadow-lamp)" }}>
                {subBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <UploadCloud className="h-4 w-4" />}
                {subBusy ? "در حال آپلود…" : "آپلود زیرنویس"}
              </button>
            </form>
          ) : (
            <form onSubmit={addSubUrl} className="flex flex-col gap-2.5">
              <input className={inputClass} value={subLabel} onChange={(e) => setSubLabel(e.target.value)} placeholder="برچسب (مثلا: فارسی)" />
              <input className={inputClass} dir="ltr" value={subUrl} onChange={(e) => setSubUrl(e.target.value)} placeholder="https://example.com/sub.srt" />
              <button type="submit" disabled={!subUrl.trim() || subBusy} className={primaryBtn} style={{ background: "linear-gradient(135deg, var(--color-amber-soft), var(--color-amber))", boxShadow: "var(--shadow-lamp)" }}>
                {subBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Link2 className="h-4 w-4" />}
                افزودن زیرنویس
              </button>
            </form>
          )}

          {subStatus && (
            <div className={`mt-2.5 flex items-center gap-2 rounded-xl border px-3.5 py-2.5 text-[13px] ${subStatus.error ? "border-[color:var(--color-coral)]/40 bg-[color:var(--color-coral)]/10 text-[color:var(--color-coral)]" : "border-emerald-500/40 bg-emerald-500/10 text-emerald-400"}`}>
              {subStatus.error ? <AlertCircle className="h-4 w-4 shrink-0" /> : <CheckCircle className="h-4 w-4 shrink-0" />}
              {subStatus.text}
            </div>
          )}
        </div>

        <div>
          <h4 className="mb-1.5 flex items-center gap-1.5 text-[14.5px] font-bold text-[color:var(--color-ink)]"><Headphones className="h-4 w-4 text-[color:var(--color-teal)]" /> افزودن کانال صدا (دوبله)</h4>
          <p className="mb-3 text-[12.5px] leading-relaxed text-[color:var(--color-ink-dim)]">یک فایل صوتی جدا که به‌صورت خودکار با تصویر سینک می‌شود.</p>
          <form onSubmit={addAudio} className="flex flex-col gap-2.5">
            <input className={inputClass} value={audioLabel} onChange={(e) => setAudioLabel(e.target.value)} placeholder="برچسب (مثلا: دوبله فارسی)" />
            <input className={inputClass} dir="ltr" value={audioUrl} onChange={(e) => setAudioUrl(e.target.value)} placeholder="https://example.com/dub-fa.mp3" />
            <button type="submit" disabled={!audioUrl.trim() || audioBusy} className={primaryBtn} style={{ background: "linear-gradient(135deg, var(--color-amber-soft), var(--color-amber))", boxShadow: "var(--shadow-lamp)" }}>
              {audioBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Headphones className="h-4 w-4" />}
              افزودن کانال صدا
            </button>
          </form>
          {audioStatus && (
            <div className={`mt-2.5 flex items-center gap-2 rounded-xl border px-3.5 py-2.5 text-[13px] ${audioStatus.error ? "border-[color:var(--color-coral)]/40 bg-[color:var(--color-coral)]/10 text-[color:var(--color-coral)]" : "border-emerald-500/40 bg-emerald-500/10 text-emerald-400"}`}>
              {audioStatus.error ? <AlertCircle className="h-4 w-4 shrink-0" /> : <CheckCircle className="h-4 w-4 shrink-0" />}
              {audioStatus.text}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
