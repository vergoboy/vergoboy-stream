"use client";

import { useEffect, useRef } from "react";


import { Upload, Video, Radio, Plus, CheckCircle, AlertCircle, Loader2, UploadCloud, FileUp, Link2, CirclePlay, Search } from "lucide-react";


import { useState } from "react";
import { api, uploadVideo } from "@/lib/api";
import type { YoutubeScanVideo, YoutubeScanPlaylist } from "@/lib/api";

type Mode = "link" | "file";

const inputClass =
  "w-full rounded-xl border border-[color:var(--color-border)] bg-white/5 px-3.5 py-2.5 text-[13.5px] text-[color:var(--color-ink)] outline-none focus:border-[color:var(--color-amber)]";
const labelClass = "mt-1 text-xs text-[color:var(--color-ink-muted)]";
const primaryBtn =
  "mt-1.5 rounded-xl px-5 py-2.5 text-[14px] font-bold text-white transition-transform active:scale-[0.98] disabled:opacity-50";

export function AddVideoPanel({ myName, onArchiveLink }: { myName: string; onArchiveLink?: (url: string) => void }) {
  const [mode, setMode] = useState<Mode>("link");
  return (
    <div>
      <div className="mb-4 grid grid-cols-2 gap-1 rounded-2xl bg-white/6 p-1">
        {([
          ["link", <Link2 key="l" className="h-4 w-4" />, "لینک"],
          ["file", <FileUp key="f" className="h-4 w-4" />, "فایل از گوشی/کامپیوتر"],
        ] as [Mode, React.ReactNode, string][]).map(([m, icon, label]) => (
          <button
            key={m}
            onClick={() => setMode(m)}
            className={`flex items-center justify-center gap-1.5 rounded-xl py-2 text-[13px] font-semibold transition-colors ${
              mode === m ? "bg-white/15 text-white" : "text-white/55 hover:text-white"
            }`}
          >
            {icon}
            {label}
          </button>
        ))}
      </div>
      {mode === "link" && <LinkForm myName={myName} onArchiveLink={onArchiveLink} />}
      {mode === "file" && <FileForm myName={myName} />}
    </div>
  );
}

/** One box for every kind of link — we work out what it is so you don't have to. */
function LinkForm({ myName, onArchiveLink }: { myName: string; onArchiveLink?: (url: string) => void }) {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [scanUrl, setScanUrl] = useState<string | null>(null);
  const [note, setNote] = useState<{ msg: string; error?: boolean } | null>(null);

  async function go(e: React.FormEvent) {
    e.preventDefault();
    const u = url.trim();
    if (!u) return;
    setNote(null);
    if (!/^https?:\/\//i.test(u)) {
      setNote({ msg: "لینک باید با http یا https شروع بشه", error: true });
      return;
    }
    if (/(^|\.)digimoviez\.com\//i.test(new URL(u).hostname + "/") && onArchiveLink) {
      onArchiveLink(u);
      return;
    }
    if (/\.(mp4|webm|ogv|mov|m4v|mkv|m3u8)(\?|#|$)/i.test(u)) {
      setBusy(true);
      try {
        await api.addUrl(u, "", myName);
        setUrl("");
        setNote({ msg: "اضافه شد به صف ✓" });
      } catch (err) {
        setNote({ msg: err instanceof Error ? err.message : "نشد", error: true });
      }
      setBusy(false);
      return;
    }
    // YouTube and anything else yt-dlp understands
    setScanUrl(u);
  }

  if (scanUrl) {
    return (
      <div>
        <button type="button" onClick={() => { setScanUrl(null); setUrl(""); }} className="mb-3 text-[12.5px] text-white/60 hover:text-white">
          ← لینک دیگه
        </button>
        <VideoForm myName={myName} initialUrl={scanUrl} autoScan />
      </div>
    );
  }

  return (
    <form onSubmit={go} className="flex flex-col gap-2.5">
      <label className={labelClass}>لینک یوتیوب، ویدیو یا m3u8 مستقیم، یا صفحهٔ دیجی‌موویز</label>
      <input className={inputClass} dir="ltr" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://" inputMode="url" />
      <button type="submit" disabled={busy || !url.trim()} className={primaryBtn} style={{ background: "linear-gradient(135deg, var(--color-amber-soft), var(--color-amber))", boxShadow: "var(--shadow-lamp)" }}>
        {busy ? <Loader2 className="mx-auto h-4 w-4 animate-spin" /> : "بریم جلو"}
      </button>
      {note && <p className={`text-[12.5px] ${note.error ? "text-[color:var(--color-coral)]" : "text-[color:var(--color-teal)]"}`}>{note.msg}</p>}
    </form>
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
        style={{ background: "linear-gradient(135deg, var(--color-amber-soft), var(--color-amber))", boxShadow: "var(--shadow-lamp)" }}
      >
        آپلود و افزودن به پلی‌لیست
      </button>
      <p className="text-[12.5px] leading-relaxed text-[color:var(--color-ink-dim)]">
        فرمت‌های پشتیبانی‌شده: mp4، webm، ogg، mov، mkv — به‌صورت خودکار با کیفیت پیش‌فرض آماده پخش می‌شه؛ کیفیت‌های بیشتر on-demandان.
      </p>
    </form>
  );
}

function VideoForm({ myName, initialUrl = "", autoScan = false }: { myName: string; initialUrl?: string; autoScan?: boolean }) {
  const [url, setUrl] = useState(initialUrl);
  const [loading, setLoading] = useState(false);
  const [addingAll, setAddingAll] = useState(false);
  const [preview, setPreview] = useState<YoutubeScanVideo | null>(null);
  const [playlist, setPlaylist] = useState<YoutubeScanPlaylist | null>(null);
  const [formatId, setFormatId] = useState("__best__");
  const [customTitle, setCustomTitle] = useState("");
  const [subLang, setSubLang] = useState("");
  const [status, setStatus] = useState<{ msg: string; error?: boolean } | null>(null);

  function subOptions(preview: YoutubeScanVideo) {
    const merged = new Map<string, { code: string; name: string }>();
    for (const s of [...preview.subtitles, ...preview.auto_captions]) {
      if (!merged.has(s.code)) merged.set(s.code, s);
    }
    const rank = (code: string) => (code === "fa" ? 0 : code === "en" ? 1 : 2);
    return [...merged.values()].sort((a, b) => rank(a.code) - rank(b.code) || a.name.localeCompare(b.name));
  }

  async function loadFormats() {
    if (!url.trim()) return;
    setLoading(true);
    setPreview(null);
    setPlaylist(null);
    setStatus({ msg: "در حال اسکن لینک… (۱۵–۶۰ ثانیه)" });
    try {
      const data = await api.youtubeFormats(url.trim());
      if (data.type === "playlist") {
        setPlaylist(data);
        setStatus(null);
      } else {
        setPreview(data);
        setFormatId(data.formats[0]?.format_id ?? "__best__");
        setSubLang(subOptions(data).find((s) => s.code === "fa")?.code ?? "");
        setStatus(null);
      }
    } catch (err) {
      setStatus({ msg: `${err instanceof Error ? err.message : "خطای نامشخص"}`, error: true });
    }
    setLoading(false);
  }

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- one-shot scan of the link we were handed
    if (autoScan && initialUrl) void loadFormats();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!url.trim()) return;
    setStatus({ msg: "در حال دریافت لینک مستقیم با کیفیت انتخابی… (۱۵–۳۰ ثانیه)" });
    try {
      const data = await api.addYoutube(url.trim(), formatId, customTitle || preview?.title || "", myName, subLang);
      setStatus({ msg: `«${data.title}» اضافه شد — دانلود و آماده‌سازی در پس‌زمینه ادامه داره` });
      setPreview(null);
      setUrl("");
      setCustomTitle("");
      setTimeout(() => setStatus(null), 4000);
    } catch (err) {
      setStatus({ msg: `${err instanceof Error ? err.message : "خطای نامشخص"}`, error: true });
    }
  }

  async function addAllPlaylist() {
    if (!playlist) return;
    setAddingAll(true);
    setStatus({ msg: `در حال افزودن ${playlist.entries.length} ویدیوی پلی‌لیست… (چند دقیقه صبر کن)` });
    try {
      const data = await api.addYoutubePlaylist(url.trim(), playlist.playlist_title, myName);
      setStatus({ msg: `${data.added} ویدیو از پلی‌لیست اضافه شد — دانلودها در پس‌زمینه ادامه داره` });
      setPlaylist(null);
      setUrl("");
      setTimeout(() => setStatus(null), 5000);
    } catch (err) {
      setStatus({ msg: `${err instanceof Error ? err.message : "خطای نامشخص"}`, error: true });
    }
    setAddingAll(false);
  }

  function fmtViews(n?: number): string {
    if (!n) return "";
    if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M بازدید`;
    if (n >= 1e3) return `${(n / 1e3).toFixed(0)}K بازدید`;
    return `${n} بازدید`;
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-2.5">
      <label className={labelClass}>لینک یوتیوب (یا هر سایت پشتیبانی‌شده توسط yt-dlp)</label>
      <div className="flex gap-2">
        <input className={`${inputClass} flex-1`} dir="ltr" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://youtu.be/... یا لینک پلی‌لیست" />
        <button
          type="button"
          onClick={loadFormats}
          disabled={loading}
          className="shrink-0 whitespace-nowrap rounded-xl border border-[color:var(--color-border)] bg-white/5 px-4 py-2.5 text-[13px] text-[color:var(--color-ink)] hover:border-[color:var(--color-amber)]/50 disabled:opacity-50"
        >
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
        </button>
      </div>

      {/* single video scan result */}
      {preview && (
        <div className="flex flex-wrap items-start gap-3.5 rounded-2xl border border-[color:var(--color-border)] bg-white/5 p-3">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          {preview.thumbnail && <img src={preview.thumbnail} alt="" className="h-17 w-30 shrink-0 rounded-lg bg-black object-cover" style={{ width: 120, height: 68 }} />}
          <div className="min-w-0 flex-1 flex flex-col gap-2">
            <div className="truncate text-[13px] font-semibold text-[color:var(--color-ink)]">{preview.title}</div>
            {(preview.duration_string || preview.channel || preview.view_count) && (
              <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11.5px] text-[color:var(--color-ink-muted)]" dir="ltr">
                {preview.duration_string && <span className="rounded-md bg-white/10 px-1.5 py-0.5">⏱ {preview.duration_string}</span>}
                {preview.channel && <span className="truncate">📺 {preview.channel}</span>}
                {fmtViews(preview.view_count) && <span>👁 {fmtViews(preview.view_count)}</span>}
              </div>
            )}
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
            {preview && subOptions(preview).length > 0 && (
              <select value={subLang} onChange={(e) => setSubLang(e.target.value)} className={inputClass}>
                <option value="">بدون زیرنویس</option>
                {subOptions(preview).map((s) => (
                  <option key={s.code} value={s.code}>
                    {s.name}
                  </option>
                ))}
              </select>
            )}
          </div>
        </div>
      )}

      {preview && !addingAll && (
        <button
          type="submit"
          className="mt-1.5 self-end rounded-xl px-4 py-2 text-[12.5px] font-bold text-white transition-transform active:scale-[0.98] disabled:opacity-50"
          style={{ background: "linear-gradient(135deg, var(--color-amber-soft), var(--color-amber))", boxShadow: "var(--shadow-lamp)" }}
        >
          <Plus className="h-4 w-4" /> افزودن
        </button>
      )}

      {/* playlist scan result */}
      {playlist && (
        <div className="flex flex-col gap-2 rounded-2xl border border-[color:var(--color-border)] bg-white/5 p-3">
          <div className="flex items-center justify-between gap-2">
            <div className="truncate text-[13px] font-semibold text-[color:var(--color-ink)]">
              📑 {playlist.playlist_title || "پلی‌لیست"}
            </div>
            <span className="shrink-0 rounded-md bg-white/10 px-1.5 py-0.5 text-[11px] text-[color:var(--color-ink-muted)]">
              {playlist.playlist_count} ویدیو
            </span>
          </div>
          <div className="max-h-44 overflow-y-auto flex flex-col gap-1 pr-1">
            {playlist.entries.slice(0, 30).map((e) => (
              <div key={e.id} className="flex items-center gap-2 rounded-lg bg-white/5 px-2 py-1.5">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                {e.thumbnail && <img src={e.thumbnail} alt="" className="h-8 w-12 shrink-0 rounded object-cover bg-black" />}
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[12px] text-[color:var(--color-ink)]" dir="auto">{e.title}</div>
                  {e.duration_string && <div className="text-[10.5px] text-[color:var(--color-ink-muted)]" dir="ltr">⏱ {e.duration_string}</div>}
                </div>
              </div>
            ))}
            {playlist.entries.length > 30 && (
              <div className="px-2 py-1 text-[11px] text-[color:var(--color-ink-muted)]">… و {playlist.entries.length - 30} ویدیوی دیگر</div>
            )}
          </div>
          <button
            type="button"
            onClick={addAllPlaylist}
            disabled={addingAll}
            className={primaryBtn}
            style={{ background: "linear-gradient(135deg, var(--color-amber-soft), var(--color-amber))", boxShadow: "var(--shadow-lamp)" }}
          >
            {addingAll ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="w-4 h-4" />} افزودن کل پلی‌لیست
          </button>
        </div>
      )}

      {status && (
        <div
          className={`rounded-xl border px-3.5 py-2.5 text-[13px] ${
            status.error ? "border-[color:var(--color-coral)]/40 bg-[color:var(--color-coral)]/10 text-[color:var(--color-coral)]" : "border-[color:var(--color-plum)]/40 bg-[color:var(--color-plum)]/10 text-[color:var(--color-ink)]"
          }`}
        >
          {status.error ? <AlertCircle className="ml-1.5 inline h-4 w-4" /> : <Loader2 className="ml-1.5 inline h-4 w-4 animate-spin" />}
          {status.msg}
        </div>
      )}
      <p className="text-[12.5px] leading-relaxed text-[color:var(--color-ink-dim)]">افزودن الان انجام می‌شه و بلافاصله جواب می‌گیری؛ دانلود و آماده‌سازی در پس‌زمینه ادامه پیدا می‌کنه. لینک پلی‌لیست هم پشتیبانی می‌شه.</p>
    </form>
  );
}
