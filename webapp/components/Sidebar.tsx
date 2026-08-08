"use client";

import { motion, AnimatePresence } from "framer-motion";
import { Users, Bell, LogOut, Shield, Gamepad2, Eye } from "lucide-react";
import { Avatar } from "./Avatar";
import type { NotifyEvent, PlaylistItem, PresenceUser, UserRole } from "@/lib/types";
import { notifyText } from "@/lib/notifyText";

const ROLE_LABELS: Record<UserRole, { label: string; icon: React.ReactNode; color: string }> = {
  admin: { label: "ادمین", icon: <Shield className="h-3.5 w-3.5" />, color: "text-[color:var(--color-amber)]" },
  controller: { label: "کنترلر", icon: <Gamepad2 className="h-3.5 w-3.5" />, color: "text-[color:var(--color-teal)]" },
  watcher: { label: "تماشاگر", icon: <Eye className="h-3.5 w-3.5" />, color: "text-[color:var(--color-ink-dim)]" },
};

export function Sidebar({
  myName,
  myAvatarUrl,
  role,
  onChangeAvatar,
  onLogout,
  onlineUsers,
  notifications,
  playlist,
}: {
  myName: string;
  myAvatarUrl: string | null;
  role: UserRole;
  onChangeAvatar: (file: File) => void;
  onLogout: () => void;
  onlineUsers: PresenceUser[];
  notifications: NotifyEvent[];
  playlist: PlaylistItem[];
}) {
  const roleInfo = ROLE_LABELS[role] ?? ROLE_LABELS.watcher;

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
              <button onClick={onLogout} className="flex items-center gap-1 text-[color:var(--color-coral)] hover:underline">
                <LogOut className="h-3 w-3" />
                خروج
              </button>
            </div>
            <div className={`mt-1 flex items-center gap-1 text-[11px] ${roleInfo.color}`}>
              {roleInfo.icon}
              {roleInfo.label}
            </div>
          </div>
        </div>

        <h4 className="mb-2.5 flex items-center gap-1.5 text-[13px] font-bold text-[color:var(--color-ink)]">
          <Users className="h-3.5 w-3.5 text-[color:var(--color-teal)]" />
          آنلاین ({onlineUsers.length})
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
        <h4 className="mb-2.5 flex items-center gap-1.5 text-[13px] font-bold text-[color:var(--color-ink)]">
          <Bell className="h-3.5 w-3.5 text-[color:var(--color-amber)]" />
          رویدادهای اخیر
        </h4>
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
