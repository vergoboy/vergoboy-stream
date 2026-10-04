"use client";

import {
  Play,
  Pause,
  Film,
  Radio,
  Loader2,
  TriangleAlert,
  Plus,
  X,
  GripVertical,
  Maximize2,
  RotateCcw,
  Popcorn,
  Clapperboard,
} from "lucide-react";
/* eslint-disable react-hooks/refs */

import { useCallback, useEffect, useRef, useState, startTransition } from "react";
import type Hls from "hls.js";
import { formatTime, isHlsUrl, levelLabelFromUrl, prettyTitle } from "@/lib/format";
import { api } from "@/lib/api";
import { mediaUrl } from "@/lib/config";
import { DEFAULT_SETTINGS, useAppSettings, type ShortcutAction } from "@/lib/settings";
import type { NotifyEvent, PlaylistItem, Rendition, SubStyle, TranscodeProgress } from "@/lib/types";
import { PlayerControls, type QualityOption } from "./PlayerControls";
import { run, ripple, popIn, spring } from "@/lib/anim";
import { useIdle, useViewport } from "@/lib/useIdle";
import { canNativeFloat, enterNativeMini, setNativeFullscreen, isTauri, isMobileOS } from "@/lib/tauri";
import { localPoint } from "@/lib/geometry";



/** hls.js doesn't expose a retry budget; we track our own so the fatal-error
 *  handler can stop recovering and surface a real message to the user. */
type HlsWithBudget = Hls & { networkRecoveryAttempts: number; mediaRecoveryAttempts: number };

function defaultRendition(item: PlaylistItem | null): Rendition | undefined {
  return item?.renditions?.find((r) => r.is_default) ?? item?.renditions?.[0];
}

export function Player({
  item,
  playing,
  rate,
  expectedPosition,
  requestControl,
  transcodeProgress,
  latestNotify,
  myName,
  subStyle,
  canSpeed,
  canPrev,
  canNext,
  canControl,
  onPlaybackState,
  shuffle,
  canShuffle,
  onPrev,
  onNext,
  onToggleShuffle,
  onGoToAdd,
  onGoToSubStyle,
  onOpenShortcuts,
  onOpenSettings,
}: {
  item: PlaylistItem | null;
  playing: boolean;
  rate: number;
  expectedPosition: () => number;
  requestControl: (action: "play" | "pause" | "seek" | "rate" | "select" | "shuffle", extra?: Record<string, number>) => void;
  transcodeProgress: Record<string, TranscodeProgress>;
  latestNotify: NotifyEvent | null;
  myName: string;
  subStyle: SubStyle;
  canSpeed: boolean;
  canPrev: boolean;
  canNext: boolean;
  /** May change the shared media/playlist (room owner or promoted manager). */
  canControl: boolean;
  /** Report real playback state upward so the server can mark this person as
   *  actually watching (vs merely browsing the room). */
  onPlaybackState?: (watching: boolean, itemId: string | null) => void;
  shuffle: boolean;
  canShuffle: boolean;
  onPrev: () => void;
  onNext: () => void;
  onToggleShuffle: () => void;
  onGoToAdd?: () => void;
  onGoToSubStyle: () => void;
  onOpenShortcuts: () => void;
  onOpenSettings?: () => void;
}) {
  const { settings, setSettings } = useAppSettings();
  const sc = settings.shortcuts;
  const videoRef = useRef<HTMLVideoElement>(null);
  const dubAudioRef = useRef<HTMLAudioElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const hlsRef = useRef<Hls | null>(null);
  const myRequestedLabels = useRef<Set<string>>(new Set());
  const needsMenuRefresh = useRef(false);
  const lastFrontierWarn = useRef(0);
  const [activeLevelLabel, setActiveLevelLabel] = useState<string | null>(null);
  const [levels, setLevels] = useState<{ height: number; label: string }[]>([]);
  const [currentLevel, setCurrentLevel] = useState(-1);
  const [curTime, setCurTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [dragging, setDragging] = useState(false);
  const dragValueRef = useRef(0);
  // Seeded from (and written back to) settings so the room always opens at the
  // listener's own level. Seeded once; writes only happen from user actions.
  const [volume, setVolume] = useState(settings.playback?.volume ?? 1);
  const [muted, setMuted] = useState(settings.playback?.muted ?? false);
  const changeVolume = useCallback((next: number | ((x: number) => number)) => {
    setVolume(next);
    setSettings((s) => {
      const current = s.playback?.volume ?? DEFAULT_SETTINGS.playback.volume;
      const volume = Math.min(1, Math.max(0, typeof next === "function" ? next(current) : next));
      return { ...s, playback: { volume, muted: s.playback?.muted ?? DEFAULT_SETTINGS.playback.muted } };
    });
  }, [setSettings]);
  const changeMuted = useCallback((next: boolean | ((m: boolean) => boolean)) => {
    setMuted(next);
    setSettings((s) => {
      const current = s.playback?.muted ?? DEFAULT_SETTINGS.playback.muted;
      return {
        ...s,
        playback: { volume: s.playback?.volume ?? DEFAULT_SETTINGS.playback.volume, muted: typeof next === "function" ? next(current) : next },
      };
    });
  }, [setSettings]);
  const [buffering, setBuffering] = useState(false);
  const [bigPlay, setBigPlay] = useState(false);
  // A REAL playback failure reported by the media element or by hls.js.
  // Never invented: it is only ever set from an actual MediaError /
  // hls.js fatal error / failed media request, and is cleared as soon as a
  // source loads and starts playing again.
  const [playbackError, setPlaybackError] = useState<{ title: string; detail: string; code?: string } | null>(null);
  const [fullscreen, setFullscreen] = useState(false);
  const [currentSubIndex, setCurrentSubIndex] = useState(-1);
  const [activeCueText, setActiveCueText] = useState<string[]>([]);
  const [currentAudioTrackId, setCurrentAudioTrackId] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [showDebug, setShowDebug] = useState(false);
  // ---- floating / mini player -------------------------------------------
  // "native": the Tauri desktop window itself shrinks into a frameless,
  //   always-on-top mini player (real floating over other apps).
  // "dom": a draggable mini window inside the page (phones, and browsers
  //   without Picture-in-Picture). Browsers that have PiP use that instead.
  const [floatMode, setFloatMode] = useState<"none" | "dom" | "native">("none");
  const floating = floatMode === "dom";
  const nativeMini = floatMode === "native";
  const [floatPos, setFloatPos] = useState({ x: 16, y: 16 });
  const restoreNativeRef = useRef<null | (() => Promise<void>)>(null);
  const [inPip, setInPip] = useState(false);
  const vp = useViewport();
  const touch = vp.touch;
  const [forceLandscape, setForceLandscape] = useState(false);
  const rotated = fullscreen && forceLandscape && vp.portrait;
  const domFsRef = useRef(false);
  const floatDrag = useRef<{ sx: number; sy: number; ox: number; oy: number } | null>(null);
  const floatBoxRef = useRef<HTMLDivElement>(null);

  const exitNative = useCallback(async () => {
    const restore = restoreNativeRef.current;
    restoreNativeRef.current = null;
    setFloatMode("none");
    try {
      await restore?.();
    } catch {
      /* window state is best-effort */
    }
  }, []);

  const togglePip = useCallback(async () => {
    const v = videoRef.current;
    if (!v) return;
    if (floatMode === "native") return void exitNative();
    if (floatMode === "dom") return setFloatMode("none");
    if (document.pictureInPictureElement) {
      document.exitPictureInPicture().catch(() => {});
      return;
    }
    if (canNativeFloat()) {
      try {
        if (fullscreen) {
          setFullscreen(false);
          setForceLandscape(false);
        }
        restoreNativeRef.current = await enterNativeMini();
        setFloatMode("native");
      } catch (err) {
        console.warn("[player] native mini window unavailable, using in-page float", err);
        setFloatMode("dom");
      }
      return;
    }
    if (!isTauri() && typeof v.requestPictureInPicture === "function" && document.pictureInPictureEnabled) {
      v.requestPictureInPicture().catch(() => setFloatMode("dom"));
      return;
    }
    setFloatMode("dom");
  }, [floatMode, exitNative, fullscreen]);

  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    const on = () => setInPip(true);
    const off = () => setInPip(false);
    v.addEventListener("enterpictureinpicture", on);
    v.addEventListener("leavepictureinpicture", off);
    return () => {
      v.removeEventListener("enterpictureinpicture", on);
      v.removeEventListener("leavepictureinpicture", off);
    };
  }, []);

  // Never leave the OS window shrunk if this component goes away.
  useEffect(() => {
    return () => {
      const restore = restoreNativeRef.current;
      restoreNativeRef.current = null;
      void restore?.();
    };
  }, []);

  // Dragging the in-page float: pointer capture on the handle, then a springy
  // snap to the nearest side so it never ends up half off-screen.
  const onFloatDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (e.pointerType === "mouse" && e.button !== 0) return;
      e.currentTarget.setPointerCapture(e.pointerId);
      floatDrag.current = { sx: e.clientX, sy: e.clientY, ox: floatPos.x, oy: floatPos.y };
    },
    [floatPos]
  );
  const onFloatMove = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    const d = floatDrag.current;
    const box = floatBoxRef.current;
    if (!d || !box) return;
    const x = Math.min(window.innerWidth - box.offsetWidth - 4, Math.max(4, d.ox - (e.clientX - d.sx)));
    const y = Math.min(window.innerHeight - box.offsetHeight - 4, Math.max(4, d.oy - (e.clientY - d.sy)));
    setFloatPos({ x, y });
  }, []);
  const floatPosRef = useRef(floatPos);
  floatPosRef.current = floatPos;
  const onFloatUp = useCallback(() => {
    const box = floatBoxRef.current;
    if (!floatDrag.current || !box) return;
    floatDrag.current = null;
    const p = floatPosRef.current;
    const w = box.offsetWidth;
    const margin = 12;
    const centerX = window.innerWidth - p.x - w / 2;
    const snapX = centerX < window.innerWidth / 2 ? window.innerWidth - w - margin : margin;
    const target = { x: snapX, y: Math.max(margin, p.y) };
    run(box, {
      right: [p.x, target.x],
      bottom: [p.y, target.y],
      duration: 520,
      ease: spring({ stiffness: 260, damping: 24 }),
      onComplete: () => setFloatPos(target),
    });
  }, []);

  const showToast = useCallback((msg: string) => {
    setToast(msg);
    window.setTimeout(() => setToast((t) => (t === msg ? null : t)), 3200);
  }, []);

  // ---- seek-frontier guard: don't let anyone scrub past what's actually
  // encoded yet for the rendition they're currently watching. ----
  const encodedFrontier = useCallback(
    (target?: PlaylistItem | null) => {
      const it = target ?? item;
      if (!it || it.type === "live") return Infinity;
      const renditions = it.renditions || [];
      if (!renditions.length) return it.status === "complete" ? Infinity : 0;
      const label = activeLevelLabel || defaultRendition(it)?.label;
      const rend = renditions.find((r) => r.label === label);
      if (!rend) return Infinity;
      if (rend.status === "complete" || !rend.status) return Infinity;
      if (rend.status !== "ready") return 0;
      const info = label ? transcodeProgress[`${it.id}:${label}`] : undefined;
      if (!info || info.encoded_seconds == null) return Infinity;
      return info.encoded_seconds;
    },
    [item, activeLevelLabel, transcodeProgress]
  );

  const encodedFrontierRef = useRef(encodedFrontier);
  useEffect(() => { encodedFrontierRef.current = encodedFrontier; }, [encodedFrontier]);

  const clampToFrontier = useCallback(
    (target: number) => {
      const frontier = encodedFrontier();
      if (frontier === Infinity) return target;
      const MARGIN = 3;
      if (target > frontier - MARGIN) {
        if (Date.now() - lastFrontierWarn.current > 2500) {
          lastFrontierWarn.current = Date.now();
          showToast(`این بخش هنوز آماده نشده — انکد تا دقیقه ${formatTime(frontier)} پیش رفته.`);
        }
        return Math.max(0, frontier - MARGIN);
      }
      return target;
    },
    [encodedFrontier, showToast]
  );

  const seek = useCallback(
    (to: number) => {
      const target = clampToFrontier(to);
      const v = videoRef.current;
      if (v) {
        try { v.currentTime = target; } catch {}
        setCurTime(target);
      }
      requestControl("seek", { to: target });
    },
    [requestControl, clampToFrontier]
  );

  const togglePlay = useCallback(() => {
    const v = videoRef.current;
    if (!item || !v) return;
    // live streams have no shared transport controls — a click can only
    // (re)start local playback, never pause what everyone else watches
    if (item.type === "live") {
      if (v.paused) v.play().catch(() => {});
      return;
    }
    // VOD: start/pause local playback INSIDE the user click gesture, then
    // tell the server so the shared clock follows. Browsers block play()
    // calls made from timers/effects (autoplay policy), so playback would
    // never start if we only emitted the control request and waited for the
    // 6s sync loop to call v.play() on our behalf.
    if (v.paused) {
      v.play().catch(() => setBigPlay(true));
      requestControl("play", { at: v.currentTime || 0 });
    } else {
      v.pause();
      requestControl("pause", { at: v.currentTime || 0 });
    }
  }, [item, requestControl]);

  // ---- load / teardown source whenever the underlying item id changes ----
  // Derived, not raw item.status: ready and complete are BOTH "playable" and
  // use the exact same src — we must not tear down and rebuild the whole
  // hls.js instance just because status ticked from "ready" to "complete"
  // (that was the bug causing playback to glitch/reset right as encoding
  // finished). We only want to react when playability actually changes.
  const isPlayable = Boolean(
    item && item.src && (item.status === "ready" || item.status === "complete" || item.type === "live" || item.status === undefined)
  );
  const mediaSrc = item?.src ? mediaUrl(item.src) : null;
  // Bumped by the Retry button to force the source-loading effect to tear the
  // old player down and attach a brand new one.
  const [sourceEpoch, setSourceEpoch] = useState(0);

  const retryPlayback = useCallback(() => {
    setPlaybackError(null);
    setBuffering(true);
    setBigPlay(false);
    setSourceEpoch((n) => n + 1);
  }, []);

  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;

    if (hlsRef.current) {
      hlsRef.current.destroy();
      hlsRef.current = null;
    }
    setLevels([]);
    setCurrentLevel(-1);
    setActiveLevelLabel(null);
    myRequestedLabels.current = new Set();
    needsMenuRefresh.current = false;
    setCurrentSubIndex(-1);
    setCurrentAudioTrackId(null);
    // A new source gets a fresh error slate and fresh recovery budget, so the
    // error from the previous item can't linger over a working video.
    setPlaybackError(null);
    setBuffering(isPlayable);
    setBigPlay(false);

    if (!isPlayable) {
      v.removeAttribute("src");
      v.load();
      return;
    }

    let cancelled = false;
    (async () => {
      if (isHlsUrl(mediaSrc!)) {
        const HlsMod = (await import("hls.js")).default;
        if (cancelled) return;
        if (HlsMod.isSupported()) {
          const hls = new HlsMod({
          enableWorker: true,
          startLevel: 0,
          lowLatencyMode: false,
          backBufferLength: 90,
          maxBufferLength: 60,
          maxMaxBufferLength: 120,
          maxBufferSize: 60 * 1024 * 1024,
          maxBufferHole: 0.5,
          highBufferWatchdogPeriod: 2,
          nudgeOffset: 0.1,
          nudgeMaxRetry: 10,
          manifestLoadingMaxRetry: 15,
          manifestLoadingRetryDelay: 1000,
          levelLoadingMaxRetry: 15,
          levelLoadingRetryDelay: 1000,
          fragLoadingMaxRetry: 3,
          fragLoadingRetryDelay: 1000,
        });
          hlsRef.current = hls;
          // Bounded recovery: track how many automatic retries we already
          // spent so the fatal handler can give up and show a real error
          // instead of looping silently forever.
          (hls as HlsWithBudget).networkRecoveryAttempts = 0;
          (hls as HlsWithBudget).mediaRecoveryAttempts = 0;
          hls.on(HlsMod.Events.MANIFEST_PARSED, () => {
            setLevels(hls.levels.map((l) => ({ height: l.height, label: levelLabelFromUrl(l.url) || `${l.height}p` })));
            setCurrentLevel(hls.currentLevel);
          });
          hls.on(HlsMod.Events.LEVEL_SWITCHED, (_e, data) => {
            const lvl = hls.levels[data.level];
            setActiveLevelLabel(lvl ? levelLabelFromUrl(lvl.url) : null);
            setCurrentLevel(data.level);
          });
          hls.on(HlsMod.Events.ERROR, (_e, data) => {
            if (data.fatal) {
              switch (data.type) {
                case HlsMod.ErrorTypes.NETWORK_ERROR:
                  // Only ONE recovery attempt: a manifest that 404s will fail
                  // again immediately, and silently retrying forever is what
                  // produced the "stuck loading" state with no explanation.
                  if ((hls as HlsWithBudget).networkRecoveryAttempts < 2) {
                    (hls as HlsWithBudget).networkRecoveryAttempts++;
                    console.warn("[hls.js] Network error, recovering...", data);
                    hls.startLoad();
                  } else {
                    setPlaybackError({
                      title: "این ویدیو لود نشد",
                      detail: "سرور پخش، داده‌ی ویدیو رو نفرستاد.",
                      code: data.details || data.type,
                    });
                  }
                  break;
                case HlsMod.ErrorTypes.MEDIA_ERROR:
                  if ((hls as HlsWithBudget).mediaRecoveryAttempts < 2) {
                    (hls as HlsWithBudget).mediaRecoveryAttempts++;
                    console.warn("[hls.js] Media error, recovering...", data);
                    hls.recoverMediaError();
                  } else {
                    setPlaybackError({
                      title: "این ویدیو قابل پخش نیست",
                      detail: "مرورگرت نتونست جریان ویدیو رو رمزگشایی کنه.",
                      code: data.details || data.type,
                    });
                  }
                  break;
                default:
                  // Previously this just destroyed the player, leaving a black
                  // rectangle and an endless spinner with no cause. Surface it.
                  console.error("[hls.js] Unrecoverable error:", data);
                  setPlaybackError({
                    title: "این ویدیو پخش نشد",
                    detail: data.details ? String(data.details).replace(/_/g, " ").toLowerCase() : "جریان پخش خراب است.",
                    code: data.details,
                  });
                  hls.destroy();
                  hlsRef.current = null;
                  break;
              }
            } else if (data.details === HlsMod.ErrorDetails.BUFFER_STALLED_ERROR) {
              hls.startLoad();
              if (v && !v.paused && v.readyState >= 2) {
                v.play().catch(() => {});
              }
            }
          });
          hls.loadSource(mediaSrc!);
          hls.attachMedia(v);
        } else if (v.canPlayType("application/vnd.apple.mpegurl")) {
          v.src = mediaSrc!; // Safari native HLS — ABR is internal, no manual level API
        }
      } else {
        v.src = mediaSrc!;
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [item?.id, mediaSrc, isPlayable, sourceEpoch]);

  // ---- reload the manifest (without disrupting anyone else) once a new
  // rendition becomes available; auto-switch only if *I* asked for it ----
  useEffect(() => {
    if (!latestNotify || !item) return;
    if ((latestNotify.type === "quality_ready" || latestNotify.type === "quality_partial_ready") && latestNotify.id === item.id) {
      const label = latestNotify.label as string;
      if (myRequestedLabels.current.has(label)) {
        const hls = hlsRef.current;
        const v = videoRef.current;
        if (hls && v) {
          const resumeAt = v.currentTime;
          const wasPlaying = !v.paused;
          const onParsed = () => {
            const idx = hls.levels.findIndex((l) => levelLabelFromUrl(l.url) === label);
            if (idx !== -1) hls.currentLevel = idx;
            try {
              v.currentTime = resumeAt;
            } catch {}
            if (wasPlaying) v.play().catch(() => {});
          };
          import("hls.js").then(({ default: HlsMod }) => {
            hls.once(HlsMod.Events.MANIFEST_PARSED, onParsed);
            hls.loadSource(mediaSrc!);
          });
        }
      } else {
        needsMenuRefresh.current = true;
      }
    }
  }, [latestNotify, item, mediaSrc]);

  function refreshMenuIfStale() {
    if (needsMenuRefresh.current && hlsRef.current && mediaSrc) {
      needsMenuRefresh.current = false;
      const hls = hlsRef.current;
      const v = videoRef.current;
      const resumeAt = v?.currentTime ?? 0;
      const wasPlaying = v ? !v.paused : false;
      import("hls.js").then(({ default: HlsMod }) => {
        hls.once(HlsMod.Events.MANIFEST_PARSED, () => {
          if (v) {
            try {
              v.currentTime = resumeAt;
            } catch {}
            if (wasPlaying) v.play().catch(() => {});
          }
        });
        hls.loadSource(mediaSrc);
      });
    }
  }

  // requestQuality: the ref write (myRequestedLabels.current.add) must not
  // happen during render. We split it: onClick sets a state flag, a useEffect
  // watches and does the ref write + API call outside of render.
  const [pendingQualityLabel, setPendingQualityLabel] = useState<string | null>(null);

  useEffect(() => {
    if (!pendingQualityLabel || !item) return;
    const label = pendingQualityLabel;
    startTransition(() => setPendingQualityLabel(null));
    api.requestQuality(item.id, label, myName)
      .then(() => {
        myRequestedLabels.current.add(label);
        showToast(`آماده‌سازی کیفیت ${label} شروع شد…`);
      })
      .catch((e: unknown) => {
        showToast(`${e instanceof Error ? e.message : "شروع آماده‌سازی این کیفیت ناموفق بود"}`);
      });
  }, [pendingQualityLabel, item, myName, showToast]);

  // ---- sync loop: nudge currentTime/play-state toward the shared position ----
  // Guards:
  // 1. Never seek before loadedmetadata (video.readyState < 1) — seeking on an
  //    HLS stream before the manifest is parsed causes a black frame + playback
  //    reset loop.
  // 2. Only seek when the drift exceeds the threshold (1.2s) — smaller jumps
  //    cause more stutter than they fix.
  // 3. Use a ref for expectedPosition so we always get the current value from
  //    the closure rather than a stale snapshot from the last render.
  const expectedPositionRef = useRef(expectedPosition);
  useEffect(() => { expectedPositionRef.current = expectedPosition; }, [expectedPosition]);

  // Refs for the end-of-media handler so its listeners can stay attached for
  // the life of the media element without re-binding on every state change.
  const itemRef = useRef(item);
  const playingRef = useRef(playing);
  const canNextRef = useRef(canNext);
  const canControlRef = useRef(canControl);
  const onNextRef = useRef(onNext);
  const requestControlRef = useRef(requestControl);
  const onPlaybackStateRef = useRef(onPlaybackState);
  useEffect(() => { itemRef.current = item; }, [item]);
  useEffect(() => { playingRef.current = playing; }, [playing]);
  useEffect(() => { canNextRef.current = canNext; }, [canNext]);
  useEffect(() => { canControlRef.current = canControl; }, [canControl]);
  useEffect(() => { onNextRef.current = onNext; }, [onNext]);
  useEffect(() => { requestControlRef.current = requestControl; }, [requestControl]);
  useEffect(() => { onPlaybackStateRef.current = onPlaybackState; }, [onPlaybackState]);

  useEffect(() => {
    const v = videoRef.current;
    if (!v || !item) return;
    v.playbackRate = rate;
    if (dubAudioRef.current && currentAudioTrackId) dubAudioRef.current.playbackRate = rate;

    function sync() {
      if (!v) return;
      // live streams: no shared position/pause — just keep local playback going
      if (item?.type === "live") {
        if (v.paused) v.play().catch(() => setBigPlay(true));
        return;
      }
      // Don't touch currentTime before the browser has parsed the metadata.
      if (v.readyState < 1) return;
      let expected = expectedPositionRef.current();
      if (isFinite(expected)) {
        const frontier = encodedFrontierRef.current(item);
        if (frontier !== Infinity) {
          const MARGIN = 3;
          if (expected > frontier - MARGIN) {
            if (Date.now() - lastFrontierWarn.current > 2500) {
              lastFrontierWarn.current = Date.now();
          showToast(`این بخش هنوز آماده نشده — انکد تا دقیقه ${formatTime(frontier)} پیش رفته.`);
            }
            expected = Math.max(0, frontier - MARGIN);
          }
        }
        // The shared playhead must NEVER be past the real end of this media.
        // The server has no idea how long the video is and keeps advancing
        // position while playing=true, so a position carried over from a
        // longer item (or from before a re-encode shortened the file) used to
        // be assigned straight to currentTime. The browser silently clamped
        // it to the end, fired `ended`, and the player stayed frozen on the
        // last frame forever — no error, no explanation, no way out.
        const mediaDuration = v.duration;
        if (isFinite(mediaDuration) && mediaDuration > 0 && expected >= mediaDuration) {
          expected = Math.max(0, mediaDuration - 0.05);
        }
        if (!v.seeking && Math.abs((v.currentTime || 0) - expected) > 1.2) {
          try { v.currentTime = expected; } catch {}
        }
      }
      if (playing && v.paused) {
        v.play().catch(() => setBigPlay(true));
      } else if (!playing && !v.paused) {
        v.pause();
      }
    }
    // Delay initial sync until after metadata — avoids the black-frame loop on
    // HLS sources where the first frames aren't buffered yet.
    const onReady = () => sync();
    if (v.readyState >= 1) {
      sync();
    } else {
      v.addEventListener("loadedmetadata", onReady, { once: true });
    }
    const t = setInterval(sync, 6000);
    return () => {
      clearInterval(t);
      v.removeEventListener("loadedmetadata", onReady);
    };
  }, [playing, rate, item, currentAudioTrackId, showToast]);

  // ---- video element event wiring ----
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    const onLoadedMeta = () => setDuration(v.duration || 0);
    const onTime = () => {
      if (!dragging) setCurTime(v.currentTime || 0);
      const track = currentSubIndex >= 0 ? v.textTracks[currentSubIndex] : null;
      if (track?.activeCues) {
        setActiveCueText(Array.from(track.activeCues).map((c) => (c as VTTCue).text.replace(/<[^>]+>/g, "")));
      } else {
        setActiveCueText([]);
      }
    };
    const onWaiting = () => setBuffering(true);
    // "watching" is driven by the media element's own play/pause events, so
    // the lounge couch reflects real playback. `onPause` also fires at end of
    // media, which is exactly right — a finished video is not "watching".
    const onPlaying = () => {
      setBuffering(false);
      setBigPlay(false);
      onPlaybackStateRef.current?.(true, itemRef.current?.id ?? null);
    };
    const onPause = () => {
      onPlaybackStateRef.current?.(false, itemRef.current?.id ?? null);
    };
    const onCanPlay = () => setBuffering(false);
    // A real media-element failure (codec unsupported, demux failure,
    // src unreachable after a reload). MediaError.code is the browser's own
    // code, surfaced verbatim so the message is never invented.
    const onMediaError = () => {
      const err = v.error;
      if (!err) return;
      const names: Record<number, string> = {
        1: "بارگذاری ویدیو متوقف شد.",
        2: "قطعی شبکه پخش رو قطع کرد.",
        3: "مرورگرت نتونست این ویدیو رو رمزگشایی کنه.",
        4: "مرورگرت این فرمت ویدیو رو پشتیبانی نمی‌کنه.",
      };
      setPlaybackError({
        title: "این ویدیو پخش نشد",
        detail: names[err.code] || "ویدیو با یه خطای ناشناخته متوقف شد.",
        code: `MediaError ${err.code}`,
      });
      setBuffering(false);
    };
    // THE end-of-media handler. Without this the player had no way to react
    // to finishing: it just sat on the last frame. Now the room reconciles
    // itself — advance to the next item when we're allowed to, otherwise
    // stop the shared playhead at the real end so the server stops counting
    // position past the end of the file.
    const onEnded = () => {
      setBuffering(false);
      setBigPlay(false);
      if (!itemRef.current) return;
      if (itemRef.current.type === "live") return;
      // Idempotent by construction: the only branch that doesn't flip
      // playing=false changes the current item, so a repeat `ended` event
      // finds nothing left to do. No extra bookkeeping needed.
      if (!playingRef.current) return;
      if (canControlRef.current && canNextRef.current) {
        // Shared watch-together playlist: run the room on to the next item.
        onNextRef.current();
        return;
      }
      // Either we're a viewer (can't change the playlist) or this was the last
      // item. Nothing left to play, yet the shared clock keeps running because
      // the server has no idea how long the file is — stop it at the truth so
      // the playhead can never drift past the end of the media again.
      const realEnd = v.duration && isFinite(v.duration) ? v.duration : v.currentTime || 0;
      requestControlRef.current("pause", { at: realEnd });
    };
    v.addEventListener("loadedmetadata", onLoadedMeta);
    v.addEventListener("timeupdate", onTime);
    v.addEventListener("waiting", onWaiting);
    v.addEventListener("playing", onPlaying);
    v.addEventListener("pause", onPause);
    v.addEventListener("canplay", onCanPlay);
    v.addEventListener("error", onMediaError);
    v.addEventListener("ended", onEnded);
    return () => {
      v.removeEventListener("loadedmetadata", onLoadedMeta);
      v.removeEventListener("timeupdate", onTime);
      v.removeEventListener("waiting", onWaiting);
      v.removeEventListener("playing", onPlaying);
      v.removeEventListener("pause", onPause);
      v.removeEventListener("canplay", onCanPlay);
      v.removeEventListener("error", onMediaError);
      v.removeEventListener("ended", onEnded);
    };
  }, [dragging, currentSubIndex]);

  // ---- backup curTime update interval (ensures progress bar keeps
  // updating during HLS playback even if timeupdate events stall) ----
  useEffect(() => {
    const v = videoRef.current;
    if (!v || item?.type === "live") return;
    const t = setInterval(() => {
      if (!dragging) setCurTime(v.currentTime || 0);
    }, 250);
    return () => clearInterval(t);
  }, [dragging, item?.type]);

  // ---- stall detection: reload HLS if video freezes for ~6s while playing ----
  useEffect(() => {
    const v = videoRef.current;
    if (!v || item?.type === "live" || !item?.src) return;
    let lastPos = v.currentTime || 0;
    let stallCount = 0;
    const t = setInterval(() => {
      if (!playing) { lastPos = v.currentTime || 0; stallCount = 0; return; }
      const pos = v.currentTime || 0;
      if (pos === lastPos && v.readyState > 1 && !v.seeking) {
        stallCount++;
        if (stallCount >= 3) {
          const hls = hlsRef.current;
          if (hls) {
            v.currentTime = pos + 0.01;
            stallCount = 0;
          }
        }
      } else {
        stallCount = 0;
      }
      lastPos = pos;
    }, 2000);
    return () => clearInterval(t);
  }, [playing, item?.type, item?.src]);

  // ---- sync track.mode with currentSubIndex so the VTT file actually loads ----
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    for (let i = 0; i < v.textTracks.length; i++) {
      v.textTracks[i].mode = i === currentSubIndex ? (inPip ? "showing" : "hidden") : "disabled";
    }
  }, [currentSubIndex, item?.subtitles, inPip]);

  // ---- dub audio track sync ----
  useEffect(() => {
    const v = videoRef.current;
    const a = dubAudioRef.current;
    if (!v || !a) return;
    if (!currentAudioTrackId) {
      v.muted = false;
      a.pause();
      return;
    }
    v.muted = true;
    a.currentTime = v.currentTime || 0;
    a.playbackRate = v.playbackRate;
    a.volume = volume;
    if (!v.paused) a.play().catch(() => {});
    const onPlay = () => a.paused && a.play().catch(() => {});
    const onPause = () => a.pause();
    const onSeeked = () => {
      a.currentTime = v.currentTime;
    };
    v.addEventListener("play", onPlay);
    v.addEventListener("pause", onPause);
    v.addEventListener("seeked", onSeeked);
    const drift = setInterval(() => {
      if (!a.paused && !v.paused && Math.abs(a.currentTime - v.currentTime) > 0.3) a.currentTime = v.currentTime;
    }, 2000);
    return () => {
      v.removeEventListener("play", onPlay);
      v.removeEventListener("pause", onPause);
      v.removeEventListener("seeked", onSeeked);
      clearInterval(drift);
    };
  }, [currentAudioTrackId, volume]);


  // ---- fullscreen ---------------------------------------------------------
  // The player is always the same DOM node; fullscreen just re-styles it to
  // cover the viewport (fixed, no transformed ancestors anywhere above it) and
  // *also* asks the platform for real fullscreen when it can:
  //   • browsers / Android webview → Fullscreen API, then lock to landscape
  //   • Tauri desktop              → OS window fullscreen
  //   • iOS & anything that refuses → CSS-only; on a phone held upright we turn
  //     the player 90° ("forced landscape") so it still fills the screen.
  const toggleFullscreen = useCallback(async () => {
    type Lockable = ScreenOrientation & { lock?: (o: string) => Promise<void> };
    const orient = window.screen?.orientation as Lockable | undefined;
    const el = wrapRef.current as (HTMLDivElement & { webkitRequestFullscreen?: () => Promise<void> }) | null;

    if (fullscreen) {
      setFullscreen(false);
      setForceLandscape(false);
      domFsRef.current = false;
      try {
        if (document.fullscreenElement) await document.exitFullscreen();
      } catch {}
      try {
        orient?.unlock?.();
      } catch {}
      void setNativeFullscreen(false).catch(() => {});
      return;
    }

    if (floatMode === "native") await exitNative();
    setFloatMode((m) => (m === "dom" ? "none" : m));
    setFullscreen(true);
    // let React commit the fixed layout first so the browser fullscreens the final box
    requestAnimationFrame(async () => {
      try {
        if (isTauri() && !isMobileOS()) {
          await setNativeFullscreen(true);
        } else if (el?.requestFullscreen) {
          await el.requestFullscreen({ navigationUI: "hide" });
          domFsRef.current = true;
        } else if (el?.webkitRequestFullscreen) {
          await el.webkitRequestFullscreen();
          domFsRef.current = true;
        }
      } catch {
        /* CSS fullscreen still works */
      }
      if (touch) {
        // lock() can reject, resolve without turning anything, or (on some
        // webviews) never settle at all — so race it against a timer, then
        // trust what the viewport actually looks like.
        try {
          if (orient?.lock) await Promise.race([orient.lock("landscape"), new Promise((r) => setTimeout(r, 500))]);
        } catch {}
        await new Promise((r) => setTimeout(r, 150));
        if (window.innerHeight > window.innerWidth) setForceLandscape(true);
      }
    });
  }, [fullscreen, floatMode, exitNative, touch]);

  const rotateScreen = useCallback(async () => {
    type Lockable = ScreenOrientation & { lock?: (o: string) => Promise<void> };
    const orient = window.screen?.orientation as Lockable | undefined;
    if (orient?.lock && document.fullscreenElement) {
      try {
        await orient.lock(orient.type.startsWith("landscape") ? "portrait" : "landscape");
        setForceLandscape(false);
        return;
      } catch {}
    }
    setForceLandscape((f) => !f);
  }, []);

  useEffect(() => {
    function onFsChange() {
      // Escape (or the system back gesture) left browser fullscreen: follow it.
      if (!document.fullscreenElement && domFsRef.current) {
        domFsRef.current = false;
        setFullscreen(false);
        setForceLandscape(false);
        try {
          (window.screen?.orientation as ScreenOrientation & { unlock?: () => void })?.unlock?.();
        } catch {}
      }
    }
    document.addEventListener("fullscreenchange", onFsChange);
    return () => document.removeEventListener("fullscreenchange", onFsChange);
  }, []);

  // keep the screen awake while a video is actually playing
  useEffect(() => {
    if (!playing || !item) return;
    type WL = { request: (t: "screen") => Promise<{ release: () => Promise<void> }> };
    const wl = (navigator as Navigator & { wakeLock?: WL }).wakeLock;
    if (!wl) return;
    let lock: { release: () => Promise<void> } | null = null;
    let cancelled = false;
    const acquire = () =>
      wl
        .request("screen")
        .then((l) => {
          if (cancelled) void l.release();
          else lock = l;
        })
        .catch(() => {});
    void acquire();
    const onVis = () => document.visibilityState === "visible" && !lock && void acquire();
    document.addEventListener("visibilitychange", onVis);
    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", onVis);
      void lock?.release();
    };
  }, [playing, item]);

  useEffect(() => {
    // Map each configured key to its action (rebindable in Settings).
    const keyToAction: Record<string, ShortcutAction> = {};
    for (const [action, key] of Object.entries(sc)) keyToAction[key] = action as ShortcutAction;

    function onKey(e: KeyboardEvent) {
      const tag = (e.target as HTMLElement).tagName?.toLowerCase();
      const type = (e.target as HTMLInputElement).type?.toLowerCase();
      if (tag === "textarea" || tag === "select") return;
      if (tag === "input" && type !== "range") return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const v = videoRef.current;
      const action = keyToAction[e.key];

      switch (action) {
        case "playPause":
          e.preventDefault();
          if (item && item.type !== "live") togglePlay();
          return;
        case "seekBack":
          e.preventDefault();
          if (item && item.type !== "live" && v) seek(Math.max(0, v.currentTime - 10));
          return;
        case "seekForward":
          e.preventDefault();
          if (item && item.type !== "live" && v) seek(Math.min(v.duration || 1e9, v.currentTime + 10));
          return;
        case "volumeUp":
          e.preventDefault();
          changeVolume((x) => Math.min(1, x + 0.05));
          return;
        case "volumeDown":
          e.preventDefault();
          changeVolume((x) => Math.max(0, x - 0.05));
          return;
        case "mute":
          changeMuted((m) => !m);
          return;
        case "fullscreen":
          toggleFullscreen();
          return;
        case "prev":
          if (canPrev) onPrev();
          return;
        case "next":
          if (canNext) onNext();
          return;
        case "pip":
          togglePip();
          return;
        case "shortcuts":
          onOpenShortcuts();
          return;
        case "settings":
          onOpenSettings?.();
          return;
        default:
          break;
      }

      // Legacy aliases kept for muscle memory (not rebindable).
      switch (e.key) {
        case "Escape":
          if (fullscreen) {
            e.preventDefault();
            void toggleFullscreen();
          } else if (floatMode === "native") {
            e.preventDefault();
            void exitNative();
          }
          break;
        case "k":
          e.preventDefault();
          if (item && item.type !== "live") togglePlay();
          break;
        case "j":
          e.preventDefault();
          if (item && item.type !== "live" && v) seek(Math.max(0, v.currentTime - 10));
          break;
        case "l":
          e.preventDefault();
          if (item && item.type !== "live" && v) seek(Math.min(v.duration || 1e9, v.currentTime + 10));
          break;
        case "d":
        case "D":
          setShowDebug((s) => !s);
          break;
        case "M":
          changeMuted((m) => !m);
          break;
        case "F":
          toggleFullscreen();
          break;
        case "P":
          if (canPrev) onPrev();
          break;
        case "N":
          if (canNext) onNext();
          break;
        case "?":
          onOpenShortcuts();
          break;
        case "0":
        case "1":
        case "2":
        case "3":
        case "4":
        case "5":
        case "6":
        case "7":
        case "8":
        case "9":
          if (item && item.type !== "live" && v && isFinite(v.duration)) {
            e.preventDefault();
            seek(v.duration * (parseInt(e.key, 10) / 10));
          }
          break;
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [item, togglePlay, seek, onOpenShortcuts, onOpenSettings, canPrev, canNext, onPrev, onNext, fullscreen, floatMode, exitNative, toggleFullscreen, togglePip, sc, changeVolume, changeMuted]);

  useEffect(() => {
    const v = videoRef.current;
    if (v) {
      v.volume = volume;
      v.muted = muted;
    }
  }, [volume, muted]);

  // commands from the right-click menu (fullscreen / floating live in this component)
  const cmdRef = useRef({ toggleFullscreen, togglePip });
  cmdRef.current = { toggleFullscreen, togglePip };
  useEffect(() => {
    const on = (e: Event) => {
      const d = (e as CustomEvent<string>).detail;
      if (d === "fullscreen") void cmdRef.current.toggleFullscreen();
      if (d === "pip") void cmdRef.current.togglePip();
    };
    window.addEventListener("player:cmd", on);
    return () => window.removeEventListener("player:cmd", on);
  }, []);

  const processing = !item || item.status === "queued" || item.status === "encoding" || item.status === "error";
  // The item can be marked `complete` yet have no playable src: without an
  // explicit state the player used to render a bare black rectangle.
  const noPlayableSource = Boolean(item) && !processing && !isPlayable;
  const def = defaultRendition(item);
  const defProgress = def ? transcodeProgress[`${item?.id}:${def.label}`] : undefined;
  const totalDuration = defProgress?.duration ?? duration;
  const encodedSeconds = defProgress?.encoded_seconds ?? 0;
  const isLive = item?.type === "live";
  const blocked = processing || !!playbackError || noPlayableSource;

  // ---- "lights down": controls fade away while a film plays and nobody touches anything
  const [menuOpen, setMenuOpen] = useState(false);
  const [touchHide, setTouchHide] = useState(false);
  const idle = useIdle(3000, playing && !blocked && !menuOpen && !dragging);
  const chromeShown = !(idle || touchHide) || !playing || blocked || menuOpen;
  const chromeShownRef = useRef(chromeShown);
  chromeShownRef.current = chromeShown;
  const placement: "overlay" | "below" = vp.phone && vp.portrait && !fullscreen && floatMode === "none" ? "below" : "overlay";
  const mini = floating || nativeMini;

  const controlsRef = useRef<HTMLDivElement>(null);
  const topBarRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const show = chromeShown;
    if (controlsRef.current) {
      run(controlsRef.current, { opacity: show ? 1 : 0, translateY: show ? 0 : 14, duration: show ? 320 : 420, ease: "outQuart" });
      controlsRef.current.style.pointerEvents = show ? "auto" : "none";
    }
    if (topBarRef.current) {
      run(topBarRef.current, { opacity: show ? 1 : 0, translateY: show ? 0 : -10, duration: show ? 320 : 420, ease: "outQuart" });
      topBarRef.current.style.pointerEvents = show ? "auto" : "none";
    }
  }, [chromeShown, placement, fullscreen, mini]);

  // pop the floating window in when it opens
  useEffect(() => {
    if (floating) popIn(floatBoxRef.current, { from: "100% 100%", y: 20 });
  }, [floating]);

  // ---- ambient light: the screen's own colours spill onto the room ----
  const glowRef = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    if (fullscreen || mini) return;
    const v = videoRef.current;
    const c = glowRef.current;
    if (!v || !c) return;
    const ctx = c.getContext("2d", { willReadFrequently: true });
    if (!ctx) return;
    const W = 48;
    const H = 27;
    c.width = W;
    c.height = H;
    let raf = 0;
    let last = 0;
    let lastColor = 0;
    let tainted = false;
    const tick = (t: number) => {
      raf = requestAnimationFrame(tick);
      if (t - last < 130) return;
      last = t;
      if (v.readyState < 2 || v.paused) return;
      try {
        ctx.drawImage(v, 0, 0, W, H);
      } catch {
        return;
      }
      if (tainted || t - lastColor < 1200) return;
      lastColor = t;
      try {
        const d = ctx.getImageData(0, 0, W, H).data;
        let r = 0, g = 0, b = 0;
        const n = d.length / 4;
        for (let i = 0; i < d.length; i += 4) {
          r += d[i];
          g += d[i + 1];
          b += d[i + 2];
        }
        r /= n; g /= n; b /= n;
        const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
        if (lum > 24) {
          const boost = 1.25;
          document.documentElement.style.setProperty(
            "--screen-glow",
            `rgb(${Math.min(255, r * boost) | 0}, ${Math.min(255, g * boost) | 0}, ${Math.min(255, b * boost) | 0})`
          );
        }
      } catch {
        tainted = true; // cross-origin media without CORS: the blurred canvas still works, colour sampling doesn't
      }
    };
    raf = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf);
      document.documentElement.style.removeProperty("--screen-glow");
    };
  }, [fullscreen, mini, item?.id]);

  // ---- touch gestures: tap = lights, double-tap sides = ±10s, double-tap middle = play/pause ----
  const surfaceRef = useRef<HTMLDivElement>(null);
  const lastPointerType = useRef<string>("mouse");
  const wasShownAtDown = useRef(true);
  const lastTap = useRef<{ t: number; side: number } | null>(null);
  const tapTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [skipFlash, setSkipFlash] = useState<{ key: number; side: -1 | 1 } | null>(null);

  const skip = useCallback(
    (delta: number) => {
      const v = videoRef.current;
      if (!v || !item || item.type === "live") return;
      seek(Math.min(v.duration || 1e9, Math.max(0, v.currentTime + delta)));
    },
    [seek, item]
  );

  const onSurfaceDown = (e: React.PointerEvent) => {
    lastPointerType.current = e.pointerType;
    wasShownAtDown.current = chromeShownRef.current;
    setTouchHide(false);
  };
  const onSurfaceUp = (e: React.PointerEvent) => {
    if (e.pointerType === "mouse" || blocked || !surfaceRef.current) return;
    const p = localPoint(e, surfaceRef.current, rotated);
    const frac = p.w ? p.x / p.w : 0.5;
    const side = frac < 0.33 ? -1 : frac > 0.66 ? 1 : 0;
    const now = Date.now();
    const lt = lastTap.current;
    if (lt && now - lt.t < 320 && lt.side === side) {
      lastTap.current = null;
      if (tapTimer.current) clearTimeout(tapTimer.current);
      if (isLive) return;
      if (side === 0) {
        togglePlay();
        ripple(surfaceRef.current, p.x, p.y);
      } else {
        skip(side * 10);
        setSkipFlash({ key: now, side: side as -1 | 1 });
        ripple(surfaceRef.current, p.x, p.y);
      }
      return;
    }
    lastTap.current = { t: now, side };
    if (tapTimer.current) clearTimeout(tapTimer.current);
    // a single tap only toggles the lights once we know it isn't half of a double-tap
    tapTimer.current = setTimeout(() => {
      if (wasShownAtDown.current && playing) setTouchHide(true);
    }, 300);
  };
  const onSurfaceClick = (e: React.MouseEvent) => {
    if (lastPointerType.current !== "mouse" || blocked) return;
    togglePlay();
    if (surfaceRef.current && !isLive) {
      const p = localPoint(e, surfaceRef.current, rotated);
      ripple(surfaceRef.current, p.x, p.y, "rgba(247,195,90,.22)");
    }
  };

  // ---- derived props for the control bar ----
  const bufferedEnd = (() => {
    const v = videoRef.current;
    if (!v) return 0;
    const b = v.buffered;
    for (let i = 0; i < b.length; i++) if (curTime >= b.start(i) - 0.5 && curTime <= b.end(i) + 0.5) return b.end(i);
    return 0;
  })();
  const lastScrubSeek = useRef(0);
  const qualityOptions: QualityOption[] = (
    item?.renditions?.length
      ? [...item.renditions].sort((a, b) => b.height - a.height)
      : levels.map((l) => ({ ...l, status: "ready" as Rendition["status"] }))
  ).map((r) => {
    const idx = levels.findIndex((l) => l.label === r.label);
    if (!r.status || r.status === "ready" || r.status === "complete") {
      return {
        key: r.label,
        label: r.label,
        state: "ready",
        active: idx !== -1 && currentLevel === idx,
        onPick: () => {
          if (idx !== -1 && hlsRef.current) hlsRef.current.currentLevel = idx;
        },
      } satisfies QualityOption;
    }
    return {
      key: r.label,
      label: r.label,
      state: r.status === "pending" ? "pending" : r.status === "error" ? "error" : "busy",
      active: false,
      onPick: () => {
        if (r.status === "pending" || r.status === "error") setPendingQualityLabel(r.label);
      },
    } satisfies QualityOption;
  });

  const title = item ? prettyTitle(item.title) : "";
  const controls = (
    <PlayerControls
      rotated={rotated}
      live={isLive}
      placement={placement}
      touch={touch}
      title={title}
      playing={playing}
      onTogglePlay={togglePlay}
      curTime={curTime}
      duration={isFinite(totalDuration) && totalDuration > 0 ? totalDuration : duration}
      bufferedEnd={bufferedEnd}
      encodedSeconds={encodedSeconds}
      onScrubStart={() => setDragging(true)}
      onScrub={(t) => {
        dragValueRef.current = t;
        setCurTime(t);
        const now = performance.now();
        if (now - lastScrubSeek.current > 140) {
          lastScrubSeek.current = now;
          try {
            if (videoRef.current) videoRef.current.currentTime = t;
          } catch {}
        }
      }}
      onScrubEnd={(t) => {
        setDragging(false);
        seek(t);
      }}
      onSkip={skip}
      canPrev={canPrev}
      canNext={canNext}
      onPrev={onPrev}
      onNext={onNext}
      shuffle={shuffle}
      canShuffle={canShuffle}
      onToggleShuffle={onToggleShuffle}
      volume={volume}
      muted={muted}
      onVolume={(v) => {
        changeVolume(v);
        if (v > 0) changeMuted(false);
      }}
      onToggleMute={() => changeMuted((m) => !m)}
      subtitles={(item?.subtitles ?? []).map((s) => ({ id: s.id, label: s.label }))}
      subIndex={currentSubIndex}
      onSub={setCurrentSubIndex}
      onOpenSubStyle={onGoToSubStyle}
      audioTracks={(item?.audio_tracks ?? []).map((t) => ({ id: t.id, label: t.label }))}
      audioId={currentAudioTrackId}
      onAudio={(id) => {
        setCurrentAudioTrackId(id);
        const t = item?.audio_tracks?.find((x) => x.id === id);
        if (t && dubAudioRef.current) dubAudioRef.current.src = t.url;
      }}
      rate={rate}
      canSpeed={canSpeed}
      onRate={(r) => requestControl("rate", { rate: r })}
      autoQuality={currentLevel === -1}
      onAutoQuality={() => {
        if (hlsRef.current) hlsRef.current.currentLevel = -1;
        setCurrentLevel(-1);
      }}
      qualities={qualityOptions}
      onOpenSettings={onOpenSettings}
      floatSupported={Boolean(item)}
      floating={mini || inPip}
      onToggleFloat={togglePip}
      fullscreen={fullscreen}
      onToggleFullscreen={toggleFullscreen}
      onRotate={rotateScreen}
      onMenuOpenChange={(open) => {
        setMenuOpen(open);
        if (open) refreshMenuIfStale();
      }}
    />
  );

  const wrapStyle: React.CSSProperties = fullscreen
    ? { position: "fixed", inset: 0, zIndex: 9999 }
    : nativeMini
      ? { position: "fixed", inset: 0, zIndex: 9999 }
      : floating
        ? { position: "fixed", right: floatPos.x, bottom: `calc(${floatPos.y}px + env(safe-area-inset-bottom, 0px))`, width: "min(340px, 78vw)", zIndex: 9997 }
        : {};
  const subScale = fullscreen ? 1.3 : mini ? 0.55 : 1;
  const subLift = chromeShown && placement === "overlay" && !mini ? (fullscreen ? 96 : 72) : 0;
  const showCenterPlay = !blocked && !buffering && (!playing || bigPlay) && !isLive && !mini;
  const pctDone = totalDuration > 0 ? Math.min(100, (curTime / totalDuration) * 100) : 0;

  const [debugInfo, setDebugInfo] = useState<Record<string, string> | null>(null);
  void debugInfo;
  void setDebugInfo;

  return (
    <>
      <div className="relative w-full">
        {(fullscreen || mini) && !nativeMini && (
          <div className="relative flex aspect-video w-full flex-col items-center justify-center gap-3 rounded-md border border-dashed border-white/15 bg-black/40 text-center text-white/70">
            <Clapperboard className="h-8 w-8 text-[color:var(--color-amber)]" />
            <p className="px-6 text-[13px]">{fullscreen ? "الان تمام‌صفحه داری می‌بینی" : "فیلم شناور شده و داره روی صفحه پخش می‌شه"}</p>
            {floating && (
              <button
                type="button"
                onClick={() => setFloatMode("none")}
                className="rounded-full bg-[color:var(--color-amber)] px-4 py-2 text-[13px] font-bold text-black"
              >
                برگردونش روی پرده
              </button>
            )}
          </div>
        )}

        <div
          ref={(n) => {
            wrapRef.current = n;
            floatBoxRef.current = n;
          }}
          data-context-kind="player"
          tabIndex={-1}
          style={wrapStyle}
          className={`isolate flex flex-col outline-none ${
            fullscreen || nativeMini
              ? `bg-black ${idle && playing && !menuOpen ? "cursor-none" : ""}`
              : floating
                ? "overflow-hidden rounded-2xl border border-white/10 bg-black shadow-[0_24px_70px_-10px_rgba(0,0,0,0.85)]"
                : "relative"
          } ${fullscreen || nativeMini || floating ? "overflow-hidden" : ""}`}
        >
          {/* Forced landscape turns this inner box; the outer node stays upright because browsers force `transform: none` on a real fullscreen element. */}
          <div
            className={rotated ? "absolute" : "contents"}
            style={rotated ? { top: 0, left: 0, width: "100dvh", height: "100dvw", transform: "translateX(100dvw) rotate(90deg)", transformOrigin: "top left" } : undefined}
          >
          {/* ambient light behind the screen */}
          <canvas
            ref={glowRef}
            aria-hidden
            className="pointer-events-none absolute -inset-[5%] -z-10 h-[110%] w-[110%] opacity-60"
            style={{ filter: "blur(34px) saturate(1.7)", display: fullscreen || mini ? "none" : "block" }}
          />

          {/* ===== the screen ===== */}
          <div
            ref={surfaceRef}
            className={
              fullscreen || nativeMini
                ? "absolute inset-0 overflow-hidden bg-black"
                : "relative aspect-video w-full overflow-hidden bg-black " + (floating ? "" : "rounded-[5px]")
            }
            style={
              fullscreen || mini
                ? undefined
                : { boxShadow: "0 0 0 1px rgba(255,255,255,.07), 0 40px 140px -30px color-mix(in oklab, var(--screen-glow, #f7c35a) 60%, transparent)" }
            }
          >
            <video ref={videoRef} playsInline preload="metadata" className="absolute inset-0 h-full w-full object-contain">
              {item?.subtitles?.map((s) => (
                <track key={s.id} kind="subtitles" label={s.label} srcLang={s.lang || "fa"} src={mediaUrl(s.url)} default={false} />
              ))}
            </video>
            <audio ref={dubAudioRef} preload="auto" />

            {/* gesture layer */}
            <div
              className="absolute inset-0 z-[2]"
              onPointerDown={onSurfaceDown}
              onPointerUp={onSurfaceUp}
              onClick={onSurfaceClick}
              onDoubleClick={(e) => e.preventDefault()}
            />

            {/* subtitles */}
            {!processing && !inPip && activeCueText.length > 0 && (
              <div
                className="pointer-events-none absolute left-0 right-0 z-[3] flex flex-col items-center gap-1 px-6 text-center transition-[bottom] duration-300"
                style={{ bottom: subStyle.offset * (mini ? 0.4 : 1) + subLift }}
              >
                {activeCueText.map((line, i) => (
                  <div
                    key={i}
                    className="max-w-[92%] rounded-lg px-3.5 py-1"
                    style={{
                      fontFamily: subStyle.font,
                      fontSize: subStyle.size * subScale,
                      fontWeight: subStyle.bold ? 700 : 400,
                      color: subStyle.color,
                      background: `rgba(0,0,0,${subStyle.bgOpacity / 100})`,
                      lineHeight: 1.45,
                      textShadow: subStyle.outline ? "0 0 3px rgba(0,0,0,.95), 0 0 7px rgba(0,0,0,.8)" : "none",
                    }}
                  >
                    {line}
                  </div>
                ))}
              </div>
            )}

            {isLive && (
              <div
                className="absolute right-3 top-3 z-[5] flex items-center gap-1.5 rounded-full bg-[color:var(--color-coral)] px-3 py-1 text-[11.5px] font-bold text-white shadow-lg"
                style={{ animation: "pulse-live 1.8s infinite" }}
              >
                <Radio className="h-3.5 w-3.5" /> زنده
              </div>
            )}

            {buffering && !blocked && (
              <div className="pointer-events-none absolute inset-0 z-[4] flex items-center justify-center">
                <div className="h-12 w-12 rounded-full border-[3px] border-white/15" style={{ borderTopColor: "var(--color-amber)", animation: "spin .9s linear infinite" }} />
              </div>
            )}

            {showCenterPlay && <CenterPlay onClick={() => { setBigPlay(false); togglePlay(); }} />}
            {skipFlash && <SkipFlash key={skipFlash.key} side={skipFlash.side} onDone={() => setSkipFlash(null)} />}

            {/* friendly empty / loading / error states */}
            {processing && (
              <div className="absolute inset-0 z-[6] flex flex-col items-center justify-center gap-3 bg-[color:var(--color-bg)]/92 px-6 text-center">
                {!item ? (
                  <EmptyScreen onGoToAdd={onGoToAdd} />
                ) : item.status === "error" ? (
                  <>
                    <TriangleAlert className="h-11 w-11 text-[color:var(--color-coral)]" />
                    <p className="display text-[20px] text-white">ای بابا! این یکی خراب شد</p>
                    <p className="max-w-[380px] text-[13px] text-white/60">{item.error || "تبدیل ویدیو با خطا روبه‌رو شد. یه فایل یا لینک دیگه امتحان کن."}</p>
                  </>
                ) : (
                  <>
                    <Loader2 className="h-10 w-10 animate-spin text-[color:var(--color-amber)]" />
                    <p className="display text-[20px] text-white">فیلم داره آماده می‌شه…</p>
                    <p className="max-w-[420px] truncate text-[13px] text-white/60" dir="auto">«{prettyTitle(item.title)}»</p>
                    <div className="flex w-[70%] max-w-[340px] items-center gap-2.5" dir="ltr">
                      <div className="h-2 flex-1 overflow-hidden rounded-full bg-white/10">
                        <div
                          className="h-full rounded-full transition-all duration-500"
                          style={{ width: `${defProgress?.pct ?? 0}%`, background: "linear-gradient(90deg, var(--color-plum-soft), var(--color-amber))" }}
                        />
                      </div>
                      <span className="font-mono text-xs text-[color:var(--color-amber)]">{defProgress?.pct ?? 0}%</span>
                    </div>
                    <p className="text-[12px] text-white/45">معمولاً تا ۲ دقیقه‌ی دیگه پخشش شروع می‌شه 🍿</p>
                  </>
                )}
              </div>
            )}

            {noPlayableSource && (
              <div role="alert" className="absolute inset-0 z-[7] flex flex-col items-center justify-center gap-3 bg-[color:var(--color-bg)] px-6 text-center">
                <Film className="h-11 w-11 text-white/60" />
                <p className="display text-[20px] text-white">این فیلم فایل پخش ندارد</p>
                <p className="max-w-[420px] text-[13px] text-white/60" dir="auto">«{prettyTitle(item?.title)}» آماده است ولی هیچ فایل قابل پخشی برایش ساخته نشده.</p>
                {onGoToAdd && (
                  <button onClick={onGoToAdd} className="rounded-full bg-[color:var(--color-amber)] px-5 py-2 text-[13px] font-bold text-black">
                    یه فیلم دیگه اضافه کن
                  </button>
                )}
              </div>
            )}

            {playbackError && (
              <div role="alert" aria-live="assertive" className="absolute inset-0 z-[7] flex flex-col items-center justify-center gap-3 bg-[color:var(--color-bg)] px-6 text-center">
                <TriangleAlert className="h-11 w-11 text-[color:var(--color-coral)]" />
                <p className="display text-[20px] text-white">{playbackError.title}</p>
                <p className="max-w-[420px] text-[13px] text-white/60">{playbackError.detail}</p>
                {playbackError.code && <code className="rounded-md bg-white/5 px-2 py-1 font-mono text-[11px] text-white/50">{playbackError.code}</code>}
                <div className="mt-1 flex items-center gap-2">
                  <button onClick={retryPlayback} className="flex items-center gap-1.5 rounded-full bg-[color:var(--color-amber)] px-5 py-2 text-[13px] font-bold text-black">
                    <RotateCcw className="h-4 w-4" />
                    دوباره امتحان کن
                  </button>
                  {onGoToAdd && (
                    <button onClick={onGoToAdd} className="rounded-full border border-white/20 px-5 py-2 text-[13px] text-white/80 hover:bg-white/10">
                      فیلم دیگه
                    </button>
                  )}
                </div>
              </div>
            )}

            {toast && (
              <div
                className="pointer-events-none absolute left-1/2 z-20 -translate-x-1/2 rounded-full border border-[color:var(--color-amber)]/40 bg-black/80 px-4 py-2 text-[12px] text-white backdrop-blur"
                style={{ bottom: placement === "overlay" ? 110 : 16 }}
              >
                {toast}
              </div>
            )}

            {/* fullscreen top strip: where am I, and a clear way out */}
            {fullscreen && (
              <div
                ref={topBarRef}
                dir="rtl"
                className="absolute inset-x-0 top-0 z-[20] flex items-center gap-3 bg-gradient-to-b from-black/85 via-black/40 to-transparent px-4 pb-8 pt-3"
                style={{ paddingTop: "max(12px, env(safe-area-inset-top, 0px))" }}
              >
                <button
                  type="button"
                  onClick={toggleFullscreen}
                  aria-label="خروج از تمام‌صفحه"
                  className="hit flex h-10 w-10 items-center justify-center rounded-full bg-white/10 text-white hover:bg-white/20"
                >
                  <X className="h-5 w-5" />
                </button>
                <span className="min-w-0 flex-1 truncate text-[14px] font-semibold text-white" dir="auto">{title}</span>
              </div>
            )}

            {/* mini-player strip (in-page float and the Tauri mini window) */}
            {mini && (
              <>
                <div
                  {...(nativeMini ? { "data-tauri-drag-region": "" } : {})}
                  onPointerDown={floating ? onFloatDown : undefined}
                  onPointerMove={floating ? onFloatMove : undefined}
                  onPointerUp={floating ? onFloatUp : undefined}
                  onPointerCancel={floating ? onFloatUp : undefined}
                  dir="rtl"
                  className="absolute inset-x-0 top-0 z-[30] flex touch-none cursor-grab items-center gap-1 bg-gradient-to-b from-black/85 to-transparent px-2 pb-5 pt-1.5 active:cursor-grabbing"
                >
                  <GripVertical className="h-4 w-4 shrink-0 text-white/50" {...(nativeMini ? { "data-tauri-drag-region": "" } : {})} />
                  <span className="min-w-0 flex-1 truncate text-[11.5px] text-white/85" dir="auto" {...(nativeMini ? { "data-tauri-drag-region": "" } : {})}>
                    {title || "پخش زنده"}
                  </span>
                  {!isLive && (
                    <button
                      type="button"
                      aria-label={playing ? "توقف" : "پخش"}
                      onPointerDown={(e) => e.stopPropagation()}
                      onClick={togglePlay}
                      className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-white hover:bg-white/15"
                    >
                      {playing ? <Pause className="h-3.5 w-3.5 fill-current" /> : <Play className="h-3.5 w-3.5 fill-current" />}
                    </button>
                  )}
                  <button
                    type="button"
                    aria-label="برگرد به پرده"
                    onPointerDown={(e) => e.stopPropagation()}
                    onClick={togglePip}
                    className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-white hover:bg-white/15"
                  >
                    <Maximize2 className="h-3.5 w-3.5" />
                  </button>
                </div>
                {!isLive && (
                  <div className="absolute inset-x-0 bottom-0 z-[30] h-[3px] bg-white/15">
                    <div className="h-full" style={{ width: `${pctDone}%`, background: "linear-gradient(90deg, var(--color-plum-soft), var(--color-amber))" }} />
                  </div>
                )}
              </>
            )}

            {/* overlay controls (desktop, landscape, fullscreen) */}
            {!mini && !blocked && placement === "overlay" && (
              <div
                ref={controlsRef}
                className="absolute inset-x-0 bottom-0 z-[20] bg-gradient-to-t from-black/90 via-black/55 to-transparent pt-10"
                style={fullscreen ? { paddingBottom: "env(safe-area-inset-bottom, 0px)" } : undefined}
              >
                {controls}
              </div>
            )}
          </div>

          {/* controls under the screen on portrait phones: big, always reachable, never covering the picture */}
          {!mini && !blocked && placement === "below" && (
            <>
              <div className="mt-2 rounded-2xl bg-white/[0.05]">{controls}</div>
              <div dir="rtl" className="px-1 pt-2">
                <h2 className="truncate text-[15px] font-bold text-white" dir="auto">{title}</h2>
                {item?.added_by && <p className="mt-0.5 text-[12px] text-white/45">{item.added_by} اضافه‌ش کرده</p>}
              </div>
            </>
          )}
          </div>
        </div>
      </div>

      {showDebug && (
        <div className="fixed bottom-4 left-4 z-[99999] rounded-xl border border-[color:var(--color-border)] bg-black/85 p-3 font-mono text-[11px] text-[color:var(--color-ink-muted)] backdrop-blur-md" dir="ltr">
          <div className="mb-1.5 flex items-center justify-between gap-4">
            <span className="font-bold text-white">Debug</span>
            <button onClick={() => setShowDebug(false)} className="text-xs text-[color:var(--color-ink-dim)] hover:text-white">✕</button>
          </div>
          {(() => {
            const v = videoRef.current;
            const h = hlsRef.current;
            const items: Record<string, string | number | undefined | null> = {
              "src": item?.src ? item.src.substring(0, 60) + "…" : null,
              "status": item?.status,
              "playing": playing ? "✓" : "✗",
              "curTime": v ? formatTime(v.currentTime) : "—",
              "total": formatTime(totalDuration),
              "encoded": formatTime(encodedSeconds),
              "expected": isFinite(expectedPositionRef.current()) ? formatTime(expectedPositionRef.current() / 1000) : "—",
              "frontier": isFinite(encodedFrontierRef.current()) ? formatTime(encodedFrontierRef.current()) : "∞",
              "readyState": v ? v.readyState : "—",
              "paused": v ? (v.paused ? "✓" : "✗") : "—",
              "seeking": v ? (v.seeking ? "✓" : "✗") : "—",
              "buffered": v && v.buffered.length > 0 ? `${formatTime(v.buffered.start(0))} – ${formatTime(v.buffered.end(v.buffered.length - 1))}` : "—",
              "level": h ? h.currentLevel : "—",
              "pct": defProgress?.pct ?? "—",
              "dubTrack": currentAudioTrackId || "—",
            };
            return (
              <table className="border-collapse">
                <tbody>
                  {Object.entries(items).map(([k, v]) => (
                    <tr key={k}>
                      <td className="pr-3 text-right text-[color:var(--color-ink-dim)]">{k}</td>
                      <td className="max-w-[220px] truncate text-left text-white">{v ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            );
          })()}
        </div>
      )}
    </>
  );
}

/* ---------------------------------------------------------------------- */

function CenterPlay({ onClick }: { onClick: () => void }) {
  const ref = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    run(ref.current, { scale: [0.5, 1], opacity: [0, 1], duration: 520, ease: spring({ stiffness: 320, damping: 16 }) });
  }, []);
  return (
    <button
      ref={ref}
      type="button"
      aria-label="پخش"
      onClick={onClick}
      className="absolute inset-0 z-[6] m-auto flex h-[76px] w-[76px] items-center justify-center rounded-full bg-[color:var(--color-amber)] text-black shadow-[0_0_0_10px_rgba(247,195,90,0.18),0_20px_60px_-10px_rgba(0,0,0,0.8)] transition-[filter] hover:brightness-110"
    >
      <Play className="ml-1.5 h-9 w-9 fill-current" />
    </button>
  );
}

function SkipFlash({ side, onDone }: { side: -1 | 1; onDone: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    run(ref.current, {
      opacity: [0, 1, 1, 0],
      scale: [0.8, 1, 1, 1.05],
      duration: 700,
      ease: "outQuad",
      onComplete: onDone,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return (
    <div
      ref={ref}
      dir="rtl"
      className={`pointer-events-none absolute inset-y-0 z-[5] flex w-1/3 items-center justify-center ${side === 1 ? "right-0 rounded-l-[100%]" : "left-0 rounded-r-[100%]"} bg-white/10`}
      style={{ opacity: 0 }}
    >
      <span className="rounded-full bg-black/55 px-4 py-2 text-[14px] font-bold text-white">{side === 1 ? "۱۰ ثانیه جلو ⏩" : "⏪ ۱۰ ثانیه عقب"}</span>
    </div>
  );
}

function EmptyScreen({ onGoToAdd }: { onGoToAdd?: () => void }) {
  const icon = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // the one idle flourish in the whole player: a popcorn bucket that bobs, because the screen is waiting for you
    const a = run(icon.current, { translateY: [0, -8], rotate: [-4, 4], duration: 1400, ease: "inOutSine", loop: true, alternate: true });
    return () => {
      a?.cancel();
    };
  }, []);
  return (
    <>
      <div ref={icon} className="text-[color:var(--color-amber)]">
        <Popcorn className="h-14 w-14" strokeWidth={1.5} />
      </div>
      <p className="display text-[26px] leading-tight text-white">پرده خالیه!</p>
      <p className="max-w-[340px] text-[13.5px] text-white/60">یه فیلم، سریال یا لینک بیار تا همه با هم ببینیم.</p>
      {onGoToAdd && (
        <button
          onClick={onGoToAdd}
          className="mt-1 flex items-center gap-2 rounded-full bg-[color:var(--color-amber)] px-6 py-2.5 text-[14px] font-bold text-black shadow-[var(--shadow-lamp)] transition-transform active:scale-95"
        >
          <Plus className="h-4 w-4" />
          فیلم اضافه کن
        </button>
      )}
    </>
  );
}
