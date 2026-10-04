import localFont from "next/font/local";

// Self-hosted via next/font/local rather than a public/ path — this is what
// makes font URLs correctly respect `basePath` in the web build AND resolve
// fine with no basePath in the Tauri build, with zero manual path juggling,
// plus automatic content-hashed caching and preloading.
export const vazirmatn = localFont({
  src: [
    { path: "./_fonts/Vazirmatn-Regular.woff2", weight: "400", style: "normal" },
    { path: "./_fonts/Vazirmatn-Medium.woff2", weight: "500 600", style: "normal" },
    { path: "./_fonts/Vazirmatn-Bold.woff2", weight: "700 900", style: "normal" },
  ],
  variable: "--font-vazirmatn",
  display: "optional",
  fallback: ["Tahoma", "ui-sans-serif"],
});

export const jetbrainsMono = localFont({
  src: [
    { path: "./_fonts/JetBrainsMono-Regular.woff2", weight: "400", style: "normal" },
    { path: "./_fonts/JetBrainsMono-Bold.woff2", weight: "700", style: "normal" },
  ],
  variable: "--font-jetbrains-mono",
  display: "optional",
  fallback: ["ui-monospace", "SFMono-Regular"],
});

// Chunky, friendly Persian display face — used only for the marquee/logo and
// playful empty states, never for body text.
export const lalezar = localFont({
  src: [{ path: "./_fonts/Lalezar-Regular.woff2", weight: "400", style: "normal" }],
  variable: "--font-lalezar",
  display: "swap",
  fallback: ["Vazirmatn", "Tahoma"],
});
