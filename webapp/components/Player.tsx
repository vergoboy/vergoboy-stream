"use client";

import {
  Play,
  Pause,
  Volume2,
  VolumeX,
  RotateCw,
  RotateCcw,
  Film,
  MessageSquare,
  Keyboard,
  Maximize,
  PictureInPicture2,
  Radio,
  Loader2,
  Slash,
  Settings,
  SkipBack,
  SkipForward,
  Shuffle,
  GripVertical,
  X,
  TriangleAlert,
  MoreHorizontal,
  Headphones,
  Plus,
} from "lucide-react";
/* eslint-disable react-hooks/refs */


import { useCallback, useEffect, useRef, useState, startTransition } from "react";
import type Hls from "hls.js";
import { formatTime, isHlsUrl, levelLabelFromUrl, prettyTitle } from "@/lib/format";
import { api } from "@/lib/api";
import { mediaUrl } from "@/lib/config";
import { DEFAULT_SETTINGS, useAppSettings, type ShortcutAction } from "@/lib/settings";
import type { NotifyEvent, PlaylistItem, Rendition, SubStyle, TranscodeProgress } from "@/lib/types";

const SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2];
type MenuName = "speed" | "quality" | "audio" | "subtitle" | "more" | null;

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
  onGoToAdd: () => void;
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
  const cursorTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const myRequestedLabels = useRef<Set<string>>(new Set());
  const needsMenuRefresh = useRef(false);
  const lastFrontierWarn = useRef(0);
  const [activeLevelLabel, setActiveLevelLabel] = useState<string | null>(null);
  const [levels, setLevels] = useState<{ height: number; label: string }[]>([]);
  const [currentLevel, setCurrentLevel] = useState(-1);
  const [openMenu, setOpenMenu] = useState<MenuName>(null);
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
  const [cursorHidden, setCursorHidden] = useState(false);
  const [currentSubIndex, setCurrentSubIndex] = useState(-1);
  const [activeCueText, setActiveCueText] = useState<string[]>([]);
  const [currentAudioTrackId, setCurrentAudioTrackId] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [showDebug, setShowDebug] = useState(false);
  const [floating, setFloating] = useState(false);
  const [floatPos, setFloatPos] = useState({ x: 20, y: 20 });

  const togglePip = useCallback(() => {
    const v = videoRef.current;
    if (!v) return;
    if (document.pictureInPictureElement) {
      document.exitPictureInPicture().catch(() => {});
      return;
    }
    if (typeof v.requestPictureInPicture === "function" && document.pictureInPictureEnabled) {
      v.requestPictureInPicture().catch(() => {});
      return;
    }
    // Firefox / browsers without the PiP API: fall back to a custom floating
    // mini-player (same <video> element, just re-positioned fixed on screen).
    setFloating((f) => !f);
  }, []);

  // Drag the floating window by listening on `window` (not pointer capture)
  // — capture retargets pointer events and can corrupt their coordinates.
  const onFloatDragStart = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (!floating || e.button !== 0) return;
      e.preventDefault();
      const start = { x: e.clientX, y: e.clientY, right: floatPos.x, bottom: floatPos.y };
      const onMove = (ev: PointerEvent) => {
        setFloatPos({
          x: Math.max(8, start.right - (ev.clientX - start.x)),
          y: Math.max(8, start.bottom - (ev.clientY - start.y)),
        });
      };
      const onUp = () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        window.removeEventListener("pointercancel", onUp);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
      window.addEventListener("pointercancel", onUp);
    },
    [floating, floatPos]
  );

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
                      title: "Couldn't load this video",
                      detail: "The streaming server didn't return the video data.",
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
                      title: "This video can't be decoded",
                      detail: "Your browser couldn't decode the video stream.",
                      code: data.details || data.type,
                    });
                  }
                  break;
                default:
                  // Previously this just destroyed the player, leaving a black
                  // rectangle and an endless spinner with no cause. Surface it.
                  console.error("[hls.js] Unrecoverable error:", data);
                  setPlaybackError({
                    title: "Couldn't play this video",
                    detail: data.details ? String(data.details).replace(/_/g, " ").toLowerCase() : "The stream is broken.",
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
        1: "Loading this video was aborted.",
        2: "A network error interrupted the video.",
        3: "This video could not be decoded by your browser.",
        4: "This video format isn't supported by your browser.",
      };
      setPlaybackError({
        title: "Couldn't play this video",
        detail: names[err.code] || "The video stopped with an unknown error.",
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
      v.textTracks[i].mode = i === currentSubIndex ? "hidden" : "disabled";
    }
  }, [currentSubIndex, item?.subtitles]);

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

  // ---- fullscreen: we use a React Portal rather than the Fullscreen API
  // so the fullscreen UI is a child of document.body — escaping *all*
  // ancestor transform/stacking contexts (including any framer-motion
  // motion.div wrappers higher up the tree). This is the only way to
  // guarantee correct fixed-position coverage on *all* browsers,
  // including Firefox's stricter video compositing behaviour.
  // We still call requestFullscreen() on the portal root itself so the OS
  // hides the browser chrome on desktop. On mobile, the portal covers the
  // whole screen via dvh/dvw without needing the Fullscreen API at all.
  const toggleFullscreen = useCallback(() => {
    type ScreenOrientExt = ScreenOrientation & { lock?: (o: "landscape") => Promise<void>; unlock?: () => void };
    const orient = window.screen?.orientation as ScreenOrientExt | undefined;
    if (fullscreen) {
      setFullscreen(false);
      if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
      try {
        if (orient?.unlock) orient.unlock();
      } catch {}
    } else {
      setFullscreen(true);
      setTimeout(() => {
        wrapRef.current?.requestFullscreen().catch(() => {});
        try {
          if (orient?.lock) orient.lock("landscape").catch(() => {});
        } catch {}
      }, 30);
    }
  }, [fullscreen]);

  useEffect(() => {
    function onFsChange() {
      // Sync our state if the user pressed Escape to exit native fullscreen.
      if (!document.fullscreenElement && fullscreen) {
        setFullscreen(false);
      }
    }
    document.addEventListener("fullscreenchange", onFsChange);
    return () => document.removeEventListener("fullscreenchange", onFsChange);
  }, [fullscreen]);

  // ---- keyboard shortcuts ----
  // resetCursorTimer must be declared before this effect.
  const resetCursorTimer = useCallback(() => {
    if (!fullscreen) return;
    setCursorHidden(false);
    if (cursorTimerRef.current) clearTimeout(cursorTimerRef.current);
    cursorTimerRef.current = setTimeout(() => setCursorHidden(true), 3000);
  }, [fullscreen]);

  // Auto-hide: hide immediately on fullscreen entry, show on interaction
  useEffect(() => {
    if (fullscreen) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- reacting to an external fullscreen change is intentional (sync with the browser UI)
      setCursorHidden(true);
    } else {
      setCursorHidden(false);
      if (cursorTimerRef.current) clearTimeout(cursorTimerRef.current);
    }
  }, [fullscreen]);

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
      if (fullscreen) resetCursorTimer();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [item, togglePlay, seek, onOpenShortcuts, onOpenSettings, canPrev, canNext, onPrev, onNext, fullscreen, resetCursorTimer, toggleFullscreen, togglePip, sc, changeVolume, changeMuted]);

  useEffect(() => {
    const v = videoRef.current;
    if (v) {
      v.volume = volume;
      v.muted = muted;
    }
  }, [volume, muted]);

  const processing = !item || item.status === "queued" || item.status === "encoding" || item.status === "error";
  // A third dead-end state the old code had: the item is marked `complete` but
  // has no playable src. `processing` was false and `isPlayable` was false, so
  // the player rendered a bare black rectangle with nothing on it at all.
  const noPlayableSource = Boolean(item) && !processing && !isPlayable;
  const def = defaultRendition(item);
  const defProgress = def ? transcodeProgress[`${item?.id}:${def.label}`] : undefined;
  const totalDuration = defProgress?.duration ?? duration;
  const encodedSeconds = defProgress?.encoded_seconds ?? 0;

  const [debugInfo, setDebugInfo] = useState<Record<string, string> | null>(null);

  return (
    <>
      <div
        ref={wrapRef}
        data-context-kind="player"
        tabIndex={-1}
        onMouseMove={fullscreen ? resetCursorTimer : undefined}
        onMouseDown={fullscreen ? resetCursorTimer : undefined}
        onTouchStart={fullscreen ? () => { resetCursorTimer(); } : undefined}
        className={
          fullscreen
            ? `fixed inset-0 z-[9999] bg-black overflow-hidden select-none ${cursorHidden ? "cursor-none" : ""}`
            : floating
              ? "fixed z-[9997] rounded-3xl border border-[color:var(--color-border)] bg-black/60 backdrop-blur-md flex flex-col overflow-hidden shadow-2xl"
              : "relative w-full rounded-3xl border border-[color:var(--color-border)] bg-black/40 backdrop-blur-md flex flex-col overflow-hidden"
        }
        style={fullscreen ? { width: "100vw", height: "100vh" } : floating ? { width: 380, maxWidth: "86vw", right: floatPos.x, bottom: floatPos.y } : undefined}
      >
        {floating && (
          <div className="absolute top-0 left-0 right-0 z-[30] flex items-center gap-1.5 rounded-t-3xl bg-black/70 px-2 py-1.5 select-none">
            <div
              className="flex h-6 w-6 shrink-0 cursor-grab items-center justify-center rounded-lg hover:bg-white/10 active:cursor-grabbing"
              title="جابه‌جایی پنجره"
              onPointerDown={onFloatDragStart}
            >
              <GripVertical className="h-3.5 w-3.5 text-white/50" />
            </div>
            <span className="min-w-0 flex-1 truncate text-[11px] text-white/80">{item?.title || "پخش زنده"}</span>
            {item?.type !== "live" && (
              <button
                title={playing ? "توقف" : "پخش"}
                onClick={togglePlay}
                className="flex h-6 w-6 shrink-0 items-center justify-center rounded-lg text-white/80 hover:bg-white/10 hover:text-white"
              >
                {playing ? <Pause className="h-3.5 w-3.5 fill-current" /> : <Play className="h-3.5 w-3.5 fill-current" />}
              </button>
            )}
            <button
              title="بستن پنجره‌ی شناور"
              onClick={() => setFloating(false)}
              className="flex h-6 w-6 shrink-0 items-center justify-center rounded-lg text-white/80 hover:bg-white/10 hover:text-white"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        )}
        <div
          className={
            fullscreen
              ? "absolute inset-0 z-[1] flex items-center justify-center bg-black overflow-hidden"
              : "relative flex items-center justify-center overflow-hidden bg-black aspect-video rounded-t-3xl"
          }
        >
          <video
            ref={videoRef}
            playsInline
            preload="metadata"
            onClick={togglePlay}
            className="h-full w-full object-contain"
        >
          {item?.subtitles?.map((s) => (
            <track key={s.id} kind="subtitles" label={s.label} srcLang={s.lang || "fa"} src={mediaUrl(s.url)} default={false} />
          ))}
        </video>
          <audio ref={dubAudioRef} preload="auto" />

        {!processing && activeCueText.length > 0 && (
          <div
            className="pointer-events-none absolute left-0 right-0 z-[3] flex flex-col items-center gap-1 px-6 text-center"
            style={{ bottom: subStyle.offset }}
          >
            {activeCueText.map((line, i) => (
              <div
                key={i}
                className="max-w-[90%] rounded-lg px-3.5 py-1"
                style={{
                  fontFamily: subStyle.font,
                  fontSize: subStyle.size,
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

        {item?.type === "live" && (
          <div className="absolute top-3.5 right-3.5 z-[5] flex items-center gap-1.5 rounded-full bg-[color:var(--color-coral)]/90 px-3 py-1 text-xs font-bold text-white" style={{ animation: "pulse-live 1.8s infinite" }}>
            <Radio className="w-3.5 h-3.5 text-white animate-pulse" /> پخش زنده
          </div>
        )}

        {buffering && !processing && !playbackError && !noPlayableSource && (
          <div className="absolute inset-0 z-[4] flex items-center justify-center">
            <div className="h-11 w-11 rounded-full border-[3px] border-white/15" style={{ borderTopColor: "var(--color-amber)", animation: "spin .9s linear infinite" }} />
          </div>
        )}

        {bigPlay && !processing && !playbackError && !noPlayableSource && (
          <button
            onClick={() => {
              setBigPlay(false);
              togglePlay();
            }}
            className="absolute inset-0 z-[6] m-auto flex h-20 w-20 items-center justify-center rounded-full border border-white/10 bg-black/55 text-2xl text-white backdrop-blur-sm transition-transform hover:scale-105"
          >
            <Play className="w-8 h-8 fill-white text-white ml-1" />
          </button>
        )}

        {processing && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3.5 bg-[color:var(--color-bg)] px-6 text-center text-[color:var(--color-ink-muted)]">
            <div className="flex items-center justify-center text-5xl opacity-80">
              {item?.status === "error" ? <TriangleAlert className="h-12 w-12 text-[color:var(--color-coral)]" /> : !item ? <Film className="h-12 w-12" /> : <Loader2 className="h-12 w-12 animate-spin" />}
            </div>
            <p className="m-0 text-[14.5px]">
              {!item
                ? "هنوز ویدیویی برای پخش انتخاب نشده"
                : item.status === "error"
                  ? `تبدیل این ویدیو ناموفق بود: ${item.error || "خطای نامشخص"}`
                  : `در حال آماده‌سازی «${prettyTitle(item.title)}» — پخش معمولاً ظرف حدود ۲ دقیقه شروع می‌شه`}
            </p>
            {item && item.status !== "error" && (
              <div className="flex w-[70%] max-w-[340px] items-center gap-2.5">
                <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-white/10">
                  <div
                    className="h-full rounded-full transition-all duration-500"
                    style={{ width: `${defProgress?.pct ?? 0}%`, background: "linear-gradient(90deg, var(--color-amber), var(--color-plum-soft))" }}
                  />
                </div>
                <span className="font-mono text-xs text-[color:var(--color-amber)]">{defProgress?.pct ?? 0}%</span>
              </div>
            )}
            {!item && (
              <button
                onClick={onGoToAdd}
                className="rounded-xl border border-[color:var(--color-border)] bg-white/5 px-5 py-2.5 text-[13.5px] text-[color:var(--color-ink)] hover:border-[color:var(--color-amber)]/50"
              >
                <span className="flex items-center gap-1.5">
                  <Plus className="h-4 w-4" />
                  افزودن ویدیو
                </span>
              </button>
            )}
          </div>
        )}

        {noPlayableSource && (
          <div
            role="alert"
            className="absolute inset-0 z-[7] flex flex-col items-center justify-center gap-3 bg-[color:var(--color-bg)] px-6 text-center"
          >
            <Film className="h-11 w-11 text-[color:var(--color-ink-muted)]" />
            <p className="m-0 text-[15px] text-[color:var(--color-ink)]">این ویدیو فایل پخشی ندارد</p>
            <p className="m-0 max-w-[420px] text-[13px] text-[color:var(--color-ink-muted)]">
              «{prettyTitle(item?.title)}» آماده است ولی هیچ فایل قابل پخشی برایش ساخته نشده.
            </p>
            <button
              onClick={onGoToAdd}
              className="mt-1 rounded-xl border border-[color:var(--color-amber)]/50 bg-white/5 px-4 py-2 text-[13px] text-[color:var(--color-ink)] transition-colors hover:bg-white/10"
            >
              افزودن ویدیو
            </button>
          </div>
        )}

        {playbackError && (
          <div
            role="alert"
            aria-live="assertive"
            className="absolute inset-0 z-[7] flex flex-col items-center justify-center gap-3 bg-[color:var(--color-bg)] px-6 text-center"
          >
            <TriangleAlert className="h-11 w-11 text-[color:var(--color-coral)]" />
            <p className="m-0 text-[15px] font-medium text-[color:var(--color-ink)]">{playbackError.title}</p>
            <p className="m-0 max-w-[420px] text-[13px] text-[color:var(--color-ink-muted)]">{playbackError.detail}</p>
            {playbackError.code && (
              <code className="rounded-md bg-white/5 px-2 py-1 font-mono text-[11px] text-[color:var(--color-ink-muted)]">{playbackError.code}</code>
            )}
            <div className="mt-1 flex items-center gap-2">
              <button
                onClick={retryPlayback}
                className="flex items-center gap-1.5 rounded-xl border border-[color:var(--color-amber)]/50 bg-white/5 px-4 py-2 text-[13px] text-[color:var(--color-ink)] transition-colors hover:bg-white/10"
              >
                <RotateCcw className="h-4 w-4" />
                تلاش دوباره
              </button>
              <button
                onClick={onGoToAdd}
                className="rounded-xl border border-[color:var(--color-border)] px-4 py-2 text-[13px] text-[color:var(--color-ink-muted)] transition-colors hover:text-[color:var(--color-ink)]"
              >
                ویدیوی دیگر
              </button>
            </div>
          </div>
        )}

        {toast && (
          <div className="absolute bottom-24 left-1/2 z-20 -translate-x-1/2 rounded-xl border border-[color:var(--color-amber)]/40 bg-black/80 px-4 py-2 text-xs text-[color:var(--color-ink)] backdrop-blur">
            {toast}
          </div>
        )}
        </div>

        {!processing && !floating && item?.type === "live" && (
        <div
          dir="ltr"
          className={`transition-opacity duration-300 ${
            fullscreen
              ? `absolute bottom-0 left-0 right-0 z-[20] p-4 sm:p-6 bg-gradient-to-t from-black/95 via-black/60 to-transparent ${
                  cursorHidden ? "pointer-events-none opacity-0" : "opacity-100"
                }`
              : "relative z-10 p-3.5 pb-4 border-t border-[color:var(--color-border)] bg-[color:var(--color-bg-elevated)]/90 rounded-b-3xl"
          }`}
        >
          <div className="flex items-center justify-between gap-2.5">
            <div className="flex items-center gap-1">
              <CtrlBtn title="بی‌صدا" onClick={() => changeMuted((m) => !m)}>
                {muted || volume === 0 ? <VolumeX className="w-4 h-4" /> : <Volume2 className="w-4 h-4" />}
              </CtrlBtn>
              <input type="range" min={0} max={1} step={0.01} value={volume} onChange={(e) => changeVolume(parseFloat(e.target.value))} className="w-16 accent-[color:var(--color-amber)] sm:w-20" />
            </div>
            <div className="hidden min-w-0 flex-1 truncate px-2.5 text-center text-[13px] text-[color:var(--color-ink-muted)] sm:block">{item ? prettyTitle(item.title) : ""}</div>
            <div className="flex items-center gap-1">
              <CtrlBtn title="تمام‌صفحه (F)" onClick={toggleFullscreen}>
                <Maximize className="w-4 h-4" />
              </CtrlBtn>
            </div>
          </div>
        </div>
      )}

{!processing && !floating && item?.type !== "live" && (
        <div
          dir="ltr"
          className={`transition-opacity duration-300 ${
            fullscreen
              ? `absolute bottom-0 left-0 right-0 z-[20] p-4 sm:p-6 bg-gradient-to-t from-black/95 via-black/60 to-transparent ${
                  cursorHidden ? "pointer-events-none opacity-0" : "opacity-100"
                }`
              : "relative z-10 p-3.5 pb-4 border-t border-[color:var(--color-border)] bg-[color:var(--color-bg-elevated)]/90 rounded-b-3xl"
          }`}
        >
          {/* The timeline is the shared playhead. */}
          <div className="mb-2 flex items-center gap-2.5">
            <span className="min-w-[90px] text-center font-mono text-xs text-[color:var(--color-ink-muted)]" dir="ltr">{formatTime(curTime)}{isFinite(totalDuration) && totalDuration > 0 ? ` / ${formatTime(totalDuration)}` : ""}</span>
            <div className="relative flex-1">
              {!processing && encodedSeconds > 0 && encodedSeconds < totalDuration && (
                <div
                  className="pointer-events-none absolute bottom-0 left-0 z-[1] h-1.5 rounded-full bg-white/15"
                  style={{ width: `${(encodedSeconds / totalDuration) * 100}%` }}
                />
              )}
              <input
                type="range"
                min={0}
                max={isFinite(totalDuration) ? totalDuration || 0 : (duration || 0)}
                step={0.1}
                value={dragging ? dragValueRef.current : curTime}
                onMouseDown={() => setDragging(true)}
                onTouchStart={() => setDragging(true)}
                onChange={(e) => {
                  const v = parseFloat(e.target.value);
                  dragValueRef.current = v;
                  setCurTime(v);
                  const vid = videoRef.current;
                  if (vid) vid.currentTime = v;
                }}
                onMouseUp={() => {
                  setDragging(false);
                  seek(dragValueRef.current);
                }}
                onTouchEnd={() => {
                  setDragging(false);
                  seek(dragValueRef.current);
                }}
                className="relative z-[2] h-1.5 w-full cursor-pointer accent-[color:var(--color-amber)]"
              />
            </div>
            <span className="min-w-[90px] text-center font-mono text-xs text-[color:var(--color-ink-muted)]" dir="ltr">{isFinite(totalDuration) && totalDuration > 0 ? formatTime(totalDuration) : formatTime(duration)}</span>
          </div>

          {/* Only four things live on the bar by default: play/pause, the
              timeline above, volume, and fullscreen. Everything else is a
              secondary option and belongs in a menu — so the controls people
              actually reach for stay legible instead of drowning in chips. */}
          <div className="flex flex-wrap items-center justify-between gap-2.5">
            <div className="flex items-center gap-1">
              <CtrlBtn title="پخش / مکث (K)" main onClick={togglePlay}>
                {playing ? <Pause className="w-4 h-4 fill-current" /> : <Play className="w-4 h-4 fill-current ml-0.5" />}
              </CtrlBtn>
            </div>

            <div className="hidden min-w-0 flex-1 truncate px-2.5 text-center text-[13px] text-[color:var(--color-ink-muted)] sm:block">
              {item ? prettyTitle(item.title) : ""}
            </div>

            <div className="flex items-center gap-1">
              <CtrlBtn title="بی‌صدا (M)" onClick={() => changeMuted((m) => !m)}>
                {muted || volume === 0 ? <VolumeX className="w-4 h-4" /> : <Volume2 className="w-4 h-4" />}
              </CtrlBtn>
              <input
                type="range"
                min={0}
                max={1}
                step={0.01}
                value={muted ? 0 : volume}
                onChange={(e) => {
                  changeVolume(parseFloat(e.target.value));
                  if (parseFloat(e.target.value) > 0) changeMuted(false);
                }}
                aria-label="بلندی صدا"
                className="w-16 accent-[color:var(--color-amber)] sm:w-20"
              />

              <div className="relative">
                <button
                  type="button"
                  aria-label="گزینه‌های بیشتر"
                  aria-expanded={openMenu === "more"}
                  onClick={() => {
                    if (openMenu !== "more") refreshMenuIfStale();
                    setOpenMenu(openMenu === "more" ? null : "more");
                  }}
                  className="flex h-[38px] min-w-[38px] items-center justify-center rounded-xl border border-[color:var(--color-border)] bg-white/5 px-2 text-[color:var(--color-ink)] transition-colors hover:border-[color:var(--color-amber)]/50"
                >
                  <MoreHorizontal className="w-4 h-4" />
                </button>
                {openMenu === "more" && (
                  <div
                    role="menu"
                    aria-label="گزینه‌های بیشتر"
                    className="absolute bottom-[calc(100%+10px)] right-0 z-50 max-h-[min(70vh,520px)] w-64 overflow-y-auto rounded-2xl border border-[color:var(--color-border)] bg-[color:var(--color-bg-elevated)]/97 p-1.5 shadow-[var(--shadow-soft)] backdrop-blur-md"
                    style={{ isolation: "isolate" }}
                  >
                    <MenuSectionLabel>پخش</MenuSectionLabel>
                    <MenuItem disabled={!canPrev} onClick={onPrev}>
                      <span className="flex items-center gap-2"><SkipBack className="w-3.5 h-3.5" />آیتم قبلی <kbd className="ms-auto">P</kbd></span>
                    </MenuItem>
                    <MenuItem disabled={!canNext} onClick={onNext}>
                      <span className="flex items-center gap-2"><SkipForward className="w-3.5 h-3.5" />آیتم بعدی <kbd className="ms-auto">N</kbd></span>
                    </MenuItem>
                    <MenuItem onClick={() => videoRef.current && seek(Math.max(0, videoRef.current.currentTime - 10))}>
                      <span className="flex items-center gap-2"><RotateCcw className="w-3.5 h-3.5" />۱۰ ثانیه عقب</span>
                    </MenuItem>
                    <MenuItem onClick={() => videoRef.current && seek(Math.min(duration || 1e9, videoRef.current.currentTime + 10))}>
                      <span className="flex items-center gap-2"><RotateCw className="w-3.5 h-3.5" />۱۰ ثانیه جلو</span>
                    </MenuItem>
                    <MenuItem disabled={!canShuffle} onClick={onToggleShuffle} active={shuffle}>
                      <span className="flex items-center gap-2"><Shuffle className="w-3.5 h-3.5" />پخش تصادفی</span>
                    </MenuItem>

                    <MenuDivider />
                    <MenuSectionLabel>سرعت</MenuSectionLabel>
                    <div className="grid grid-cols-4 gap-1 px-1 pb-1">
                      {SPEEDS.map((s) => (
                        <button
                          key={s}
                          type="button"
                          disabled={!canSpeed}
                          onClick={() => requestControl("rate", { rate: s })}
                          className={`rounded-lg px-1.5 py-1.5 text-[12px] disabled:opacity-40 ${
                            rate === s
                              ? "bg-[color:var(--color-amber)]/15 font-bold text-[color:var(--color-amber)]"
                              : "text-[color:var(--color-ink)] hover:bg-white/5"
                          }`}
                        >
                          {s}x
                        </button>
                      ))}
                    </div>

                    <MenuDivider />
                    <MenuSectionLabel>کیفیت</MenuSectionLabel>
                    <MenuItem active={currentLevel === -1} onClick={() => hlsRef.current && (hlsRef.current.currentLevel = -1)}>
                      خودکار
                    </MenuItem>
                    {(item?.renditions?.length
                      ? [...item.renditions].sort((a, b) => b.height - a.height)
                      : levels.map((l) => ({ ...l, status: "ready" as const, vbr: "", abr: "" }))
                    ).map((r) => {
                      if (r.status === "ready" || r.status === "complete" || !r.status) {
                        const idx = levels.findIndex((l) => l.label === r.label);
                        return (
                          <MenuItem key={r.label} active={idx !== -1 && currentLevel === idx} onClick={() => idx !== -1 && hlsRef.current && (hlsRef.current.currentLevel = idx)}>
                            {r.label}
                          </MenuItem>
                        );
                      }
                      if (r.status === "pending" || r.status === "error") {
                        return (
                          <MenuItem key={r.label} onClick={() => setPendingQualityLabel(r.label)}>
                            {r.label} <span className="mr-1.5 text-[10.5px] text-[color:var(--color-ink-dim)]">{r.status === "error" ? "خطا — دوباره امتحان کن" : "برای آماده‌سازی کلیک کن"}</span>
                          </MenuItem>
                        );
                      }
                      return (
                        <MenuItem key={r.label} disabled>
                          {r.label} <span className="mr-1.5 text-[10.5px] text-[color:var(--color-ink-dim)]">در حال آماده‌سازی…</span>
                        </MenuItem>
                      );
                    })}

                    <MenuDivider />
                    <MenuSectionLabel>صدا</MenuSectionLabel>
                    <MenuItem active={!currentAudioTrackId} onClick={() => setCurrentAudioTrackId(null)}>
                      <span className="flex items-center gap-2"><Headphones className="w-3.5 h-3.5" />صدای اصلی</span>
                    </MenuItem>
                    {item?.audio_tracks?.map((t) => (
                      <MenuItem
                        key={t.id}
                        active={currentAudioTrackId === t.id}
                        onClick={() => {
                          setCurrentAudioTrackId(t.id);
                          if (dubAudioRef.current) dubAudioRef.current.src = t.url;
                        }}
                      >
                        {t.label}
                      </MenuItem>
                    ))}

                    <MenuDivider />
                    <MenuSectionLabel>زیرنویس</MenuSectionLabel>
                    <MenuItem active={currentSubIndex === -1} onClick={() => setCurrentSubIndex(-1)}>
                      <Slash className="w-3.5 h-3.5 text-rose-400" /> خاموش
                    </MenuItem>
                    {item?.subtitles?.map((sub, i) => (
                      <MenuItem key={sub.id} active={currentSubIndex === i} onClick={() => setCurrentSubIndex(i)}>
                        {sub.label}
                      </MenuItem>
                    ))}

                    <MenuDivider />
                    <MenuSectionLabel>نمایش</MenuSectionLabel>
                    <MenuItem onClick={togglePip}>
                      <span className="flex items-center gap-2"><PictureInPicture2 className="w-3.5 h-3.5" />تصویر در تصویر</span>
                    </MenuItem>
                    <MenuItem onClick={onGoToSubStyle}>
                      <span className="flex items-center gap-2"><MessageSquare className="w-3.5 h-3.5" />استایل زیرنویس</span>
                    </MenuItem>
                    <MenuItem onClick={onOpenShortcuts}>
                      <span className="flex items-center gap-2"><Keyboard className="w-3.5 h-3.5" />کلیدهای میانبر</span>
                    </MenuItem>
                    <MenuItem onClick={() => onOpenSettings?.()}>
                      <span className="flex items-center gap-2"><Settings className="w-3.5 h-3.5" />تنظیمات</span>
                    </MenuItem>
                  </div>
                )}
              </div>

              <CtrlBtn title="تمام‌صفحه (F)" onClick={toggleFullscreen}>
                <Maximize className="w-4 h-4" />
              </CtrlBtn>
            </div>
          </div>
        </div>
      )}

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

function CtrlBtn({
  children,
  title,
  onClick,
  main,
  disabled,
}: {
  children: React.ReactNode;
  title: string;
  onClick: () => void;
  main?: boolean;
  disabled?: boolean;
}) {
  return (
    <button
      title={title}
      onClick={onClick}
      disabled={disabled}
      className={`flex h-[38px] w-[38px] items-center justify-center rounded-xl text-[15px] transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
        main
          ? "text-white shadow-[0_4px_18px_-4px_rgba(232,161,92,0.5)]"
          : "border border-[color:var(--color-border)] bg-white/5 text-[color:var(--color-ink)] hover:border-[color:var(--color-amber)]/50"
      }`}
      style={main ? { background: "linear-gradient(135deg, var(--color-amber), var(--color-plum))" } : undefined}
    >
      {children}
    </button>
  );
}


function MenuItem({ children, active, onClick, disabled }: { children: React.ReactNode; active?: boolean; onClick?: () => void; disabled?: boolean }) {
  return (
    <div
      onClick={disabled ? undefined : onClick}
      className={`flex items-center justify-between gap-2 whitespace-nowrap rounded-lg px-3 py-2 text-[13.5px] ${
        disabled ? "cursor-default opacity-60" : "cursor-pointer hover:bg-white/5"
      } ${active ? "font-bold text-[color:var(--color-amber)]" : "text-[color:var(--color-ink)]"}`}
    >
      {children}
      {active && <span>✓</span>}
    </div>
  );
}

function MenuDivider() {
  return <div className="my-1 h-px bg-[color:var(--color-border)]" />;
}

/** Small non-interactive header that groups items in a long menu. */
function MenuSectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <p className="px-3 pb-1 pt-2 text-[9.5px] font-semibold tracking-[0.18em] text-[color:var(--color-ink-dim)] uppercase">
      {children}
    </p>
  );
}