"use client";

import { useEffect } from "react";
import { registerServiceWorker } from "../lib/serviceWorker";

/**
 * Fires the gated /sw.js registration once on mount (docs/parity/pwa-shell.md
 * section 8, docs/invariants.md INV-3). Renders nothing -- this is wiring,
 * not UI. Lives in the root layout so it runs on every route, matching both
 * legacy pages registering the worker themselves.
 */
export default function ServiceWorkerRegistration() {
  useEffect(() => {
    registerServiceWorker();
  }, []);

  return null;
}
