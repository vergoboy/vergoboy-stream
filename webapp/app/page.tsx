"use client";
/* eslint-disable react-hooks/refs */

import { useEffect, useCallback, useMemo, useRef, useState } from "react";
import { useLocalStorage } from "@/lib/useLocalStorage";
import { useRoomState } from "@/lib/useRoomState";
import { api } from "@/lib/api";
import { DEFAULT_SUB_STYLE, type SubStyle } from "@/lib/types";
import type { NotifyEvent } from "@/lib/types";
import { useAuth, logout, updateUser, isAdmin, mayAdd, mayControl, mayManage, finishOAuthLogin } from "@/lib/auth";
import { useViewport, useIdle } from "@/lib/useIdle";
import { guardNativeClose, isTauri } from "@/lib/tauri";

import { Audience } from "@/components/Sofa";
import { Player } from "@/components/Player";
import { Playlist } from "@/components/Playlist";
import { AddHub } from "@/components/AddHub";
import { SubAudioPanel } from "@/components/SubAudioPanel";
import { SubStylePanel } from "@/components/SubStylePanel";
import { ChatPanel } from "@/components/ChatPanel";
import { VoicePanel } from "@/components/VoicePanel";
import { Sidebar } from "@/components/Sidebar";
import { MembersPanel } from "@/components/MembersPanel";
import { Toasts, type Toast } from "@/components/Toasts";
import { AuthGate } from "@/components/AuthGate";
import { RoomBar } from "@/components/RoomBar";
import { OnboardingModal } from "@/components/OnboardingModal";
import { ShortcutsModal } from "@/components/ShortcutsModal";
import { SettingsModal } from "@/components/SettingsModal";
import { Scene, Marquee } from "@/components/Scene";
import { Dock, type PanelId } from "@/components/Dock";
import { Drawer } from "@/components/Drawer";
import { ContextMenu, type MenuEntry } from "@/components/ContextMenu";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { SettingsProvider } from "@/lib/settings";
import { useVoiceRoom } from "@/lib/useVoiceRoom";
import { notifyText } from "@/lib/notifyText";
import {
  ListVideo, Plus, MessageCircle, Users, Captions, CircleUserRound, Play, Pause, Undo2, Redo2, SkipBack, SkipForward, Shuffle, Gauge, Maximize,
  PictureInPicture2, Settings, Keyboard, Copy, VolumeX, LayoutDashboard, LogOut, Ticket,
} from "lucide-react";

let toastSeq = 0;

type ContextTarget = "player" | "user" | "voice" | "page";
type ContextMenuState = { x: number; y: number; kind: ContextTarget; name: string | null };

const basePath = process.env.NEXT_PUBLIC_BUILD_TARGET === "tauri" ? "" : "/stream";

const PANEL_META: Record<PanelId, { title: string; icon: React.ReactNode }> = {
  queue: { title: "صف پخش", icon: <ListVideo className="h-6 w-6" /> },
  add: { title: "چی ببینیم؟", icon: <Plus className="h-6 w-6" /> },
  chat: { title: "چت", icon: <MessageCircle className="h-6 w-6" /> },
  people: { title: "حاضرین", icon: <Users className="h-6 w-6" /> },
  subs: { title: "زیرنویس و صدا", icon: <Captions className="h-6 w-6" /> },
  me: { title: "من", icon: <CircleUserRound className="h-6 w-6" /> },
};

export default function Page() {
  return (
    <SettingsProvider>
      <PageInner />
    </SettingsProvider>
  );
}

function PageInner() {
  const { user, access, hydrated } = useAuth();
  const [myAvatarUrl, setMyAvatarUrl] = useLocalStorage<string | null>("stream_user_avatar", null);
  const [subStyle, setSubStyle] = useLocalStorage<SubStyle>("stream_sub_style", DEFAULT_SUB_STYLE);
  const [roomCode, setRoomCode] = useState<string | null>(null);
  const [panel, setPanel] = useState<PanelId | null>(null);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [latestNotify, setLatestNotify] = useState<NotifyEvent | null>(null);
  const [unreadChat, setUnreadChat] = useState(0);
  const [voiceProfiles, setVoiceProfiles] = useState<{ name: string; avatarUrl: string | null; speaking: boolean }[]>([]);
  const [joinCode, setJoinCode] = useState<string | null>(null);
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);

  const myName = user?.username ?? "";
  const isAdm = isAdmin(user);
  const canControl = mayControl(user);

  // Grab ?join=CODE once on load, then strip it from the URL so a refresh
  // doesn't re-trigger the join.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const p = new URLSearchParams(window.location.search);
    const c = p.get("join");
    if (c) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- one-time read of the invite code from the URL, then it's removed from the address bar
      setJoinCode(c.trim().toUpperCase());
      p.delete("join");
      const q = p.toString();
      window.history.replaceState(null, "", `${window.location.pathname}${q ? `?${q}` : ""}`);
    }
  }, []);

  // OAuth callback: the server redirected back with fresh tokens in the URL
  // hash. Save the session and clean the address bar in one pass.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const h = new URLSearchParams(window.location.hash.replace(/^#/, ""));
    const at = h.get("access_token");
    const rt = h.get("refresh_token");
    if (!at || !rt) return;
    window.history.replaceState(null, "", window.location.pathname + window.location.search);
    finishOAuthLogin(at, rt).catch(() => {
      const id = `t${toastSeq++}`;
      setToasts((prev) => [...prev, { id, text: "ورود با حساب خارجی ناموفق بود؛ دوباره تلاش کن" }]);
      setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 4000);
    });
  }, []);

  // One active room per user — always follow user.current_room_id.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- keep the live room in sync with the account's current_room_id (external DB state)
    if (user?.current_room_id) setRoomCode(user.current_room_id);
  }, [user?.current_room_id]);

  // Mark our pages so the browser screen-share feature can detect when one of
  // them is being captured and drop its audio (prevents the room's own sound
  // from feeding back into an external stream).
  useEffect(() => {
    const md = navigator.mediaDevices as MediaDevices & {
      setCaptureHandleConfig?: (c: { handle?: string; permittedOrigins?: string[] }) => void;
    };
    md?.setCaptureHandleConfig?.({ handle: JSON.stringify({ app: "vergoboy-stream" }), permittedOrigins: ["*"] });
  }, []);

  // Auto-join once logged in, if the invite code came from the URL.
  const joinedRef = useRef<string | null>(null);
  useEffect(() => {
    if (!user || !joinCode || joinedRef.current === joinCode) return;
    joinedRef.current = joinCode;
    api
      .roomJoin(joinCode)
      .then((res) => {
        updateUser(res.user);
        setRoomCode(res.room.id);
      })
      .catch((e) => {
        const msg = e instanceof Error ? e.message : "اتاق پیدا نشد";
        const id = `t${toastSeq++}`;
        setToasts((prev) => [...prev, { id, text: `ورود به اتاق ${joinCode} ناموفق بود: ${msg}` }]);
        setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 4000);
      })
      .finally(() => setJoinCode(null));
  }, [user, joinCode]);

  const voice = useVoiceRoom(myName, myAvatarUrl);

  const handleNotify = (n: NotifyEvent) => {
    setLatestNotify(n);
    const id = `t${toastSeq++}`;
    setToasts((prev) => [...prev, { id, text: notifyText(n, room.playlist) }]);
    setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 4000);
  };

  // When a room manager bans me, the server drops my socket and clears my
  // current_room_id; bounce back to my own room.
  const handleKicked = useCallback(() => {
    const id = `t${toastSeq++}`;
    setToasts((prev) => [...prev, { id, text: "از اتاق اخراج شدی و به اتاق خودت برگشتی" }]);
    setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 4000);
    api
      .roomMine()
      .then((res) => {
        updateUser(res.user);
        setRoomCode(res.room.id);
      })
      .catch(() => {});
  }, []);

  const { connected, room, presenceUsers, notifications, chatMessages, transcodeProgress, expectedPosition, requestControl, sendChat, setVoiceActive, reportWatching } =
    useRoomState({ token: access, roomCode: roomCode ?? "", myName, canControl, myId: user?.id, onNotify: handleNotify, onKicked: handleKicked });

  // The two lounge couches. These are NOT mutually exclusive: the server sends
  // one entry per person carrying every state they hold at once, so someone
  // who is watching AND in voice appears on both couches as the same person.
  //
  // The previous code filtered the watching couch with `!u.in_voice`, which
  // made anyone who joined voice vanish from the watching area entirely — they
  // were rendered as a different kind of person rather than as one person doing
  // two things. "watching" now means the real thing: this person is actually
  // playing the shared media.
  const watchingUsers = presenceUsers.filter((u) => u.watching === true);
  const voiceUsers = presenceUsers.filter((u) => u.in_voice);

  const isOwner = presenceUsers.some((u) => u.id !== undefined && u.id === user?.id && u.is_owner) || false;
  const canManage = mayManage(user, isOwner);
  const canAdd = mayAdd(user, isOwner);

  const currentItem = useMemo(
    () => (room.current_index !== null ? room.playlist[room.current_index] ?? null : null),
    [room.current_index, room.playlist]
  );

  // pick the next/prev playlist index. In shuffle mode the items are followed
  // in the room's shuffled order (which wraps around); otherwise it's a plain
  // +/-1 step clamped by the caller's canPrev/canNext.
  function shuffleStep(room: { playlist: { id: string }[]; shuffle: boolean; shuffle_order: string[]; current_index: number | null }, dir: 1 | -1): number {
    const ci = room.current_index;
    if (ci === null) return 0;
    if (room.shuffle && room.shuffle_order.length > 0) {
      const cur = room.playlist[ci]?.id;
      const pos = room.shuffle_order.indexOf(cur);
      const N = room.shuffle_order.length;
      const nid = room.shuffle_order[(((pos % N) + N) + dir) % N];
      const idx = room.playlist.findIndex((p) => p.id === nid);
      return idx >= 0 ? idx : 0;
    }
    return Math.max(0, ci + dir);
  }

  const prevChatLenRef = useRef(0);
  useEffect(() => {
    const grew = chatMessages.length - prevChatLenRef.current;
    if (grew > 0 && panel !== "chat") {
      const newOnes = chatMessages.slice(-grew);
      const fromOthers = newOnes.filter((m) => m.name !== myName).length;
      if (fromOthers > 0) setUnreadChat((n) => n + fromOthers);
    }
    prevChatLenRef.current = chatMessages.length;
  }, [chatMessages, panel, myName]);

  function openPanel(id: PanelId | null) {
    setPanel(id);
    if (id === "chat") setUnreadChat(0);
  }
  function togglePanel(id: PanelId) {
    openPanel(panel === id ? null : id);
  }

  function showContextMenu(target: EventTarget | null, x: number, y: number) {
    const element = target instanceof Element ? target : null;
    const contextTarget = element?.closest<HTMLElement>("[data-context-kind]");
    const kind = contextTarget?.dataset.contextKind as ContextTarget | undefined;
    setContextMenu({ x, y, kind: kind ?? "page", name: contextTarget?.dataset.contextName ?? null });
  }

  function handleContextMenu(event: React.MouseEvent<HTMLElement>) {
    const element = event.target as HTMLElement;
    if (element.closest("[data-context-menu-root], input, textarea, select, [contenteditable='true']")) return;
    event.preventDefault();
    showContextMenu(event.target, event.clientX, event.clientY);
  }

  function handleContextKeyDown(event: React.KeyboardEvent<HTMLElement>) {
    if (event.key !== "ContextMenu" && !(event.shiftKey && event.key === "F10")) return;
    event.preventDefault();
    const target = event.target as HTMLElement;
    const bounds = target.getBoundingClientRect();
    showContextMenu(target, bounds.left + bounds.width / 2, bounds.top + bounds.height / 2);
  }

  async function runContextAction(action: string) {
    const target = contextMenu;
    setContextMenu(null);
    if (!target) return;
    switch (action) {
      case "play-pause":
        if (currentItem && currentItem.type !== "live") {
          requestControl(room.playing ? "pause" : "play", { at: expectedPosition() });
        }
        break;
      case "seek-back":
        if (currentItem && currentItem.type !== "live") {
          requestControl("seek", { to: Math.max(0, expectedPosition() - 10) });
        }
        break;
      case "seek-forward":
        if (currentItem && currentItem.type !== "live") {
          requestControl("seek", { to: expectedPosition() + 10 });
        }
        break;
      case "prev":
        if (canManage && room.current_index !== null) requestControl("select", { index: shuffleStep(room, -1) });
        break;
      case "next":
        if (canManage && room.current_index !== null) requestControl("select", { index: shuffleStep(room, 1) });
        break;
      case "shuffle":
        if (canManage) requestControl("shuffle", { on: !room.shuffle });
        break;
      case "fullscreen":
      case "pip":
        window.dispatchEvent(new CustomEvent("player:cmd", { detail: action }));
        break;
      case "queue":
      case "people":
      case "subs":
      case "me":
        openPanel(action);
        break;
      case "admin":
        window.location.href = `${basePath}/admin/`;
        break;
      default:
        if (action.startsWith("rate:") && canManage) requestControl("rate", { rate: parseFloat(action.slice(5)) });
        break;
      case "copy-name":
        if (target.name) {
          try {
            await navigator.clipboard.writeText(target.name);
          } catch {
            const id = `t${toastSeq++}`;
            setToasts((prev) => [...prev, { id, text: "کپی نام کاربر ناموفق بود" }]);
            setTimeout(() => setToasts((prev) => prev.filter((toast) => toast.id !== id)), 4000);
          }
        }
        break;
      case "mute-participant": {
        const participant = voice.participants.find((entry) => entry.name === target.name && !entry.isLocal);
        if (participant) voice.setParticipantVolume(participant.identity, 0);
        break;
      }
      case "chat":
        openPanel("chat");
        break;
      case "add-media":
        if (canAdd) openPanel("add");
        break;
      case "settings":
        setSettingsOpen(true);
        break;
      case "shortcuts":
        setShortcutsOpen(true);
        break;
    }
  }

  const speedChips = [0.75, 1, 1.25, 1.5, 1.75, 2];
  const hasPlayable = Boolean(currentItem && currentItem.type !== "live");

  const contextEntries: MenuEntry[] = !contextMenu
    ? []
    : contextMenu.kind === "player"
      ? [
          ...(currentItem ? [{ type: "title", label: currentItem.title.slice(0, 40) } as MenuEntry] : []),
          ...(hasPlayable
            ? ([
                { type: "item", id: "play-pause", label: room.playing ? "توقف" : "پخش", icon: room.playing ? Pause : Play, hint: "K" },
                { type: "item", id: "seek-back", label: "۱۰ ثانیه عقب", icon: Undo2, hint: "J" },
                { type: "item", id: "seek-forward", label: "۱۰ ثانیه جلو", icon: Redo2, hint: "L" },
                { type: "sep" },
                { type: "item", id: "prev", label: "قبلی", icon: SkipBack, disabled: !canManage, hint: "P" },
                { type: "item", id: "next", label: "بعدی", icon: SkipForward, disabled: !canManage, hint: "N" },
                { type: "item", id: "shuffle", label: "پخش تصادفی", icon: Shuffle, active: room.shuffle, disabled: !canManage },
                { type: "chips", label: "سرعت", icon: Gauge, disabled: !canManage, options: speedChips.map((r) => ({ id: `rate:${r}`, label: `${r}x`, active: room.rate === r })) },
                { type: "sep" },
              ] as MenuEntry[])
            : []),
          { type: "item", id: "fullscreen", label: "تمام‌صفحه", icon: Maximize, hint: "F" },
          { type: "item", id: "pip", label: "پخش شناور", icon: PictureInPicture2 },
          { type: "item", id: "subs", label: "زیرنویس و صدا", icon: Captions },
          { type: "sep" },
          { type: "item", id: "shortcuts", label: "میانبرها", icon: Keyboard, hint: "?" },
          { type: "item", id: "settings", label: "تنظیمات", icon: Settings },
        ]
      : contextMenu.kind === "user" || contextMenu.kind === "voice"
        ? [
            ...(contextMenu.name ? ([{ type: "title", label: contextMenu.name }, { type: "item", id: "copy-name", label: "کپی نام", icon: Copy }] as MenuEntry[]) : []),
            ...(contextMenu.kind === "voice" && voice.participants.some((p) => p.name === contextMenu.name && !p.isLocal)
              ? ([{ type: "item", id: "mute-participant", label: "بی‌صدا کردن (فقط برای من)", icon: VolumeX }] as MenuEntry[])
              : []),
            { type: "item", id: "chat", label: "برو به چت", icon: MessageCircle },
            ...(contextMenu.kind === "voice" ? ([{ type: "item", id: "settings", label: "تنظیمات صدا", icon: Settings }] as MenuEntry[]) : []),
          ]
        : [
            { type: "item", id: "queue", label: "صف پخش", icon: ListVideo },
            ...(canAdd ? ([{ type: "item", id: "add-media", label: "فیلم اضافه کن", icon: Plus }] as MenuEntry[]) : []),
            { type: "item", id: "chat", label: "چت", icon: MessageCircle },
            { type: "item", id: "people", label: "حاضرین", icon: Users },
            { type: "sep" },
            { type: "item", id: "shortcuts", label: "میانبرها", icon: Keyboard, hint: "?" },
            { type: "item", id: "settings", label: "تنظیمات", icon: Settings },
            ...(isAdm ? ([{ type: "sep" }, { type: "item", id: "admin", label: "پنل مدیریت", icon: LayoutDashboard }] as MenuEntry[]) : []),
          ];

  // ---- layout ----
  const vp = useViewport();
  const phonePortrait = vp.phone && vp.portrait;
  const sheet = phonePortrait;
  const shortScreen = vp.h < 560;
  const shiftForDrawer = Boolean(panel) && !sheet && vp.w >= 1020;
  const watchingNow = room.playing && !!currentItem && currentItem.type !== "live";
  const idle = useIdle(4500, watchingNow && !panel && !contextMenu);

  // ---- never let someone drift out of the cinema by accident ----
  const activeRef = useRef(false);
  activeRef.current = room.playing || voice.connected;
  const panelRef = useRef<PanelId | null>(null);
  panelRef.current = panel;
  const [leaveAsk, setLeaveAsk] = useState(false);
  const leaveResolver = useRef<((leave: boolean) => void) | null>(null);
  const leavingRef = useRef(false);

  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => {
      if (leavingRef.current || !activeRef.current) return;
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, []);

  useEffect(() => {
    // Back button / back swipe: first close whatever is open, then ask before leaving.
    if (isTauri() && !/android|iphone|ipad/i.test(navigator.userAgent)) return;
    history.pushState({ cinema: true }, "");
    const onPop = () => {
      if (leavingRef.current) return;
      history.pushState({ cinema: true }, "");
      if (panelRef.current) {
        setPanel(null);
        return;
      }
      leaveResolver.current = (leave) => {
        if (!leave) return;
        leavingRef.current = true;
        history.go(-2);
      };
      setLeaveAsk(true);
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  useEffect(() => {
    // Desktop app: closing the window while a film is playing asks first.
    if (!isTauri()) return;
    let off: (() => void) | undefined;
    guardNativeClose(
      () => activeRef.current,
      () =>
        new Promise<boolean>((resolve) => {
          leaveResolver.current = resolve;
          setLeaveAsk(true);
        })
    ).then((u) => (off = u));
    return () => off?.();
  }, []);

  const answerLeave = (leave: boolean) => {
    setLeaveAsk(false);
    const r = leaveResolver.current;
    leaveResolver.current = null;
    r?.(leave);
  };

  const [logoutAsk, setLogoutAsk] = useState(false);
  const [chipCopied, setChipCopied] = useState(false);

  if (!hydrated) return null;
  if (!user) return <AuthGate />;

  const voiceLive = voice.connected;
  const compactAudience = shortScreen || vp.w < 760;
  const stageStyle: React.CSSProperties = {
    paddingLeft: phonePortrait ? 0 : 24,
    paddingRight: phonePortrait ? 0 : shiftForDrawer ? "calc(76px + min(400px, calc(100vw - 100px)) + 24px)" : "76px",
    paddingBottom: phonePortrait ? "calc(76px + env(safe-area-inset-bottom, 0px))" : 0,
    transition: "padding 480ms cubic-bezier(.2,.8,.2,1)",
  };
  const screenWidth = phonePortrait
    ? "100%"
    : `min(100%, calc((100dvh - ${shortScreen ? 90 : 250}px) * 16 / 9))`;

  const copyInvite = () => {
    const origin = process.env.NEXT_PUBLIC_API_ORIGIN || window.location.origin;
    navigator.clipboard
      ?.writeText(`${origin}${basePath}/?join=${roomCode}`)
      .then(() => {
        setChipCopied(true);
        window.setTimeout(() => setChipCopied(false), 1800);
      })
      .catch(() => {});
  };

  const pushToast = (text: string) => {
    const id = `t${toastSeq++}`;
    setToasts((prev) => [...prev, { id, text }]);
    setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 4000);
  };

  const meta = panel ? PANEL_META[panel] : null;

  return (
    <main
      className={`cinema-root ${idle ? "lights-down" : ""}`}
      onContextMenu={handleContextMenu}
      onKeyDown={handleContextKeyDown}
    >
      <Scene />
      <Toasts toasts={toasts} />
      <OnboardingModal />
      <ShortcutsModal open={shortcutsOpen} onClose={() => setShortcutsOpen(false)} />
      <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} voice={voice} />

      {/* the stage */}
      <div className={`absolute inset-0 flex flex-col items-center ${phonePortrait ? "justify-start pt-2" : "justify-center"}`} style={{ ...stageStyle, paddingTop: phonePortrait ? "calc(8px + env(safe-area-inset-top, 0px))" : undefined }}>
        {!phonePortrait && !shortScreen && (
          <div className="mb-3">
            <Marquee pulseKey={presenceUsers.length} compact={vp.w < 900} />
          </div>
        )}

        <div className={phonePortrait ? "w-full px-2" : "relative z-10"} style={phonePortrait ? undefined : { width: screenWidth }}>
          <Player
            item={currentItem}
            playing={room.playing}
            rate={room.rate}
            expectedPosition={expectedPosition}
            requestControl={requestControl}
            transcodeProgress={transcodeProgress}
            latestNotify={latestNotify}
            myName={myName}
            subStyle={subStyle}
            canSpeed={canManage}
            canControl={canManage}
            onPlaybackState={reportWatching}
            canPrev={canManage && room.current_index !== null && (room.shuffle ? room.playlist.length > 1 : room.current_index > 0)}
            canNext={canManage && room.current_index !== null && (room.shuffle ? room.playlist.length > 1 : room.current_index < room.playlist.length - 1)}
            onPrev={() => canManage && room.current_index !== null && requestControl("select", { index: shuffleStep(room, -1) })}
            onNext={() => canManage && room.current_index !== null && requestControl("select", { index: shuffleStep(room, 1) })}
            shuffle={room.shuffle}
            canShuffle={canManage && room.playlist.length > 1}
            onToggleShuffle={() => requestControl("shuffle", { on: !room.shuffle })}
            onGoToAdd={canAdd ? () => openPanel("add") : undefined}
            onGoToSubStyle={() => openPanel("subs")}
            onOpenShortcuts={() => setShortcutsOpen(true)}
            onOpenSettings={() => setSettingsOpen(true)}
          />
        </div>

        {!phonePortrait && !shortScreen && (
          <div className="room-dim relative z-0 mt-5 w-full max-w-[760px]">
            <Audience users={presenceUsers} voiceSpeaking={voiceProfiles} compact={compactAudience} />
          </div>
        )}
        {phonePortrait && (
          <div className="room-dim mt-4 w-full px-2">
            <Audience users={presenceUsers} voiceSpeaking={voiceProfiles} compact max={8} />
          </div>
        )}
      </div>

      {/* your ticket: code + one-tap invite, top corner */}
      <button
        type="button"
        onClick={copyInvite}
        aria-label="کپی لینک دعوت"
        className={`room-dim glass absolute left-3 z-30 flex items-center gap-2 rounded-full py-1.5 pl-3.5 pr-2.5 text-[12px] text-white transition-opacity hover:bg-white/10`}
        style={{ top: "calc(10px + env(safe-area-inset-top, 0px))" }}
      >
        <span className={`h-2 w-2 rounded-full ${connected ? "bg-[color:var(--color-teal)]" : "animate-pulse bg-[color:var(--color-coral)]"}`} />
        {roomCode ? (
          <>
            <Ticket className="h-3.5 w-3.5 text-[color:var(--color-amber)]" />
            <span className="font-mono tracking-widest" dir="ltr">{roomCode}</span>
            <span className="text-white/55">{chipCopied ? "کپی شد ✓" : connected ? `${room.online} نفر` : "در حال اتصال…"}</span>
          </>
        ) : (
          <span>در حال اتصال…</span>
        )}
      </button>

      <Dock
        active={panel}
        onPick={togglePanel}
        canAdd={canAdd}
        unreadChat={unreadChat}
        online={room.online}
        voiceLive={voiceLive}
        layout={phonePortrait ? "bar" : "rail"}
        chromeHidden={idle}
      />

      <Drawer open={!!panel} title={meta?.title ?? ""} icon={meta?.icon} sheet={sheet} onClose={() => openPanel(null)}>
        {panel === "queue" && (
          <Playlist
            playlist={room.playlist}
            currentIndex={room.current_index}
            transcodeProgress={transcodeProgress}
            myName={myName}
            onSelect={(index) => {
              if (canManage) {
                requestControl("select", { index });
                if (sheet) openPanel(null);
              } else pushToast("فقط مدیر اتاق می‌تونه فیلم رو عوض کنه");
            }}
          />
        )}
        {panel === "add" && canAdd && (
          <AddHub myName={myName} canAdd={canAdd} canManage={canManage} onLiveStarted={() => requestControl("select", { index: room.playlist.length })} />
        )}
        {panel === "chat" && <ChatPanel messages={chatMessages} myName={myName} onSend={sendChat} />}
        {panel === "people" && (
          <div className="flex flex-col gap-6">
            {roomCode && <RoomBar roomCode={roomCode} online={room.online} onRoomChange={setRoomCode} />}
            <VoicePanel
              myName={myName}
              myAvatarUrl={myAvatarUrl}
              v={voice}
              onSpeakingChange={setVoiceProfiles}
              onVoiceJoin={() => setVoiceActive(true)}
              onVoiceLeave={() => setVoiceActive(false)}
              onOpenSettings={() => setSettingsOpen(true)}
            />
            <MembersPanel myName={myName} canManage={canManage} onToast={pushToast} />
          </div>
        )}
        {panel === "subs" && (
          <div className="flex flex-col gap-8">
            <SubAudioPanel playlist={room.playlist} myName={myName} canUpload={canManage} />
            <div>
              <h3 className="display mb-3 text-[18px] text-white">ظاهر زیرنویس</h3>
              <SubStylePanel value={subStyle} onChange={setSubStyle} />
            </div>
          </div>
        )}
        {panel === "me" && (
          <div className="flex flex-col gap-4">
            <Sidebar
              myName={myName}
              myAvatarUrl={myAvatarUrl}
              role={user.role}
              onChangeAvatar={async (file) => {
                try {
                  const { url } = await api.avatarUpload(file, myName);
                  setMyAvatarUrl(url);
                } catch (e) {
                  pushToast(e instanceof Error ? e.message : "خطا در آپلود عکس");
                }
              }}
              notifications={notifications}
              playlist={room.playlist}
            />
            <button onClick={() => setSettingsOpen(true)} className="flex items-center justify-center gap-2 rounded-2xl bg-white/8 py-3 text-[14px] font-semibold text-white hover:bg-white/14">
              <Settings className="h-4 w-4" /> تنظیمات
            </button>
            <button
              onClick={() => (logoutAsk ? logout() : (setLogoutAsk(true), window.setTimeout(() => setLogoutAsk(false), 3000)))}
              className={`flex items-center justify-center gap-2 rounded-2xl py-3 text-[14px] font-semibold transition-colors ${
                logoutAsk ? "bg-[color:var(--color-coral)] text-white" : "text-[color:var(--color-coral)] hover:bg-white/8"
              }`}
            >
              <LogOut className="h-4 w-4" /> {logoutAsk ? "مطمئنی؟ دوباره بزن" : "خروج از حساب"}
            </button>
          </div>
        )}
      </Drawer>

      {contextMenu && (
        <ContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          entries={contextEntries}
          onPick={(id) => void runContextAction(id)}
          onClose={() => setContextMenu(null)}
        />
      )}

      <ConfirmDialog
        open={leaveAsk}
        title="وسط فیلمی!"
        body={room.playing || voice.connected ? "اگه بری بیرون، پخش و صدات قطع می‌شه و بقیه بدون تو ادامه می‌دن." : "مطمئنی می‌خوای از سالن بری بیرون؟"}
        stay="می‌مونم 🍿"
        leave="آره، برم"
        onStay={() => answerLeave(false)}
        onLeave={() => answerLeave(true)}
      />
    </main>
  );
}
