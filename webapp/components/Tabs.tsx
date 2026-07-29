"use client";

export interface TabDef {
  id: string;
  label: string;
  badge?: number;
}

export function Tabs({ tabs, active, onChange }: { tabs: TabDef[]; active: string; onChange: (id: string) => void }) {
  return (
    <div className="mb-4.5 flex flex-wrap gap-1.5 border-b border-[color:var(--color-border)] pb-3.5">
      {tabs.map((t) => (
        <button
          key={t.id}
          onClick={() => onChange(t.id)}
          className={`flex items-center gap-1.5 rounded-xl px-4 py-2 text-[13.5px] font-semibold transition-colors ${
            active === t.id
              ? "border border-[color:var(--color-plum)]/40 bg-[color:var(--color-plum)]/20 text-white"
              : "border border-transparent text-[color:var(--color-ink-muted)] hover:text-white"
          }`}
        >
          {t.label}
          {!!t.badge && <span className="rounded-full bg-[color:var(--color-coral)] px-1.5 text-[10.5px] font-extrabold text-white">{t.badge}</span>}
        </button>
      ))}
    </div>
  );
}
