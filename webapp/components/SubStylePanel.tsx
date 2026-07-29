"use client";

import { DEFAULT_SUB_STYLE, type SubStyle } from "@/lib/types";
import { hexToRgba } from "@/lib/format";

export function SubStylePanel({ value, onChange }: { value: SubStyle; onChange: (s: SubStyle) => void }) {
  const set = <K extends keyof SubStyle>(key: K, v: SubStyle[K]) => onChange({ ...value, [key]: v });

  return (
    <div>
      <p className="mb-3.5 text-[12.5px] leading-relaxed text-[color:var(--color-ink-dim)]">
        این تنظیمات فقط روی نمایش زیرنویس برای خودت اثر دارد و برای بقیه‌ی بیننده‌ها تغییری ایجاد نمی‌کند.
      </p>
      <div className="grid grid-cols-2 gap-4">
        <label className="flex flex-col gap-1.5 text-xs text-[color:var(--color-ink-muted)]">
          فونت
          <select
            value={value.font}
            onChange={(e) => set("font", e.target.value as SubStyle["font"])}
            className="rounded-lg border border-[color:var(--color-border)] bg-white/5 px-2.5 py-2 text-[13px] text-[color:var(--color-ink)]"
          >
            <option value="Vazirmatn">وزیرمتن</option>
            <option value="JetBrainsMono">جت‌برینز مونو</option>
            <option value="Tahoma">Tahoma</option>
            <option value="system-ui">پیش‌فرض سیستم</option>
          </select>
        </label>
        <label className="flex flex-col gap-1.5 text-xs text-[color:var(--color-ink-muted)]">
          اندازه‌ی متن {value.size}px
          <input type="range" min={14} max={64} value={value.size} onChange={(e) => set("size", +e.target.value)} className="accent-[color:var(--color-amber)]" />
        </label>
        <label className="flex flex-col gap-1.5 text-xs text-[color:var(--color-ink-muted)]">
          رنگ متن
          <input type="color" value={value.color} onChange={(e) => set("color", e.target.value)} className="h-9 w-full cursor-pointer rounded-lg border border-[color:var(--color-border)]" />
        </label>
        <label className="flex flex-col gap-1.5 text-xs text-[color:var(--color-ink-muted)]">
          رنگ پس‌زمینه
          <input type="color" value={value.bg} onChange={(e) => set("bg", e.target.value)} className="h-9 w-full cursor-pointer rounded-lg border border-[color:var(--color-border)]" />
        </label>
        <label className="flex flex-col gap-1.5 text-xs text-[color:var(--color-ink-muted)]">
          شفافیت پس‌زمینه {value.bgOpacity}٪
          <input type="range" min={0} max={100} value={value.bgOpacity} onChange={(e) => set("bgOpacity", +e.target.value)} className="accent-[color:var(--color-amber)]" />
        </label>
        <label className="flex flex-col gap-1.5 text-xs text-[color:var(--color-ink-muted)]">
          فاصله از پایین صفحه {value.offset}px
          <input type="range" min={0} max={300} value={value.offset} onChange={(e) => set("offset", +e.target.value)} className="accent-[color:var(--color-amber)]" />
        </label>
        <label className="flex flex-row items-center gap-2 text-xs text-[color:var(--color-ink-muted)]">
          <input type="checkbox" checked={value.outline} onChange={(e) => set("outline", e.target.checked)} className="h-4 w-4 accent-[color:var(--color-amber)]" />
          دور خط (Outline)
        </label>
        <label className="flex flex-row items-center gap-2 text-xs text-[color:var(--color-ink-muted)]">
          <input type="checkbox" checked={value.bold} onChange={(e) => set("bold", e.target.checked)} className="h-4 w-4 accent-[color:var(--color-amber)]" />
          ضخیم (Bold)
        </label>
      </div>
      <button onClick={() => onChange(DEFAULT_SUB_STYLE)} className="mt-3.5 rounded-xl border border-[color:var(--color-border)] bg-white/5 px-4 py-2 text-xs text-[color:var(--color-ink)] hover:border-[color:var(--color-amber)]/50">
        بازنشانی به پیش‌فرض
      </button>

      <div className="mt-5 flex aspect-[16/6] items-end justify-center overflow-hidden rounded-2xl bg-[#17101f] p-5">
        <div
          className="rounded-lg px-3.5 py-1"
          style={{
            fontFamily: value.font,
            fontSize: value.size,
            fontWeight: value.bold ? 700 : 400,
            color: value.color,
            background: hexToRgba(value.bg, value.bgOpacity / 100),
            textShadow: value.outline ? "0 0 3px rgba(0,0,0,.95), 0 0 7px rgba(0,0,0,.8)" : "none",
          }}
        >
          این یک پیش‌نمایش از زیرنویس شماست
        </div>
      </div>
    </div>
  );
}
