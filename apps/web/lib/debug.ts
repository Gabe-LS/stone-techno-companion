/*
 * Debug logging, ported from services/companion/static/shared.js so the
 * Next.js port keeps the exact same opt-in convention: output is silent
 * unless localStorage.stc_debug === '1' (set it and reload), verify()
 * failures always print regardless of the flag. See CLAUDE.md "Conventions".
 */

let debugEnabled = false;
if (typeof window !== "undefined") {
  try {
    debugEnabled = window.localStorage.getItem("stc_debug") === "1";
  } catch {
    debugEnabled = false;
  }
}

const t0 = typeof performance !== "undefined" ? performance.now() : 0;
let dbgTag = "[app]";

function ts(): string {
  const now = typeof performance !== "undefined" ? performance.now() : 0;
  return "+" + Math.floor(now - t0) + "ms";
}

/** Prefixes every subsequent dbg()/verify() line with `[tag]`, e.g. "transport". */
export function setDbgTag(tag: string): void {
  dbgTag = `[${tag}]`;
}

/** Timecoded debug line. No-op unless localStorage.stc_debug === '1'. */
export function dbg(...args: unknown[]): void {
  if (debugEnabled) console.log(ts(), dbgTag, ...args);
}

/**
 * Asserts a condition, mirroring shared.js's verify(). Success lines are
 * gated by the same debug flag as dbg(); failure lines always print.
 */
export function verify(label: string, condition: boolean, detail?: unknown): boolean {
  if (condition) {
    if (debugEnabled) console.log(ts(), `${dbgTag} OK: ${label}`, detail ?? "");
  } else {
    console.error(ts(), `${dbgTag} FAIL: ${label}`, detail ?? "");
  }
  return condition;
}
