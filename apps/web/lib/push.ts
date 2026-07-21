/*
 * Push subscription lifecycle for the LINEUP surface, ported from
 * services/data/scraper/render.py's enableNotifications()/disableNotifications()/
 * re-sync-on-load (lines ~2426-2491, ~2900-2913), matching
 * docs/invariants.md INV-3 ("one origin, one shared push subscription") and
 * docs/parity/pwa-shell.md section 7 exactly:
 *
 *   - enable NEVER unsubscribes before subscribing: an existing subscription
 *     (possibly created by chat, since both surfaces share one root-scope
 *     SW/subscription) is reused, never rotated.
 *   - enable only flips the `stc_push` flag on when BOTH the browser
 *     subscribe/getSubscription call AND the server POST succeeded.
 *   - disable always deletes this surface's own server-side row, but only
 *     calls `sub.unsubscribe()` on the shared browser subscription when the
 *     OTHER surface (chat, flag `push_enabled`) is ALSO disabled.
 *   - repair-on-load is gated on this surface's OWN `stc_push` flag (never
 *     overrides an explicit disable): if no subscription exists it
 *     re-subscribes; if one exists it just resyncs the endpoint server-side
 *     (chat's enable flow may have created/rotated the shared subscription
 *     without lineup's backend ever finding out).
 *
 * Endpoints (docs/api/companion-openapi.yaml): GET /api/push/vapid-key,
 * POST/DELETE /api/session/{code}/push/subscribe.
 *
 * iOS/Brave-specific dialog UX (render.py's `_isIOS`/`_needsSafariSwitch`
 * gating and the "add to home screen" / "switch browser" dialogs) is
 * page-chrome, not push plumbing -- deliberately left for the lineup port
 * itself to layer on top of this module, not duplicated here.
 *
 * Fully unit-testable without a browser via dependency injection: callers
 * supply `getSessionId` (this Next app doesn't have a lineup session yet)
 * and the DOM/Push API surface is read through `navigator`/`window`
 * directly, so tests only need to stub those globals -- see push.test.ts
 * for the pure-logic subset that's tested today.
 */

import { dbg } from "./debug";
import { storageGet, storageRemove, storageSet } from "./storage";

/** This surface's own enabled flag -- exact key render.py uses. */
export const LINEUP_PUSH_FLAG = "stc_push";
/** Chat's enabled flag. Read-only from here: used ONLY for the
 * cross-surface disable check (INV-3), never written by this module. */
const CHAT_PUSH_FLAG = "push_enabled";

const DEFAULT_API_BASE = "/api";

/** Ported byte-for-byte from services/companion/static/shared.js
 * `_urlBase64ToUint8Array` (also duplicated in chat.html) -- decodes the
 * VAPID public key for `PushManager.subscribe`'s `applicationServerKey`. */
export function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  const arr = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
  return arr;
}

export function isLineupPushEnabled(): boolean {
  return storageGet(LINEUP_PUSH_FLAG) === "1";
}

/** INV-3's cross-surface disable rule, pure so it's unit-testable without a
 * real PushManager: only unsubscribe the shared browser subscription when
 * the OTHER surface (chat) is also off. */
export function shouldUnsubscribeSharedSubscription(): boolean {
  return storageGet(CHAT_PUSH_FLAG) !== "1";
}

export interface LineupPushDeps {
  /** Resolves (creating if necessary) this browser's lineup session id,
   * mirroring render.py's `ensureSession()`/`sessionId`. Return null if a
   * session could not be established -- enable then fails with
   * "no-session" rather than silently subscribing with nowhere to store
   * the subscription server-side. */
  getSessionId: () => Promise<string | null>;
  /** Override for tests / non-default deployments. Defaults to "/api". */
  apiBase?: string;
}

function apiBase(deps: Pick<LineupPushDeps, "apiBase">): string {
  return deps.apiBase ?? DEFAULT_API_BASE;
}

export type EnableFailureReason =
  | "unsupported"
  | "permission-denied"
  | "vapid-fetch-failed"
  | "no-session"
  | "server-rejected"
  | "error";

export type EnableResult = { ok: true } | { ok: false; reason: EnableFailureReason; detail?: unknown };

/**
 * Enable flow. Never unsubscribes before subscribing (INV-3); only sets
 * `stc_push` once both the subscribe and the server POST succeed.
 */
export async function enableLineupPush(deps: LineupPushDeps): Promise<EnableResult> {
  if (typeof navigator === "undefined" || !("serviceWorker" in navigator) || !("PushManager" in window)) {
    dbg("[push] enable: unsupported in this browser");
    return { ok: false, reason: "unsupported" };
  }
  const perm = await Notification.requestPermission();
  if (perm !== "granted") {
    dbg("[push] enable: permission not granted", perm);
    return { ok: false, reason: "permission-denied" };
  }
  try {
    const vapidRes = await fetch(`${apiBase(deps)}/push/vapid-key`);
    if (!vapidRes.ok) {
      dbg("[push] enable: vapid-key fetch failed", vapidRes.status);
      return { ok: false, reason: "vapid-fetch-failed" };
    }
    const { public_key: publicKey } = (await vapidRes.json()) as { public_key: string };
    const reg = await navigator.serviceWorker.ready;

    // INV-3: reuse an existing subscription (chat may already have created
    // the shared one) instead of unsubscribing/rotating it.
    let sub = await reg.pushManager.getSubscription();
    if (!sub) {
      dbg("[push] enable: no existing subscription, subscribing");
      sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        // Cast needed because lib.dom types `applicationServerKey` against
        // `ArrayBufferView<ArrayBuffer>` specifically, while a freshly
        // constructed `Uint8Array` types as `ArrayBufferLike` (which also
        // covers `SharedArrayBuffer`) as of TS 5.7's stricter typed-array
        // generics -- this Uint8Array is always backed by a plain
        // ArrayBuffer (built from `new Uint8Array(raw.length)` above), never
        // a SharedArrayBuffer, so the cast is safe.
        applicationServerKey: urlBase64ToUint8Array(publicKey) as BufferSource,
      });
    } else {
      dbg("[push] enable: reusing existing shared subscription");
    }

    const sessionId = await deps.getSessionId();
    if (!sessionId) {
      dbg("[push] enable: no session id available");
      return { ok: false, reason: "no-session" };
    }
    const subRes = await fetch(`${apiBase(deps)}/session/${sessionId}/push/subscribe`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(sub.toJSON()),
    });
    if (!subRes.ok) {
      dbg("[push] enable: subscribe POST failed", subRes.status);
      return { ok: false, reason: "server-rejected" };
    }
    storageSet(LINEUP_PUSH_FLAG, "1");
    dbg("[push] enable: success");
    return { ok: true };
  } catch (err) {
    dbg("[push] enable: threw", err);
    return { ok: false, reason: "error", detail: err };
  }
}

/**
 * Disable flow. Always deletes this surface's own server-side row; only
 * calls `sub.unsubscribe()` when chat is also disabled (INV-3).
 */
export async function disableLineupPush(deps: LineupPushDeps): Promise<void> {
  try {
    if (typeof navigator !== "undefined" && "serviceWorker" in navigator) {
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.getSubscription();
      if (sub) {
        const sessionId = await deps.getSessionId();
        if (sessionId) {
          await fetch(`${apiBase(deps)}/session/${sessionId}/push/subscribe`, {
            method: "DELETE",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ endpoint: sub.endpoint }),
          });
        }
        if (shouldUnsubscribeSharedSubscription()) {
          await sub.unsubscribe();
          dbg("[push] disable: unsubscribed shared subscription (chat also off)");
        } else {
          dbg("[push] disable: chat still enabled, keeping shared subscription");
        }
      }
    }
  } catch (err) {
    dbg("[push] disable: failed", err);
  }
  storageRemove(LINEUP_PUSH_FLAG);
}

/**
 * Repair-on-load. Gated on this surface's OWN flag so it never overrides an
 * explicit disable. If a subscription already exists, just resyncs the
 * endpoint server-side (the shared subscription may have been
 * created/rotated by chat's own enable flow without lineup's backend ever
 * finding out); if none exists, self-heals by clearing the stale flag.
 */
export async function repairLineupPushOnLoad(deps: LineupPushDeps): Promise<void> {
  if (!isLineupPushEnabled()) return;
  if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) return;
  try {
    const reg = await navigator.serviceWorker.ready;
    const existing = await reg.pushManager.getSubscription();
    if (existing) {
      const sessionId = await deps.getSessionId();
      if (sessionId) {
        fetch(`${apiBase(deps)}/session/${sessionId}/push/subscribe`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(existing.toJSON()),
        }).catch((err) => dbg("[push] repair: resync POST failed", err));
      }
    } else {
      dbg("[push] repair: no active subscription, clearing stale stc_push flag");
      storageRemove(LINEUP_PUSH_FLAG);
    }
  } catch (err) {
    dbg("[push] repair: failed", err);
  }
}
