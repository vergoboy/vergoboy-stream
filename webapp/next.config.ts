import type { NextConfig } from "next";

// Two build targets share this one config:
//  - "web"   (default): served by Nginx at https://vergoboy.ir/stream/ —
//    needs basePath "/stream" and same-origin (empty) API origin.
//  - "tauri": bundled inside the desktop/Android app, where the frontend's
//    own origin is tauri://localhost (NOT vergoboy.ir) — so it must NOT
//    have a basePath, and NEXT_PUBLIC_API_ORIGIN must be set to the real
//    absolute backend URL at build time. See TAURI.md.
const isTauriBuild = process.env.NEXT_PUBLIC_BUILD_TARGET === "tauri";

const nextConfig: NextConfig = {
  output: "export",
  basePath: isTauriBuild ? "" : "/stream",
  trailingSlash: true,
  images: {
    unoptimized: true,
  },
  // Silences the "multiple lockfiles" warning by pinning this project's own
  // root explicitly — otherwise Turbopack may guess wrong if a stray
  // package-lock.json exists elsewhere on the machine (e.g. in $HOME).
  turbopack: {
    root: __dirname,
  },
  // Next dev's HMR websocket refuses cross-origin requests by default. Add
  // any host/IP you'll actually open the dev server from that ISN'T
  // localhost (e.g. your server's LAN/public IP, or a domain). Not needed
  // for the production static export — dev only.
  allowedDevOrigins: [
    "37.32.43.212",
    // "your-other-dev-host.example.com",
  ],
};

export default nextConfig;
