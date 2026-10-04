"use client";

import { useEffect, useRef, useState } from "react";
import {
  Mic,
  MicOff,
  Headphones,
  HeadphoneOff,
  Settings,
  Volume2,
  VolumeX,
  Loader2,
  Signal,
} from "lucide-react";
import { AnimatePresence, motion } from "./anim";
import { Avatar } from "./Avatar";
import type { VoiceParticipant } from "@/lib/types";
import type { useVoiceRoom, VoiceStatus, MicStatus } from "@/lib/useVoiceRoom";

const NETWORK_QUALITY_LABEL: Record<string, string> = {
  excellent: "عالی",
  good: "خوب",
  poor: "ضعیف",
  lost: "قطعی",
  unknown: "—",
};

/**
 * Every label here is driven by a real LiveKit event or a real browser signal.
 * There is deliberately no "connected"-looking state that can appear without a
 * genuine Connected event behind it — that was the whole point of replacing the
 * old boolean with an explicit status.
 */
const STATUS_VIEW: Record<
  VoiceStatus,
  { label: string; dot: string; chip: string; pulse?: boolean }
> = {
  disconnected: { label: "قطع", dot: "bg-[color:var(--color-ink-dim)]", chip: "text-[color:var(--color-ink-dim)]" },
  connecting: { label: "در حال اتصال…", dot: "bg-[color:var(--color-amber)]", chip: "text-[color:var(--color-amber)]", pulse: true },
  connected: { label: "متصل", dot: "bg-[color:var(--color-teal)]", chip: "text-[color:var(--color-teal)]", pulse: true },
  reconnecting: { label: "در حال اتصال دوباره…", dot: "bg-[color:var(--color-amber)]", chip: "text-[color:var(--color-amber)]", pulse: true },
  failed: { label: "اتصال برقرار نشد", dot: "bg-[color:var(--color-coral)]", chip: "text-[color:var(--color-coral)]" },
};

/** Microphone is a separate axis from the room connection. */
const MIC_VIEW: Record<MicStatus, { label: string; className: string } | null> = {
  ready: { label: "میکروفون روشن", className: "text-[color:var(--color-teal)]" },
  muted: { label: "بی‌صدا", className: "text-[color:var(--color-ink-dim)]" },
  blocked: { label: "میکروفون مسدود است", className: "text-[color:var(--color-coral)]" },
  denied: { label: "اجازه میکروفون داده نشده", className: "text-[color:var(--color-coral)]" },
  nodevice: { label: "میکروفونی پیدا نشد", className: "text-[color:var(--color-coral)]" },
};

export function VoicePanel({
  myName,
  myAvatarUrl,
  v,
  onSpeakingChange,
  onVoiceJoin,
  onVoiceLeave,
  onOpenSettings,
}: {
  myName: string;
  myAvatarUrl: string | null;
  v: ReturnType<typeof useVoiceRoom>;
  onSpeakingChange?: (voices: { name: string; avatarUrl: string | null; speaking: boolean }[]) => void;
  onVoiceJoin?: () => void;
  onVoiceLeave?: () => void;
  onOpenSettings?: () => void;
}) {
  const prevParticipantsRef = useRef<{ name: string; avatarUrl: string | null; speaking: boolean }[]>([]);
  const voiceActiveRef = useRef(false);

  const { participants, connected, connecting, error, speakingIdentities, status } = v;

  // We are genuinely in the voice room while connected OR reconnecting: during
  // a reconnect LiveKit is still retrying the same room, so we are still a
  // member of it. Using the bare `connected` boolean here made every transient
  // blip emit `voice_left` and made the person vanish from everyone else's
  // couch for as long as the retry took.
  const inVoiceRoom = status === "connected" || status === "reconnecting";

  // Tell the server when we enter/leave the voice room, so the shared couches
  // know who is actually sitting in it (others shouldn't see me there unless
  // I'm really in the voice chat).
  useEffect(() => {
    if (inVoiceRoom && !voiceActiveRef.current) {
      voiceActiveRef.current = true;
      onVoiceJoin?.();
    } else if (!inVoiceRoom && voiceActiveRef.current) {
      voiceActiveRef.current = false;
      onVoiceLeave?.();
    }
  }, [inVoiceRoom, onVoiceJoin, onVoiceLeave]);

  // Report voice-room presence + who is speaking up to the page, so the sofa
  // and online-list can render the "talking" effect on profiles.
  useEffect(() => {
    const cur = participants.map((p) => ({
      name: p.name,
      avatarUrl: p.avatarUrl,
      speaking: speakingIdentities.has(p.identity) && !p.muted,
    }));
    const prev = prevParticipantsRef.current;
    const same =
      cur.length === prev.length &&
      cur.every((c, i) => {
        const p = prev[i];
        return p && c.name === p.name && c.avatarUrl === p.avatarUrl && c.speaking === p.speaking;
      });
    if (!same) {
      prevParticipantsRef.current = cur;
      onSpeakingChange?.(cur);
    }
  }, [participants, speakingIdentities, onSpeakingChange]);

  return (
    <div className="rounded-3xl border border-[color:var(--color-border)] bg-[color:var(--color-bg-soft)]/70 p-4 backdrop-blur-md">
      <div className="mb-3 flex items-center justify-between">
        <h4 className="flex items-center gap-2 text-[13px] font-bold text-[color:var(--color-ink)]">
          <Mic className="h-3.5 w-3.5 text-[color:var(--color-teal)]" />
          اتاق صوتی
          {status !== "disconnected" && (
            <span
              data-voice-status={status}
              className={`inline-flex items-center gap-1 rounded-full bg-white/5 px-2 py-0.5 text-[10.5px] font-normal ${STATUS_VIEW[status].chip}`}
            >
              <span
                className={`h-1.5 w-1.5 rounded-full ${STATUS_VIEW[status].dot}`}
                style={STATUS_VIEW[status].pulse ? { animation: "pulse-live 1.6s infinite" } : undefined}
              />
              {STATUS_VIEW[status].label}
              {status === "connected" && ` (${participants.length})`}
            </span>
          )}
        </h4>
        {!inVoiceRoom ? (
          <button
            onClick={() => v.join()}
            disabled={connecting}
            className="flex h-8 items-center gap-1.5 rounded-xl border border-[color:var(--color-teal)]/40 bg-[color:var(--color-teal)]/10 px-3 text-xs font-bold text-[color:var(--color-teal)] transition-colors hover:bg-[color:var(--color-teal)]/20 disabled:opacity-50"
          >
            {connecting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Mic className="h-3.5 w-3.5" />}
            {status === "failed" ? "تلاش دوباره" : connecting ? "در حال اتصال…" : "ورود به صدا"}
          </button>
        ) : (
          <button
            onClick={() => v.leave()}
            className="flex h-8 items-center gap-1.5 rounded-xl border border-[color:var(--color-border)] bg-white/5 px-3 text-xs font-bold text-[color:var(--color-coral)] transition-colors hover:border-[color:var(--color-coral)]/50"
          >
            <HeadphoneOff className="h-3.5 w-3.5" />
            خروج
          </button>
        )}
      </div>

      {error && status === "failed" && (
        <div className="mb-2.5 rounded-xl border border-[color:var(--color-coral)]/30 bg-[color:var(--color-coral)]/10 px-3 py-2">
          <p className="m-0 text-[11.5px] leading-relaxed text-[color:var(--color-coral)]">{error}</p>
          {v.diagnostics.signalUrl && (
            <p className="m-0 mt-1 font-mono text-[10px] opacity-70" dir="ltr">
              {v.diagnostics.signalUrl}
            </p>
          )}
        </div>
      )}

      {status === "reconnecting" && (
        <p className="mb-2.5 rounded-xl border border-[color:var(--color-amber)]/30 bg-[color:var(--color-amber)]/10 px-3 py-2 text-[11.5px] leading-relaxed text-[color:var(--color-ink)]">
          ارتباط صدا قطع شد و داریم دوباره وصل می‌شویم. تا وقتی برنگردد از اتاق صوتی خارج نمی‌شوی.
        </p>
      )}

      {/* Microphone state is independent of the room connection: you can be
          connected and still have no working mic, and that must not look like a
          failed connection. */}
      {inVoiceRoom && MIC_VIEW[v.micStatus] && (
        <p data-mic-status={v.micStatus} className={`mb-2.5 text-[11.5px] ${MIC_VIEW[v.micStatus]!.className}`}>
          {MIC_VIEW[v.micStatus]!.label}
        </p>
      )}

      {connected && v.audioBlocked && (
        <button
          type="button"
          onClick={() => v.unlockAudio()}
          className="mb-2.5 flex w-full items-center gap-2 rounded-xl border border-[color:var(--color-amber)]/40 bg-[color:var(--color-amber)]/10 px-3 py-2 text-left text-[11.5px] leading-relaxed text-[color:var(--color-ink)] transition-colors hover:bg-[color:var(--color-amber)]/20"
        >
          <VolumeX className="h-4 w-4 shrink-0 text-[color:var(--color-amber)]" />
          <span>
            <b>صدای بقیه شنیده نمی‌شود.</b> روی این پیام (یا هر جای صفحه) کلیک کنید تا صدا فعال شود.
          </span>
        </button>
      )}

      {connected && (
        <>
          {participants.length === 0 && (
            <p className="mb-2 text-[12px] text-[color:var(--color-ink-dim)]">در حال بارگذاری شرکت‌کننده‌ها…</p>
          )}
          <ul className="flex max-h-56 flex-col gap-1.5 overflow-y-auto">
            <AnimatePresence initial={false}>
              {participants.map((p) => (
                <VoiceRow key={p.identity} p={p} v={v} />
              ))}
            </AnimatePresence>
          </ul>
        </>
      )}

      {connected && (
        <div className="mt-3 border-t border-[color:var(--color-border)] pt-3">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Avatar name={myName} url={myAvatarUrl} size={30} className="ring-2 ring-[color:var(--color-border)]" />
              <div className="text-[12px] leading-tight">
                <div className="font-bold text-[color:var(--color-ink)]">{myName}</div>
                <div className="flex items-center gap-1 text-[10px] text-[color:var(--color-ink-dim)]">
                  <Signal className="h-3 w-3" />
                  اتصال: {NETWORK_QUALITY_LABEL[v.networkQuality]}
                </div>
              </div>
            </div>
            <div className="flex items-center gap-1.5">
              <SquareBtn title={v.micMuted ? "قطع صدا — روشن" : "بی‌صدا کردن"} active={!v.micMuted} onClick={v.toggleMic}>
                {v.micMuted ? <MicOff className="h-4 w-4 text-[color:var(--color-coral)]" /> : <Mic className="h-4 w-4" />}
              </SquareBtn>
              <SquareBtn title={v.deafened ? "شنیدن فعال" : "قطع کامل صدا"} active={!v.deafened} onClick={v.toggleDeafen}>
                {v.deafened ? <HeadphoneOff className="h-4 w-4 text-[color:var(--color-coral)]" /> : <Headphones className="h-4 w-4" />}
              </SquareBtn>
              <SquareBtn title="تنظیمات صدا" active={false} onClick={() => onOpenSettings?.()}>
                <Settings className="h-4 w-4" />
              </SquareBtn>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function VoiceRow({
  p,
  v,
}: {
  p: VoiceParticipant;
  v: ReturnType<typeof useVoiceRoom>;
}) {
  const [volOpen, setVolOpen] = useState(false);
  const speaking = speakingStyle(p);
  return (
    <motion.li
      layout
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.9 }}
      className="flex items-center gap-2 rounded-xl px-2 py-1.5 transition-colors hover:bg-white/5"
      data-context-kind="voice"
      data-context-name={p.name}
      onMouseEnter={() => setVolOpen(true)}
      onMouseLeave={() => setVolOpen(false)}
      onTouchStart={() => setVolOpen((o) => !o)}
    >
      <div className="relative shrink-0">
        <Avatar name={p.name} url={p.avatarUrl} size={34} className={speaking.ring} />
        {speaking.badge && (
          <span className="absolute -bottom-1 -left-1 rounded-full border border-[color:var(--color-bg)] bg-[color:var(--color-teal)] p-[3px]">
            <span className="block h-1.5 w-1.5 rounded-full bg-white" />
          </span>
        )}
      </div>

      <div className="min-w-0 flex-1">
        <div className={`flex items-center gap-1.5 text-[12.5px] ${p.isLocal ? "font-bold text-[color:var(--color-amber)]" : "text-[color:var(--color-ink)]"}`}>
          <span className="truncate">{p.name}</span>
          {p.muted && !p.isLocal && <MicOff className="h-3 w-3 shrink-0 text-[color:var(--color-ink-dim)]" />}
        </div>
        {/* audio level bar */}
        <div className="mt-1 h-1 w-full overflow-hidden rounded-full bg-white/10">
          <div
            className={`h-full rounded-full transition-[width] duration-150 ${
              p.isSpeaking && !p.muted ? "bg-[color:var(--color-teal)]" : "bg-[color:var(--color-amber)]"
            }`}
            style={{ width: `${Math.min(100, Math.max(3, p.audioLevel * 100))}%` }}
          />
        </div>
      </div>

      <AnimatePresence>
        {volOpen && !p.isLocal && (
          <motion.div
            initial={{ opacity: 0, width: 0 }}
            animate={{ opacity: 1, width: 96 }}
            exit={{ opacity: 0, width: 0 }}
            transition={{ duration: 0.15 }}
            className="flex shrink-0 items-center gap-1 overflow-hidden"
          >
            {p.volume === 0 ? (
              <VolumeX className="h-3.5 w-3.5 shrink-0 text-[color:var(--color-ink-dim)]" />
            ) : (
              <Volume2 className="h-3.5 w-3.5 shrink-0 text-[color:var(--color-ink-dim)]" />
            )}
            <input
              type="range"
              min={0}
              max={1}
              step={0.01}
              value={p.volume}
              onChange={(e) => v.setParticipantVolume(p.identity, parseFloat(e.target.value))}
              className="w-14 accent-[color:var(--color-amber)]"
              title={`بلندی صدای ${p.name}`}
            />
          </motion.div>
        )}
      </AnimatePresence>
    </motion.li>
  );
}

function speakingStyle(p: VoiceParticipant) {
  if (p.isLocal) {
    return {
      ring: "ring-2 ring-[color:var(--color-border)]",
      badge: false,
    };
  }
  if (p.isSpeaking && !p.muted) {
    return {
      ring: "ring-2 ring-[color:var(--color-teal)] shadow-[0_0_14px_-2px_rgba(64,205,160,0.65)]",
      badge: true,
    };
  }
  return {
    ring: p.muted ? "ring-2 ring-white/10 opacity-60" : "ring-2 ring-[color:var(--color-border)]",
    badge: false,
  };
}

function SquareBtn({
  children,
  title,
  onClick,
  active,
}: {
  children: React.ReactNode;
  title: string;
  onClick: () => void;
  active?: boolean;
}) {
  return (
    <button
      title={title}
      onClick={onClick}
      className={`flex h-8 w-8 items-center justify-center rounded-xl border text-[color:var(--color-ink)] transition-colors ${
        active
          ? "border-[color:var(--color-border)] bg-white/5 hover:border-[color:var(--color-amber)]/50"
          : "border-[color:var(--color-coral)]/40 bg-[color:var(--color-coral)]/10"
      }`}
    >
      {children}
    </button>
  );
}
