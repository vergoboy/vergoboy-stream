"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Download, MonitorPlay, Pause, Play, RotateCw, SkipBack, SkipForward, TriangleAlert } from "lucide-react";

import { fetchCapabilities } from "@/lib/capabilities";
import { formatTime, prettyTitle } from "@/lib/format";
import {
  nativePlayerAvailable,
  onNativePlayerExit,
  startNativePlayer,
  stopNativePlayer,
  trace,
} from "@/lib/nativePlayer";
import { isTauri } from "@/lib/tauri";
import type { PlaylistItem } from "@/lib/types";

/**
 * The room's screen.
 *
 * There is no in-browser player any more: nothing is transcoded, and a browser cannot decode most
 * of what people add (mkv/H.265/…). Playback belongs to the desktop app's native player, which reads
 * the ORIGINAL file and follows the room's clock. This component launches it, keeps it in step with
 * the selected item, and tells a browser visitor plainly where to watch.
 */
export function Stage({
  item,
  playing,
  position,
  canControl,
  canPrev,
  canNext,
  onTogglePlay,
  onSeek,
  onPrev,
  onNext,
  onGoToAdd,
}: {
  item: PlaylistItem | null;
  playing: boolean;
  position: number;
  canControl: boolean;
  canPrev: boolean;
  canNext: boolean;
  onTogglePlay: () => void;
  onSeek: (to: number) => void;
  onPrev: () => void;
  onNext: () => void;
  onGoToAdd?: () => void;
}) {
  const [mode, setMode] = useState<"checking" | "desktop" | "unavailable" | "browser">(
    isTauri() ? "checking" : "browser"
  );
  const [active, setActive] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lastStarted = useRef<string | null>(null);

  const open = useCallback(async (target: PlaylistItem) => {
    setError(null);
    trace(`stage: opening item=${target.id}`);
    try {
      await startNativePlayer({ itemId: target.id, title: prettyTitle(target.title) });
      lastStarted.current = target.id;
      setActive(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setActive(false);
    }
  }, []);

  // Decide once whether this build can play at all: the mpv fork must be installed next to the
  // app, and the server must run the sync service (otherwise the player could only drift).
  useEffect(() => {
    // The initial state already resolved the browser case; nothing to ask the OS.
    if (!isTauri()) return;
    let dead = false;
    (async () => {
      const available = await nativePlayerAvailable();
      let sync = false;
      try {
        sync = (await fetchCapabilities()).server.sync;
      } catch {
        /* an old/unreachable server: leave the player unavailable rather than pretend */
      }
      trace(`stage: native available=${available} sync=${sync}`);
      if (dead) return;
      setMode(available && sync ? "desktop" : "unavailable");
      if (!available) setError("پخشکننده روی این نصب پیدا نشد (mpv و پلاگین همزمان لازم است)");
      else if (!sync) setError("سرور سرویس همگامسازی را اجرا نمیکند");
    })();
    return () => {
      dead = true;
    };
  }, []);

  // The player window closing is the only signal that it stopped; keep the button honest.
  useEffect(() => {
    let off: (() => void) | undefined;
    let dead = false;
    onNativePlayerExit(() => {
      setActive(false);
      lastStarted.current = null;
    }).then((f) => {
      if (dead) f();
      else off = f;
    });
    return () => {
      dead = true;
      off?.();
    };
  }, []);

  // Selecting another item replaces what is playing. `native_player_start` refuses to run twice, so
  // stop first, then start the new one. Live is deliberately excluded: it is broadcast, not a file
  // with a room clock, and a sync client seeking it would keep dragging it off the live edge.
  useEffect(() => {
    if (mode !== "desktop" || !item) return;
    if (item.type === "live" || item.status !== "ready") {
      // Something unsynchronizable took the screen: close the file player instead of leaving the
      // previous film running behind a live broadcast.
      if (lastStarted.current) {
        lastStarted.current = null;
        void stopNativePlayer().then(() => setActive(false)).catch(() => {});
      }
      return;
    }
    if (lastStarted.current === item.id) return;
    let cancelled = false;
    (async () => {
      if (active) await stopNativePlayer().catch(() => {});
      if (cancelled) return;
      await open(item);
    })();
    return () => {
      cancelled = true;
    };
    // `active` is deliberately not a dependency: it changes as a RESULT of this effect.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, item?.id, item?.status, item?.type, open]);

  // Leaving the room closes the player; nothing should keep playing for an audience of one.
  useEffect(() => () => void stopNativePlayer().catch(() => {}), []);

  const title = item ? prettyTitle(item.title) : "";
  const live = item?.type === "live";
  const playable = item?.status === "ready" && !live;
  const [copiedLive, setCopiedLive] = useState(false);
  const copyLiveUrl = () => {
    if (!item?.src) return;
    navigator.clipboard
      ?.writeText(item.src)
      .then(() => {
        setCopiedLive(true);
        setTimeout(() => setCopiedLive(false), 1800);
      })
      .catch(() => {});
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="relative overflow-hidden rounded-3xl border border-[color:var(--color-border)] bg-black/70">
        <div className="relative flex aspect-video w-full flex-col items-center justify-center gap-3 px-6 text-center">
          {error && (
            <p className="flex max-w-[46ch] items-start gap-2 rounded-xl border border-[color:var(--color-coral)]/30 bg-[color:var(--color-coral)]/10 px-3 py-2 text-[12px] leading-relaxed text-[color:var(--color-coral)]">
              <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" />
              <span dir="auto">{error}</span>
            </p>
          )}

          {!item ? (
            <>
              <p className="display text-[22px] text-white">هنوز چیزی برای پخش نیست</p>
              <p className="max-w-[40ch] text-[12.5px] leading-relaxed text-white/55">
                صف پخش خالیه. یه فیلم اضافه کن تا در اپلیکیشن دسکتاپ پخش بشه.
              </p>
              {onGoToAdd && (
                <button
                  onClick={onGoToAdd}
                  className="mt-1 rounded-xl bg-[color:var(--color-amber)] px-4 py-2 text-[12.5px] font-bold text-black"
                >
                  افزودن فیلم
                </button>
              )}
            </>
          ) : mode === "desktop" || mode === "unavailable" ? (
            <>
              <MonitorPlay className="h-10 w-10 text-[color:var(--color-amber)]" />
              <p className="display max-w-[38ch] truncate text-[19px] text-white" title={title}>
                {title}
              </p>
              <p className="text-[12px] text-white/55">
                {live
                  ? "پخش زنده — بدون همگام‌سازی با ساعت اتاق"
                  : playable
                    ? active
                      ? "پخش در پنجره‌ی جداگانه‌ی پخش‌کننده باز است"
                      : "پخش‌کننده بسته است"
                    : "این آیتم منبع قابل پخشی ندارد"}
              </p>
              {live && item?.src && (
                <button
                  onClick={copyLiveUrl}
                  className="mt-1 rounded-xl border border-[color:var(--color-border)] bg-white/5 px-3.5 py-2 text-[12.5px] text-white hover:bg-white/10"
                >
                  {copiedLive ? "کپی شد ✓" : "کپی لینک پخش"}
                </button>
              )}
              {playable && mode === "desktop" && (
                <button
                  onClick={() => (active ? stopNativePlayer().then(() => setActive(false)) : open(item))}
                  className="mt-1 flex items-center gap-1.5 rounded-xl border border-[color:var(--color-border)] bg-white/5 px-3.5 py-2 text-[12.5px] text-white hover:bg-white/10"
                >
                  {active ? <RotateCw className="h-4 w-4" /> : <Play className="h-4 w-4" />}
                  {active ? "بازکردن دوباره" : "بازکردن پخش‌کننده"}
                </button>
              )}
            </>
          ) : (
            <>
              <Download className="h-10 w-10 text-[color:var(--color-amber)]" />
              <p className="display text-[19px] text-white">پخش در اپلیکیشن دسکتاپ</p>
              <p className="max-w-[42ch] text-[12.5px] leading-relaxed text-white/55">
                این نسخه هیچ ویدیویی را در مرورگر رمزگشایی یا تبدیل نمی‌کند؛ فایل اصلی با پخش‌کننده‌ی
                دسکتاپ و همگام با بقیه‌ی اتاق اجرا می‌شود.
              </p>
              {item && (
                <p className="max-w-[38ch] truncate text-[12px] text-white/40" title={title}>
                  «{title}»
                </p>
              )}
            </>
          )}
        </div>
      </div>

      {/* Room transport: the player window follows the room, so these work from any device. */}
      {item && playable && (
        <div className="flex items-center gap-2 rounded-2xl border border-[color:var(--color-border)] bg-white/[0.03] px-2.5 py-2">
          <button
            onClick={onTogglePlay}
            disabled={!canControl}
            aria-label={playing ? "مکث" : "پخش"}
            className="flex h-9 w-9 items-center justify-center rounded-xl bg-white/8 text-white disabled:opacity-40"
          >
            {playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
          </button>
          <button
            onClick={() => onSeek(Math.max(0, position - 10))}
            disabled={!canControl}
            aria-label="۱۰ ثانیه عقب"
            className="flex h-9 w-9 items-center justify-center rounded-xl text-white/70 hover:bg-white/8 disabled:opacity-40"
          >
            <SkipBack className="h-4 w-4" />
          </button>
          <button
            onClick={() => onSeek(position + 10)}
            disabled={!canControl}
            aria-label="۱۰ ثانیه جلو"
            className="flex h-9 w-9 items-center justify-center rounded-xl text-white/70 hover:bg-white/8 disabled:opacity-40"
          >
            <SkipForward className="h-4 w-4" />
          </button>
          <span className="mx-1 font-mono text-[11.5px] text-white/60" dir="ltr">
            {formatTime(position)}
          </span>
          <div className="mr-auto flex items-center gap-1.5">
            <button
              onClick={onPrev}
              disabled={!canControl || !canPrev}
              className="rounded-lg px-2.5 py-1.5 text-[12px] text-white/70 hover:bg-white/8 disabled:opacity-30"
            >
              قبلی
            </button>
            <button
              onClick={onNext}
              disabled={!canControl || !canNext}
              className="rounded-lg px-2.5 py-1.5 text-[12px] text-white/70 hover:bg-white/8 disabled:opacity-30"
            >
              بعدی
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
