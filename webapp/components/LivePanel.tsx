"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { MonitorUp, Plus, Radio, Square } from "lucide-react";
import { api } from "@/lib/api";
import { publishWhip, type WhipState } from "@/lib/whip";

const inputClass =
  "w-full rounded-xl border border-[color:var(--color-border)] bg-white/5 px-3.5 py-2.5 text-[13.5px] text-[color:var(--color-ink)] outline-none focus:border-[color:var(--color-amber)]";

function CopyField({ label, value, primary }: { label: string; value: string; primary?: boolean }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-xs text-[color:var(--color-ink-muted)]">{label}</label>
      <div className={`flex items-center gap-2 rounded-xl border px-3 py-2 ${primary ? "border-[color:var(--color-amber)]/40 bg-[color:var(--color-amber)]/5" : "border-[color:var(--color-border)] bg-white/5"}`}>
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

// ────────────────────────────────────────────────────────────────────────────
// استریم مستقیم با مرورگر — مثل گوگل میت: دسکتاپ/پنجره/تب ضبط می‌شود و با
// WebRTC (WHIP) مستقیم به سرور پوش داده می‌شود؛ خروجی HLS مثل بقیه‌ی پخش‌های
// زنده وارد پلی‌لیست می‌شود و همه در پلیر هم‌زمان تماشا می‌کنند.
//
// قانون صدا: فقط صدای همان چیزی که انتخاب شده استریم می‌شود. وقتی یک «تب» را
// اشتراک می‌گذاری صدای همان تب پخش می‌شود؛ اما برای دسکتاپ/پنجره صدای بقیه‌ی
// تب‌ها و پنجره‌ها عمداً استریم نمی‌شود (systemAudio از قبل خاموش است).
// اگر سطح اشتراک‌گذاشته‌شده یکی از صفحات خودِ سایت باشد، صدایش هم حذف
// می‌شود تا صدای اتاق داخل استریم برنگردد (اکو).
// ────────────────────────────────────────────────────────────────────────────

type BrowserPhase = "idle" | "publishing" | "live";

interface CaptureHandleEvent extends Event {
  handle?: string;
}

function BrowserStreamSection({
  myName,
  canAdd,
  canManage,
  onLiveStarted,
}: {
  myName: string;
  canAdd: boolean;
  canManage: boolean;
  onLiveStarted?: () => void;
}) {
  const [phase, setPhase] = useState<BrowserPhase>("idle");
  const [statusText, setStatusText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [audioNote, setAudioNote] = useState<string | null>(null);

  const pcRef = useRef<RTCPeerConnection | null>(null);
  const workStreamRef = useRef<MediaStream | null>(null);
  const previewRef = useRef<HTMLVideoElement | null>(null);
  const addedRef = useRef(false);
  const phaseRef = useRef<BrowserPhase>("idle");
  phaseRef.current = phase;

  const cleanup = useCallback(() => {
    pcRef.current?.getSenders().forEach((s) => s.replaceTrack(null).catch(() => {}));
    pcRef.current?.close();
    pcRef.current = null;
    workStreamRef.current?.getTracks().forEach((t) => t.stop());
    workStreamRef.current = null;
    if (previewRef.current) previewRef.current.srcObject = null;
    addedRef.current = false;
    setPhase("idle");
    setStatusText(null);
  }, []);

  useEffect(() => cleanup, [cleanup]);

  // attach the local preview whenever the <video> mounts (it only renders
  // while a capture session exists)
  useEffect(() => {
    if (previewRef.current && workStreamRef.current && !previewRef.current.srcObject) {
      previewRef.current.srcObject = workStreamRef.current;
    }
  }, [phase]);

  // اگر سطح اشتراک‌گذاشته‌شده یکی از صفحات خود سایت بود، صدایش را حذف کن —
  // وگرنه صدای اتاق دوباره داخل استریم می‌رود و اکو می‌سازد.
  const stripAudio = useCallback(() => {
    let removed = false;
    workStreamRef.current?.getAudioTracks().forEach((t) => {
      removed = true;
      t.enabled = false;
      t.stop();
      workStreamRef.current?.removeTrack(t);
    });
    pcRef.current?.getSenders().forEach((s) => {
      if (s.track?.kind === "audio") s.replaceTrack(null).catch(() => {});
    });
    if (removed) setAudioNote("این صفحه خودِ سایت است — صدایش استریم نمی‌شود تا اکو نیفتد");
  }, []);

  async function start() {
    setError(null);
    setAudioNote(null);
    let disp: MediaStream;
    try {
      disp = await navigator.mediaDevices.getDisplayMedia({
        video: { frameRate: 30 },
        audio: true,
        // خودِ همین صفحه قابل انتخاب نباشد (اولین سپر ضد-اکو)
        selfBrowserSurface: "exclude",
        // صدای کل سیستم هرگز ضبط نمی‌شود — فقط صدای سطح انتخاب‌شده (تب)
        systemAudio: "exclude",
      } as unknown as DisplayMediaStreamOptions);
    } catch {
      return; // کاربر پنجره انتخاب را بست
    }

    const videoTrack = disp.getVideoTracks()[0];
    if (!videoTrack) {
      disp.getTracks().forEach((t) => t.stop());
      return;
    }

    // قانون صدا: فقط برای «تب» صدا داریم؛ دسکتاپ/پنجره بدون صدای سایر برنامه‌ها.
    const surface = (videoTrack.getSettings() as MediaTrackSettings & { displaySurface?: string }).displaySurface;
    const work = new MediaStream([videoTrack]);
    if (surface === "browser" && disp.getAudioTracks().length) {
      work.addTrack(disp.getAudioTracks()[0]);
      setAudioNote("صدا: تب انتخاب‌شده");
    } else {
      disp.getAudioTracks().forEach((t) => t.stop());
      setAudioNote("بدون صدا — برای داشتن صدا «تب» را انتخاب کن");
    }
    disp.getVideoTracks().slice(1).forEach((t) => t.stop());

    // دومین سپر ضد-اکو: اگر صفحه‌ی دیگری از خود سایت اشتراک شود
    videoTrack.addEventListener("capturehandle", (e) => {
      try {
        const data = JSON.parse((e as CaptureHandleEvent).handle || "{}");
        if (data?.app === "vergoboy-stream") stripAudio();
      } catch {
        /* ignore */
      }
    });
    videoTrack.addEventListener("ended", cleanup);

    workStreamRef.current = work;

    setPhase("publishing");
    setStatusText("در حال اتصال به سرور…");
    try {
      const keyInfo = await api.newLiveKey();
      const whipUrl = `${window.location.origin}/whip/${keyInfo.key}/whip`;
      const pc = await publishWhip(work, whipUrl, {
        onState: (state: WhipState) => {
          if (phaseRef.current === "idle") return;
          if (state === "connected") {
            setPhase("live");
            setStatusText("در حال پخش زنده");
            if (!addedRef.current) {
              addedRef.current = true;
              api
                .addLive(keyInfo.playback_url, `پخش زنده ${myName}`.trim(), myName, keyInfo.key)
                .then(() => {
                  // switching the whole room to the live stream needs
                  // controller permission — same rule as the backend
                  if (canManage) onLiveStarted?.();
                })
                .catch((e: unknown) => {
                  setError(e instanceof Error ? e.message : "افزودن به پلی‌لیست ناموفق بود");
                });
            }
          } else if (state === "failed") {
            setError("اتصال به سرور قطع شد");
            cleanup();
          } else if (state === "connecting") {
            setStatusText("در حال اتصال به سرور…");
          }
        },
      });
      pcRef.current = pc;
    } catch (e) {
      setError(e instanceof Error ? e.message : "شروع استریم ناموفق بود");
      cleanup();
    }
  }

  const busy = phase !== "idle";

  return (
    <div className="rounded-2xl border border-[color:var(--color-amber)]/30 bg-[color:var(--color-amber)]/[0.04] p-4">
      <h4 className="mb-1 flex items-center gap-2 text-[14.5px] font-bold text-[color:var(--color-ink)]">
        <MonitorUp className="h-4 w-4 text-[color:var(--color-amber)]" />
        استریم مستقیم با مرورگر
      </h4>

      <div className="flex flex-wrap items-center gap-3">
        {!busy ? (
          <button
            onClick={start}
            disabled={!canAdd}
            title={canAdd ? undefined : "اجازه‌ی افزودن پخش زنده نداری"}
            className="rounded-xl px-5 py-2.5 text-[14px] font-bold text-white disabled:cursor-not-allowed disabled:opacity-50"
            style={{ background: "linear-gradient(135deg, var(--color-amber), var(--color-plum))" }}
          >
            <span className="flex items-center gap-1.5">
              <MonitorUp className="h-4 w-4" />
              شروع استریم صفحه‌نمایش
            </span>
          </button>
        ) : (
          <>
            <span
              className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-bold ${
                phase === "live"
                  ? "border-[color:var(--color-coral)]/50 bg-[color:var(--color-coral)]/10 text-[color:var(--color-coral)]"
                  : "border-[color:var(--color-border)] bg-white/5 text-[color:var(--color-ink-muted)]"
              }`}
            >
              <span className={`h-2 w-2 rounded-full ${phase === "live" ? "animate-pulse bg-[color:var(--color-coral)]" : "bg-[color:var(--color-ink-muted)]"}`} />
              {statusText ?? "…"}
            </span>
            <button
              onClick={cleanup}
              className="rounded-xl border border-[color:var(--color-coral)]/50 bg-[color:var(--color-coral)]/10 px-4 py-2 text-[13px] font-bold text-[color:var(--color-coral)] hover:bg-[color:var(--color-coral)]/20"
            >
              <span className="flex items-center gap-1.5">
                <Square className="h-3.5 w-3.5" />
                پایان استریم
              </span>
            </button>
          </>
        )}
      </div>

      {(busy || error) && (
        <div className="mt-3 overflow-hidden rounded-xl border border-[color:var(--color-border)] bg-black/40" style={{ maxWidth: 420 }}>
          <video ref={previewRef} autoPlay muted playsInline className="aspect-video w-full object-contain" />
        </div>
      )}
      {audioNote && !error && <p className="mt-2 text-[11.5px] text-[color:var(--color-ink-muted)]">{audioNote}</p>}
      {error && <p className="mt-2 text-[12px] leading-relaxed text-[color:var(--color-coral)]">{error}</p>}
    </div>
  );
}

export function LivePanel({ myName, canAdd, canManage, onLiveStarted }: { myName: string; canAdd: boolean; canManage: boolean; onLiveStarted?: () => void }) {
  const [key, setKey] = useState<{ key: string; push_url: string; server_url?: string; push_urls?: string[]; playback_url: string } | null>(null);
  const [manualTitle, setManualTitle] = useState("");
  const [manualUrl, setManualUrl] = useState("");

  return (
    <div className="flex flex-col gap-6">
      <BrowserStreamSection myName={myName} canAdd={canAdd} canManage={canManage} onLiveStarted={onLiveStarted} />

      <div className="grid gap-6 md:grid-cols-2">
        <div>
          <h4 className="mb-1.5 text-[14.5px] font-bold text-[color:var(--color-ink)]">۱) ساخت کلید استریم</h4>
          <p className="mb-3 text-[12.5px] leading-relaxed text-[color:var(--color-ink-dim)]">یک کلید بساز و در نرم‌افزار پخشت (OBS و ...) آدرس Server و کلید رو جدا در دو فیلد وارد کن.</p>
          <button
            onClick={async () => setKey(await api.newLiveKey())}
            className="rounded-xl border border-[color:var(--color-border)] bg-white/5 px-4 py-2.5 text-[13px] text-[color:var(--color-ink)] hover:border-[color:var(--color-amber)]/50"
          >
            🔑 ساخت کلید جدید
          </button>

          {key && (
            <div className="mt-4 flex flex-col gap-3">
              <CopyField label="Server (فقط آدرس، بدون کلید)" value={key.server_url || key.push_url.replace(/\/[^/]+$/, "")} primary />
              <CopyField label="Stream Key (کلید استریم)" value={key.key} />
              <p className="text-[11.5px] leading-relaxed text-[color:var(--color-ink-dim)]">
                این دو مقدار رو جدا در OBS وارد کن — کلید نباید توی آدرس Server تکرار بشه.
              </p>
              <CopyField label="کل پوش‌آدرس (یکجا، اگر نرم‌افزارت یک فیلد دارد)" value={key.push_url} />
              {(key.push_urls || []).filter((u) => u !== key.push_url).map((u) => (
                <CopyField key={u} label="Push جایگزین (اگر اولی وصل نشد)" value={u} />
              ))}
              <CopyField label="آدرس پخش (HLS Playback)" value={key.playback_url} />
              <button
                onClick={async () => {
                  await api.addLive(key.playback_url, "پخش زنده", myName, key.key);
                  setKey(null);
                }}
                className="mt-1 rounded-xl px-5 py-2.5 text-[14px] font-bold text-white"
                style={{ background: "linear-gradient(135deg, var(--color-amber), var(--color-plum))" }}
              >
                <span className="flex items-center gap-1.5">
                  <Plus className="h-4 w-4" />
                  افزودن این پخش زنده به پلی‌لیست
                </span>
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
    </div>
  );
}
