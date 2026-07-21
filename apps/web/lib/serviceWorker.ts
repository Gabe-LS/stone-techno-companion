/*
 * Service worker registration for the Next.js shell (docs/parity/pwa-shell.md
 * section 8, docs/invariants.md INV-3). Registers the SAME /sw.js at the
 * SAME root scope the legacy pages use (services/data/scraper/render.py line
 * ~750 `navigator.serviceWorker.register('/sw.js')`, services/companion/chat/chat.html
 * line 5491) -- root scope matters because a browser holds exactly one push
 * subscription per (origin, scope), and lineup/chat sharing that one
 * subscription is the whole point of INV-3.
 *
 * Gated so plain `next dev` on localhost:3000 doesn't mint a throwaway
 * PushSubscription (and a throwaway push_subscriptions/chat_push_subscriptions
 * row server-side) on every reload during everyday development: registration
 * only happens in production, or in dev when a developer has explicitly
 * opted in via `localStorage.stc_dev_sw = '1'` (set it and reload -- same
 * opt-in convention as `stc_debug`, see CLAUDE.md "Conventions").
 */

import { dbg } from "./debug";

export const DEV_SW_FLAG = "stc_dev_sw";

/** Pure decision function (no side effects), unit-testable without a real
 * ServiceWorkerContainer. */
export function shouldRegisterServiceWorker(nodeEnv: string | undefined): boolean {
  if (nodeEnv === "production") return true;
  try {
    return localStorage.getItem(DEV_SW_FLAG) === "1";
  } catch {
    return false;
  }
}

/** Registers /sw.js if supported and gated on. Never throws; every outcome
 * (registered, skipped-unsupported, skipped-gated, failed) is dbg()'d per
 * CLAUDE.md "Conventions" / docs/invariants.md INV-17. */
export function registerServiceWorker(): void {
  if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) {
    dbg("[sw] registration skipped: serviceWorker unsupported in this browser");
    return;
  }
  if (!shouldRegisterServiceWorker(process.env.NODE_ENV)) {
    dbg(
      "[sw] registration skipped: not production and localStorage.stc_dev_sw !== '1'",
    );
    return;
  }
  dbg("[sw] registering /sw.js at root scope");
  navigator.serviceWorker.register("/sw.js").then(
    (reg) => dbg("[sw] registered", reg.scope),
    (err) => dbg("[sw] registration failed", err),
  );
}
