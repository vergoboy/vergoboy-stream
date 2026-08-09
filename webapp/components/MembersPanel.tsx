"use client";

import { useEffect, useState } from "react";
import { Crown, UserRoundPlus, UserRoundX, ShieldCheck, ShieldOff, Users, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import type { RoomMember, RoomBannedUser } from "@/lib/types";

interface MembersPanelProps {
  myName: string;
  canManage: boolean;
  onToast?: (msg: string) => void;
}

const ACTIONS = { promote: "ارتقا به کنترلر", demote: "لغو کنترلر", ban: "حذف از اتاق", unban: "برداشتن از لیست سیاه", error: "عملیات ناموفق بود" } as const;

export function MembersPanel({ myName, canManage, onToast }: MembersPanelProps) {
  const [members, setMembers] = useState<RoomMember[]>([]);
  const [banned, setBanned] = useState<RoomBannedUser[]>([]);
  const [ownerId, setOwnerId] = useState<string | null>(null);

  const refresh = () => {
    api
      .roomMembers()
      .then((d) => {
        setMembers(d.online);
        setBanned(d.banned);
        setOwnerId(d.owner_id);
      })
      .catch(() => {});
  };

  useEffect(() => {
    refresh();
  }, []);

  const act = async (fn: () => Promise<unknown>, okMsg: string) => {
    try {
      await fn();
      onToast?.(okMsg);
    } catch {
      onToast?.(ACTIONS.error);
    }
    refresh();
  };

  return (
    <section className="rounded-3xl border border-[color:var(--color-border)] bg-[color:var(--color-bg-soft)]/70 p-5 backdrop-blur-md">
      <h2 className="mb-3 flex items-center gap-2 text-[15px] font-bold text-[color:var(--color-ink)]">
        <Users className="h-4 w-4 text-[color:var(--color-amber)]" />
        اعضای اتاق
      </h2>

      <ul className="flex flex-col gap-2">
        {members.map((m) => {
          const isOwner = m.id !== null && m.id === ownerId;
          const isSelf = m.name === myName;
          return (
            <li key={m.id ?? m.name} className="flex items-center gap-2.5 rounded-2xl border border-[color:var(--color-border)] bg-white/5 px-3 py-2">
              <img
                src={m.avatar_url ?? ""}
                alt=""
                className="h-8 w-8 rounded-full object-cover bg-white/10"
                onError={(e) => { (e.currentTarget as HTMLImageElement).style.visibility = "hidden"; }}
              />
              <span className="min-w-0 flex-1 truncate text-[13.5px] font-medium text-[color:var(--color-ink)]">
                {m.name}
                {isSelf && <span className="mr-1.5 text-[11px] text-[color:var(--color-ink-muted)]">(تو)</span>}
              </span>
              {isOwner && <Crown className="h-4 w-4 shrink-0 text-[color:var(--color-amber)]" />}
              {m.in_voice && <span className="shrink-0 rounded-full bg-[color:var(--color-coral)]/90 px-2 py-0.5 text-[10.5px] font-bold text-white">صدای فعال</span>}
              {canManage && !isSelf && !isOwner && (
                <div className="flex shrink-0 items-center gap-1">
                  <button
                    title={m.can_control ? ACTIONS.demote : ACTIONS.promote}
                    onClick={() => act(() => (m.can_control ? api.roomDemote(m.id!) : api.roomPromote(m.id!)), m.can_control ? ACTIONS.demote : ACTIONS.promote)}
                    className="flex h-7 w-7 items-center justify-center rounded-lg text-[color:var(--color-ink-muted)] hover:bg-white/10 hover:text-[color:var(--color-ink)]"
                  >
                    {m.can_control ? <ShieldOff className="h-4 w-4" /> : <ShieldCheck className="h-4 w-4" />}
                  </button>
                  <button
                    title={ACTIONS.ban}
                    onClick={() => act(() => api.roomBan(m.id!), ACTIONS.ban)}
                    className="flex h-7 w-7 items-center justify-center rounded-lg text-[color:var(--color-coral)] hover:bg-white/10"
                  >
                    <UserRoundX className="h-4 w-4" />
                  </button>
                </div>
              )}
            </li>
          );
        })}
        {members.length === 0 && (
          <li className="py-2 text-center text-[13px] text-[color:var(--color-ink-muted)]">هنوز کسی آنلاین نیست</li>
        )}
      </ul>

      {banned.length > 0 && (
        <>
          <h3 className="mb-2 mt-5 text-[13px] font-bold text-[color:var(--color-ink-muted)]">لیست سیاه</h3>
          <ul className="flex flex-col gap-2">
            {banned.map((b) => (
              <li key={b.id} className="flex items-center gap-2.5 rounded-2xl border border-[color:var(--color-border)] bg-white/5 px-3 py-2">
                <UserRoundPlus className="h-4 w-4 shrink-0 text-[color:var(--color-coral)]" />
                <span className="min-w-0 flex-1 truncate text-[13.5px] text-[color:var(--color-ink)]">{b.display_name}</span>
                {canManage && (
                  <button
                    title={ACTIONS.unban}
                    onClick={() => act(() => api.roomUnban(b.id), ACTIONS.unban)}
                    className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-[color:var(--color-ink-muted)] hover:bg-white/10 hover:text-[color:var(--color-ink)]"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
