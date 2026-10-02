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
  Palette,
  Keyboard,
  Maximize,
  Minimize,
  PictureInPicture2,
  Radio,
  Loader2,
  Slash,
  Check,
  HelpCircle,
  Settings,
  SkipBack,
  SkipForward,
  Shuffle,
  GripVertical,
  X,
  TriangleAlert,
  MonitorPlay,
  Headphones,
  Plus,
} from "lucide-react";
/* eslint-disable react-hooks/refs */


import { useCallback, useEffect, useRef, useState, startTransition } from "react";
import type Hls from "hls.js";
import { formatTime, isHlsUrl, levelLabelFromUrl, prettyTitle } from "@/lib/format";
import { api } from "@/lib/api";
import { useAppSettings, type ShortcutAction } from "@/lib/settings";
import type { NotifyEvent, PlaylistItem, Rendition, SubStyle, TranscodeProgress } from "@/lib/types";

const SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2];
type MenuName = "speed" | "quality" | "audio" | "subtitle" | null;

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
  const { settings } = useAppSettings();
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
  const [volume, setVolume] = useState(1);
  const [muted, setMuted] = useState(false);
  const [buffering, setBuffering] = useState(false);
  const [bigPlay, setBigPlay] = useState(false);
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

    if (!isPlayable) {
      v.removeAttribute("src");
      v.load();
      return;
    }

    let cancelled = false;
    (async () => {
      if (isHlsUrl(item!.src)) {
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
          fragLoadingMaxRetry: 15,
          fragLoadingRetryDelay: 1000,
        });
          hlsRef.current = hls;
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
                  console.warn("[hls.js] Network error, recovering...", data);
                  hls.startLoad();
                  break;
                case HlsMod.ErrorTypes.MEDIA_ERROR:
                  console.warn("[hls.js] Media error, recovering...", data);
                  hls.recoverMediaError();
                  break;
                default:
                  console.error("[hls.js] Unrecoverable error:", data);
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
          hls.loadSource(item!.src!);
          hls.attachMedia(v);
        } else if (v.canPlayType("application/vnd.apple.mpegurl")) {
          v.src = item!.src!; // Safari native HLS — ABR is internal, no manual level API
        }
      } else {
        v.src = item!.src!;
      }
    })();

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [item?.id, item?.src, isPlayable]);

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
            hls.loadSource(item.src!);
          });
        }
      } else {
        needsMenuRefresh.current = true;
      }
    }
  }, [latestNotify, item]);

  function refreshMenuIfStale() {
    if (needsMenuRefresh.current && hlsRef.current && item?.src) {
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
        hls.loadSource(item.src!);
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
    const onPlaying = () => {
      setBuffering(false);
      setBigPlay(false);
    };
    const onCanPlay = () => setBuffering(false);
    v.addEventListener("loadedmetadata", onLoadedMeta);
    v.addEventListener("timeupdate", onTime);
    v.addEventListener("waiting", onWaiting);
    v.addEventListener("playing", onPlaying);
    v.addEventListener("canplay", onCanPlay);
    return () => {
      v.removeEventListener("loadedmetadata", onLoadedMeta);
      v.removeEventListener("timeupdate", onTime);
      v.removeEventListener("waiting", onWaiting);
      v.removeEventListener("playing", onPlaying);
      v.removeEventListener("canplay", onCanPlay);
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
          setVolume((x) => Math.min(1, x + 0.05));
          return;
        case "volumeDown":
          e.preventDefault();
          setVolume((x) => Math.max(0, x - 0.05));
          return;
        case "mute":
          setMuted((m) => !m);
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
          setMuted((m) => !m);
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
  }, [item, togglePlay, seek, onOpenShortcuts, onOpenSettings, canPrev, canNext, onPrev, onNext, fullscreen, resetCursorTimer, toggleFullscreen, togglePip, sc]);

  useEffect(() => {
    const v = videoRef.current;
    if (v) {
      v.volume = volume;
      v.muted = muted;
    }
  }, [volume, muted]);

  const processing = !item || item.status === "queued" || item.status === "encoding" || item.status === "error";
  const def = defaultRendition(item);
  const defProgress = def ? transcodeProgress[`${item?.id}:${def.label}`] : undefined;
  const totalDuration = defProgress?.duration ?? duration;
  const encodedSeconds = defProgress?.encoded_seconds ?? 0;

  const [debugInfo, setDebugInfo] = useState<Record<string, string> | null>(null);

  return (
    <>
      <div
        ref={wrapRef}
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
            <track key={s.id} kind="subtitles" label={s.label} srcLang={s.lang || "fa"} src={s.url} default={false} />
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

        {buffering && !processing && (
          <div className="absolute inset-0 z-[4] flex items-center justify-center">
            <div className="h-11 w-11 rounded-full border-[3px] border-white/15" style={{ borderTopColor: "var(--color-amber)", animation: "spin .9s linear infinite" }} />
          </div>
        )}

        {bigPlay && !processing && (
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
              <CtrlBtn title="بی‌صدا" onClick={() => setMuted((m) => !m)}>
                {muted || volume === 0 ? <VolumeX className="w-4 h-4" /> : <Volume2 className="w-4 h-4" />}
              </CtrlBtn>
              <input type="range" min={0} max={1} step={0.01} value={volume} onChange={(e) => setVolume(parseFloat(e.target.value))} className="w-16 accent-[color:var(--color-amber)] sm:w-20" />
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

          <div className="flex flex-wrap items-center justify-between gap-2.5">
            <div className="flex flex-wrap items-center gap-1">
              <CtrlBtn title="پخش / مکث" main onClick={togglePlay}>
                {playing ? <Pause className="w-4 h-4 fill-current" /> : <Play className="w-4 h-4 fill-current ml-0.5" />}
              </CtrlBtn>
              <CtrlBtn title="آیتم قبلی (P)" onClick={onPrev} disabled={!canPrev}>
                <SkipBack className="w-4 h-4" />
              </CtrlBtn>
              <CtrlBtn title="آیتم بعدی (N)" onClick={onNext} disabled={!canNext}>
                <SkipForward className="w-4 h-4" />
              </CtrlBtn>
              <CtrlBtn title="پخش تصادفی" onClick={onToggleShuffle} disabled={!canShuffle}>
                <Shuffle className={`w-4 h-4 ${shuffle ? "text-[color:var(--color-amber)]" : ""}`} />
              </CtrlBtn>
              <CtrlBtn title="۱۰ ثانیه عقب (کلید ◄)" onClick={() => videoRef.current && seek(Math.max(0, videoRef.current.currentTime - 10))}>
                <RotateCcw className="w-4 h-4" />
              </CtrlBtn>
              <CtrlBtn title="۱۰ ثانیه جلو (کلید ►)" onClick={() => videoRef.current && seek(Math.min(duration || 1e9, videoRef.current.currentTime + 10))}>
                <RotateCw className="w-4 h-4" />
              </CtrlBtn>
              <CtrlBtn title="بی‌صدا" onClick={() => setMuted((m) => !m)}>
                {muted || volume === 0 ? <VolumeX className="w-4 h-4" /> : <Volume2 className="w-4 h-4" />}
              </CtrlBtn>
              <input type="range" min={0} max={1} step={0.01} value={volume} onChange={(e) => setVolume(parseFloat(e.target.value))} className="w-16 accent-[color:var(--color-amber)] sm:w-20" />
            </div>

            <div className="hidden min-w-0 flex-1 truncate px-2.5 text-center text-[13px] text-[color:var(--color-ink-muted)] sm:block">{item ? prettyTitle(item.title) : ""}</div>

            <div className="flex flex-wrap items-center gap-1">
              <MenuBtn label={`${rate}x`} open={openMenu === "speed"} onToggle={() => setOpenMenu(openMenu === "speed" ? null : "speed")} disabled={!canSpeed}>
                {SPEEDS.map((s) => (
                  <MenuItem key={s} active={rate === s} onClick={() => requestControl("rate", { rate: s })}>
                    {s}x
                  </MenuItem>
                ))}
              </MenuBtn>

              <MenuBtn
                icon={<MonitorPlay className="w-4 h-4" />}
                disabled={!item?.renditions?.length && levels.length === 0}
                open={openMenu === "quality"}
                onToggle={() => {
                  if (openMenu !== "quality") refreshMenuIfStale();
                  setOpenMenu(openMenu === "quality" ? null : "quality");
                }}
              >
                <MenuItem active={currentLevel === -1} onClick={() => hlsRef.current && (hlsRef.current.currentLevel = -1)}>
                  خودکار
                </MenuItem>
                <MenuDivider />
                {(item?.renditions?.length ? [...item.renditions].sort((a, b) => b.height - a.height) : levels.map((l) => ({ ...l, status: "ready" as const, vbr: "", abr: "" }))).map((r) => {
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
              </MenuBtn>

              <MenuBtn icon={<Headphones className="w-4 h-4" />} open={openMenu === "audio"} onToggle={() => setOpenMenu(openMenu === "audio" ? null : "audio")}>
                <MenuItem active={!currentAudioTrackId} onClick={() => setCurrentAudioTrackId(null)}>
                  صدای اصلی
                </MenuItem>
                {(item?.audio_tracks?.length ?? 0) > 0 && <MenuDivider />}
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
              </MenuBtn>

              <MenuBtn icon={<MessageSquare className="w-4 h-4" />} open={openMenu === "subtitle"} onToggle={() => setOpenMenu(openMenu === "subtitle" ? null : "subtitle")}>
                <MenuItem active={currentSubIndex === -1} onClick={() => setCurrentSubIndex(-1)}>
                  <Slash className="w-4 h-4 text-rose-400 inline ml-1.5" /> خاموش
                </MenuItem>
                {(item?.subtitles?.length ?? 0) > 0 && <MenuDivider />}
                {item?.subtitles?.map((s, i) => (
                  <MenuItem key={s.id} active={currentSubIndex === i} onClick={() => setCurrentSubIndex(i)}>
                    {s.label}
                  </MenuItem>
                ))}
              </MenuBtn>

              <CtrlBtn title="تصویر در تصویر" onClick={togglePip}>
                <PictureInPicture2 className="w-4 h-4" />
              </CtrlBtn>
              <CtrlBtn title="استایل زیرنویس" onClick={onGoToSubStyle}>
                <Palette className="w-4 h-4" />
              </CtrlBtn>
              <CtrlBtn title="راهنمای کلیدهای میانبر (؟)" onClick={onOpenShortcuts}>
                <Keyboard className="w-4 h-4" />
              </CtrlBtn>
              <CtrlBtn title="تنظیمات (S)" onClick={() => onOpenSettings?.()}>
                <Settings className="w-4 h-4" />
              </CtrlBtn>
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

function MenuBtn({
  children,
  label,
  icon,
  open,
  onToggle,
  disabled,
}: {
  children: React.ReactNode;
  label?: string;
  icon?: React.ReactNode;
  open: boolean;
  onToggle: () => void;
  disabled?: boolean;
}) {
  return (
    <div className="relative">
      <button
        disabled={disabled}
        onClick={onToggle}
        className="flex h-[38px] min-w-[38px] items-center justify-center rounded-xl border border-[color:var(--color-border)] bg-white/5 px-2 text-[13px] text-[color:var(--color-ink)] hover:border-[color:var(--color-amber)]/50 disabled:opacity-40"
      >
        {label ?? icon}
      </button>
      {open && (
        <div
          className="absolute bottom-[calc(100%+10px)] left-1/2 z-50 min-w-[170px] -translate-x-1/2 rounded-2xl border border-[color:var(--color-border)] bg-[color:var(--color-bg-elevated)]/97 p-1.5 shadow-[var(--shadow-soft)] backdrop-blur-md"
          style={{ isolation: "isolate", transform: "translate(-50%, 0) translateZ(0)" }}
        >
          {children}
        </div>
      )}
    </div>
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