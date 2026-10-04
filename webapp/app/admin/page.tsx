"use client";

import { useCallback, useEffect, useState } from "react";
import { LayoutDashboard, Users, DoorOpen, Radio, Film, RotateCw, Save, Trash2, ArrowRight } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import type { AdminUser, UserRole } from "@/lib/types";

const basePath = process.env.NEXT_PUBLIC_BUILD_TARGET === "tauri" ? "" : "/stream";

const ROLE_OPTIONS: { value: UserRole; label: string }[] = [
  { value: "watcher", label: "تماشاگر" },
  { value: "controller", label: "کنترلر" },
  { value: "admin", label: "ادمین" },
];

interface Stats {
  users: number;
  rooms: number;
  online: number;
  media: number;
}

function fmtDate(iso?: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString("fa-IR");
  } catch {
    return "—";
  }
}

function quotaLabel(q: number): string {
  return q === -1 ? "نامحدود" : `${q}`;
}

export default function AdminPage() {
  const { user, hydrated } = useAuth();
  const [stats, setStats] = useState<Stats | null>(null);
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [edits, setEdits] = useState<Record<string, { role: UserRole; can_control: boolean; youtube_allowed: boolean; upload_quota: number }>>({});
  const [savingId, setSavingId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [s, u] = await Promise.all([api.adminStats(), api.adminUsers()]);
      setStats(s);
      setUsers(u.users);
    } catch (e) {
      setError(e instanceof Error ? e.message : "خطا در دریافت اطلاعات");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (user?.role === "admin") {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- initial data load for the dashboard (external DB data)
      load();
    }
  }, [user?.role, load]);

  if (!hydrated) return null;

  if (!user) {
    return (
      <main className="flex min-h-screen items-center justify-center p-5">
        <div className="rounded-3xl border border-[color:var(--color-border)] bg-[color:var(--color-bg-soft)]/70 p-8 text-center backdrop-blur-md">
          <p className="mb-4 text-[14.5px] text-[color:var(--color-ink-muted)]">برای ورود به پنل ادمین اول وارد حساب شو</p>
          <a href={`${basePath}/`} className="inline-flex items-center gap-2 rounded-2xl px-6 py-3 text-[14px] font-bold text-white" style={{ background: "linear-gradient(135deg, var(--color-amber-soft), var(--color-amber))" }}>
            <ArrowRight className="h-4 w-4" />
            بازگشت به استریم
          </a>
        </div>
      </main>
    );
  }

  if (user.role !== "admin") {
    return (
      <main className="flex min-h-screen items-center justify-center p-5">
        <div className="rounded-3xl border border-[color:var(--color-border)] bg-[color:var(--color-bg-soft)]/70 p-8 text-center backdrop-blur-md">
          <p className="mb-4 text-[14.5px] text-[color:var(--color-coral)]">این صفحه فقط برای ادمین است.</p>
          <a href={`${basePath}/`} className="inline-flex items-center gap-2 rounded-2xl px-6 py-3 text-[14px] font-bold text-white" style={{ background: "linear-gradient(135deg, var(--color-amber-soft), var(--color-amber))" }}>
            <ArrowRight className="h-4 w-4" />
            بازگشت به استریم
          </a>
        </div>
      </main>
    );
  }

  const statCards = [
    { label: "کاربران", value: stats?.users ?? "—", icon: <Users className="h-4 w-4" />, color: "text-[color:var(--color-amber)]" },
    { label: "اتاق‌ها", value: stats?.rooms ?? "—", icon: <DoorOpen className="h-4 w-4" />, color: "text-[color:var(--color-teal)]" },
    { label: "آنلاین", value: stats?.online ?? "—", icon: <Radio className="h-4 w-4" />, color: "text-[color:var(--color-coral)]" },
    { label: "مدیا", value: stats?.media ?? "—", icon: <Film className="h-4 w-4" />, color: "text-[color:var(--color-plum-soft)]" },
  ];

  function rowEdit(u: AdminUser) {
    const e = edits[u.id];
    if (e) return e;
    return { role: u.role, can_control: u.can_control, youtube_allowed: u.youtube_allowed, upload_quota: u.upload_quota };
  }

  async function save(u: AdminUser) {
    const e = rowEdit(u);
    setSavingId(u.id);
    setNotice(null);
    try {
      const res = await api.adminUpdateUser(u.id, {
        role: e.role,
        can_control: e.can_control,
        youtube_allowed: e.youtube_allowed,
        upload_quota: e.upload_quota,
      });
      setUsers((prev) => (prev ? prev.map((x) => (x.id === u.id ? { ...x, ...res.user } : x)) : prev));
      setEdits((prev) => {
        const next = { ...prev };
        delete next[u.id];
        return next;
      });
      setNotice(`تغییرات «${res.user.username}» ذخیره شد`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "خطا در ذخیره");
    } finally {
      setSavingId(null);
    }
  }

  function setEdit(u: AdminUser, patch: Partial<{ role: UserRole; can_control: boolean; youtube_allowed: boolean; upload_quota: number }>) {
    setEdits((prev) => ({ ...prev, [u.id]: { ...rowEdit(u), ...patch } }));
  }

  async function del(u: AdminUser) {
    const ok = window.confirm(
      `حساب «${u.username}» به‌همراه اتاق و مدیاهایش برای همیشه حذف شود؟\n\nاین کار قابل بازگشت نیست.`
    );
    if (!ok) return;
    setDeletingId(u.id);
    setError(null);
    setNotice(null);
    try {
      await api.adminDeleteUser(u.id);
      setUsers((prev) => (prev ? prev.filter((x) => x.id !== u.id) : prev));
      setNotice(`حساب «${u.username}» حذف شد`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "خطا در حذف کاربر");
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <main className="page relative z-[1]">
      <header className="sticky top-0 z-[100] border-b border-[color:var(--color-border)] bg-[color:var(--color-bg)]/85 backdrop-blur-xl">
        <div className="mx-auto flex h-[64px] max-w-[1120px] items-center justify-between px-5 md:px-7">
          <div className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl text-lg font-black text-white" style={{ background: "linear-gradient(135deg, var(--color-amber-soft), var(--color-amber))" }}>
              V
            </div>
            <span className="text-[15px] font-bold text-[color:var(--color-ink)]">پنل مدیریت استریم</span>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={load} disabled={loading} className="flex items-center gap-1.5 rounded-xl border border-[color:var(--color-border)] bg-white/5 px-3 py-2 text-[13px] text-[color:var(--color-ink)] hover:border-[color:var(--color-amber)]/50 disabled:opacity-40">
              <RotateCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
              به‌روزرسانی
            </button>
            <a href={`${basePath}/`} className="flex items-center gap-1.5 rounded-xl border border-[color:var(--color-border)] bg-white/5 px-3 py-2 text-[13px] text-[color:var(--color-ink)] hover:border-[color:var(--color-amber)]/50">
              <ArrowRight className="h-4 w-4" />
              بازگشت
            </a>
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-[1120px] px-4 pt-8 md:px-7">
        <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-4">
          {statCards.map((c) => (
            <div key={c.label} className="rounded-3xl border border-[color:var(--color-border)] bg-[color:var(--color-bg-soft)]/70 p-5 backdrop-blur-md">
              <div className={`mb-2 flex items-center gap-1.5 text-[12.5px] text-[color:var(--color-ink-muted)] ${c.color}`}>
                {c.icon}
                {c.label}
              </div>
              <div className="text-3xl font-extrabold text-[color:var(--color-ink)]">{c.value}</div>
            </div>
          ))}
        </div>

        {error && (
          <div className="mb-4 rounded-2xl border border-[color:var(--color-coral)]/30 bg-[color:var(--color-coral)]/10 px-4 py-3 text-[13px] text-[color:var(--color-coral)]">
            {error}
          </div>
        )}
        {notice && (
          <div className="mb-4 rounded-2xl border border-[color:var(--color-teal)]/30 bg-[color:var(--color-teal)]/10 px-4 py-3 text-[13px] text-[color:var(--color-teal)]">
            {notice}
          </div>
        )}

        <div className="mb-4 flex items-center gap-2 text-[15px] font-bold text-[color:var(--color-ink)]">
          <LayoutDashboard className="h-4 w-4 text-[color:var(--color-amber)]" />
          کاربران
        </div>

        <div className="overflow-x-auto rounded-3xl border border-[color:var(--color-border)] bg-[color:var(--color-bg-soft)]/70 backdrop-blur-md">
          <table className="w-full min-w-[860px] border-collapse text-[13px]">
            <thead>
              <tr className="border-b border-[color:var(--color-border)] text-[12px] text-[color:var(--color-ink-muted)]">
                <th className="px-4 py-3 text-right font-medium">کاربر</th>
                <th className="px-3 py-3 text-right font-medium">نقش</th>
                <th className="px-3 py-3 text-right font-medium">کنترل پخش</th>
                <th className="px-3 py-3 text-right font-medium">یوتیوب</th>
                <th className="px-3 py-3 text-right font-medium">سهمیه مدیا</th>
                <th className="px-3 py-3 text-right font-medium">وضعیت</th>
                <th className="px-3 py-3 text-right font-medium">عضویت</th>
                <th className="px-3 py-3 text-right font-medium"></th>
              </tr>
            </thead>
            <tbody>
              {users?.map((u) => {
                const e = rowEdit(u);
                const dirty = e.role !== u.role || e.can_control !== u.can_control || e.youtube_allowed !== u.youtube_allowed || e.upload_quota !== u.upload_quota;
                const isSelf = u.id === user.id;
                return (
                  <tr key={u.id} className="border-b border-white/5 last:border-0 hover:bg-white/[0.02]">
                    <td className="px-4 py-3">
                      <div className="font-bold text-[color:var(--color-ink)]">{u.username}</div>
                      <div className="mt-0.5 text-[11.5px] text-[color:var(--color-ink-dim)]">
                        اتاق: <span className="font-mono" dir="ltr">{u.own_room_id ?? "—"}</span>
                        {u.online ? <span className="text-[color:var(--color-teal)]"> · آنلاین ({u.online})</span> : ""}
                      </div>
                    </td>
                    <td className="px-3 py-3">
                      <select
                        value={e.role}
                        disabled={isSelf}
                        onChange={(ev) => setEdit(u, { role: ev.target.value as UserRole })}
                        className="rounded-lg border border-[color:var(--color-border)] bg-white/5 px-2 py-1.5 text-[12.5px] text-[color:var(--color-ink)] outline-none focus:border-[color:var(--color-amber)] disabled:opacity-50"
                      >
                        {ROLE_OPTIONS.map((o) => (
                          <option key={o.value} value={o.value}>{o.label}</option>
                        ))}
                      </select>
                    </td>
                    <td className="px-3 py-3">
                      <input type="checkbox" checked={e.can_control} onChange={(ev) => setEdit(u, { can_control: ev.target.checked })} className="accent-[color:var(--color-amber)]" />
                    </td>
                    <td className="px-3 py-3">
                      <input type="checkbox" checked={e.youtube_allowed} onChange={(ev) => setEdit(u, { youtube_allowed: ev.target.checked })} className="accent-[color:var(--color-amber)]" />
                    </td>
                    <td className="px-3 py-3">
                      <div className="flex items-center gap-1.5">
                        <input
                          type="number"
                          value={e.upload_quota}
                          min={-1}
                          onChange={(ev) => setEdit(u, { upload_quota: parseInt(ev.target.value, 10) || 0 })}
                          className="w-20 rounded-lg border border-[color:var(--color-border)] bg-white/5 px-2 py-1.5 text-[12.5px] text-[color:var(--color-ink)] outline-none focus:border-[color:var(--color-amber)]"
                        />
                        <span className="text-[11.5px] text-[color:var(--color-ink-dim)]">
                          ({u.uploads_used} / {quotaLabel(u.upload_quota)})
                        </span>
                      </div>
                    </td>
                    <td className="px-3 py-3">
                      <span className="text-[12px] text-[color:var(--color-ink-muted)]">
                        {u.room_items ?? 0} مدیا
                      </span>
                    </td>
                    <td className="px-3 py-3 text-[12px] text-[color:var(--color-ink-dim)]">{fmtDate(u.created_at)}</td>
                    <td className="px-3 py-3">
                      <div className="flex items-center gap-1.5">
                        <button
                          onClick={() => save(u)}
                          disabled={!dirty || savingId === u.id || isSelf}
                          title={isSelf ? "نمی‌توانی دسترسی خودت را تغییر دهی" : undefined}
                          className="flex items-center gap-1.5 rounded-lg border border-[color:var(--color-border)] bg-white/5 px-3 py-1.5 text-[12px] text-[color:var(--color-ink)] hover:border-[color:var(--color-amber)]/50 disabled:cursor-not-allowed disabled:opacity-40"
                        >
                          <Save className="h-3.5 w-3.5" />
                          {savingId === u.id ? "در حال ذخیره…" : "ذخیره"}
                        </button>
                        <button
                          onClick={() => del(u)}
                          disabled={deletingId === u.id || isSelf}
                          title={isSelf ? "نمی‌توانی حساب خودت را حذف کنی" : "حذف حساب و اتاق"}
                          className="flex items-center gap-1.5 rounded-lg border border-[color:var(--color-coral)]/25 bg-[color:var(--color-coral)]/5 px-2.5 py-1.5 text-[12px] text-[color:var(--color-coral)] hover:border-[color:var(--color-coral)]/50 disabled:cursor-not-allowed disabled:opacity-40"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                          {deletingId === u.id ? "در حال حذف…" : "حذف"}
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {!users && !error && <div className="p-8 text-center text-[13.5px] text-[color:var(--color-ink-muted)]">در حال بارگذاری…</div>}
          {users?.length === 0 && <div className="p-8 text-center text-[13.5px] text-[color:var(--color-ink-muted)]">هنوز کاربری ثبت نشده</div>}
        </div>
      </div>
    </main>
  );
}
