"use client";

import { motion, AnimatePresence } from "framer-motion";
import { Avatar } from "./Avatar";
import type { NotifyEvent, PlaylistItem, PresenceUser } from "@/lib/types";
import { notifyText } from "@/lib/notifyText";

export function Sidebar({
  myName,
  myAvatarUrl,
  onChangeName,
  onChangeAvatar,
  onlineUsers,
  notifications,
  playlist,
}: {
  myName: string;
  myAvatarUrl: string | null;
  onChangeName: () => void;
  onChangeAvatar: (file: File) => void;
  onlineUsers: PresenceUser[];
  notifications: NotifyEvent[];
  playlist: PlaylistItem[];
}) {
  return (
    <aside className="flex flex-col gap-5 rounded-3xl border border-[color:var(--color-border)] bg-[color:var(--color-bg-soft)]/70 p-5 backdrop-blur-md">
      <div>
        <div className="mb-3 flex items-center gap-3 border-b border-[color:var(--color-border)] pb-3">
          <Avatar name={myName} url={myAvatarUrl} size={38} />
          <div className="min-w-0 flex-1">
            <div className="truncate text-[13px] text-[color:var(--color-ink-muted)]">
              شما: <b className="text-[color:var(--color-ink)]">{myName}</b>
            </div>
            <div className="flex gap-3 text-[11.5px]">
              <button onClick={onChangeName} className="text-[color:var(--color-amber)] hover:underline">
                تغییر نام
              </button>
              <label className="cursor-pointer text-[color:var(--color-amber)] hover:underline">
                تغییر عکس
                <input
                  type="file"
                  accept="image/png,image/jpeg,image/webp,image/gif"
                  hidden
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) onChangeAvatar(f);
                    e.target.value = "";
                  }}
                />
              </label>
            </div>
          </div>
        </div>

        <h4 className="mb-2.5 text-[13px] font-bold text-[color:var(--color-ink)]">
          👥 آنلاین ({onlineUsers.length})
        </h4>
        <ul className="flex flex-wrap gap-1.5">
          {onlineUsers.map((u) => (
            <li
              key={u.name}
              className="flex items-center gap-1.5 rounded-full border border-[color:var(--color-border)] bg-white/5 py-1 pl-2.5 pr-1 text-xs text-[color:var(--color-ink)]"
            >
              <Avatar name={u.name} url={u.avatar_url} size={18} />
              <span className="h-1.5 w-1.5 rounded-full bg-[color:var(--color-teal)]" />
              {u.name}
            </li>
          ))}
        </ul>
      </div>

      <div>
        <h4 className="mb-2.5 text-[13px] font-bold text-[color:var(--color-ink)]">🔔 رویدادهای اخیر</h4>
        <ul className="flex max-h-80 flex-col gap-2 overflow-y-auto">
          <AnimatePresence initial={false}>
            {notifications.map((n, i) => (
              <motion.li
                key={`${n.ts}-${i}`}
                initial={{ opacity: 0, x: -8 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0 }}
                className="border-b border-white/5 pb-2 text-[12.5px] leading-relaxed text-[color:var(--color-ink-muted)]"
                dangerouslySetInnerHTML={{
                  __html: `${notifyText(n, playlist)}<span class="mt-0.5 block font-mono text-[10.5px] text-[color:var(--color-ink-dim)]" dir="ltr">${new Date(
                    n.ts * 1000
                  ).toLocaleTimeString("fa-IR")}</span>`,
                }}
              />
            ))}
          </AnimatePresence>
        </ul>
      </div>
    </aside>
  );
}