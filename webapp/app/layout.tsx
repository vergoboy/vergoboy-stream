import type { Metadata } from "next";
import { vazirmatn, jetbrainsMono } from "./fonts";
import "./globals.css";

// metadata.icons doesn't auto-prefix basePath for a plain string path, so we
// compute the same basePath next.config.ts uses and prepend it ourselves.
const basePath = process.env.NEXT_PUBLIC_BUILD_TARGET === "tauri" ? "" : "/stream";

export const metadata: Metadata = {
  title: "تماشای مشترک | vergoboy.ir",
  description:
    "پخش‌کننده‌ی ویدیوی همزمان (Watch Party) vergoboy.ir — تماشای فیلم با هم، زیرنویس و دوبله‌ی اختصاصی هرکس، استریم زنده از دستگاه‌های خارجی.",
  icons: {
    icon: `${basePath}/favicon.svg`,
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="fa" dir="rtl" suppressHydrationWarning className={`${vazirmatn.variable} ${jetbrainsMono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
