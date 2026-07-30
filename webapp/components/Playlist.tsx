"use client";

import { Play, Trash2, Clock, CheckCircle2, AlertTriangle, Loader2, Radio, Video, Link, FileVideo } from "lucide-react";


import { AnimatePresence, motion } from "framer-motion";
import { api } from "@/lib/api";
import type { PlaylistItem, TranscodeProgress } from "@/lib/types";

const ICONS: Record<string, React.ReactNode> = { live: <Radio className="w-3.5 h-3.5 text-rose-500 animate-pulse inline" />, youtube: <Video className="w-3.5 h-3.5 text-red-500 inline" />, url: <Link className="w-3.5 h-3.5 text-blue-400 inline" />, file: <FileVideo className="w-3.5 h-3.5 text-amber-400 inline" /> };

function defaultRendition(item: PlaylistItem) {
  return item.renditions?.find((r) => r.is_default) ?? item.renditions?.[0];
}

export function Playlist({
  playlist,
  currentIndex,
  transcodeProgress,
  myName,
  onSelect,
}: {
  playlist: PlaylistItem[];
  currentIndex: number | null;
  transcodeProgress: Record<string, TranscodeProgress>;
  myName: string;
  onSelect: (index: number) => void;
}) {
  if (!playlist.length) {
    return <p className="py-5 text-center text-[13.5px] text-[color:var(--color-ink-dim)]">پلی‌لیست خالی‌ست. از تب «افزودن ویدیو» یکی اضافه کن.</p>;
  }

  return (
    <>
      <ul className="flex max-h-[460px] flex-col gap-2 overflow-y-auto">
        <AnimatePresence initial={false}>
          {playlist.map((item, idx) => {
            const def = defaultRendition(item);
            const prog = def ? transcodeProgress[`${item.id}:${def.label}`] : undefined;
            const pct = prog?.pct ?? 0;
            const processing = item.status === "queued" || item.status === "encoding";
            const icon = processing ? <Loader2 className="w-4 h-4 animate-spin text-amber-400" /> : item.status === "error" ? <AlertTriangle className="w-4 h-4 text-rose-400" /> : item.status === "ready" ? <Play className="w-3.5 h-3.5 fill-current text-emerald-400" /> : (ICONS[item.type] ?? <FileVideo className="w-3.5 h-3.5 text-amber-400" />);

            return (
              <motion.li
                layout
                key={item.id}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, height: 0 }}
                onClick={() => onSelect(idx)}
                className={`flex cursor-pointer items-center gap-2.5 rounded-2xl border p-2.5 transition-colors ${
                  idx === currentIndex
                    ? "border-[color:var(--color-plum)] bg-[color:var(--color-plum)]/15"
                    : "border-[color:var(--color-border)] bg-white/[0.03] hover:border-[color:var(--color-plum-soft)]/50"
                }`}
              >
                <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-white/5 text-[15px]">{icon}</div>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[13.5px] text-[color:var(--color-ink)]">{item.title}</div>
                  <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11.5px] text-[color:var(--color-ink-dim)]">
                    افزوده‌شده توسط {item.added_by || "ناشناس"}
                    {processing && (
                      <span className="rounded-full bg-[color:var(--color-amber)]/15 px-2 py-0.5 text-[10.5px] text-[color:var(--color-amber)]">
                        {pct}% {item.status === "queued" ? "در صف انکد" : "در حال آماده‌سازی"}
                      </span>
                    )}
                    {item.status === "ready" && (
                      <span className="rounded-full bg-[color:var(--color-teal)]/15 px-2 py-0.5 text-[10.5px] text-[color:var(--color-teal)]">پخش‌پذیر</span>
                    )}
                    {item.status === "error" && (
                      <span title={item.error} className="cursor-help rounded-full bg-[color:var(--color-coral)]/15 px-2 py-0.5 text-[10.5px] text-[color:var(--color-coral)]">
                        خطا در تبدیل
                      </span>
                    )}
                  </div>
                  {processing && (
                    <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-white/10">
                      <motion.div
                        className="h-full rounded-full"
                        style={{ background: "linear-gradient(90deg, var(--color-amber), var(--color-plum-soft))" }}
                        animate={{ width: `${pct}%` }}
                        transition={{ duration: 0.4 }}
                      />
                    </div>
                  )}
                </div>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    if (confirm("این آیتم از پلی‌لیست حذف شود؟")) api.removeItem(item.id, myName);
                  }}
                  className="shrink-0 rounded-lg px-1.5 py-1 text-[color:var(--color-ink-dim)] hover:bg-[color:var(--color-coral)]/10 hover:text-[color:var(--color-coral)]"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </motion.li>
            );
          })}
        </AnimatePresence>
      </ul>
      <div className="mt-3 flex items-center gap-2.5">
        <CleanupButton />
      </div>
    </>
  );
}

function CleanupButton() {
  return (
    <button
      onClick={async (e) => {
        const btn = e.currentTarget;
        const original = btn.textContent;
        btn.disabled = true;
        try {
          const data = await api.cleanup();
          btn.textContent = data.total > 0 ? `✓ ${data.total} فایل یتیم حذف شد` : "✓ هیچ فایل یتیمی پیدا نشد";
        } catch {
          btn.textContent = "⚠ خطا در پاکسازی";
        }
        setTimeout(() => {
          btn.textContent = original;
          btn.disabled = false;
        }, 3000);
      }}
      className="rounded-xl border border-[color:var(--color-border)] bg-white/5 px-3.5 py-2 text-xs text-[color:var(--color-ink)] hover:border-[color:var(--color-amber)]/50 disabled:opacity-60"
      title="حذف فایل‌هایی که در پلی‌لیست نیستند از سرور"
    >
      <Trash2 className="w-4 h-4" /> پاکسازی فایل‌های یتیم
    </button>
  );
}