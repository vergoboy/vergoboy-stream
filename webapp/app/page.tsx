"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { motion } from "framer-motion";
import { useLocalStorage } from "@/lib/useLocalStorage";
import { useRoomState } from "@/lib/useRoomState";
import { api } from "@/lib/api";
import { DEFAULT_SUB_STYLE, type SubStyle } from "@/lib/types";
import type { NotifyEvent } from "@/lib/types";

import { Header } from "@/components/Header";
import { Footer } from "@/components/Footer";
import { Sofa } from "@/components/Sofa";
import { Player } from "@/components/Player";
import { Tabs } from "@/components/Tabs";
import { Playlist } from "@/components/Playlist";
import { AddVideoPanel } from "@/components/AddVideoPanel";
import { LivePanel } from "@/components/LivePanel";
import { SubAudioPanel } from "@/components/SubAudioPanel";
import { SubStylePanel } from "@/components/SubStylePanel";
import { ChatPanel } from "@/components/ChatPanel";
import { Sidebar } from "@/components/Sidebar";
import { Toasts, type Toast } from "@/components/Toasts";
import { NameGate } from "@/components/NameGate";
import { ShortcutsModal } from "@/components/ShortcutsModal";
import { notifyText } from "@/lib/notifyText";

let toastSeq = 0;

export default function Page() {
  const [myName, setMyName, nameHydrated] = useLocalStorage("stream_user_name", "");
  const [myAvatarUrl, setMyAvatarUrl] = useLocalStorage<string | null>("stream_user_avatar", null);
  const [subStyle, setSubStyle] = useLocalStorage<SubStyle>("stream_sub_style", DEFAULT_SUB_STYLE);
  const [renaming, setRenaming] = useState(false);
  const [activeTab, setActiveTab] = useState("playlist");
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [latestNotify, setLatestNotify] = useState<NotifyEvent | null>(null);
  const [unreadChat, setUnreadChat] = useState(0);

  const handleNotify = (n: NotifyEvent) => {
    setLatestNotify(n);
    const id = `t${toastSeq++}`;
    setToasts((prev) => [...prev, { id, text: notifyText(n, room.playlist) }]);
    setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 4000);
  };

  const { connected, room, presenceUsers, notifications, chatMessages, transcodeProgress, expectedPosition, requestControl, sendChat } =
    useRoomState({ myName, onNotify: handleNotify });

  const currentItem = useMemo(
    () => (room.current_index !== null ? room.playlist[room.current_index] ?? null : null),
    [room.current_index, room.playlist]
  );

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

  if (!nameHydrated) return null;

  if (!myName || renaming) {
    return (
      <NameGate
        initialName={myName}
        onJoin={(n) => {
          setMyName(n.trim().slice(0, 24) || "ناشناس");
          setRenaming(false);
        }}
      />
    );
  }

  return (
    <main className="page relative z-[1]">
      <Header />
      <Toasts toasts={toasts} />
      <ShortcutsModal open={shortcutsOpen} onClose={() => setShortcutsOpen(false)} />

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
              👥 <span className="font-mono text-[color:var(--color-amber)]">{room.online}</span> آنلاین
              {!connected && <span className="text-[color:var(--color-coral)]">· در حال اتصال…</span>}
            </span>
          </p>
        </motion.div>

        <Sofa users={presenceUsers} />

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
            canPrev={room.current_index !== null && room.current_index > 0}
            canNext={room.current_index !== null && room.current_index < room.playlist.length - 1}
            onPrev={() => room.current_index !== null && requestControl("select", { index: room.current_index - 1 })}
            onNext={() => room.current_index !== null && requestControl("select", { index: room.current_index + 1 })}
            onGoToAdd={() => setActiveTab("add")}
            onGoToSubStyle={() => setActiveTab("substyle")}
            onOpenShortcuts={() => setShortcutsOpen(true)}
          />

        <section className="my-6 grid gap-5 lg:grid-cols-[1fr_300px]">
          <div className="rounded-3xl border border-[color:var(--color-border)] bg-[color:var(--color-bg-soft)]/70 p-5 backdrop-blur-md">
            <Tabs
              active={activeTab}
              onChange={handleTabChange}
              tabs={[
                { id: "playlist", label: "🎬 پلی‌لیست" },
                { id: "add", label: "➕ افزودن ویدیو" },
                { id: "live", label: "📡 استریم خارجی" },
                { id: "subaudio", label: "🔤 زیرنویس و صدا" },
                { id: "substyle", label: "🎨 استایل زیرنویس" },
                { id: "chat", label: "💬 چت", badge: unreadChat },
              ]}
            />

            <motion.div key={activeTab} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25 }}>
              {activeTab === "playlist" && (
                <Playlist
                  playlist={room.playlist}
                  currentIndex={room.current_index}
                  transcodeProgress={transcodeProgress}
                  myName={myName}
                  onSelect={(index) => requestControl("select", { index })}
                />
              )}
              {activeTab === "add" && <AddVideoPanel myName={myName} />}
              {activeTab === "live" && <LivePanel myName={myName} />}
              {activeTab === "subaudio" && <SubAudioPanel playlist={room.playlist} myName={myName} />}
              {activeTab === "substyle" && <SubStylePanel value={subStyle} onChange={setSubStyle} />}
              {activeTab === "chat" && <ChatPanel messages={chatMessages} myName={myName} onSend={sendChat} />}
            </motion.div>
          </div>

          <Sidebar
            myName={myName}
            myAvatarUrl={myAvatarUrl}
            onChangeName={() => setRenaming(true)}
            onChangeAvatar={async (file) => {
              try {
                const { url } = await api.avatarUpload(file, myName);
                setMyAvatarUrl(url);
              } catch (e) {
                alert(e instanceof Error ? e.message : "خطا در آپلود عکس");
              }
            }}
            onlineUsers={presenceUsers}
            notifications={notifications}
            playlist={room.playlist}
          />
        </section>
      </div>

      <Footer />
    </main>
  );
}
