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
      return `<b>${name}</b> به اتاق پیوست`;
    case "leave":
      return `<b>${name}</b> از اتاق خارج شد`;
    case "playlist_add":
      return `<b>${name}</b> «${title}» را اضافه کرد`;
    case "playlist_add_downloading":
      return `<b>${name}</b> «${title}» را اضافه کرد (در حال دانلود…)`;
    case "media_ready":
      return `ویدیوی «${title}» آماده‌ی پخش شد`;
    case "media_error":
      return `پخش «${title}» با خطا مواجه شد`;
    case "subtitle_auto_extracted":
      return `${n.count ?? ""} زیرنویس از داخل «${title}» به‌صورت خودکار استخراج شد`;
    case "playlist_add_live":
      return `<b>${name}</b> پخش زنده «${title}» را اضافه کرد`;
    case "playlist_add_many":
      return `<b>${name}</b> ${n.count ?? ""} قسمت از «${title}» را به پلی‌لیست اضافه کرد`;
    case "playlist_remove":
      return `<b>${name}</b> «${title}» را حذف کرد`;
    case "subtitle_add":
      return `<b>${name}</b> زیرنویس «${label}» را اضافه کرد`;
    case "audio_track_add":
      return `<b>${name}</b> کانال صدای «${label}» را اضافه کرد`;
    case "ctrl_play":
      return `<b>${name}</b> پخش را شروع کرد`;
    case "ctrl_pause":
      return `<b>${name}</b> پخش را مکث کرد`;
    case "ctrl_seek":
      return `<b>${name}</b> زمان را به ${formatTime(extra.to as number)} برد`;
    case "ctrl_rate":
      return `<b>${name}</b> سرعت پخش را ${extra.rate}x کرد`;
    case "ctrl_select": {
      const idx = typeof extra.index === "number" ? extra.index : -1;
      const item = idx >= 0 ? playlist[idx] : null;
      return `<b>${name}</b> «${escapeHtml(item ? item.title : "یک ویدیو")}» را برای پخش انتخاب کرد`;
    }
    case "room_promote": {
      const target = escapeHtml(String(extra.target || ""));
      return target ? `<b>${name}</b> <b>${target}</b> را به کنترلر ارتقا داد` : `<b>${name}</b> یک کاربر را به کنترلر ارتقا داد`;
    }
    case "room_demote": {
      const target = escapeHtml(String(extra.target || ""));
      return target ? `<b>${name}</b> <b>${target}</b> را از کنترلری برداشت` : `<b>${name}</b> یک کاربر را از کنترلری برداشت`;
    }
    case "room_ban": {
      const target = escapeHtml(String(extra.target || ""));
      return target ? `<b>${name}</b> <b>${target}</b> را از اتاق اخراج کرد` : `<b>${name}</b> یک کاربر را از اتاق اخراج کرد`;
    }
    case "room_unban": {
      const target = escapeHtml(String(extra.target || ""));
      return target ? `<b>${name}</b> <b>${target}</b> را از لیست اخراج خارج کرد` : `<b>${name}</b> یک کاربر را از لیست اخراج خارج کرد`;
    }
    default:
      return `${name} یک تغییر اعمال کرد`;
  }
}
