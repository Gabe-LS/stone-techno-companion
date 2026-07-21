/*
 * localStorage helpers, ported from services/companion/static/shared.js
 * (storageGet/storageSet/storageRemove) so the Next.js port reads/writes the
 * exact same keys with the exact same swallow-everything semantics: private
 * browsing / a full quota must never throw out of these calls, since the
 * legacy app relies on that to keep push/view-state code from crashing on a
 * locked-down browser.
 */

export function storageGet(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function storageSet(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* quota exceeded or private browsing -- same as shared.js */
  }
}

export function storageRemove(key: string): void {
  try {
    localStorage.removeItem(key);
  } catch {
    /* private browsing -- same as shared.js */
  }
}
