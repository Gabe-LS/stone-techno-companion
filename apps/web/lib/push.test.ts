/*
 * Unit tests for the pure logic in lib/push.ts -- the part of the INV-3
 * ("one origin, one shared push subscription", docs/invariants.md) lineup
 * push plumbing that doesn't require a real browser/Push API. The
 * behavioral gate for the rest (actual subscribe/unsubscribe against a real
 * service worker) is the notification test harness at cutover
 * (docs/parity/pwa-shell.md section 11), not this file.
 *
 * Runs via `npm test` (vitest, node environment). A minimal in-memory
 * localStorage polyfill is used instead of pulling in jsdom, since these
 * tests only touch storage + a couple of pure functions.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  enableLineupPush,
  disableLineupPush,
  isLineupPushEnabled,
  LINEUP_PUSH_FLAG,
  repairLineupPushOnLoad,
  shouldUnsubscribeSharedSubscription,
  urlBase64ToUint8Array,
} from "./push";

class MemoryStorage {
  private store = new Map<string, string>();
  getItem(key: string): string | null {
    return this.store.has(key) ? this.store.get(key)! : null;
  }
  setItem(key: string, value: string): void {
    this.store.set(key, value);
  }
  removeItem(key: string): void {
    this.store.delete(key);
  }
  clear(): void {
    this.store.clear();
  }
}

beforeEach(() => {
  vi.stubGlobal("localStorage", new MemoryStorage());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("urlBase64ToUint8Array", () => {
  it("decodes a URL-safe base64 VAPID key into bytes", () => {
    // "hello" -> base64 "aGVsbG8=" -> URL-safe form drops the padding.
    const bytes = urlBase64ToUint8Array("aGVsbG8");
    expect(Array.from(bytes)).toEqual([104, 101, 108, 108, 111]);
  });

  it("handles the -/_ URL-safe substitutions", () => {
    // Bytes [0xfb, 0xff] base64-encode to "-_8=" in URL-safe form
    // ("+/8=" in standard base64).
    const bytes = urlBase64ToUint8Array("-_8");
    expect(Array.from(bytes)).toEqual([0xfb, 0xff]);
  });
});

describe("isLineupPushEnabled / shouldUnsubscribeSharedSubscription", () => {
  it("is disabled by default", () => {
    expect(isLineupPushEnabled()).toBe(false);
  });

  it("reflects the stc_push flag", () => {
    localStorage.setItem(LINEUP_PUSH_FLAG, "1");
    expect(isLineupPushEnabled()).toBe(true);
  });

  it("unsubscribes the shared subscription when chat's push_enabled flag is absent", () => {
    expect(shouldUnsubscribeSharedSubscription()).toBe(true);
  });

  it("keeps the shared subscription when chat is still enabled (INV-3)", () => {
    localStorage.setItem("push_enabled", "1");
    expect(shouldUnsubscribeSharedSubscription()).toBe(false);
  });

  it("unsubscribes once chat's flag is cleared again", () => {
    localStorage.setItem("push_enabled", "1");
    localStorage.removeItem("push_enabled");
    expect(shouldUnsubscribeSharedSubscription()).toBe(true);
  });
});

describe("enableLineupPush", () => {
  it("reports unsupported when the browser lacks Push API support", async () => {
    vi.stubGlobal("navigator", {});
    // window.PushManager is intentionally left undefined.
    const result = await enableLineupPush({ getSessionId: async () => "abc" });
    expect(result).toEqual({ ok: false, reason: "unsupported" });
    expect(isLineupPushEnabled()).toBe(false);
  });
});

describe("disableLineupPush / repairLineupPushOnLoad", () => {
  it("disable always clears the local flag even with no serviceWorker support", async () => {
    localStorage.setItem(LINEUP_PUSH_FLAG, "1");
    vi.stubGlobal("navigator", {});
    await disableLineupPush({ getSessionId: async () => "abc" });
    expect(isLineupPushEnabled()).toBe(false);
  });

  it("repair is a no-op when the flag was never set (never overrides an explicit disable)", async () => {
    const getSessionId = vi.fn(async () => "abc");
    await repairLineupPushOnLoad({ getSessionId });
    expect(getSessionId).not.toHaveBeenCalled();
  });
});
