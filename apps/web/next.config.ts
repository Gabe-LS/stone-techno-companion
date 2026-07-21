import path from "node:path";
import type { NextConfig } from "next";

// The companion FastAPI backend, run separately in dev (see README.md "Dev
// integration"). Override with BACKEND_ORIGIN if it's not on the default
// local port/cert.
const BACKEND_ORIGIN = process.env.BACKEND_ORIGIN ?? "https://localhost:64728";

const nextConfig: NextConfig = {
  // Hide the floating dev-tools button; it reads as a mystery UI element
  // during design review and never ships to production anyway.
  devIndicators: false,
  // Dev requests arrive as localhost (IPv6 ::1) or 127.0.0.1 depending on
  // the browser's resolver; without both allowed, the 127.0.0.1 path gets
  // Next's cross-origin dev degradation (broken HMR, stale-looking pages).
  allowedDevOrigins: ["localhost", "127.0.0.1"],
  // apps/web has its own lockfile, so Turbopack would otherwise infer
  // apps/web as the project root and refuse to resolve files outside it
  // (e.g. packages/design-tokens/tokens.css, imported by app/layout.tsx).
  // Point it at the monorepo root instead.
  turbopack: {
    root: path.join(__dirname, "..", ".."),
  },
  async rewrites() {
    // Only proxy in dev. In production the front end and the companion API
    // are expected to sit behind the same reverse proxy (Caddy), so no
    // rewrite is needed there.
    if (process.env.NODE_ENV !== "development") return [];
    return [
      { source: "/api/:path*", destination: `${BACKEND_ORIGIN}/api/:path*` },
      { source: "/chat/api/:path*", destination: `${BACKEND_ORIGIN}/chat/api/:path*` },
      { source: "/timetable-transport.json", destination: `${BACKEND_ORIGIN}/timetable-transport.json` },
      { source: "/getting-there.json", destination: `${BACKEND_ORIGIN}/getting-there.json` },
    ];
  },
};

export default nextConfig;
