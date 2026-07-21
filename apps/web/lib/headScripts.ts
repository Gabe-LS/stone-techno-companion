/*
 * Raw inline script bodies for the two head scripts that must run via
 * next/script's `beforeInteractive` strategy (injected server-side into
 * <head>, executed before hydration -- see docs/parity/pwa-shell.md section
 * 2 "Head-script contract"). Kept as plain strings (not TS logic imported
 * some other way) because next/script's beforeInteractive children ARE the
 * literal script text; centralizing them here at least means app/layout.tsx
 * doesn't inline two screens of stringified JS, and a future page-scoped
 * consumer (once lineup/timetable land in this app) can import the exact
 * same text instead of hand-copying it.
 *
 * Semantics must match services/data/scraper/render.py lines ~264-294
 * (lineup/timetable) and services/companion/chat/chat.html lines 5-6
 * (chat) exactly -- see docs/parity/pwa-shell.md section 2 for the
 * line-by-line spec this was checked against.
 */

/**
 * Head script 3 (parity doc section 2): stamp `pwa-standalone` on
 * <html> before first paint, on EVERY page (both lineup/timetable and chat
 * do this today; render.py currently only does the equivalent check late,
 * inline, in its bell logic -- render.py's `_isStandalone` at line ~2412).
 * Standalone-only CSS (safe-area padding etc., ported alongside whichever
 * surface needs it) keys off this class from first paint, not after JS
 * settles.
 */
export const PWA_STANDALONE_CLASS_SCRIPT = `(function(){
  try {
    if (window.navigator.standalone || window.matchMedia('(display-mode: standalone)').matches) {
      document.documentElement.classList.add('pwa-standalone');
    }
  } catch (e) {}
})();`;

/**
 * Head script 1 (parity doc section 2): the standalone-launch resume
 * redirect, PLUS the last_section='lineup' reclaim stamp render.py's real
 * script always falls through to.
 *
 * Legacy semantics (render.py lines ~264-278): this script only ever ships
 * on the lineup/timetable HTML, never on chat.html or transport.html, so its
 * unconditional "stamp lineup" fallthrough is scoped by WHICH PAGE embeds
 * the script, not by a pathname check inside it. Next's root layout is
 * shared by every route, so "lineup-side" is encoded here as "anything
 * that isn't under /chat" (chat is the only surface that must stamp
 * 'chat' instead, per CLAUDE.md "last_section flag, stamped by chat,
 * reclaimed by any lineup visit" -- chat itself is not built in this Next
 * app yet, so this is forward-compatible with chat landing here later,
 * without needing to revisit this file).
 *
 * This also intentionally makes /transport stamp last_section='lineup',
 * which the CURRENT legacy transport.html does NOT do (verified: it has no
 * localStorage/manifest/standalone code at all) -- see the pwa-shell wave
 * report for why this is a deliberate improvement-on-parity rather than a
 * literal one, made because CLAUDE.md groups transport as lineup-side and
 * the task asked for resume behavior to "stay coherent whichever world
 * serves the user".
 *
 * next/script's beforeInteractive strategy requires this to live in the
 * root layout (see node_modules/next/dist/docs/.../components/script.md),
 * so the pathname === '/' gate that the parity doc describes as implicit
 * (because the script only ever shipped on one page) is made explicit here
 * instead.
 */
export const STANDALONE_RESUME_SCRIPT = `(function(){
  try {
    var isStandalone = window.navigator.standalone || window.matchMedia('(display-mode: standalone)').matches;
    if (location.pathname === '/' && isStandalone && localStorage.getItem('last_section') === 'chat') {
      location.replace('/chat');
      return;
    }
    if (location.pathname.indexOf('/chat') !== 0) {
      localStorage.setItem('last_section', 'lineup');
    }
  } catch (e) {}
})();`;
