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

  const { connected, room, presenceUsers, notifications, chatMessages, transcodeProgress, expectedPosition, requestControl, sendChat, setVoiceActive } =
    useRoomState({ token: access, roomCode: roomCode ?? "", myName, canControl, myId: user?.id, onNotify: handleNotify, onKicked: handleKicked });

  // Only people actually connected to the voice room sit on the sofa — a
  // regular online visitor must stay invisible to the others until they
  // join the voice chat.
  const sofaUsers = presenceUsers.filter((u) => u.in_voice);

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

  if (!hydrated) return null;

  if (!user) {
    return <AuthGate />;
  }

  return (
    <main className="page relative z-[1]">
      <Header user={user} onLogout={() => logout()} />
      <Toasts toasts={toasts} />
      <OnboardingModal />
      <ShortcutsModal open={shortcutsOpen} onClose={() => setShortcutsOpen(false)} />
      <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} voice={voice} />

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

        <Sofa users={sofaUsers} voiceSpeaking={voiceProfiles} />

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
                { id: "playlist", label: (<span className="flex items-center gap-1.5"><ListVideo className="h-4 w-4" /> پلی‌لیست</span>) },
                ...(canAdd
                  ? [
                      { id: "add", label: (<span className="flex items-center gap-1.5"><Plus className="h-4 w-4" /> افزودن ویدیو</span>) } as const,
                      { id: "archive", label: (<span className="flex items-center gap-1.5"><Archive className="h-4 w-4" /> آرشیو جستجو</span>) } as const,
                    ]
                  : []),
                ...(canAdd ? [{ id: "live", label: (<span className="flex items-center gap-1.5"><Radio className="h-4 w-4" /> استریم خارجی</span>) } as const] : []),
                { id: "subaudio", label: (<span className="flex items-center gap-1.5"><Captions className="h-4 w-4" /> زیرنویس و صدا</span>) },
                { id: "substyle", label: (<span className="flex items-center gap-1.5"><Palette className="h-4 w-4" /> استایل زیرنویس</span>) },
                { id: "chat", label: (<span className="flex items-center gap-1.5"><MessageSquare className="h-4 w-4" /> چت</span>), badge: unreadChat },
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
              onLogout={() => logout()}
              onlineUsers={presenceUsers}
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
