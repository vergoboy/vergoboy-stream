export function Footer() {
  return (
    <footer className="relative z-[1] mt-14 border-t border-[color:var(--color-border)] bg-white/[0.02]">
      <div className="mx-auto max-w-[1120px] px-5 py-8 md:px-7">
        <div className="flex flex-wrap items-center justify-between gap-3.5 text-xs text-[color:var(--color-ink-dim)]">
          <span>© {new Date().getFullYear()} vergoboy.ir — ساخته‌شده با ❤️ توسط آرمان</span>
          <span>⚡ Powered by Nginx</span>
        </div>
      </div>
    </footer>
  );
}
