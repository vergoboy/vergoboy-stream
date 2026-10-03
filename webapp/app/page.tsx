"use client";

import { useEffect, useCallback, useMemo, useRef, useState } from "react";
import { motion } from "framer-motion";
import { useLocalStorage } from "@/lib/useLocalStorage";
import { useRoomState } from "@/lib/useRoomState";
import { api } from "@/lib/api";
import { DEFAULT_SUB_STYLE, type SubStyle } from "@/lib/types";
import type { NotifyEvent } from "@/lib/types";
import { useAuth, logout, updateUser, isAdmin, mayAdd, mayControl, mayManage, finishOAuthLogin } from "@/lib/auth";

import { Header } from "@/components/Header";
import { Footer } from "@/components/Footer";
import { Sofa } from "@/components/Sofa";
import { Player } from "@/components/Player";
import { Tabs } from "@/components/Tabs";
import { Playlist } from "@/components/Playlist";
import { AddVideoPanel } from "@/components/AddVideoPanel";
import { ArchivePanel } from "@/components/ArchivePanel";
import { LivePanel } from "@/components/LivePanel";
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
import { SettingsProvider } from "@/lib/settings";
import { useVoiceRoom } from "@/lib/useVoiceRoom";
import { notifyText } from "@/lib/notifyText";
import { ListVideo, Plus, Archive, Radio, Captions, Palette, MessageSquare, Users } from "lucide-react";

let toastSeq = 0;

type ContextTarget = "player" | "user" | "voice" | "page";
type ContextMenuState = { x: number; y: number; kind: ContextTarget; name: string | null };

const basePath = process.env.NEXT_PUBLIC_BUILD_TARGET === "tauri" ? "" : "/stream";

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
  const [activeTab, setActiveTab] = useState("playlist");
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [latestNotify, setLatestNotify] = useState<NotifyEvent | null>(null);
  const [unreadChat, setUnreadChat] = useState(0);
  const [voiceProfiles, setVoiceProfiles] = useState<{ name: string; avatarUrl: string | null; speaking: boolean }[]>([]);
  const [joinCode, setJoinCode] = useState<string | null>(null);
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);
  const contextMenuRef = useRef<HTMLDivElement>(null);

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
    if (grew > 0 && activeTab !== "chat") {
      const newOnes = chatMessages.slice(-grew);
      const fromOthers = newOnes.filter((m) => m.name !== myName).length;
      if (fromOthers > 0) setUnreadChat((n) => n + fromOthers);
    }
    prevChatLenRef.current = chatMessages.length;
  }, [chatMessages, activeTab, myName]);

  function handleTabChange(id: string) {
    setActiveTab(id);
    if (id === "chat") setUnreadChat(0);
  }

  function showContextMenu(target: EventTarget | null, x: number, y: number) {
    const element = target instanceof Element ? target : null;
    const contextTarget = element?.closest<HTMLElement>("[data-context-kind]");
    const kind = contextTarget?.dataset.contextKind as ContextTarget | undefined;
    const menuWidth = 224;
    const menuHeight = 240;
    setContextMenu({
      x: Math.max(8, Math.min(x, window.innerWidth - menuWidth - 8)),
      y: Math.max(8, Math.min(y, window.innerHeight - menuHeight - 8)),
      kind: kind ?? "page",
      name: contextTarget?.dataset.contextName ?? null,
    });
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

  function handleContextMenuKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      setContextMenu(null);
      return;
    }
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    const items = Array.from(contextMenuRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]') ?? []);
    if (!items.length) return;
    event.preventDefault();
    const currentIndex = items.indexOf(document.activeElement as HTMLButtonElement);
    const step = event.key === "ArrowDown" ? 1 : -1;
    items[(currentIndex + step + items.length) % items.length]?.focus();
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
      case "rate-1":
        if (canManage) requestControl("rate", { rate: 1 });
        break;
      case "rate-1.5":
        if (canManage) requestControl("rate", { rate: 1.5 });
        break;
      case "rate-2":
        if (canManage) requestControl("rate", { rate: 2 });
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
        handleTabChange("chat");
        break;
      case "add-media":
        if (canAdd) setActiveTab("add");
        break;
      case "settings":
        setSettingsOpen(true);
        break;
      case "shortcuts":
        setShortcutsOpen(true);
        break;
    }
  }

  const contextActions = contextMenu?.kind === "player"
    ? [
        ...(currentItem && currentItem.type !== "live" ? [
          { id: "play-pause", label: room.playing ? "توقف" : "پخش" },
          { id: "seek-back", label: "۱۰ ثانیه عقب" },
          { id: "seek-forward", label: "۱۰ ثانیه جلو" },
          { id: "prev", label: "آیتم قبلی" },
          { id: "next", label: "آیتم بعدی" },
          { id: "shuffle", label: room.shuffle ? "پخش ترتیبی" : "پخش تصادفی" },
          { id: "rate-1", label: "سرعت ۱x" },
          { id: "rate-1.5", label: "سرعت ۱.۵x" },
          { id: "rate-2", label: "سرعت ۲x" },
        ] : []),
        { id: "shortcuts", label: "میانبرها" },
        { id: "settings", label: "تنظیمات" },
      ]
    : contextMenu?.kind === "user"
      ? [
          ...(contextMenu.name ? [{ id: "copy-name", label: `کپی نام ${contextMenu.name}` }] : []),
          { id: "chat", label: "رفتن به چت" },
        ]
      : contextMenu?.kind === "voice"
        ? [
            ...(contextMenu.name ? [{ id: "copy-name", label: `کپی نام ${contextMenu.name}` }] : []),
            ...(voice.participants.some((entry) => entry.name === contextMenu.name && !entry.isLocal)
              ? [{ id: "mute-participant", label: "بی‌صدا کردن این کاربر برای من" }]
              : []),
            { id: "settings", label: "تنظیمات صدا" },
          ]
        : [
            { id: "chat", label: "رفتن به چت" },
            ...(canAdd ? [{ id: "add-media", label: "افزودن ویدیو" }] : []),
            { id: "shortcuts", label: "میانبرها" },
            { id: "settings", label: "تنظیمات" },
          ];

  useEffect(() => {
    if (!contextMenu) return;
    const closeOnOutsidePointer = (event: PointerEvent) => {
      if (!(event.target instanceof Element) || !event.target.closest("[data-context-menu-root]")) {
        setContextMenu(null);
      }
    };
    window.addEventListener("pointerdown", closeOnOutsidePointer);
    return () => window.removeEventListener("pointerdown", closeOnOutsidePointer);
  }, [contextMenu]);

  if (!hydrated) return null;

  if (!user) {
    return <AuthGate />;
  }

  return (
    <main
      className="page relative z-[1]"
      onContextMenu={handleContextMenu}
      onKeyDown={handleContextKeyDown}
      onClick={(event) => {
        if (!(event.target as HTMLElement).closest("[data-context-menu-root]")) setContextMenu(null);
      }}
    >
      <Header user={user} onLogout={() => logout()} />
      <Toasts toasts={toasts} />
      <OnboardingModal />
      <ShortcutsModal open={shortcutsOpen} onClose={() => setShortcutsOpen(false)} />
      <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} voice={voice} />
      {contextMenu && (
        <div
          ref={contextMenuRef}
          data-context-menu-root
          role="menu"
          aria-label="عملیات"
          dir="rtl"
          onContextMenu={(event) => event.stopPropagation()}
          onClick={(event) => event.stopPropagation()}
          onKeyDown={handleContextMenuKeyDown}
          className="fixed z-[10000] min-w-56 overflow-hidden rounded-xl border border-[color:var(--color-border)] bg-[color:var(--color-bg)]/95 p-1.5 shadow-2xl backdrop-blur-xl"
          style={{ left: contextMenu.x, top: contextMenu.y }}
        >
          {contextActions.map((action, index) => (
            <button
              key={action.id}
              type="button"
              role="menuitem"
              autoFocus={index === 0}
              onClick={() => void runContextAction(action.id)}
              className="flex min-h-9 w-full items-center rounded-lg px-3 text-right text-[13px] text-[color:var(--color-ink)] outline-none transition-colors hover:bg-white/10 focus-visible:bg-white/10 focus-visible:ring-2 focus-visible:ring-[color:var(--color-amber)]"
            >
              {action.label}
            </button>
          ))}
        </div>
      )}

      <div className="mx-auto max-w-[1120px] px-4 pt-8 md:px-7">
        <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4 }} className="mb-5">
          <h1
            className="mb-1.5 text-2xl font-extrabold md:text-3xl"
            style={{ background: "linear-gradient(135deg, var(--color-amber), var(--color-plum-soft))", WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent" }}
          >
            تماشای مشترک
          </h1>
          <p className="flex flex-wrap items-center gap-2.5 text-[14.5px] text-[color:var(--color-ink-muted)]">
            فیلم رو هم‌زمان با بقیه تماشا کن — پخش، توقف و زمان برای همه یکی‌ست.
            <span className="inline-flex items-center gap-1.5 rounded-full border border-[color:var(--color-border)] bg-white/5 px-3 py-1 text-xs">
              <Users className="h-3.5 w-3.5 text-[color:var(--color-amber)]" />
              <span className="font-mono text-[color:var(--color-amber)]">{room.online}</span> آنلاین
              {!connected && <span className="text-[color:var(--color-coral)]">· در حال اتصال…</span>}
            </span>
          </p>
        </motion.div>

        {roomCode && <RoomBar roomCode={roomCode} online={room.online} onRoomChange={setRoomCode} />}

        {/* Exactly two couches. They are a metaphor for doing something together,
            not a capacity limit — any number of people can be on either one,
            and the same person can be on both at once (watching while talking). */}
        <div className="grid gap-4 mb-6 md:grid-cols-2">
          <Sofa
            title="WATCHING"
            description="کسانی که همین حالا دارند ویدیوی مشترک را تماشا می‌کنند."
            users={watchingUsers}
            voiceSpeaking={voiceProfiles}
            kind="watching"
            emptyLabel="هنوز کسی در حال تماشا نیست — پخش را شروع کنید."
          />
          <Sofa
            title="VOICE"
            description="در اتاق صدا با هم کنار هم هستیم؛ کسی که صحبت می‌کند، روشن می‌شود."
            users={voiceUsers}
            voiceSpeaking={voiceProfiles}
            kind="voice"
            emptyLabel="هنوز کسی وارد اتاق صدا نشده."
          />
        </div>

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
            onGoToAdd={() => setActiveTab("add")}
            onGoToSubStyle={() => setActiveTab("substyle")}
            onOpenShortcuts={() => setShortcutsOpen(true)}
            onOpenSettings={() => setSettingsOpen(true)}
          />

        <section className="my-6 grid gap-5 lg:grid-cols-[1fr_300px]">
          <div className="rounded-3xl border border-[color:var(--color-border)] bg-[color:var(--color-bg-soft)]/70 p-5 backdrop-blur-md">
            <Tabs
              active={activeTab}
              onChange={handleTabChange}
              tabs={[
                { id: "playlist", label: (<span className="flex items-center gap-1.5"><ListVideo className="w-4 h-4" /> پلی‌لیست</span>) },
                ...(canAdd
                  ? [
                      { id: "add", label: (<span className="flex items-center gap-1.5"><Plus className="w-4 h-4" /> افزودن ویدیو</span>) } as const,
                      { id: "archive", label: (<span className="flex items-center gap-1.5"><Archive className="w-4 h-4" /> آرشیو جستجو</span>) } as const,
                    ]
                  : []),
                ...(canAdd ? [{ id: "live", label: (<span className="flex items-center gap-1.5"><Radio className="w-4 h-4" /> استریم خارجی</span>) } as const] : []),
                { id: "subaudio", label: (<span className="flex items-center gap-1.5"><Captions className="w-4 h-4" /> زیرنویس و صدا</span>) },
                { id: "substyle", label: (<span className="flex items-center gap-1.5"><Palette className="w-4 h-4" /> استایل زیرنویس</span>) },
                { id: "chat", label: (<span className="flex items-center gap-1.5"><MessageSquare className="w-4 h-4" /> چت</span>), badge: unreadChat },
              ]}
            />

            <motion.div key={activeTab} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25 }}>
              {activeTab === "playlist" && (
                <Playlist
                  playlist={room.playlist}
                  currentIndex={room.current_index}
                  transcodeProgress={transcodeProgress}
                  myName={myName}
                  onSelect={(index) => canManage && requestControl("select", { index })}
                />
              )}
              {activeTab === "add" && <AddVideoPanel myName={myName} />}
              {activeTab === "archive" && <ArchivePanel myName={myName} />}
              {activeTab === "live" && (
                <LivePanel
                  myName={myName}
                  canAdd={canAdd}
                  canManage={canManage}
                  onLiveStarted={() => requestControl("select", { index: room.playlist.length })}
                />
              )}
              {activeTab === "subaudio" && <SubAudioPanel playlist={room.playlist} myName={myName} canUpload={canManage} />}
              {activeTab === "substyle" && <SubStylePanel value={subStyle} onChange={setSubStyle} />}
              {activeTab === "chat" && <ChatPanel messages={chatMessages} myName={myName} onSend={sendChat} />}
            </motion.div>
          </div>

          <div className="flex flex-col gap-5">
            <VoicePanel
              myName={myName}
              myAvatarUrl={myAvatarUrl}
              v={voice}
              onSpeakingChange={setVoiceProfiles}
              onVoiceJoin={() => setVoiceActive(true)}
              onVoiceLeave={() => setVoiceActive(false)}
              onOpenSettings={() => setSettingsOpen(true)}
            />
            <Sidebar
              myName={myName}
              myAvatarUrl={myAvatarUrl}
              role={user.role}
              onChangeAvatar={async (file) => {
                try {
                  const { url } = await api.avatarUpload(file, myName);
                  setMyAvatarUrl(url);
                } catch (e) {
                  alert(e instanceof Error ? e.message : "خطا در آپلود عکس");
                }
              }}
              notifications={notifications}
              playlist={room.playlist}
            />
            <MembersPanel
              myName={myName}
              canManage={canManage}
              onToast={(msg) => {
                const id = `t${toastSeq++}`;
                setToasts((prev) => [...prev, { id, text: msg }]);
                setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 4000);
              }}
            />
            {isAdm && (
              <a
                href={`${basePath}/admin/`}
                className="rounded-3xl border border-[color:var(--color-border)] bg-[color:var(--color-bg-soft)]/70 p-5 text-center text-[14px] font-bold text-[color:var(--color-amber)] backdrop-blur-md hover:border-[color:var(--color-amber)]/50"
              >
                پنل مدیریت کاربران
              </a>
            )}
          </div>
        </section>
      </div>

      <Footer />
    </main>
  );
}
