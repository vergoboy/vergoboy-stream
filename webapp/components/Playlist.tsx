"use client";

import { useState } from "react";
import { Play, Trash2, Clock, CheckCircle2, AlertTriangle, Loader2, Radio, Video, Link, FileVideo } from "lucide-react";


import { AnimatePresence, motion } from "./anim";
import { api } from "@/lib/api";
import { prettyTitle } from "@/lib/format";
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
  const [confirmId, setConfirmId] = useState<string | null>(null);
  if (!playlist.length) {
    return (
      <div className="py-10 text-center">
        <div className="mb-2 text-[44px]" aria-hidden>🍿</div>
        <p className="display text-[22px] text-white">صف خالیه</p>
        <p className="mt-1 text-[13px] text-white/55">از «افزودن» یه فیلم بیار.</p>
      </div>
    );
  }

  return (
    <>
      <ul className="flex flex-col gap-2">
        <AnimatePresence initial={false}>
          {playlist.map((item, idx) => {
            const def = defaultRendition(item);
            const prog = def ? transcodeProgress[`${item.id}:${def.label}`] : undefined;
            const pct = prog?.pct ?? 0;
            const processing = item.status === "queued" || item.status === "encoding";
            const isNow = idx === currentIndex;
            const icon = isNow ? <span className="flex h-4 items-end gap-[2px]"><i className="eq-bar" /><i className="eq-bar" /><i className="eq-bar" /></span> : processing ? <Loader2 className="w-4 h-4 animate-spin text-amber-400" /> : item.status === "error" ? <AlertTriangle className="w-4 h-4 text-rose-400" /> : item.status === "ready" ? <Play className="w-3.5 h-3.5 fill-current text-emerald-400" /> : (ICONS[item.type] ?? <FileVideo className="w-3.5 h-3.5 text-amber-400" />);

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
                  <div className="truncate text-[13px] text-[color:var(--color-ink)] sm:text-[13.5px]" title={item.title}>{prettyTitle(item.title)}</div>
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
                    if (confirmId === item.id) {
                      void api.removeItem(item.id, myName);
                      setConfirmId(null);
                    } else {
                      setConfirmId(item.id);
                      window.setTimeout(() => setConfirmId((c) => (c === item.id ? null : c)), 3000);
                    }
                  }}
                  aria-label="حذف از صف"
                  className={`hit shrink-0 rounded-xl px-2 py-1.5 text-[12px] font-bold transition-colors ${
                    confirmId === item.id ? "bg-[color:var(--color-coral)] text-white" : "text-white/40 hover:bg-white/10 hover:text-[color:var(--color-coral)]"
                  }`}
                >
                  {confirmId === item.id ? "حذف؟" : <Trash2 className="h-4 w-4" />}
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
      className="flex items-center gap-2 rounded-xl bg-white/6 px-3.5 py-2 text-xs text-white/70 hover:bg-white/12 disabled:opacity-60"
      title="حذف فایل‌هایی که در پلی‌لیست نیستند از سرور"
    >
      <Trash2 className="w-4 h-4" /> پاکسازی فایل‌های یتیم
    </button>
  );
}