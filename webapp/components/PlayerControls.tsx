"use client";

import { useEffect, useRef, useState } from "react";
import {
  Play,
  Pause,
  Volume2,
  Volume1,
  VolumeX,
  Maximize,
  Minimize,
  PictureInPicture2,
  Captions,
  CaptionsOff,
  Headphones,
  Settings2,
  SkipBack,
  SkipForward,
  Shuffle,
  Gauge,
  Sparkles,
  Check,
  RotateCcwSquare,
  Loader2,
  Palette,
  Undo2,
  Redo2,
} from "lucide-react";
import { formatTime } from "@/lib/format";
import { ratioAlong } from "@/lib/geometry";
import { popIn, press } from "@/lib/anim";

export const SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2];

export type QualityOption = {
  key: string;
  label: string;
  state: "ready" | "pending" | "busy" | "error";
  active: boolean;
  onPick: () => void;
};

export type ControlsProps = {
  rotated: boolean;
  live: boolean;
  placement: "overlay" | "below";
  touch: boolean;
  title: string;

  playing: boolean;
  onTogglePlay: () => void;
  curTime: number;
  duration: number;
  bufferedEnd: number;
  encodedSeconds: number;
  onScrubStart: () => void;
  onScrub: (t: number) => void;
  onScrubEnd: (t: number) => void;
  onSkip: (delta: number) => void;

  canPrev: boolean;
  canNext: boolean;
  onPrev: () => void;
  onNext: () => void;
  shuffle: boolean;
  canShuffle: boolean;
  onToggleShuffle: () => void;

  volume: number;
  muted: boolean;
  onVolume: (v: number) => void;
  onToggleMute: () => void;

  subtitles: { id: string; label: string }[];
  subIndex: number;
  onSub: (i: number) => void;
  onOpenSubStyle: () => void;

  audioTracks: { id: string; label: string }[];
  audioId: string | null;
  onAudio: (id: string | null) => void;

  rate: number;
  canSpeed: boolean;
  onRate: (r: number) => void;
  autoQuality: boolean;
  onAutoQuality: () => void;
  qualities: QualityOption[];
  onOpenSettings?: () => void;

  floatSupported: boolean;
  floating: boolean;
  onToggleFloat: () => void;

  fullscreen: boolean;
  onToggleFullscreen: () => void;
  onRotate: () => void;

  onMenuOpenChange: (open: boolean) => void;
};

type MenuName = "cc" | "audio" | "gear" | null;

export function PlayerControls(p: ControlsProps) {
  const [menu, setMenu] = useState<MenuName>(null);
  const barRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    p.onMenuOpenChange(menu !== null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [menu]);

  // close menus on outside press / Escape
  useEffect(() => {
    if (!menu) return;
    const down = (e: PointerEvent) => {
      if (!(e.target instanceof Element) || !e.target.closest("[data-menu-root]")) setMenu(null);
    };
    const key = (e: KeyboardEvent) => e.key === "Escape" && setMenu(null);
    window.addEventListener("pointerdown", down);
    window.addEventListener("keydown", key);
    return () => {
      window.removeEventListener("pointerdown", down);
      window.removeEventListener("keydown", key);
    };
  }, [menu]);

  const toggle = (m: Exclude<MenuName, null>) => setMenu((cur) => (cur === m ? null : m));
  const VolIcon = p.muted || p.volume === 0 ? VolumeX : p.volume < 0.5 ? Volume1 : Volume2;
  const hasMulti = p.canPrev || p.canNext;

  return (
    <div ref={barRef} dir="ltr" className="@container w-full select-none px-3 pb-3 pt-2 sm:px-5 sm:pb-4" data-player-ui>
      {!p.live && (
        <Timeline
          rotated={p.rotated}
          value={p.curTime}
          max={p.duration}
          buffered={p.bufferedEnd}
          encoded={p.encodedSeconds}
          onScrubStart={p.onScrubStart}
          onScrub={p.onScrub}
          onScrubEnd={p.onScrubEnd}
        />
      )}

      <div className="mt-1 flex items-center gap-1 sm:gap-1.5">
        {/* transport */}
        <IconBtn label={p.playing ? "توقف (K)" : "پخش (K)"} onClick={p.onTogglePlay} big gold>
          {p.playing ? <Pause className="h-5 w-5 fill-current" /> : <Play className="ml-0.5 h-5 w-5 fill-current" />}
        </IconBtn>

        {!p.live && (
          <>
            {hasMulti && (
              <IconBtn label="قبلی (P)" onClick={p.onPrev} disabled={!p.canPrev} hideOnPhone>
                <SkipBack className="h-[18px] w-[18px] fill-current" />
              </IconBtn>
            )}
            {hasMulti && (
              <IconBtn label="بعدی (N)" onClick={p.onNext} disabled={!p.canNext} hideOnPhone>
                <SkipForward className="h-[18px] w-[18px] fill-current" />
              </IconBtn>
            )}
            <IconBtn label="۱۰ ثانیه عقب" onClick={() => p.onSkip(-10)} hideOnPhone>
              <Undo2 className="h-[18px] w-[18px]" />
            </IconBtn>
            <IconBtn label="۱۰ ثانیه جلو" onClick={() => p.onSkip(10)} hideOnPhone>
              <Redo2 className="h-[18px] w-[18px]" />
            </IconBtn>
          </>
        )}

        {/* volume: on touch devices the hardware keys do this, so just a mute toggle */}
        <div className="group/vol flex items-center @max-[600px]:hidden">
          <IconBtn label={p.muted ? "پخش صدا (M)" : "بی‌صدا (M)"} onClick={p.onToggleMute}>
            <VolIcon className="h-[18px] w-[18px]" />
          </IconBtn>
          {!p.touch && (
            <input
              type="range"
              min={0}
              max={1}
              step={0.01}
              value={p.muted ? 0 : p.volume}
              onChange={(e) => p.onVolume(parseFloat(e.target.value))}
              aria-label="بلندی صدا"
              className="h-1 w-0 cursor-pointer opacity-0 transition-all duration-200 group-hover/vol:mr-1 group-hover/vol:w-20 group-hover/vol:opacity-100 focus-visible:w-20 focus-visible:opacity-100"
            />
          )}
        </div>

        <span className="ml-1 whitespace-nowrap font-mono text-[12px] text-white/80 tabular-nums max-sm:ml-0.5 max-sm:text-[11.5px]" dir="ltr">
          {p.live ? "زنده" : `${formatTime(p.curTime)} / ${formatTime(p.duration)}`}
        </span>

        <div className="min-w-0 flex-1 px-3 text-center text-[13px] text-white/70 @max-[700px]:hidden">
          <span className="block truncate" dir="auto">
            {p.placement === "overlay" ? p.title : ""}
          </span>
        </div>
        <div className="flex-1 @min-[700px]:hidden" />

        {/* settings cluster */}
        {!p.live && p.subtitles.length > 0 && (
          <div className="relative" data-menu-root>
            <IconBtn label="زیرنویس" onClick={() => toggle("cc")} active={p.subIndex >= 0 || menu === "cc"}>
              {p.subIndex >= 0 ? <Captions className="h-5 w-5" /> : <CaptionsOff className="h-5 w-5" />}
            </IconBtn>
            {menu === "cc" && (
              <Menu title="زیرنویس" icon={<Captions className="h-4 w-4" />}>
                <Row active={p.subIndex === -1} onClick={() => { p.onSub(-1); setMenu(null); }}>خاموش</Row>
                {p.subtitles.map((s, i) => (
                  <Row key={s.id} active={p.subIndex === i} onClick={() => { p.onSub(i); setMenu(null); }}>{s.label}</Row>
                ))}
                <Divider />
                <Row onClick={() => { setMenu(null); p.onOpenSubStyle(); }} icon={<Palette className="h-4 w-4" />}>ظاهر زیرنویس…</Row>
              </Menu>
            )}
          </div>
        )}

        {!p.live && p.audioTracks.length > 0 && (
          <div className="relative" data-menu-root>
            <IconBtn label="صدا و دوبله" onClick={() => toggle("audio")} active={!!p.audioId || menu === "audio"}>
              <Headphones className="h-5 w-5" />
            </IconBtn>
            {menu === "audio" && (
              <Menu title="صدا و دوبله" icon={<Headphones className="h-4 w-4" />}>
                <Row active={!p.audioId} onClick={() => { p.onAudio(null); setMenu(null); }}>صدای اصلی فیلم</Row>
                {p.audioTracks.map((t) => (
                  <Row key={t.id} active={p.audioId === t.id} onClick={() => { p.onAudio(t.id); setMenu(null); }}>{t.label}</Row>
                ))}
              </Menu>
            )}
          </div>
        )}

        {!p.live && (
          <div className="relative" data-menu-root>
            <IconBtn label="سرعت و کیفیت" onClick={() => toggle("gear")} active={menu === "gear" || p.rate !== 1}>
              <Settings2 className="h-5 w-5" />
            </IconBtn>
            {menu === "gear" && (
              <Menu title="سرعت و کیفیت" icon={<Gauge className="h-4 w-4" />} wide>
                <p className="px-3 pb-1 pt-1 text-[11px] font-semibold text-white/50">سرعت پخش برای همه‌ی اتاق</p>
                <div className="grid grid-cols-4 gap-1 px-2 pb-2" data-pop-item>
                  {SPEEDS.map((s) => (
                    <button
                      key={s}
                      type="button"
                      disabled={!p.canSpeed}
                      onClick={() => p.onRate(s)}
                      className={`rounded-xl px-1 py-2 text-[12.5px] font-semibold transition-colors disabled:opacity-40 ${
                        p.rate === s ? "bg-[color:var(--color-amber)] text-black" : "bg-white/8 text-white hover:bg-white/15"
                      }`}
                    >
                      {s}x
                    </button>
                  ))}
                </div>
                {!p.canSpeed && <p className="px-3 pb-2 text-[11px] text-white/45">سرعت رو فقط مدیر اتاق عوض می‌کنه.</p>}
                <Divider />
                <p className="px-3 pb-1 pt-2 text-[11px] font-semibold text-white/50">کیفیت تصویر (فقط برای خودت)</p>
                <Row active={p.autoQuality} onClick={p.onAutoQuality} icon={<Sparkles className="h-4 w-4" />}>خودکار</Row>
                {p.qualities.map((q) => (
                  <Row
                    key={q.key}
                    active={q.active}
                    disabled={q.state === "busy"}
                    onClick={() => { q.onPick(); if (q.state === "ready") setMenu(null); }}
                    hint={q.state === "pending" ? "برای ساختنش بزن" : q.state === "busy" ? "در حال ساخت…" : q.state === "error" ? "خطا؛ دوباره بزن" : undefined}
                    icon={q.state === "busy" ? <Loader2 className="h-4 w-4 animate-spin" /> : undefined}
                  >
                    {q.label}
                  </Row>
                ))}
                {p.onOpenSettings && (
                  <>
                    <Divider />
                    <Row onClick={() => { setMenu(null); p.onOpenSettings?.(); }} icon={<Settings2 className="h-4 w-4" />}>همه‌ی تنظیمات</Row>
                  </>
                )}
              </Menu>
            )}
          </div>
        )}

        {p.canShuffle && (
          <IconBtn label="پخش تصادفی" onClick={p.onToggleShuffle} active={p.shuffle} hideOnPhone>
            <Shuffle className="h-[18px] w-[18px]" />
          </IconBtn>
        )}

        {p.floatSupported && (
          <IconBtn label={p.floating ? "برگرد به پرده" : "پخش شناور"} onClick={p.onToggleFloat} active={p.floating}>
            <PictureInPicture2 className="h-5 w-5" />
          </IconBtn>
        )}

        {p.fullscreen && p.touch && (
          <IconBtn label="چرخش صفحه" onClick={p.onRotate}>
            <RotateCcwSquare className="h-5 w-5" />
          </IconBtn>
        )}

        <IconBtn label={p.fullscreen ? "خروج از تمام‌صفحه (F)" : "تمام‌صفحه (F)"} onClick={p.onToggleFullscreen}>
          {p.fullscreen ? <Minimize className="h-5 w-5" /> : <Maximize className="h-5 w-5" />}
        </IconBtn>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */

function IconBtn({
  children,
  label,
  onClick,
  active,
  disabled,
  big,
  gold,
  hideOnPhone,
}: {
  children: React.ReactNode;
  label: string;
  onClick: () => void;
  active?: boolean;
  disabled?: boolean;
  big?: boolean;
  gold?: boolean;
  hideOnPhone?: boolean;
}) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      aria-pressed={active}
      disabled={disabled}
      onClick={(e) => {
        press(e.currentTarget);
        onClick();
      }}
      className={`hit flex shrink-0 items-center justify-center rounded-full transition-colors disabled:cursor-not-allowed disabled:opacity-35 ${
        big ? "h-11 w-11" : "h-10 w-10"
      } ${hideOnPhone ? "@max-[600px]:hidden" : ""} ${
        gold
          ? "bg-[color:var(--color-amber)] text-black shadow-[0_6px_22px_-6px_rgba(247,195,90,0.7)] hover:brightness-110"
          : active
            ? "bg-white/20 text-[color:var(--color-amber)]"
            : "text-white/90 hover:bg-white/15"
      }`}
    >
      {children}
    </button>
  );
}

function Menu({ title, icon, children, wide }: { title: string; icon: React.ReactNode; children: React.ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    popIn(ref.current, { from: "85% 100%" });
  }, []);
  return (
    <div
      ref={ref}
      role="menu"
      dir="rtl"
      aria-label={title}
      className={`glass-strong absolute bottom-[calc(100%+10px)] right-0 z-50 max-h-[min(60dvh,420px)] overflow-y-auto rounded-2xl p-1.5 text-white ${
        wide ? "w-[min(290px,86vw)]" : "w-[min(230px,80vw)]"
      }`}
    >
      <div className="flex items-center gap-2 px-3 pb-1.5 pt-2 text-[12px] font-bold text-[color:var(--color-amber)]" data-pop-item>
        {icon}
        {title}
      </div>
      {children}
    </div>
  );
}

function Row({
  children,
  active,
  onClick,
  icon,
  hint,
  disabled,
}: {
  children: React.ReactNode;
  active?: boolean;
  onClick?: () => void;
  icon?: React.ReactNode;
  hint?: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="menuitemradio"
      aria-checked={active}
      disabled={disabled}
      data-pop-item
      onClick={onClick}
      className={`flex min-h-10 w-full items-center gap-2.5 rounded-xl px-3 text-right text-[13.5px] transition-colors hover:bg-white/10 disabled:opacity-50 ${
        active ? "font-bold text-[color:var(--color-amber)]" : "text-white"
      }`}
    >
      {icon && <span className="shrink-0 text-white/70">{icon}</span>}
      <span className="min-w-0 flex-1 truncate">{children}</span>
      {hint && <span className="shrink-0 text-[10.5px] text-white/45">{hint}</span>}
      {active && <Check className="h-4 w-4 shrink-0" />}
    </button>
  );
}

function Divider() {
  return <div className="my-1 h-px bg-white/10" />;
}

/* ------------------------------------------------------------------ */

function Timeline({
  value,
  max,
  buffered,
  encoded,
  rotated,
  onScrubStart,
  onScrub,
  onScrubEnd,
}: {
  value: number;
  max: number;
  buffered: number;
  encoded: number;
  rotated: boolean;
  onScrubStart: () => void;
  onScrub: (t: number) => void;
  onScrubEnd: (t: number) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const [active, setActive] = useState(false);
  const safeMax = isFinite(max) && max > 0 ? max : 0;
  const pct = safeMax ? Math.min(100, (value / safeMax) * 100) : 0;
  const bufPct = safeMax ? Math.min(100, (buffered / safeMax) * 100) : 0;
  const encPct = safeMax && encoded > 0 && encoded < safeMax ? (encoded / safeMax) * 100 : 100;

  const ratio = (e: React.PointerEvent) => (ref.current ? ratioAlong(e, ref.current, rotated) : 0);

  return (
    <div
      ref={ref}
      role="slider"
      aria-label="نوار زمان"
      aria-valuemin={0}
      aria-valuemax={Math.round(safeMax)}
      aria-valuenow={Math.round(value)}
      aria-valuetext={formatTime(value)}
      className="group/tl relative flex h-7 w-full cursor-pointer touch-none items-center sm:h-6"
      onPointerDown={(e) => {
        if (!safeMax) return;
        e.currentTarget.setPointerCapture(e.pointerId);
        setActive(true);
        onScrubStart();
        onScrub(ratio(e) * safeMax);
      }}
      onPointerMove={(e) => {
        const r = ratio(e);
        setHover(r);
        if (active) onScrub(r * safeMax);
      }}
      onPointerUp={(e) => {
        if (!active) return;
        setActive(false);
        onScrubEnd(ratio(e) * safeMax);
      }}
      onPointerCancel={() => {
        setActive(false);
        setHover(null);
        onScrubEnd(value);
      }}
      onPointerLeave={() => !active && setHover(null)}
    >
      <div className={`relative w-full overflow-hidden rounded-full bg-white/20 transition-[height] duration-150 ${active || hover !== null ? "h-[7px]" : "h-[4px]"}`}>
        {/* what the encoder hasn't produced yet is hatched, so a blocked seek makes sense */}
        {encPct < 100 && (
          <div
            className="absolute inset-y-0 right-0"
            style={{
              left: `${encPct}%`,
              background: "repeating-linear-gradient(135deg, rgba(255,255,255,.18) 0 4px, transparent 4px 8px)",
            }}
          />
        )}
        <div className="absolute inset-y-0 left-0 bg-white/30" style={{ width: `${bufPct}%` }} />
        <div
          className="absolute inset-y-0 left-0 rounded-full"
          style={{ width: `${pct}%`, background: "linear-gradient(90deg, var(--color-plum-soft), var(--color-amber))" }}
        />
      </div>
      <div
        className={`pointer-events-none absolute top-1/2 h-4 w-4 -translate-x-1/2 -translate-y-1/2 rounded-full bg-[color:var(--color-amber)] shadow-[0_0_14px_2px_rgba(247,195,90,0.7)] transition-transform duration-150 ${
          active || hover !== null ? "scale-100" : "scale-0"
        }`}
        style={{ left: `${pct}%` }}
      />
      {hover !== null && safeMax > 0 && (
        <div
          className="pointer-events-none absolute -top-7 -translate-x-1/2 rounded-lg bg-black/85 px-2 py-0.5 font-mono text-[11px] text-white"
          style={{ left: `${Math.min(97, Math.max(3, hover * 100))}%` }}
        >
          {formatTime(hover * safeMax)}
        </div>
      )}
    </div>
  );
}
