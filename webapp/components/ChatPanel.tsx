"use client";

import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Avatar } from "./Avatar";
import { api } from "@/lib/api";
import type { ChatMessage } from "@/lib/types";

export function ChatPanel({
  messages,
  myName,
  onSend,
}: {
  messages: ChatMessage[];
  myName: string;
  onSend: (text: string, imageUrl: string | null) => void;
}) {
  const [text, setText] = useState("");
  const [pendingImage, setPendingImage] = useState<string | null>(null);
  const feedRef = useRef<HTMLUListElement>(null);

  useEffect(() => {
    feedRef.current?.scrollTo({ top: feedRef.current.scrollHeight, behavior: "smooth" });
  }, [messages.length]);

  async function handleImagePick(file: File) {
    try {
      const { url } = await api.chatImage(file);
      setPendingImage(url);
    } catch (e) {
      alert(e instanceof Error ? e.message : "خطا در آپلود عکس");
    }
  }

  function submit() {
    if (!text.trim() && !pendingImage) return;
    onSend(text.trim(), pendingImage);
    setText("");
    setPendingImage(null);
  }

  async function clearChat() {
    if (!confirm("کل چت برای همه پاک بشه؟ این کار قابل بازگشت نیست.")) return;
    await api.chatClear(myName);
  }

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <p className="m-0 text-[12.5px] leading-relaxed text-[color:var(--color-ink-dim)]">
          چت فقط در حافظه‌ی سرور نگه داشته می‌شه؛ خودش هر ۲۴ ساعت پاک می‌شه، یا با دکمه‌ی روبه‌رو الان پاکش کن.
        </p>
        <button
          onClick={clearChat}
          className="shrink-0 rounded-xl border border-[color:var(--color-border)] bg-white/5 px-3 py-1.5 text-xs text-[color:var(--color-ink)] hover:border-[color:var(--color-coral)]/50"
        >
          🗑 پاک‌کردن چت
        </button>
      </div>

      <ul ref={feedRef} className="mb-3 flex max-h-[420px] flex-col gap-3 overflow-y-auto">
        {messages.length === 0 && (
          <p className="py-4 text-center text-[13.5px] text-[color:var(--color-ink-dim)]">
            هنوز پیامی نیست — اولین نفر باش!
          </p>
        )}
        <AnimatePresence initial={false}>
          {messages.map((m) => (
            <motion.li
              key={m.id}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              className="flex items-start gap-2.5"
            >
              <Avatar name={m.name} url={m.avatar_url} size={32} />
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline gap-2">
                  <span className={`text-xs font-bold ${m.name === myName ? "text-[color:var(--color-amber)]" : "text-[color:var(--color-ink)]"}`}>
                    {m.name}
                  </span>
                  <span className="font-mono text-[10.5px] text-[color:var(--color-ink-dim)]" dir="ltr">
                    {new Date(m.ts * 1000).toLocaleTimeString("fa-IR")}
                  </span>
                </div>
                {m.text && <div className="mt-0.5 whitespace-pre-wrap break-words text-[13.5px] text-[color:var(--color-ink)]">{m.text}</div>}
                {m.image_url && (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={m.image_url}
                    alt=""
                    className="mt-1.5 max-h-56 max-w-56 cursor-zoom-in rounded-xl border border-[color:var(--color-border)]"
                    onClick={() => window.open(m.image_url!, "_blank")}
                  />
                )}
              </div>
            </motion.li>
          ))}
        </AnimatePresence>
      </ul>

      {pendingImage && (
        <div className="mb-2 flex items-center gap-2.5 rounded-xl border border-[color:var(--color-border)] bg-white/5 px-2.5 py-2">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={pendingImage} alt="" className="h-11 w-11 rounded-lg object-cover" />
          <button onClick={() => setPendingImage(null)} className="mr-auto text-[color:var(--color-ink-dim)] hover:text-[color:var(--color-coral)]">
            ✕
          </button>
        </div>
      )}

      <form
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
        className="flex items-center gap-2"
      >
        <label className="flex h-9 w-9 shrink-0 cursor-pointer items-center justify-center rounded-xl border border-[color:var(--color-border)] bg-white/5 text-[15px] hover:border-[color:var(--color-amber)]/50">
          🖼
          <input
            type="file"
            accept="image/png,image/jpeg,image/webp,image/gif"
            hidden
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) handleImagePick(f);
              e.target.value = "";
            }}
          />
        </label>
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          maxLength={500}
          placeholder="پیامت رو بنویس…"
          className="flex-1 rounded-xl border border-[color:var(--color-border)] bg-white/5 px-3.5 py-2.5 text-[13.5px] text-[color:var(--color-ink)] outline-none focus:border-[color:var(--color-amber)]"
        />
        <button
          type="submit"
          className="shrink-0 rounded-xl px-4 py-2.5 text-[13.5px] font-bold text-white"
          style={{ background: "linear-gradient(135deg, var(--color-amber), var(--color-plum))" }}
        >
          ارسال
        </button>
      </form>
    </div>
  );
}
