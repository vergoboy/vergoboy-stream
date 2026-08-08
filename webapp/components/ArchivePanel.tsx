"use client";

import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { api } from "@/lib/api";
import type { ArchiveResult, ArchiveTitle, ArchiveGroup, ArchiveEpisode } from "@/lib/types";
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
  Link2,
  FolderOpen,
  ChevronDown,
} from "lucide-react";

const inputClass =
  "w-full rounded-xl border border-[color:var(--color-border)] bg-white/5 px-3.5 py-2.5 text-[13.5px] text-[color:var(--color-ink)] outline-none focus:border-[color:var(--color-amber)]";

const SOURCE_LABEL: Record<string, string> = { donyayeserial: "دنیای سریال", animex: "انیمکس" };

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

export function ArchivePanel({ myName }: { myName: string }) {
  const [q, setQ] = useState("");
  const [sources, setSources] = useState<Record<string, boolean>>({ donyayeserial: true, animex: true });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [results, setResults] = useState<ArchiveResult[] | null>(null);

  const [detail, setDetail] = useState<ArchiveTitle | null>(null);
  const [detailBusy, setDetailBusy] = useState(false);
  const [addBusy, setAddBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ text: string; error?: boolean } | null>(null);

  const activeSources = Object.entries(sources)
    .filter(([, on]) => on)
    .map(([id]) => id);

  async function doSearch(e?: React.FormEvent) {
    e?.preventDefault();
    const query = q.trim();
    if (!query || busy) return;
    if (activeSources.length === 0) {
      setErr("حداقل یک منبع را انتخاب کن");
      return;
    }
    setBusy(true);
    setErr(null);
    setResults(null);
    try {
      const data = await api.archiveSearch(query, activeSources);
      setResults(data.results);
    } catch (err2) {
      setErr(err2 instanceof Error ? err2.message : "خطا در جستجو");
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
    } catch (err2) {
      setErr(err2 instanceof Error ? err2.message : "خطا در دریافت صفحه");
    }
    setDetailBusy(false);
  }

  async function importUrl(rawUrl: string) {
    const url = rawUrl.trim();
    if (!url || detailBusy) return;
    const source = url.includes("animex.click") ? "animex" : url.includes("donyayeserial.com") ? "donyayeserial" : null;
    if (!source) {
      setMsg({ text: "فقط لینک صفحه از دنیای سریال یا انیمکس پشتیبانی می‌شود", error: true });
      return;
    }
    setDetailBusy(true);
    setDetail(null);
    setErr(null);
    setMsg(null);
    try {
      const info = await api.archiveTitle(source, url);
      setDetail(info);
    } catch (err2) {
      setErr(err2 instanceof Error ? err2.message : "خطا در دریافت صفحه");
    }
    setDetailBusy(false);
  }

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

  const groupCount = detail ? detail.groups.reduce((n, g) => n + (g.episodes?.length ?? 0) + (g.items?.length ?? 0), 0) : 0;

  return (
    <div>
      {!detail ? (
        <SearchView
          q={q}
          setQ={setQ}
          sources={sources}
          setSources={setSources}
          busy={busy}
          err={err}
          results={results}
          onSearch={doSearch}
          onOpen={openDetail}
          onImport={importUrl}
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

function SearchView({
  q,
  setQ,
  sources,
  setSources,
  busy,
  err,
  results,
  onSearch,
  onOpen,
  onImport,
}: {
  q: string;
  setQ: (s: string) => void;
  sources: Record<string, boolean>;
  setSources: (s: Record<string, boolean>) => void;
  busy: boolean;
  err: string | null;
  results: ArchiveResult[] | null;
  onSearch: (e?: React.FormEvent) => void;
  onOpen: (r: ArchiveResult) => void;
  onImport: (url: string) => void;
}) {
  const [link, setLink] = useState("");
  return (
    <div>
      <form onSubmit={onSearch} className="flex flex-col gap-2.5">
        <label className="text-xs text-[color:var(--color-ink-muted)]">جستجو در آرشیو فیلم و سریال</label>
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
        <div className="flex flex-wrap items-center gap-2">
          {Object.entries(SOURCE_LABEL).map(([id, label]) => (
            <button
              key={id}
              type="button"
              onClick={() => setSources({ ...sources, [id]: !sources[id] })}
              className={`rounded-full px-3.5 py-1.5 text-[12px] font-semibold transition-colors ${
                sources[id] ? "border border-[color:var(--color-plum)]/40 bg-[color:var(--color-plum)]/20 text-white" : "border border-[color:var(--color-border)] bg-white/5 text-[color:var(--color-ink-muted)]"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </form>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          onImport(link);
        }}
        className="mt-3 flex items-center gap-2"
      >
        <Link2 className="h-4 w-4 shrink-0 text-[color:var(--color-ink-dim)]" />
        <input
          className={`${inputClass} flex-1`}
          value={link}
          onChange={(e) => setLink(e.target.value)}
          placeholder="یا لینک صفحه سریال/انیمه را مستقیم وارد کن (دنیای سریال / انیمکس)"
          dir="ltr"
        />
        <button
          type="submit"
          className="shrink-0 rounded-xl border border-[color:var(--color-border)] bg-white/5 px-3.5 py-2.5 text-[13px] text-[color:var(--color-ink)] hover:border-[color:var(--color-amber)]/50"
        >
          دریافت
        </button>
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
            {detail.source === "animex"
              ? "برای افزودن، ابتدا لینک‌های مستقیم از پوشه دانلود سایت گرفته می‌شود."
              : "هر ردیف یک فصل و کیفیت است؛ «افزودن همه» تمام قسمت‌های همان ردیف را به پلی‌لیست اضافه می‌کند."}
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
                      const key = `${g.label}|${qitem.quality}`;
                      const isBusy = addBusy === key;
                      return (
                        <div key={key} className="flex items-center justify-between gap-2 rounded-xl bg-white/[0.03] px-3 py-2">
                          <span className="flex min-w-0 items-center gap-2 text-[12.5px] text-[color:var(--color-ink)]">
                            <DownloadCloud className="h-4 w-4 shrink-0 text-[color:var(--color-teal)]" />
                            <span className="truncate">{qitem.quality}</span>
                          </span>
                          <button
                            onClick={() => onAddAnimex(g, qitem.dir_url, qitem.quality)}
                            disabled={!!addBusy}
                            className="flex shrink-0 items-center gap-1.5 rounded-lg border border-[color:var(--color-border)] bg-white/5 px-3 py-1.5 text-[12px] font-bold text-[color:var(--color-ink)] hover:border-[color:var(--color-amber)]/50 disabled:opacity-50"
                          >
                            {isBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin text-amber-400" /> : <Plus className="h-3.5 w-3.5" />}
                            {isBusy ? "در حال دریافت لینک‌ها…" : "افزودن همه"}
                          </button>
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
