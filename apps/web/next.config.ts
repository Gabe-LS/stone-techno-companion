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
      // PWA shell artifacts (docs/parity/pwa-shell.md). sw.js and
      // manifest.json must have exactly ONE source of truth --
      // services/companion/static/ -- for as long as the legacy FastAPI app
      // is still live: copying either file into apps/web would fork the
      // single-worker/single-manifest invariants (docs/invariants.md INV-3,
      // INV-4) the instant one side updates and the other doesn't. In dev,
      // proxy straight through so `next dev` serves the exact same bytes the
      // legacy site does, at the same paths, so a byte-diff against the
      // backend (see tests/web/pwa_shell_check.py) is meaningful. favicon.svg
      // /favicon.png are proxied for the same single-source-of-truth reason
      // (manifest.json's icon list points at them); favicon.ico is left to
      // Next's own file-convention route (app/favicon.ico) since the backend
      // doesn't serve one. At production cutover (docs/parity/pwa-shell.md
      // "Cutover checklist" steps 2-4), this becomes a same-origin *serving*
      // decision behind Caddy instead of a Next.js rewrite -- Caddy can route
      // these paths straight to the FastAPI backend, or the front end can
      // start serving them itself byte-for-byte identical, with no change
      // needed here.
      { source: "/sw.js", destination: `${BACKEND_ORIGIN}/sw.js` },
      { source: "/manifest.json", destination: `${BACKEND_ORIGIN}/manifest.json` },
      { source: "/favicon.svg", destination: `${BACKEND_ORIGIN}/favicon.svg` },
      { source: "/favicon.png", destination: `${BACKEND_ORIGIN}/favicon.png` },
    ];
  },
};

export default nextConfig;
