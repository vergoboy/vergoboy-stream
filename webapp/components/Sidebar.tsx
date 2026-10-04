"use client";

import { motion, AnimatePresence } from "./anim";
import { Bell, Shield, Gamepad2, Eye, ImagePlus } from "lucide-react";
import { Avatar } from "./Avatar";
import type { NotifyEvent, PlaylistItem, UserRole } from "@/lib/types";
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
  notifications,
  playlist,
}: {
  myName: string;
  myAvatarUrl: string | null;
  role: UserRole;
  onChangeAvatar: (file: File) => void;
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
              <label className="flex cursor-pointer items-center gap-1 text-[color:var(--color-amber)] hover:underline">
                <ImagePlus className="h-3 w-3" />
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
            <div className={`mt-1 flex items-center gap-1 text-[11px] ${roleInfo.color}`}>
              {roleInfo.icon}
              {roleInfo.label}
            </div>
          </div>
        </div>

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
