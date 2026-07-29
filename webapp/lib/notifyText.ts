import { escapeHtml, formatTime } from "./format";
import type { NotifyEvent, PlaylistItem } from "./types";

// Builds the small HTML fragment (only <b> for emphasis) shown in both the
// toast queue and the recent-activity feed. Names/titles are user-controlled
// so they're escaped before interpolation.
export function notifyText(n: NotifyEvent, playlist: PlaylistItem[]): string {
  const name = escapeHtml(n.name || "کسی");
  const title = escapeHtml((n.title as string) || "");
  const label = escapeHtml((n.label as string) || "");
  const extra = (n.extra || {}) as Record<string, unknown>;

  switch (n.type) {
    case "join":
      return `👋 <b>${name}</b> به اتاق پیوست`;
    case "leave":
      return `🚪 <b>${name}</b> از اتاق خارج شد`;
    case "playlist_add":
      return `➕ <b>${name}</b> «${title}» را اضافه کرد`;
    case "playlist_add_processing":
      return `⏳ <b>${name}</b> «${title}» را اضافه کرد (در حال آماده‌سازی روی سرور…)`;
    case "media_partial_ready":
      return `▶️ «${title}» قابل پخش شد (کیفیت پیش‌فرض — بقیه‌ی کیفیت‌ها on-demand هستن)`;
    case "media_ready":
      return `✅ ویدیوی «${title}» کامل آماده‌ی پخش شد`;
    case "media_error":
      return `⚠️ تبدیل «${title}» با خطا مواجه شد`;
    case "quality_partial_ready":
      return `🖼 کیفیت ${label} برای «${title}» قابل پخش شد`;
    case "quality_ready":
      return `🖼 کیفیت ${label} برای «${title}» کامل آماده شد`;
    case "quality_error":
      return `⚠️ آماده‌سازی کیفیت ${label} برای «${title}» با خطا مواجه شد`;
    case "subtitle_auto_extracted":
      return `💬 ${n.count ?? ""} زیرنویس از داخل «${title}» به‌صورت خودکار استخراج شد`;
    case "playlist_add_live":
      return `📡 <b>${name}</b> پخش زنده «${title}» را اضافه کرد`;
    case "playlist_remove":
      return `🗑 <b>${name}</b> «${title}» را حذف کرد`;
    case "subtitle_add":
      return `💬 <b>${name}</b> زیرنویس «${label}» را اضافه کرد`;
    case "audio_track_add":
      return `🎧 <b>${name}</b> کانال صدای «${label}» را اضافه کرد`;
    case "ctrl_play":
      return `▶️ <b>${name}</b> پخش را شروع کرد`;
    case "ctrl_pause":
      return `⏸ <b>${name}</b> پخش را مکث کرد`;
    case "ctrl_seek":
      return `⏩ <b>${name}</b> زمان را به ${formatTime(extra.to as number)} برد`;
    case "ctrl_rate":
      return `🚀 <b>${name}</b> سرعت پخش را ${extra.rate}x کرد`;
    case "ctrl_select": {
      const idx = typeof extra.index === "number" ? extra.index : -1;
      const item = idx >= 0 ? playlist[idx] : null;
      return `🎬 <b>${name}</b> «${escapeHtml(item ? item.title : "یک ویدیو")}» را برای پخش انتخاب کرد`;
    }
    default:
      return `${name} یک تغییر اعمال کرد`;
  }
}
