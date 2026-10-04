"use client";

import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "./anim";
import { api, type ArchiveAuthStatus } from "@/lib/api";
import type { ArchiveResult, ArchiveTitle, ArchiveGroup, ArchiveEpisode, ArchiveFile, ArchiveFilterOptions, ArchiveSearchFilters } from "@/lib/types";
import {
  Search,
  ArrowRight,
  Loader2,
  CheckCircle,
  AlertCircle,
  Plus,
  Layers,
  Film,
  Tv,
  Clapperboard,
  DownloadCloud,
  FolderOpen,
  ChevronDown,
} from "lucide-react";

const inputClass =
  "w-full rounded-xl border border-[color:var(--color-border)] bg-white/5 px-3.5 py-2.5 text-[13.5px] text-[color:var(--color-ink)] outline-none focus:border-[color:var(--color-amber)]";

const SOURCE_LABEL: Record<string, string> = { digimoviez: "دیجی موویز" };

const KIND_LABEL: Record<string, string> = {
  movie: "فیلم",
  post: "فیلم",
  series: "سریال",
  serial: "سریال",
  anime: "انیمه",
  korean: "کره‌ای",
  turkey: "ترکی",
};

const KIND_ICON: Record<string, React.ReactNode> = {
  movie: <Film className="h-3.5 w-3.5" />,
  post: <Film className="h-3.5 w-3.5" />,
  series: <Tv className="h-3.5 w-3.5" />,
  serial: <Tv className="h-3.5 w-3.5" />,
  anime: <Clapperboard className="h-3.5 w-3.5" />,
};

function episodeFromName(name: string): string {
  const n = name.match(/(\d{2,3})/);
  if (n) return `قسمت ${parseInt(n[1], 10)}`;
  return name.replace(/\.(mkv|mp4|m4v|webm|avi|mov)$/i, "").slice(0, 60);
}

export function ArchivePanel({ myName, initialLink }: { myName: string; initialLink?: string | null }) {
  const [q, setQ] = useState("");
  const [options, setOptions] = useState<ArchiveFilterOptions | null>(null);
  const [filters, setFilters] = useState<ArchiveSearchFilters>({ year_min: 1888, year_max: new Date().getFullYear(), rating_min: 0, rating_max: 10 });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [results, setResults] = useState<ArchiveResult[] | null>(null);

  const [detail, setDetail] = useState<ArchiveTitle | null>(null);
  const [detailBusy, setDetailBusy] = useState(false);
  const [addBusy, setAddBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ text: string; error?: boolean } | null>(null);
  const [animexFiles, setAnimexFiles] = useState<Record<string, ArchiveFile[]>>({});
  const [animexOpen, setAnimexOpen] = useState<Record<string, boolean>>({});
  const [auth, setAuth] = useState<ArchiveAuthStatus | null>(null);
  const [authCheckBusy, setAuthCheckBusy] = useState(false);
  const [authBlocked, setAuthBlocked] = useState(false);
  const importedRef = useRef<string | null>(null);

  useEffect(() => {
    api.archiveAuthStatus().then(setAuth).catch(() => setAuth(null));
    api.archiveFilters()
      .then((data) => {
        setOptions(data);
        setFilters((f) => ({ ...f, year_min: data.year_min, year_max: data.year_max, rating_min: data.rating_min, rating_max: data.rating_max }));
      })
      .catch((cause) => setErr(cause instanceof Error ? cause.message : "اتصال به API آرشیو برقرار نشد"));
  }, []);

  async function checkArchiveAuthentication() {
    setAuthCheckBusy(true);
    setErr(null);
    try {
      const next = await api.archiveAuthCheck();
      setAuth(next);
      setAuthBlocked(next.state === "AUTH_MANUAL_INTERVENTION_REQUIRED");
      if (next.state === "AUTHENTICATED") {
        setMsg({ text: "نشست بک‌اند دیجی‌موویز تأیید شد؛ اکنون می‌توانید جستجو کنید." });
      }
    } catch (cause) {
      setErr(cause instanceof Error ? cause.message : "بررسی نشست دیجی‌موویز ناموفق بود");
    } finally {
      setAuthCheckBusy(false);
    }
  }

  async function doSearch(e?: React.FormEvent) {
    e?.preventDefault();
    const query = q.trim();
    if ((!query && !filters.director && !filters.actors) || busy) return;
    if (/^https?:\/\//i.test(query)) {
      void importUrl(query);
      return;
    }
    if (auth?.state === "AUTH_MANUAL_INTERVENTION_REQUIRED" || authBlocked) {
      setAuthBlocked(true);
      setErr("ورود بک‌اند دیجی‌موویز هنوز تأیید نشده است. ابتدا وضعیت نشست را بررسی کنید.");
      return;
    }
    setBusy(true);
    setErr(null);
    setResults(null);
    try {
      const data = await api.archiveSearch({ ...filters, query });
      setResults(data.results);
    } catch (err2: unknown) {
      const e = err2 as { kind?: string; message?: string };
      if (e?.kind === "manual_challenge_required") {
        setAuthBlocked(true);
        setAuth((current) => current ? {
          ...current,
          state: "AUTH_MANUAL_INTERVENTION_REQUIRED",
          kind: e.kind ?? "manual_challenge_required",
          message: e.message ?? "ورود دیجی‌موویز به تکمیل دستی نیاز دارد.",
        } : current);
      }
      setErr(e?.message || (e instanceof Error?e.message:"خطا در جستجو"));
    }
    setBusy(false);
  }

  async function openDetail(r: ArchiveResult) {
    setDetailBusy(true);
    setDetail(null);
    setErr(null);
    setMsg(null);
    try {
      const info = await api.archiveTitle(r.source, r.url);
      setDetail(info);
    } catch (err2: unknown) {
      const e = err2 as { kind?: string; message?: string };
      if (e?.kind === "manual_challenge_required") setAuthBlocked(true);
      setErr(e?.message || (e instanceof Error?e.message:"خطا در دریافت صفحه"));
    }
    setDetailBusy(false);
  }

  async function importUrl(rawUrl: string) {
    const url = rawUrl.trim();
    if (!url || detailBusy) return;
    if (!/^https?:\/\/([^/]+\.)?digimoviez\.com\//i.test(url)) {
      setErr("این لینک مربوط به آرشیو دیجی‌موویز نیست.");
      return;
    }
    setDetailBusy(true);
    setDetail(null);
    setErr(null);
    setMsg(null);
    try {
      const info = await api.archiveTitle("digimoviez", url);
      setDetail(info);
    } catch (cause: unknown) {
      const error = cause as { kind?: string; message?: string };
      if (error.kind === "manual_challenge_required") setAuthBlocked(true);
      setErr(error.message || "دریافت صفحهٔ آرشیو ناموفق بود");
    } finally {
      setDetailBusy(false);
    }
  }

  useEffect(() => {
    if (initialLink && importedRef.current !== initialLink) {
      importedRef.current = initialLink;
      void importUrl(initialLink);
    }
    // Import each handed-off link once, including when AddHub changes it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialLink]);

  async function runAdd(items: { title: string; url: string }[]) {
    if (!items.length) {
      setMsg({ text: "قسمتی برای افزودن پیدا نشد", error: true });
      return;
    }
    if (items.length > 12 && !confirm(`${items.length} قسمت به پلی‌لیست اضافه شود؟\nآماده‌سازی هر قسمت ممکنه کمی طول بکشه.`)) {
      return;
    }
    try {
      const data = await api.addMany(items, myName);
      const deadNote = data.dead?.length ? `؛ ${data.dead.length} لینک خراب بود` : "";
      if (data.added > 0) {
        setMsg({ text: `✓ ${data.added} قسمت به پلی‌لیست اضافه شد${deadNote}${data.skipped ? ` (${data.skipped} تکراری بود)` : ""}` });
      } else if (data.dead?.length) {
        setMsg({ text: "هیچ قسمتی اضافه نشد — لینک در سایت منبع خراب است", error: true });
      } else {
        setMsg({ text: "این قسمت‌ها از قبل در پلی‌لیست هستند", error: true });
      }
    } catch (err2) {
      setMsg({ text: err2 instanceof Error ? err2.message : "خطا در افزودن", error: true });
    }
  }

  function addDonyayeSerialGroup(group: ArchiveGroup) {
    if (!detail) return;
    const base = detail.title;
    const items = group.episodes.map((ep, i) => ({
      title: `${base} — ${group.label} — ${ep.num ? `قسمت ${ep.num}` : `قسمت ${i + 1}`}`,
      url: ep.url,
    }));
    setMsg(null);
    runAdd(items);
  }

  async function addDonyayeSerialEpisode(group: ArchiveGroup, ep: ArchiveEpisode, i: number) {
    if (!detail) return;
    const base = detail.title;
    setAddBusy("ds-one");
    setMsg(null);
    try {
      await runAdd([
        {
          title: `${base} — ${group.label} — ${ep.num ? `قسمت ${ep.num}` : `قسمت ${i + 1}`}`,
          url: ep.url,
        },
      ]);
    } finally {
      setAddBusy(null);
    }
  }

  async function addAnimexQuality(group: ArchiveGroup, dirUrl: string, quality: string) {
    if (!detail) return;
    const key = `${group.label}|${quality}`;
    setAddBusy(key);
    setMsg(null);
    try {
      const { files } = await api.archiveFiles(dirUrl);
      const items = files.map((f) => ({
        title: `${detail.title} — ${group.label} — ${episodeFromName(f.name)}`,
        url: f.url,
      }));
      await runAdd(items);
    } catch (err2) {
      setMsg({ text: err2 instanceof Error ? err2.message : "خطا در دریافت لینک‌ها", error: true });
    }
    setAddBusy(null);
  }

  async function toggleAnimexFiles(dirUrl: string) {
    setAnimexOpen((s) => ({ ...s, [dirUrl]: !s[dirUrl] }));
    if (animexFiles[dirUrl]) return;
    setAddBusy(`ax-files|${dirUrl}`);
    setMsg(null);
    try {
      const { files } = await api.archiveFiles(dirUrl);
      setAnimexFiles((s) => ({ ...s, [dirUrl]: files }));
    } catch (err2) {
      setMsg({ text: err2 instanceof Error ? err2.message : "خطا در دریافت لینک‌ها", error: true });
    }
    setAddBusy(null);
  }

  async function addAnimexEpisode(group: ArchiveGroup, dirUrl: string, quality: string, f: ArchiveFile, i: number) {
    if (!detail) return;
    setAddBusy(`ax-one|${dirUrl}|${i}`);
    setMsg(null);
    try {
      await runAdd([
        {
          title: `${detail.title} — ${group.label} — ${quality} — ${episodeFromName(f.name)}`,
          url: f.url,
        },
      ]);
    } finally {
      setAddBusy(null);
    }
  }

  const groupCount = detail ? detail.groups.reduce((n, g) => n + (g.episodes?.length ?? 0) + (g.items?.length ?? 0), 0) : 0;

  return (
    <div>
      {(auth?.state === "AUTH_MANUAL_INTERVENTION_REQUIRED" || authBlocked) && (
        <ArchiveAuthenticationPanel
          status={auth}
          busy={authCheckBusy}
          onCheck={checkArchiveAuthentication}
        />
      )}
      {!detail ? (
        <SearchView
          q={q}
          setQ={setQ}
          filters={filters}
          setFilters={setFilters}
          options={options}
          busy={busy}
          err={err}
          results={results}
          onSearch={doSearch}
          onOpen={openDetail}
        />
      ) : (
        <DetailView
          detail={detail}
          detailBusy={detailBusy}
          addBusy={addBusy}
          msg={msg}
          groupCount={groupCount}
          onBack={() => {
            setDetail(null);
            setMsg(null);
            setErr(null);
          }}
          onAddDs={addDonyayeSerialGroup}
          onAddDsOne={addDonyayeSerialEpisode}
          onAddAnimex={addAnimexQuality}
          animexFiles={animexFiles}
          animexOpen={animexOpen}
          onToggleAnimex={toggleAnimexFiles}
          onAddAnimexEp={addAnimexEpisode}
        />
      )}
      {detailBusy && (
        <div className="flex items-center gap-2 py-6 text-[13px] text-[color:var(--color-ink-muted)]">
          <Loader2 className="h-4 w-4 animate-spin text-amber-400" /> در حال دریافت لینک‌های دانلود…
        </div>
      )}
    </div>
  );
}

function ArchiveAuthenticationPanel({
  status,
  busy,
  onCheck,
}: {
  status: ArchiveAuthStatus | null;
  busy: boolean;
  onCheck: () => void;
}) {
  return (
    <section className="mb-4 rounded-2xl border border-[color:var(--color-amber)]/50 bg-[color:var(--color-amber)]/10 p-4 text-right">
      <div className="flex items-start gap-3">
        <AlertCircle className="mt-0.5 h-5 w-5 shrink-0 text-[color:var(--color-amber)]" />
        <div className="min-w-0">
          <h2 className="text-sm font-bold text-[color:var(--color-ink)]">ورود به دیجی‌موویز لازم است</h2>
          <p className="mt-1 text-[13px] leading-6 text-[color:var(--color-ink-muted)]">
            دیجی‌موویز در صفحهٔ ورود سؤال امنیتی نمایش می‌دهد. ورود عادی و پاسخ سؤال را در مرورگر خود کامل کنید، سپس نشست مستقلِ بک‌اند را بررسی کنید.
          </p>
          <p className="mt-1 text-[12px] leading-5 text-[color:var(--color-ink-dim)]">
            ورود مرورگر به‌خودی‌خود به معنی ورود بک‌اند نیست؛ هیچ کوکی یا اطلاعات محرمانه‌ای از مرورگر دریافت نمی‌شود.
          </p>
          {status?.message && <p className="mt-2 text-[12px] text-[color:var(--color-ink-muted)]">وضعیت: {status.message}</p>}
          <div className="mt-3 flex flex-wrap gap-2">
            <a
              href="https://digimoviez.com/account/login/"
              target="_blank"
              rel="noreferrer"
              className="rounded-xl bg-[color:var(--color-amber)] px-3.5 py-2 text-[13px] font-bold text-black transition-opacity hover:opacity-90"
            >
              ورود به دیجی‌موویز را باز کن
            </a>
            <button
              type="button"
              onClick={onCheck}
              disabled={busy}
              className="inline-flex items-center gap-2 rounded-xl border border-[color:var(--color-border)] px-3.5 py-2 text-[13px] font-bold text-[color:var(--color-ink)] disabled:opacity-50"
            >
              {busy && <Loader2 className="h-4 w-4 animate-spin" />}
              ورود را کامل کردم — بررسی نشست
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}

function SearchView({
  q,
  setQ,
  filters,
  setFilters,
  options,
  busy,
  err,
  results,
  onSearch,
  onOpen,
}: {
  q: string;
  setQ: (s: string) => void;
  filters: ArchiveSearchFilters;
  setFilters: (s: ArchiveSearchFilters) => void;
  options: ArchiveFilterOptions | null;
  busy: boolean;
  err: string | null;
  results: ArchiveResult[] | null;
  onSearch: (e?: React.FormEvent) => void;
  onOpen: (r: ArchiveResult) => void;
}) {
  return (
    <div>
      <form onSubmit={onSearch} className="flex flex-col gap-2.5">
        <label className="text-xs text-[color:var(--color-ink-muted)]">جستجو در آرشیو دیجی موویز</label>
        <div className="flex gap-2">
          <input
            className={`${inputClass} flex-1`}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="مثلا: game of thrones یا okitsura"
            dir="auto"
          />
          <button
            type="submit"
            disabled={busy}
            className="shrink-0 rounded-xl px-4 py-2.5 text-[13px] font-bold text-white transition-transform active:scale-[0.98] disabled:opacity-50"
            style={{ background: "linear-gradient(135deg, var(--color-amber), var(--color-plum))", boxShadow: "var(--shadow-lamp)" }}
          >
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
          </button>
        </div>
        <FilterControls filters={filters} setFilters={setFilters} options={options} />
      </form>


      {err && (
        <div className="mt-3 flex items-center gap-2 rounded-xl border border-[color:var(--color-coral)]/40 bg-[color:var(--color-coral)]/10 px-3.5 py-2.5 text-[13px] text-[color:var(--color-coral)]">
          <AlertCircle className="h-4 w-4 shrink-0" /> {err}
        </div>
      )}

      {results !== null && !busy && (
        <div className="mt-5">
          <p className="mb-3 text-[12.5px] text-[color:var(--color-ink-muted)]">{results.length > 0 ? `${results.length} نتیجه پیدا شد` : "چیزی پیدا نشد — عبارت یا منبع را عوض کن."}</p>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
            {results.map((r, i) => (
              <button
                key={`${r.source}-${r.url}-${i}`}
                onClick={() => onOpen(r)}
                className="group overflow-hidden rounded-2xl border border-[color:var(--color-border)] bg-white/[0.03] text-right transition-colors hover:border-[color:var(--color-plum-soft)]/50"
              >
                <div className="relative aspect-[2/3] w-full overflow-hidden bg-black/40">
                  {r.poster ? (
                    /* eslint-disable-next-line @next/next/no-img-element */
                    <img src={r.poster} alt={r.title} loading="lazy" className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.04]" />
                  ) : (
                    <div className="flex h-full w-full items-center justify-center text-[color:var(--color-ink-dim)]">
                      <Clapperboard className="h-8 w-8" />
                    </div>
                  )}
                  <span className="absolute right-1.5 top-1.5 rounded-full bg-black/70 px-2 py-0.5 text-[10px] font-bold text-white backdrop-blur-sm">
                    {SOURCE_LABEL[r.source]}
                  </span>
                  <span className="absolute left-1.5 top-1.5 flex items-center gap-1 rounded-full bg-black/70 px-2 py-0.5 text-[10px] font-bold text-[color:var(--color-amber)] backdrop-blur-sm">
                    {KIND_ICON[r.kind]}
                    {KIND_LABEL[r.kind] ?? r.kind}
                  </span>
                </div>
                <div className="p-2.5">
                  <div className="line-clamp-2 text-[12.5px] leading-snug text-[color:var(--color-ink)]">{r.title}</div>
                  <div className="mt-1 flex items-center gap-2.5 text-[11px] text-[color:var(--color-ink-dim)]">
                    {r.rating && <span>⭐ {r.rating}</span>}
                    {r.year && <span>{r.year}</span>}
                  </div>
                </div>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function FilterControls({ filters, setFilters, options }: { filters: ArchiveSearchFilters; setFilters: (s: ArchiveSearchFilters) => void; options: ArchiveFilterOptions | null }) {
  const set = <K extends keyof ArchiveSearchFilters>(key: K, value: ArchiveSearchFilters[K]) => setFilters({ ...filters, [key]: value });
  const select = (label: string, key: "type" | "country" | "age_rating" | "quality" | "sort", values: { value: string; label: string }[]) => (
    <label className="flex min-w-32 flex-1 flex-col gap-1 text-[11px] text-[color:var(--color-ink-muted)]">{label}
      <select className={inputClass} value={(filters[key] as string | undefined) ?? ""} onChange={(e) => set(key, (e.target.value || undefined) as ArchiveSearchFilters[typeof key])}>
        <option value="">همه</option>{values.map((v) => <option key={v.value} value={v.value}>{v.label}</option>)}
      </select>
    </label>
  );
  return <div className="grid grid-cols-2 gap-2 pt-1 sm:grid-cols-4">
    {select("نوع", "type", options?.types ?? [])}
    <label className="flex flex-col gap-1 text-[11px] text-[color:var(--color-ink-muted)]">کارگردان<input className={inputClass} value={filters.director ?? ""} onChange={(e) => set("director", e.target.value || undefined)} /></label>
    <label className="flex flex-col gap-1 text-[11px] text-[color:var(--color-ink-muted)]">بازیگران<input className={inputClass} value={filters.actors ?? ""} onChange={(e) => set("actors", e.target.value || undefined)} /></label>
    {select("کشور", "country", (options?.countries ?? []).map((x) => ({ value: x, label: x })))}
    {select("رده سنی", "age_rating", (options?.age_ratings ?? []).map((x) => ({ value: x, label: x })))}
    {select("کیفیت", "quality", (options?.qualities ?? []).map((x) => ({ value: x, label: x })))}
    {select("ترتیب", "sort", (options?.sorts ?? []).map((x) => ({ value: x, label: x })))}
    <Range label="سال ساخت" min={options?.year_min ?? 1888} max={options?.year_max ?? new Date().getFullYear()} step={1} lower={filters.year_min} upper={filters.year_max} onLower={(v) => set("year_min", v)} onUpper={(v) => set("year_max", v)} />
    <Range label="امتیاز" min={0} max={10} step={options?.rating_step ?? 0.1} lower={filters.rating_min} upper={filters.rating_max} onLower={(v) => set("rating_min", v)} onUpper={(v) => set("rating_max", v)} />
  </div>;
}

function Range({ label, min, max, step, lower, upper, onLower, onUpper }: { label: string; min: number; max: number; step: number; lower: number; upper: number; onLower: (v: number) => void; onUpper: (v: number) => void }) {
  return <div className="col-span-2 flex flex-col gap-1 text-[11px] text-[color:var(--color-ink-muted)]"><span>{label}: {lower} — {upper}</span><div className="flex gap-2"><input className="w-full" type="range" min={min} max={upper} step={step} value={lower} onChange={(e) => onLower(Number(e.target.value))} /><input className="w-full" type="range" min={lower} max={max} step={step} value={upper} onChange={(e) => onUpper(Number(e.target.value))} /></div></div>;
}

function DetailView({
  detail,
  detailBusy,
  addBusy,
  msg,
  groupCount,
  onBack,
  onAddDs,
  onAddDsOne,
  onAddAnimex,
  animexFiles,
  animexOpen,
  onToggleAnimex,
  onAddAnimexEp,
}: {
  detail: ArchiveTitle;
  detailBusy: boolean;
  addBusy: string | null;
  msg: { text: string; error?: boolean } | null;
  groupCount: number;
  onBack: () => void;
  onAddDs: (g: ArchiveGroup) => void;
  onAddDsOne: (g: ArchiveGroup, ep: ArchiveEpisode, i: number) => void;
  onAddAnimex: (g: ArchiveGroup, dirUrl: string, quality: string) => void;
  animexFiles: Record<string, ArchiveFile[]>;
  animexOpen: Record<string, boolean>;
  onToggleAnimex: (dirUrl: string) => void;
  onAddAnimexEp: (g: ArchiveGroup, dirUrl: string, quality: string, f: ArchiveFile, i: number) => void;
}) {
  const [openEps, setOpenEps] = useState<Record<string, boolean>>({});
  return (
    <div>
      <div className="mb-4 flex items-center justify-between gap-2">
        <button
          onClick={onBack}
          className="flex items-center gap-1.5 rounded-xl border border-[color:var(--color-border)] bg-white/5 px-3.5 py-2 text-[12.5px] text-[color:var(--color-ink)] hover:border-[color:var(--color-amber)]/50"
        >
          <ArrowRight className="h-4 w-4" /> بازگشت
        </button>
        <span className="flex items-center gap-1.5 text-[12px] text-[color:var(--color-ink-muted)]">
          <Layers className="h-4 w-4" /> {groupCount} گزینه دانلود
        </span>
      </div>

      <div className="mb-5 flex items-start gap-3.5 rounded-2xl border border-[color:var(--color-border)] bg-white/[0.03] p-3.5">
        {detail.poster ? (
          /* eslint-disable-next-line @next/next/no-img-element */
          <img src={detail.poster} alt="" className="h-32 w-22 shrink-0 rounded-xl object-cover" style={{ width: 88, height: 128 }} />
        ) : (
          <div className="flex h-32 w-22 shrink-0 items-center justify-center rounded-xl bg-white/5" style={{ width: 88, height: 128 }}>
            <Clapperboard className="h-8 w-8 text-[color:var(--color-ink-dim)]" />
          </div>
        )}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="rounded-full bg-[color:var(--color-plum)]/20 px-2 py-0.5 text-[10.5px] font-bold text-[color:var(--color-plum-soft)]">
              {SOURCE_LABEL[detail.source]}
            </span>
            <span className="flex items-center gap-1 rounded-full bg-white/5 px-2 py-0.5 text-[10.5px] font-bold text-[color:var(--color-ink-muted)]">
              {KIND_ICON[detail.kind]}
              {KIND_LABEL[detail.kind] ?? detail.kind}
            </span>
          </div>
          <h3 className="mt-1.5 line-clamp-3 text-[15px] font-bold leading-snug text-[color:var(--color-ink)]">{detail.title}</h3>
          <p className="mt-1 text-[11.5px] leading-relaxed text-[color:var(--color-ink-dim)]">
            هر ردیف کیفیت و لینک‌های دانلود همان مورد را نشان می‌دهد.
          </p>
        </div>
      </div>

      {msg && (
        <div
          className={`mb-3 flex items-center gap-2 rounded-xl border px-3.5 py-2.5 text-[13px] ${
            msg.error ? "border-[color:var(--color-coral)]/40 bg-[color:var(--color-coral)]/10 text-[color:var(--color-coral)]" : "border-emerald-500/40 bg-emerald-500/10 text-emerald-400"
          }`}
        >
          {msg.error ? <AlertCircle className="h-4 w-4 shrink-0" /> : <CheckCircle className="h-4 w-4 shrink-0" />}
          {msg.text}
        </div>
      )}

      {detail.groups.length === 0 && !detailBusy && (
        <p className="py-6 text-center text-[13px] text-[color:var(--color-ink-dim)]">لینک دانلودی روی این صفحه پیدا نشد.</p>
      )}

      <div className="flex max-h-[420px] flex-col gap-2.5 overflow-y-auto">
        <AnimatePresence initial={false}>
          {detail.groups.map((g, gi) => {
            const eps = g.episodes ?? [];
            const quals = g.items ?? [];
            return (
              <motion.div
                key={`${gi}-${g.label}`}
                layout
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                className="rounded-2xl border border-[color:var(--color-border)] bg-white/[0.03] p-3"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex min-w-0 items-center gap-2">
                    <FolderOpen className="h-4 w-4 shrink-0 text-[color:var(--color-amber)]" />
                    <div className="min-w-0">
                      <div className="truncate text-[13px] font-semibold text-[color:var(--color-ink)]">{g.label}</div>
                      <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11px] text-[color:var(--color-ink-dim)]">
                        {g.version && <span className="rounded-full bg-white/5 px-2 py-0.5">{g.version}</span>}
                        {eps.length > 0 && <span>{eps.length} قسمت</span>}
                        {g.size && <span>{g.size}</span>}
                      </div>
                    </div>
                  </div>
                  {eps.length > 0 && (
                    <button
                      onClick={() => onAddDs(g)}
                      disabled={!!addBusy}
                      className="flex shrink-0 items-center gap-1.5 rounded-xl px-3.5 py-2 text-[12.5px] font-bold text-white transition-transform active:scale-[0.98] disabled:opacity-50"
                      style={{ background: "linear-gradient(135deg, var(--color-amber), var(--color-plum))" }}
                    >
                      {addBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
                      افزودن همه
                    </button>
                  )}
                </div>

                {quals.length > 0 && (
                  <div className="mt-2.5 flex flex-col gap-2 border-t border-[color:var(--color-border)]/60 pt-2.5">
                    {quals.map((qitem) => {
                      const dirUrl = qitem.dir_url;
                      const files = animexFiles[dirUrl] ?? [];
                      const isOpen = !!animexOpen[dirUrl];
                      const filesBusy = addBusy === `ax-files|${dirUrl}`;
                      return (
                        <div key={dirUrl} className="rounded-xl bg-white/[0.03] px-3 py-2">
                          <div className="flex items-center justify-between gap-2">
                            <span className="flex min-w-0 items-center gap-2 text-[12.5px] text-[color:var(--color-ink)]">
                              <DownloadCloud className="h-4 w-4 shrink-0 text-[color:var(--color-teal)]" />
                              <span className="truncate">{qitem.quality}</span>
                              {files.length > 0 && (
                                <span className="rounded-full bg-white/5 px-2 py-0.5 text-[10.5px] text-[color:var(--color-ink-muted)]">{files.length} قسمت</span>
                              )}
                            </span>
                            <button
                              onClick={() => onAddAnimex(g, dirUrl, qitem.quality)}
                              disabled={!!addBusy}
                              className="flex shrink-0 items-center gap-1 rounded-lg border border-[color:var(--color-border)] bg-white/5 px-2.5 py-1.5 text-[11.5px] font-bold text-[color:var(--color-ink)] hover:border-[color:var(--color-amber)]/50 disabled:opacity-50"
                            >
                              {addBusy === `${g.label}|${qitem.quality}` ? <Loader2 className="h-3.5 w-3.5 animate-spin text-amber-400" /> : <Plus className="h-3.5 w-3.5" />}
                              افزودن همه
                            </button>
                          </div>
                          <button
                            type="button"
                            onClick={() => onToggleAnimex(dirUrl)}
                            className="mt-1.5 flex items-center gap-1 text-[12px] font-semibold text-[color:var(--color-amber)]"
                          >
                            <ChevronDown className={`h-3.5 w-3.5 transition-transform ${isOpen ? "rotate-180" : ""}`} />
                            {isOpen ? "بستن لیست قسمت‌ها" : "نمایش قسمت‌ها"}
                          </button>
                          <AnimatePresence initial={false}>
                            {isOpen && (
                              <motion.div
                                initial={{ opacity: 0, height: 0 }}
                                animate={{ opacity: 1, height: "auto" }}
                                exit={{ opacity: 0, height: 0 }}
                                className="overflow-hidden"
                              >
                                <div className="mt-2 flex max-h-52 flex-col gap-1.5 overflow-y-auto pl-1">
                                  {filesBusy && (
                                    <div className="flex items-center gap-2 py-2 text-[12px] text-[color:var(--color-ink-muted)]">
                                      <Loader2 className="h-3.5 w-3.5 animate-spin text-amber-400" /> در حال دریافت لینک‌های قسمت…
                                    </div>
                                  )}
                                  {!filesBusy && files.length === 0 && (
                                    <p className="py-2 text-center text-[12px] text-[color:var(--color-ink-dim)]">قسمتی برای نمایش پیدا نشد.</p>
                                  )}
                                  {!filesBusy &&
                                    files.map((file, i) => (
                                      <div key={`${file.url}-${i}`} className="flex items-center justify-between gap-2 rounded-xl bg-white/[0.03] px-3 py-2">
                                        <span className="flex min-w-0 items-center gap-2 text-[12.5px] text-[color:var(--color-ink)]">
                                          <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-[color:var(--color-plum)]/20 text-[10.5px] font-bold text-[color:var(--color-plum-soft)]">
                                            {i + 1}
                                          </span>
                                          <span className="truncate">{episodeFromName(file.name)}</span>
                                        </span>
                                        <button
                                          onClick={() => onAddAnimexEp(g, dirUrl, qitem.quality, file, i)}
                                          disabled={!!addBusy}
                                          className="flex shrink-0 items-center gap-1 rounded-lg border border-[color:var(--color-border)] bg-white/5 px-2.5 py-1.5 text-[11.5px] font-bold text-[color:var(--color-ink)] hover:border-[color:var(--color-amber)]/50 disabled:opacity-50"
                                        >
                                          {addBusy === `ax-one|${dirUrl}|${i}` ? <Loader2 className="h-3.5 w-3.5 animate-spin text-amber-400" /> : <Plus className="h-3.5 w-3.5" />}
                                          افزودن
                                        </button>
                                      </div>
                                    ))}
                                </div>
                              </motion.div>
                            )}
                          </AnimatePresence>
                        </div>
                      );
                    })}
                  </div>
                )}

                {eps.length > 0 && (
                  <div className="mt-2.5 border-t border-[color:var(--color-border)]/60 pt-2.5">
                    <button
                      type="button"
                      onClick={() => setOpenEps((s) => ({ ...s, [String(gi)]: !s[String(gi)] }))}
                      className="flex items-center gap-1 text-[12px] font-semibold text-[color:var(--color-amber)]"
                    >
                      <ChevronDown
                        className={`h-3.5 w-3.5 transition-transform ${openEps[String(gi)] ? "rotate-180" : ""}`}
                      />
                      {openEps[String(gi)] ? "بستن لیست قسمت‌ها" : `نمایش ${eps.length} قسمت`}
                    </button>
                    <AnimatePresence initial={false}>
                      {openEps[String(gi)] && (
                        <motion.div
                          initial={{ opacity: 0, height: 0 }}
                          animate={{ opacity: 1, height: "auto" }}
                          exit={{ opacity: 0, height: 0 }}
                          className="overflow-hidden"
                        >
                          <div className="mt-2 flex max-h-52 flex-col gap-1.5 overflow-y-auto pl-1">
                            {eps.map((ep, i) => (
                              <div key={`${ep.url}-${i}`} className="flex items-center justify-between gap-2 rounded-xl bg-white/[0.03] px-3 py-2">
                                <span className="flex min-w-0 items-center gap-2 text-[12.5px] text-[color:var(--color-ink)]">
                                  <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-[color:var(--color-plum)]/20 text-[10.5px] font-bold text-[color:var(--color-plum-soft)]">
                                    {ep.num ?? i + 1}
                                  </span>
                                  <span className="truncate">{ep.num ? `قسمت ${ep.num}` : `قسمت ${i + 1}`}</span>
                                </span>
                                <button
                                  onClick={() => onAddDsOne(g, ep, i)}
                                  disabled={!!addBusy}
                                  className="flex shrink-0 items-center gap-1 rounded-lg border border-[color:var(--color-border)] bg-white/5 px-2.5 py-1.5 text-[11.5px] font-bold text-[color:var(--color-ink)] hover:border-[color:var(--color-amber)]/50 disabled:opacity-50"
                                >
                                  {addBusy === "ds-one" ? <Loader2 className="h-3.5 w-3.5 animate-spin text-amber-400" /> : <Plus className="h-3.5 w-3.5" />}
                                  افزودن
                                </button>
                              </div>
                            ))}
                          </div>
                        </motion.div>
                      )}
                    </AnimatePresence>
                  </div>
                )}
              </motion.div>
            );
          })}
        </AnimatePresence>
      </div>
    </div>
  );
}
