"use client";

import { Send, Image, Trash2, X } from "lucide-react";


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

    const [lightboxImg, setLightboxImg] = useState<string | null>(null);

  return (
    <div>
      {lightboxImg && (
        <div
          className="fixed inset-0 z-[99999] flex items-center justify-center bg-black/90 p-4 backdrop-blur-md cursor-pointer"
          onClick={() => setLightboxImg(null)}
        >
          <div className="relative max-h-[90vh] max-w-[90vw]">
            <img src={lightboxImg} alt="" className="max-h-[85vh] max-w-[85vw] rounded-2xl object-contain shadow-2xl" />
            <button
              onClick={() => setLightboxImg(null)}
              className="absolute -top-4 -right-4 flex h-9 w-9 items-center justify-center rounded-full bg-white/20 text-white hover:bg-white/40"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>
      )}
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <p className="m-0 text-[12.5px] leading-relaxed text-[color:var(--color-ink-dim)]">
          چت فقط در حافظه‌ی سرور نگه داشته می‌شه؛ خودش هر ۲۴ ساعت پاک می‌شه، یا با دکمه‌ی روبه‌رو الان پاکش کن.
        </p>
        <button
          onClick={clearChat}
          className="shrink-0 rounded-xl border border-[color:var(--color-border)] bg-white/5 px-3 py-1.5 text-xs text-[color:var(--color-ink)] hover:border-[color:var(--color-coral)]/50"
        >
          <Trash2 className="w-4 h-4" /> پاک‌کردن چت
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
              className={`flex items-start gap-2.5 ${m.name === myName ? "flex-row-reverse" : ""}`}
            >
              <Avatar name={m.name} url={m.avatar_url} size={32} />
              <div className={`min-w-0 max-w-[82%] flex flex-col ${m.name === myName ? "items-end" : "items-start"}`}>
                <div className="flex items-center gap-2 mb-1 px-1">
                  <span className={`text-xs font-bold ${m.name === myName ? "text-[color:var(--color-amber)]" : "text-[color:var(--color-ink)]"}`}>
                    {m.name}
                  </span>
                  <span className="font-mono text-[10px] text-[color:var(--color-ink-dim)]" dir="ltr">
                    {new Date(m.ts * 1000).toLocaleTimeString("fa-IR")}
                  </span>
                </div>
                <div
                  className={`p-3 rounded-2xl text-[13.5px] leading-relaxed break-words border shadow-sm ${
                    m.name === myName
                      ? "bg-gradient-to-r from-[color:var(--color-amber)]/90 to-[color:var(--color-plum)]/90 text-white rounded-tr-xs border-transparent"
                      : "bg-white/10 text-white rounded-tl-xs border-white/10 backdrop-blur-md"
                  }`}
                >
                  {m.text && <div className="whitespace-pre-wrap">{m.text}</div>}
                  {m.image_url && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={m.image_url}
                      alt=""
                      className="mt-2 max-h-56 max-w-full cursor-pointer rounded-xl border border-white/15 hover:opacity-95 transition-opacity"
                      onClick={() => setLightboxImg(m.image_url!)}
                    />
                  )}
                </div>
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
            <X className="w-4 h-4" />
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