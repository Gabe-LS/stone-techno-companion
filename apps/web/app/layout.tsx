import type { Metadata, Viewport } from "next";
import Script from "next/script";
import "../../../packages/design-tokens/tokens.css";
import "./globals.css";
import Nav from "../components/Nav";
import ServiceWorkerRegistration from "../components/ServiceWorkerRegistration";
import { PWA_STANDALONE_CLASS_SCRIPT, STANDALONE_RESUME_SCRIPT } from "../lib/headScripts";

// PWA meta tags + manifest link (docs/parity/pwa-shell.md section 1, CLAUDE.md
// "HTML Standards"), matching services/data/scraper/render.py's head output
// (lines ~344-351) exactly: `appleWebApp` is the Metadata API's way of
// emitting mobile-web-app-capable + apple-mobile-web-app-title +
// apple-mobile-web-app-status-bar-style together (see
// node_modules/next/dist/docs/.../generate-metadata.md#applewebapp for the
// exact output shape this was checked against). `manifest` emits the single
// shared <link rel="manifest"> (INV-4: never a second, chat-specific one).
export const metadata: Metadata = {
  title: "Stone Techno Companion",
  description: "Stage 3 Next.js front end scaffold (docs/roadmap.md section 3.3).",
  manifest: "/manifest.json",
  appleWebApp: {
    title: "Stone Techno Companion",
    statusBarStyle: "black-translucent",
  },
};

// `theme-color` moved out of `metadata` into `viewport` in Next 14+ (the
// `metadata.themeColor` field is deprecated) -- see generate-viewport.md.
// Value must stay "#111" to match every legacy page's
// `<meta name="theme-color" content="#111">` exactly.
export const viewport: Viewport = {
  themeColor: "#111",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>
        {/* Head-script contract (docs/parity/pwa-shell.md section 2). Both
            run via `beforeInteractive`, which next/script injects into the
            server-rendered <head> and executes before hydration, in
            declaration order -- matching "must run before first paint". */}
        <Script id="stc-standalone-resume" strategy="beforeInteractive">
          {STANDALONE_RESUME_SCRIPT}
        </Script>
        <Script id="stc-pwa-standalone-class" strategy="beforeInteractive">
          {PWA_STANDALONE_CLASS_SCRIPT}
        </Script>
        <ServiceWorkerRegistration />
        <Nav />
        <main>{children}</main>
      </body>
    </html>
  );
}
