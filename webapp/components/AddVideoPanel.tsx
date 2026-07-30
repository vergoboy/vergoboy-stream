import { useRef } from "react";
"use client";

import { Upload, Video, Radio, Plus, CheckCircle, AlertCircle, Loader2, UploadCloud } from "lucide-react";


import { useState } from "react";
import { api, uploadVideo } from "@/lib/api";

type Mode = "file" | "url" | "youtube";

const inputClass =
  "w-full rounded-xl border border-[color:var(--color-border)] bg-white/5 px-3.5 py-2.5 text-[13.5px] text-[color:var(--color-ink)] outline-none focus:border-[color:var(--color-amber)]";
const labelClass = "mt-1 text-xs text-[color:var(--color-ink-muted)]";
const primaryBtn =
  "mt-1.5 rounded-xl px-5 py-2.5 text-[14px] font-bold text-white transition-transform active:scale-[0.98] disabled:opacity-50";

export function AddVideoPanel({ myName }: { myName: string }) {
  const [mode, setMode] = useState<Mode>("file");

  return (
    <div>
      <div className="mb-4 flex gap-1.5">
        {([
          ["file", "📁 آپلود فایل"],
          ["url", "🔗 لینک مستقیم"],
          ["youtube", "▶️ یوتیوب"],
        ] as [Mode, string][]).map(([m, label]) => (
          <button
            key={m}
            onClick={() => setMode(m)}
            className={`rounded-xl px-4 py-2 text-[13px] font-semibold transition-colors ${
              mode === m ? "bg-[color:var(--color-plum)]/25 text-white" : "bg-white/5 text-[color:var(--color-ink-muted)] hover:text-white"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {mode === "file" && <FileForm myName={myName} />}
      {mode === "url" && <UrlForm myName={myName} />}
      {mode === "youtube" && <VideoForm myName={myName} />}
    </div>
  );
}

function FileForm({ myName }: { myName: string }) {
  const [title, setTitle] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleDrag = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === "dragenter" || e.type === "dragover") {
      setDragActive(true);
    } else if (e.type === "dragleave") {
      setDragActive(false);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      setFile(e.dataTransfer.files[0]);
    }
  };

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    setProgress(0);
    try {
      await uploadVideo(file, title, myName, setProgress);
      setTitle("");
      setFile(null);
    } catch (err) {
      alert(err instanceof Error ? err.message : "خطا در آپلود");
    }
    setProgress(null);
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-2.5">
      <label className={labelClass}>عنوان (اختیاری)</label>
      <input className={inputClass} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="مثلا: قسمت ۱ — فصل دوم" />
      <label className={labelClass}>فایل ویدیو</label>
      <div
        onDragEnter={handleDrag}
        onDragOver={handleDrag}
        onDragLeave={handleDrag}
        onDrop={handleDrop}
        onClick={() => fileInputRef.current?.click()}
        className={`relative flex flex-col items-center justify-center p-8 rounded-2xl border-2 border-dashed transition-all cursor-pointer ${
          dragActive
            ? "border-[color:var(--color-amber)] bg-[color:var(--color-amber)]/10 scale-[1.01]"
            : file
            ? "border-emerald-500/50 bg-emerald-500/5"
            : "border-[color:var(--color-border)] bg-white/5 hover:border-[color:var(--color-amber)]/60 hover:bg-white/10"
        }`}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept="video/mp4,video/webm,video/ogg,.mp4,.webm,.ogv,.mov,.m4v,.mkv"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          hidden
        />
        {file ? (
          <div className="flex items-center gap-3 text-emerald-400">
            <CheckCircle className="w-8 h-8 shrink-0" />
            <div className="text-right">
              <p className="font-bold text-sm text-white truncate max-w-[260px]">{file.name}</p>
              <p className="text-xs text-[color:var(--color-ink-muted)]">{(file.size / (1024 * 1024)).toFixed(1)} MB</p>
            </div>
          </div>
        ) : (
          <>
            <UploadCloud className="w-10 h-10 text-[color:var(--color-amber)] mb-2 animate-bounce" />
            <p className="font-semibold text-sm text-[color:var(--color-ink)] mb-1">
              فایل ویدیو را بکشید و اینجا رها کنید
            </p>
            <p className="text-xs text-[color:var(--color-ink-muted)]">
              یا برای انتخاب فایل کلیک کنید (MP4, WEBM, MKV, MOV)
            </p>
          </>
        )}
      </div>
      {progress !== null && (
        <div className="h-2 overflow-hidden rounded-full bg-white/10">
          <div className="h-full rounded-full transition-all" style={{ width: `${progress}%`, background: "linear-gradient(90deg, var(--color-amber), var(--color-plum-soft))" }} />
        </div>
      )}
      <button
        type="submit"
        disabled={!file || progress !== null}
        className={primaryBtn}
        style={{ background: "linear-gradient(135deg, var(--color-amber), var(--color-plum))", boxShadow: "var(--shadow-lamp)" }}
      >
        آپلود و افزودن به پلی‌لیست
      </button>
      <p className="text-[12.5px] leading-relaxed text-[color:var(--color-ink-dim)]">
        فرمت‌های پشتیبانی‌شده: mp4، webm، ogg، mov، mkv — به‌صورت خودکار با کیفیت پیش‌فرض آماده پخش می‌شه؛ کیفیت‌های بیشتر on-demandان.
      </p>
    </form>
  );
}

function UrlForm({ myName }: { myName: string }) {
  const [title, setTitle] = useState("");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!url.trim()) return;
    setBusy(true);
    try {
      await api.addUrl(url.trim(), title, myName);
      setTitle("");
      setUrl("");
    } catch (err) {
      alert(err instanceof Error ? err.message : "خطا");
    }
    setBusy(false);
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-2.5">
      <label className={labelClass}>عنوان (اختیاری)</label>
      <input className={inputClass} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="مثلا: تریلر فیلم" />
      <label className={labelClass}>لینک مستقیم ویدیو یا HLS (m3u8)</label>
      <input className={inputClass} dir="ltr" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://example.com/movie.mp4" />
      <button
        type="submit"
        disabled={busy}
        className={primaryBtn}
        style={{ background: "linear-gradient(135deg, var(--color-amber), var(--color-plum))", boxShadow: "var(--shadow-lamp)" }}
      >
        افزودن به پلی‌لیست
      </button>
      <p className="text-[12.5px] leading-relaxed text-[color:var(--color-ink-dim)]">پخش از همین ابتدا ممکنه شروع بشه در حالی که بقیه‌ی فیلم هنوز در حال آماده‌سازیه.</p>
    </form>
  );
}

function VideoForm({ myName }: { myName: string }) {
  const [url, setUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [preview, setPreview] = useState<{ title: string; thumbnail: string; formats: { format_id: string; label: string }[] } | null>(null);
  const [formatId, setFormatId] = useState("__best__");
  const [customTitle, setCustomTitle] = useState("");
  const [status, setStatus] = useState<{ msg: string; error?: boolean } | null>(null);

  async function loadFormats() {
    if (!url.trim()) return;
    setLoading(true);
    setPreview(null);
    setStatus({ msg: "⏳ در حال دریافت اطلاعات ویدیو… (۱۵–۴۵ ثانیه)" });
    try {
      const data = await api.youtubeFormats(url.trim());
      setPreview(data);
      setFormatId(data.formats[0]?.format_id ?? "__best__");
      setStatus(null);
    } catch (err) {
      setStatus({ msg: `<AlertCircle className="w-4 h-4 text-rose-400" /> ${err instanceof Error ? err.message : "خطای نامشخص"}`, error: true });
    }
    setLoading(false);
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!url.trim()) return;
    setStatus({ msg: "⏳ در حال دریافت لینک مستقیم با کیفیت انتخابی… (۱۵–۳۰ ثانیه)" });
    try {
      const data = await api.addYoutube(url.trim(), formatId, customTitle || preview?.title || "", myName);
      setStatus({ msg: `<CheckCircle className="w-4 h-4 text-emerald-400" /> «${data.title}» اضافه شد — دانلود و آماده‌سازی در پس‌زمینه ادامه داره` });
      setPreview(null);
      setUrl("");
      setCustomTitle("");
      setTimeout(() => setStatus(null), 4000);
    } catch (err) {
      setStatus({ msg: `<AlertCircle className="w-4 h-4 text-rose-400" /> ${err instanceof Error ? err.message : "خطای نامشخص"}`, error: true });
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-2.5">
      <label className={labelClass}>لینک یوتیوب (یا هر سایت پشتیبانی‌شده توسط yt-dlp)</label>
      <div className="flex gap-2">
        <input className={`${inputClass} flex-1`} dir="ltr" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://youtu.be/..." />
        <button
          type="button"
          onClick={loadFormats}
          disabled={loading}
          className="shrink-0 whitespace-nowrap rounded-xl border border-[color:var(--color-border)] bg-white/5 px-4 py-2.5 text-[13px] text-[color:var(--color-ink)] hover:border-[color:var(--color-amber)]/50 disabled:opacity-50"
        >
          🔍 بارگذاری
        </button>
      </div>

      {preview && (
        <div className="flex flex-wrap items-start gap-3.5 rounded-2xl border border-[color:var(--color-border)] bg-white/5 p-3">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          {preview.thumbnail && <img src={preview.thumbnail} alt="" className="h-17 w-30 shrink-0 rounded-lg bg-black object-cover" style={{ width: 120, height: 68 }} />}
          <div className="min-w-0 flex-1 flex flex-col gap-2">
            <div className="truncate text-[13px] font-semibold text-[color:var(--color-ink)]">{preview.title}</div>
            <input
              className={inputClass}
              value={customTitle}
              onChange={(e) => setCustomTitle(e.target.value)}
              placeholder="عنوان سفارشی (اختیاری)"
            />
            <select value={formatId} onChange={(e) => setFormatId(e.target.value)} className={inputClass}>
              {preview.formats.map((f) => (
                <option key={f.format_id} value={f.format_id}>
                  {f.label}
                </option>
              ))}
            </select>
          </div>
        </div>
      )}

      {preview && (
        <button
          type="submit"
          className={primaryBtn}
          style={{ background: "linear-gradient(135deg, var(--color-amber), var(--color-plum))", boxShadow: "var(--shadow-lamp)" }}
        >
          <Plus className="w-4 h-4" /> افزودن به پلی‌لیست
        </button>
      )}

      {status && (
        <div
          className={`rounded-xl border px-3.5 py-2.5 text-[13px] ${
            status.error ? "border-[color:var(--color-coral)]/40 bg-[color:var(--color-coral)]/10 text-[color:var(--color-coral)]" : "border-[color:var(--color-plum)]/40 bg-[color:var(--color-plum)]/10 text-[color:var(--color-ink)]"
          }`}
        >
          {status.msg}
        </div>
      )}
      <p className="text-[12.5px] leading-relaxed text-[color:var(--color-ink-dim)]">افزودن الان انجام می‌شه و بلافاصله جواب می‌گیری؛ دانلود و آماده‌سازی در پس‌زمینه ادامه پیدا می‌کنه.</p>
    </form>
  );
}