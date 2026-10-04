import type { Metadata, Viewport } from "next";
import { vazirmatn, jetbrainsMono, lalezar } from "./fonts";
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

// viewport-fit=cover lets the cinema paint under notches / system bars on
// phones; the CSS then keeps controls clear of them via env(safe-area-inset-*).
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: "#0b070d",
  interactiveWidget: "resizes-content",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="fa" dir="rtl" suppressHydrationWarning className={`${vazirmatn.variable} ${jetbrainsMono.variable} ${lalezar.variable}`}>
      <body>{children}</body>
    </html>
  );
}
