"""
Standalone Playwright + HTTP verification for the Next.js PWA shell
(docs/parity/pwa-shell.md), covering apps/web/next.config.ts's rewrites,
apps/web/app/layout.tsx's head scripts/metadata, and
apps/web/lib/{push,serviceWorker,headScripts}.ts.

Scope: this checks the SHELL mechanics the pwa-shell wave built --
same-artifact serving, SW registration gating, standalone detection, and
PWA meta tags. It does NOT exercise a live push round-trip (that's
tests/notif_e2e/run.py and tests/verify_push_both.py, which test the
BACKEND emission plus the real sw.js -- neither of which this wave touched)
and does NOT test lineup/timetable/chat pages (not ported to this app yet).

Not part of the pytest suite (the Next.js app has no pytest integration);
run directly, same convention as tests/web/transport_nextjs_check.py. Must
run OUTSIDE the command sandbox (headless Chromium needs Mach-port access).

Prerequisites (both already running, this script does not start them):
  - the companion FastAPI backend on https://localhost:64728 (or set
    BACKEND_ORIGIN / STC_BACKEND_URL)
  - `next dev` for apps/web on a free port, with BACKEND_ORIGIN pointing at
    the backend above (see apps/web/next.config.ts)

Usage:
  STC_WEB_BASE_URL=http://localhost:3000 python tests/web/pwa_shell_check.py
"""

import os
import sys
import urllib.request
import ssl

from playwright.sync_api import sync_playwright

WEB_BASE_URL = os.environ.get("STC_WEB_BASE_URL", "http://localhost:3000")
BACKEND_URL = os.environ.get("STC_BACKEND_URL", "https://localhost:64728")

PASS = []
FAIL = []

_INSECURE_CTX = ssl.create_default_context()
_INSECURE_CTX.check_hostname = False
_INSECURE_CTX.verify_mode = ssl.CERT_NONE


def check(label, condition, detail=""):
    if condition:
        PASS.append(label)
        print(f"OK   {label}")
    else:
        FAIL.append(label)
        print(f"FAIL {label}  {detail}")


def fetch(url, timeout=8):
    """GET url (self-signed TLS tolerated for the https:// backend), return
    (status, headers, body). `headers` is the raw email.message.Message the
    stdlib returns -- NOT converted to a plain dict -- so `.get(...)` stays
    case-insensitive (both servers observed send lowercase header names,
    e.g. "cache-control", which a plain dict() would preserve verbatim and
    silently break a `.get("Cache-Control")` lookup)."""
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout, context=_INSECURE_CTX) as resp:
        return resp.status, resp.headers, resp.read()


def main():
    # --- Section 1: same-artifact serving (parity doc section 1, cutover
    # checklist step 2-4) -- sw.js/manifest.json/favicon.* must be reachable
    # through the Next dev origin and byte-identical to the backend's, since
    # services/companion/static/ is the only source of truth for either. ---
    for path, content_type_substr in [
        ("/sw.js", "javascript"),
        ("/manifest.json", "manifest+json"),
        ("/favicon.svg", "svg"),
        ("/favicon.png", "png"),
    ]:
        web_status, web_headers, web_body = fetch(f"{WEB_BASE_URL}{path}")
        backend_status, backend_headers, backend_body = fetch(f"{BACKEND_URL}{path}")
        check(f"{path}: dev origin returns 200", web_status == 200, web_status)
        check(f"{path}: byte-identical to the backend", web_body == backend_body, f"{len(web_body)} vs {len(backend_body)} bytes")
        check(
            f"{path}: content-type preserved through the rewrite",
            content_type_substr in web_headers.get("Content-Type", "").lower(),
            web_headers.get("Content-Type"),
        )

    # Cache-Control / Service-Worker-Allowed specifically (parity doc section 1
    # bullets 1-4: no-cache, never no-store; root-scope header on sw.js).
    sw_status, sw_headers, _ = fetch(f"{WEB_BASE_URL}/sw.js")
    check("/sw.js: Cache-Control: no-cache (not no-store)", sw_headers.get("Cache-Control") == "no-cache", sw_headers.get("Cache-Control"))
    check("/sw.js: Service-Worker-Allowed: /", sw_headers.get("Service-Worker-Allowed") == "/", sw_headers.get("Service-Worker-Allowed"))
    manifest_status, manifest_headers, _ = fetch(f"{WEB_BASE_URL}/manifest.json")
    check(
        "/manifest.json: Cache-Control: no-cache (not no-store)",
        manifest_headers.get("Cache-Control") == "no-cache",
        manifest_headers.get("Cache-Control"),
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        # --- Section 2: PWA meta tags + manifest link (parity doc section 1
        # + CLAUDE.md "HTML Standards"), matching render.py's head output. ---
        page = browser.new_page()
        page.goto(f"{WEB_BASE_URL}/", wait_until="networkidle")
        check(
            'meta[name="mobile-web-app-capable"] content="yes"',
            page.locator('meta[name="mobile-web-app-capable"]').get_attribute("content") == "yes",
        )
        check(
            'meta[name="apple-mobile-web-app-status-bar-style"] content="black-translucent"',
            page.locator('meta[name="apple-mobile-web-app-status-bar-style"]').get_attribute("content") == "black-translucent",
        )
        check(
            'meta[name="apple-mobile-web-app-title"] is present',
            bool(page.locator('meta[name="apple-mobile-web-app-title"]').get_attribute("content")),
        )
        check(
            'meta[name="theme-color"] content="#111"',
            page.locator('meta[name="theme-color"]').get_attribute("content") == "#111",
        )
        check(
            'link[rel="manifest"] href="/manifest.json"',
            page.locator('link[rel="manifest"]').get_attribute("href") == "/manifest.json",
        )
        page.close()

        # --- Section 3: SW registration gating (docs/invariants.md INV-3,
        # apps/web/lib/serviceWorker.ts). In dev (NODE_ENV=development), no
        # registration without the opt-in flag; setting stc_dev_sw='1' and
        # reloading registers it. Fresh context per case + explicit
        # unregister afterwards, since this mints a REAL SW registration
        # against the dev origin otherwise. ---
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(f"{WEB_BASE_URL}/", wait_until="networkidle")
        page.wait_for_timeout(500)
        regs = page.evaluate("() => navigator.serviceWorker.getRegistrations().then(r => r.length)")
        check("SW: not registered in plain dev (no stc_dev_sw flag)", regs == 0, regs)
        ctx.close()

        ctx = browser.new_context()
        page = ctx.new_page()
        page.add_init_script("try { localStorage.setItem('stc_dev_sw', '1'); } catch (e) {}")
        page.goto(f"{WEB_BASE_URL}/", wait_until="networkidle")
        page.wait_for_function("() => navigator.serviceWorker.getRegistrations().then(r => r.length > 0)", timeout=8000)
        regs = page.evaluate("() => navigator.serviceWorker.getRegistrations().then(r => r.length)")
        check("SW: registers when localStorage.stc_dev_sw === '1'", regs > 0, regs)
        scope = page.evaluate(
            "() => navigator.serviceWorker.getRegistrations().then(rs => rs[0] && new URL(rs[0].scope).pathname)"
        )
        check("SW: registered at root scope ('/')", scope == "/", scope)
        # Clean up the real registration this test created.
        page.evaluate(
            "() => navigator.serviceWorker.getRegistrations().then(rs => Promise.all(rs.map(r => r.unregister())))"
        )
        ctx.close()

        # --- Section 4: standalone class detection + resume redirect
        # (parity doc section 2, docs/invariants.md). Emulate standalone by
        # monkey-patching matchMedia before any page script runs (an
        # init script fires before Next's beforeInteractive scripts). ---
        standalone_shim = """
        (() => {
          const real = window.matchMedia.bind(window);
          window.matchMedia = (q) => {
            if (q === '(display-mode: standalone)') {
              return { matches: true, media: q, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} };
            }
            return real(q);
          };
        })();
        """
        ctx = browser.new_context()
        page = ctx.new_page()
        page.add_init_script(standalone_shim)
        page.goto(f"{WEB_BASE_URL}/", wait_until="networkidle")
        has_class = page.evaluate("() => document.documentElement.classList.contains('pwa-standalone')")
        check("pwa-standalone class added under emulated standalone mode", has_class, has_class)
        ctx.close()

        # Standalone + last_section='chat' + pathname '/' -> redirect to /chat.
        ctx = browser.new_context()
        page = ctx.new_page()
        page.add_init_script(standalone_shim)
        page.add_init_script("try { localStorage.setItem('last_section', 'chat'); } catch (e) {}")
        page.goto(f"{WEB_BASE_URL}/", wait_until="load")
        page.wait_for_timeout(300)
        check(
            "standalone + last_section=chat + '/' redirects to /chat",
            page.url.rstrip("/").endswith("/chat"),
            page.url,
        )
        ctx.close()

        # Same setup but NOT standalone (a normal browser tab) must never redirect.
        ctx = browser.new_context()
        page = ctx.new_page()
        page.add_init_script("try { localStorage.setItem('last_section', 'chat'); } catch (e) {}")
        page.goto(f"{WEB_BASE_URL}/", wait_until="load")
        page.wait_for_timeout(300)
        check(
            "non-standalone tab never redirects '/' to /chat even with last_section=chat",
            page.url.rstrip("/") == WEB_BASE_URL.rstrip("/"),
            page.url,
        )
        last_section = page.evaluate("() => { try { return localStorage.getItem('last_section'); } catch (e) { return null; } }")
        check("visiting '/' reclaims last_section='lineup'", last_section == "lineup", last_section)
        ctx.close()

        # --- Section 5: /transport also reclaims last_section='lineup'
        # (CLAUDE.md: transport is lineup-side; docs/parity/pwa-shell.md
        # wave report documents this as new behavior vs. today's
        # transport.html, which stamps nothing at all). ---
        ctx = browser.new_context()
        page = ctx.new_page()
        page.add_init_script("try { localStorage.setItem('last_section', 'chat'); } catch (e) {}")
        page.goto(f"{WEB_BASE_URL}/transport", wait_until="networkidle")
        page.wait_for_timeout(300)
        last_section = page.evaluate("() => { try { return localStorage.getItem('last_section'); } catch (e) { return null; } }")
        check("visiting /transport reclaims last_section='lineup'", last_section == "lineup", last_section)
        ctx.close()

        browser.close()

    print()
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:")
        for f in FAIL:
            print(f"  - {f}")
        sys.exit(1)


if __name__ == "__main__":
    main()
