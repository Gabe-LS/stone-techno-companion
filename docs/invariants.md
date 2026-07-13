# Migration Invariants Register

The standalone never-break register for the Stone Techno Companion platform migration. Every migration PR and every ADR references this file. It extracts cross-cutting constraints, learned the expensive way from the live system, out of `CLAUDE.md` and `docs/platform-blueprint.html` (section G) into one canonical numbered list.

Each entry has exactly four fields: Rule, Why, Verify, Constrains.

---

## Data identity

### INV-1: Slot UUIDs are permanent

- **Rule**: `slot_uuid()` in `pipeline/scraper/timetable_json.py` (also imported by `pipeline/scraper/render.py`) is the single source of truth for a set's identity, used for saved schedules, push dedup, and ICS export. It is collision-aware: an artist playing two sets on the same floor within one date+period no longer collapses to one id, and existing ids are preserved (the earliest slot keeps the historical id, only the extra one is disambiguated).
- **Why**: any schema change or Postgres migration that regenerates or reorders these ids resets every user's saved schedule and notification history silently, with no error to surface the loss.
- **Verify**: no dedicated test file is named in `CLAUDE.md` for this specific function; verify by diffing `timetable.json` slot ids across a migration dry run and confirming zero id churn for existing slots. Any new verification should be added alongside a Postgres cutover PR.
- **Constrains**: any DB migration touching `schedule`, `artist_sets`, or the `hearts.db` saved-schedule tables; the Postgres cutover (D4 in the blueprint); any rewrite of `timetable_json.py` or `render.py`.

### INV-2: Identity continuity for moderation (provider-keyed bans and strikes)

- **Rule**: bans and strikes are keyed by `provider`/`provider_id`, are FK-less (`strikes` like `bans`/`reports`), and deliberately survive user deletion. Automatic bans (strike/mute/AI) and admin bans cover ALL of a user's linked `user_providers`, not just the frozen `users.provider/provider_id`, so a second linked provider cannot evade a ban. Re-linking a fresh provider to an existing banned account is blocked via `is_user_banned(user_id)` checked across the frozen identity and every `user_providers` row.
- **Why**: without provider-id continuity, whatever identity layer fronts pretix/Medusa/Payload would let every ban silently expire at cutover.
- **Verify**: `tests/test_chat_db.py` (bans/strikes CRUD, survive-deletion), `tests/test_chat_moderation.py` (strike escalation, expiry, reset, mute cycling), `tests/test_chat_admin_roles.py` (admin ban/strike endpoints through the ASGI stack).
- **Constrains**: any identity broker ADR (D6); any auth/provider migration; any `chat.db` to Postgres migration for `users`, `user_providers`, `bans`, `strikes`.

---

## Push and PWA

### INV-3: One origin, one shared push subscription

- **Rule**: lineup and chat both register `/sw.js` at the root scope, so a browser holds exactly one push subscription per origin. The lineup record lives in `push_subscriptions` (hearts.db, keyed by `session_id`); the chat record lives in `chat_push_subscriptions` (chat.db, keyed by `user_id`), but both must point at the SAME endpoint. Both enable flows (`enableNotifications` in lineup, `_subscribePush` in chat) reuse the existing subscription instead of unsubscribing first; chat also resyncs its endpoint to the backend on every load (`_repairPushSubscription`). On disable, each surface deletes only its own server record, and only calls `unsubscribe()` on the shared browser subscription when the OTHER surface is also disabled (checked via shared-origin localStorage flags: chat `push_enabled`, lineup `stc_push`).
- **Why**: calling `unsubscribe()` before `subscribe()` in an enable flow rotates the shared endpoint and orphans the other surface's stored record, so its next push gets a 410 and silently unsubscribes that surface without the user knowing.
- **Verify**: `python tests/verify_push_both.py` (expects one endpoint, LIVE in both `push_subscriptions` and `chat_push_subscriptions` tables, run against a live Chromium subscription); `tests/notif_e2e/` stage 2 (enable/disable/repair scenarios).
- **Constrains**: the Next.js shell (must serve the same worker at root scope on the same origin); any split of lineup/chat into separate deployable services; any service worker rewrite.

### INV-4: One manifest, one app identity

- **Rule**: one shared `manifest.json` (`start_url: "/"`) covers the whole app. A separate chat manifest was tried and reverted.
- **Why**: a second manifest means a second iOS home-screen app identity, which means a second iOS storage partition, which means a separate sign-in and a separate push subscription from the user's point of view.
- **Verify**: manual check: installing the app from either `/line-up` or `/chat` must produce the same home-screen icon/identity and the same push subscription; no automated regression test is named in `CLAUDE.md`.
- **Constrains**: the Next.js shell; any restructuring of static page serving across services; PWA manifest changes.

### INV-5: VAPID keys migrate, never rotate; per-call claims dict; Chromium is the strict test target

- **Rule**: the VAPID key pair moves with the infrastructure and must never be regenerated. `pywebpush` mutates the `vapid_claims` dict it is given, stamping the FIRST endpoint's origin as `aud`; every push send must pass `dict(vapid_claims)` per call (both `chat_ws._do_send_push` and the lineup scheduler in `server/api.py` do this), never a shared dict across a subscription loop. FCM (all Chromium browsers) strictly validates both the `aud` claim and that the signing key matches the subscription's key; Apple and Mozilla accept any self-consistent JWT. A startup check (`_check_vapid_key_consistency` in `server/api.py`) logs `VAPID key pair verified` or a loud mismatch error.
- **Why**: rotating the key pair silently kills every existing subscription. A shared claims dict poisons every later push to a different push service (FCM rejects an apple `aud` with 403), so a user with both Apple and FCM subscriptions only ever received the first one. This cost a full afternoon in July 2026 because Apple and Mozilla don't enforce the binding, so iOS and Firefox kept working while Brave/Chrome silently got nothing.
- **Verify**: `tests/test_chat_ws.py::TestVapidClaimsIsolation` (claims-dict isolation); the `_check_vapid_key_consistency` startup log line; `python tests/verify_push_both.py`; any push change must additionally be manually tested against a Chromium-family browser (Brave/Chrome), since Zen/Firefox/iOS passing proves nothing.
- **Constrains**: any infrastructure migration that touches secrets/env storage; any push-sending code path in a rewritten backend; the `.env` sync step in `deploy.sh`.

### INV-6: Notification tag uniqueness across restarts

- **Rule**: every push payload carries a random `push_id` (`secrets.token_hex(8)`), and `sw.js` prefers it for the notification `tag`. `push_index` alone is not sufficient because it resets with the process and can re-collide with notifications still sitting in iOS Notification Center.
- **Why**: iOS silently drops `notificationclick` for any notification that replaced an earlier one (same tag). The tap opens the app at `start_url` with no event and no error. Room-tag reuse meant every organic message notification was a replacement (never fired), while one-off test pushes (never replaced) worked: this was the root cause of "notification click lands on line-up," diagnosed in July 2026 via the `push-diag` tool plus server-side SW timeline logging (`POST /chat/api/swlog`, `[SWLOG]`/`[PUSH-ACK]` log lines).
- **Verify**: `tests/test_notifications.py` (SW tag/version assertions); `tests/notif_e2e/` stage 3 (service-worker handler scenarios).
- **Constrains**: any service worker rewrite; any push payload schema change; the Next.js shell's notification handling if it replaces `sw.js` logic.

### INV-7: iOS notification-click navigation ordering

- **Rule**: the service worker's `notificationclick` handler must do all LOCAL work first, since iOS may kill the SW right after the app foregrounds: write the target URL to Cache Storage (`stc-push`/`_push_navigate`), then `postMessage` + `focus()` the existing client, `openWindow()` only when no window exists. Acks and logging go last, after the navigation primitives. `client.navigate()` must never be combined with `postMessage` (the two navigations race and abort each other). Pages navigate on the SW's `navigate` message and additionally poll the cache on `visibilitychange`/`focus`/`pageshow` with retries (0ms, 300ms, 1s) as a fallback, with a 3-second (non-permanent) navigation latch so an aborted navigation self-heals.
- **Why**: any network call placed before the navigation primitives can be starved by iOS killing the SW mid-handler, silently breaking notification-click navigation.
- **Verify**: `tests/notif_e2e/` stage 3 (mock SW environment dispatching synthetic `notificationclick` events); `tests/test_notifications.py`.
- **Constrains**: any service worker rewrite; any push-click handling moved into a framework-managed SW (e.g. Workbox in a Next.js migration).

---

## E2EE and privacy

### INV-8: E2EE keys live client-side; server cannot re-encrypt history

- **Rule**: each browser profile is a device with a 32-hex `device_id` and a P-256 ECDH key pair generated and kept in localStorage, never uploaded in raw form. Content is encrypted once per message with a random per-message key, wrapped separately for every device of both participants (including the sender's own other devices). The server stores only public keys (`e2ee_device_keys`, capped at 6 devices/user, pruned after 7 days inactivity) and encrypted envelopes; it cannot read DM content, so moderation is skipped, push previews are generic, reply snippets are blanked server-side and rebuilt client-side, and link previews are skipped.
- **Why**: any origin change or storage-clearing migration strips users' ability to decrypt their own DM history, since the server has no plaintext and no private keys to migrate. DM migration means preserving envelope and key continuity, not transforming data.
- **Verify**: `python tests/e2ee_browser_check.py` (21 checks across 5 browser contexts, isolated server + scratch DB via `CHAT_DB_PATH`).
- **Constrains**: any origin change (domain migration); any localStorage-clearing deploy step; the identity broker ADR (D6); any DM-related schema migration to Postgres.

### INV-9: Encrypt fails closed when a peer's keys are unwrappable

- **Rule**: keyless peers (no registered devices) fall back to plaintext with lock-icon/banner UI suppressed accordingly. But when a peer HAS registered devices and none of their keys can be wrapped (e.g. all stored JWKs are corrupt), `encrypt()` must fail CLOSED: it throws, and the send surfaces an error rather than shipping an "encrypted" envelope no recipient can decrypt.
- **Why**: silently sending an unreadable envelope would look like a successful send to the user while being permanently unrecoverable to the recipient.
- **Verify**: `python tests/e2ee_browser_check.py`.
- **Constrains**: any rewrite of the E2EE client encryption path; any device-key storage migration.

### INV-10: `media_url` column and orphan-checked unlink for encrypted DM files

- **Rule**: because the file URL is encrypted inside the E2EE envelope, the server cannot parse message content to find files to garbage-collect. The client sends the plaintext URL in a top-level `media_url` field on `messages`; TTL purge, manual delete, meetup expiry, and admin room-delete all read that column to unlink the served file (and its `_mod*.webp` copies). ALL unlink paths must go through `_unlink_media_if_orphaned` (or replicate its check inline, as the WS `delete_message` path does): a file is deleted only when no other live message still references the same `media_url`.
- **Why**: without the `media_url` column, encrypted image/video DMs orphaned their files on disk forever. Without the orphan check, a crafted `media_url` pointing at another user's file could delete it on purge/ban/expiry; this was true of the WS moderation-reject and pre-broadcast ban/mute-recheck cleanups until fixed in July 2026 (they previously unlinked unconditionally).
- **Verify**: `tests/test_chat_db.py`, `tests/test_chat_ws.py` (media cleanup/orphan-check paths).
- **Constrains**: any migration of the `messages` table schema; any storage backend change for uploads (e.g. moving off local disk to object storage).

---

## Security

### INV-11: Moderation fails closed; pending messages are never served or pushed; DMs skip content scan but not ban/mute enforcement

- **Rule**: absence of `OPENAI_API_KEY` is logged loudly at startup; without it, moderated rooms fall back to word-filter only (AI layers silently pass everything) rather than blocking all sends. Messages in moderated rooms are held `moderation_status='pending'` until AI layers clear them; pending messages are excluded from `room_history`, unread counts, and push previews. A stuck-pending sweep deletes anything left pending past ~3 minutes (moderation task died on restart). DMs are unmoderated (E2EE prevents content scanning) but `check_ban_mute` still runs on every DM send, and a banned user is rejected at WS connect before the socket is even accepted.
- **Why**: without the pending exclusion, a not-yet-moderated (or soon-to-be-rejected) message could appear in a push body or inflate a badge before it's known to be safe. Without DM ban/mute enforcement, a banned/muted user could keep sending DMs over an already-open connection since content scanning is structurally impossible on E2EE payloads.
- **Verify**: `tests/test_chat_moderation.py` (39 tests: word filter, AI mocks, strike escalation); `tests/test_chat_ws.py` (moderation flow, pending gating); the `OPENAI_API_KEY` startup log line.
- **Constrains**: any moderation pipeline rewrite; any migration that changes room-history/unread-count query paths; the identity broker (WS connect gating).

### INV-12: Transport proxies are never open proxies

- **Rule**: the Zollverein tram stop id is pinned server-side per direction (`_TRANSPORT_DIRECTIONS`), and the DUS Airport origin/destination pair is pinned per direction (`_TRANSPORT_DUES_TRIP`): neither is ever client-controlled. The walk-time proxy (`GET /api/transport/walk`) is bounded to the Essen area and proxies only to the viewed direction's server-pinned departure stop; submitted coordinates are transient and never stored or logged.
- **Why**: accepting a client-supplied stop id, trip pair, or arbitrary destination would let the endpoint be abused as a general-purpose upstream proxy to the VRR/OSRM services.
- **Verify**: `tests/test_transport.py` (20 tests: departures proxy filtering/cache/rate limits/walk bounds and targets); standalone Playwright `transport_{reverse,duesseldorf,duesseldorf_realtime,routes}_check.py`.
- **Constrains**: any migration of the transport board into a generalized "festival + route data model" (per the blueprint's Phase 1/2 plan); any new route or direction added to the proxy layer.

### INV-13: Upload security: re-processing, filename allowlist, no served moderation copies

- **Rule**: all images are re-processed through pyvips server-side regardless of client-side processing (OWASP: strip injected metadata/payloads); videos are validated in a temp file (ffprobe) before being moved to the served uploads directory. Served uploads use a strict filename allowlist (`[a-f0-9]{32}.(webp|mp4)`), `X-Content-Type-Options: nosniff`, and `Content-Security-Policy: default-src 'none'` on the serving response, with no directory listing. Moderation intermediate files (`_mod*.webp`) are never served and are deleted after use; a startup sweep cleans `chat/tmp/`.
- **Why**: this is the OWASP File Upload Cheat Sheet baseline; skipping re-processing or the allowlist would allow a crafted upload to carry an executable payload or allow path traversal/enumeration of moderation-only artifacts.
- **Verify**: `tests/stress_test/run.py` (upload paths exercised under load); `tests/test_chat_api.py` (upload endpoints, allowlist enforcement); manual header check (`curl -I` on a served upload for nosniff/CSP headers).
- **Constrains**: any migration of upload storage/serving to a different service or CDN; any rewrite of the upload endpoint in a new backend framework.

### INV-14: Render-layer URL scheme allowlist as last-stage defense

- **Rule**: every artist-facing href passes a render-layer URL scheme allowlist so anything that isn't `http(s)` (or `mailto` for social links) renders as `#`: `_safe_href` (Python) and `_safeHref` (emitted popup JS) for artist social links, plus an inline `^https?://` guard on bio "Sets" video links. Chat's link-preview cards apply the same guard client-side (`_safeHref` in `chat.html`).
- **Why**: this is defense-in-depth at the last output stage even though scraped links are already http(s)-validated upstream: it specifically blocks a `javascript:` URL injected via a hand-edited `overrides.toml`.
- **Verify**: no dedicated automated test named in `CLAUDE.md`; verify manually by attempting an `overrides.toml` entry with a `javascript:` URL and confirming the rendered href is `#`.
- **Constrains**: any rewrite of `render.py`'s HTML generation into a framework (e.g. React components in the Next.js shell): the allowlist must be reimplemented at whatever the new last output stage is, not assumed to be inherited from upstream validation.

### INV-15: Modal overlays close only on backdrop-originated press

- **Rule**: modal overlays close on backdrop click only when the press STARTED on the backdrop, tracked via `pointerdown`. Releasing a text selection or an image pan over the overlay must not close the dialog. Lineup implements this in the central `.modal-overlay` handler; chat implements it via the `_bindBackdropClose` helper (with a drag-distance gate in the image viewer, where click-on-image closes by design).
- **Why**: without pointerdown tracking, a user selecting text or panning an image whose gesture happens to end over the backdrop area would have the modal close out from under them unexpectedly.
- **Verify**: no dedicated automated test named in `CLAUDE.md` (manual interaction check); should be covered by any future e2e layer for modal components.
- **Constrains**: any rewrite of modal/dialog components in a new frontend framework.

---

## Operations

### INV-16: Deploy and backup discipline

- **Rule**: `deploy.sh` snapshots VPS SQLite DBs via `VACUUM INTO` (transactionally consistent even under live writes, no torn `.db`/`-wal` copies), downloads the snapshots to `backups/{timestamp}/` locally, and verifies each downloaded `.db` with `PRAGMA quick_check`, aborting before any change if a backup is corrupt. It performs a VAPID preflight (derives the public key from the VPS `vapid_private.pem` and aborts if the local `VAPID_PUBLIC_KEY` being synced doesn't match). `.env` sync backs up the previous file first, then writes via an atomic temp-file + byte-count check + `mv` + `chmod 600`, so a dropped connection can't leave a truncated `.env` or a world-readable secrets file. The content-deploy step normalizes staging permissions (dirs 755, files 644) before rsync.
- **Why**: a VAPID mismatch silently breaks every push. A staging directory synced with `rsync -a` once clamped `static/` to 700, locking `appuser` out of every static route (all 500s); the permissions-normalization step exists specifically because of that incident. Local backups exist so a VPS disk failure doesn't lose data; the backup step aborts before any change if the download contains zero `.db` files.
- **Verify**: `deploy.sh`'s own health check step (container + chat API) exits non-zero on failure; manually confirm the `VAPID key pair verified` log line and a non-corrupt `PRAGMA quick_check` result after any deploy touching secrets or static file permissions.
- **Constrains**: any change to `deploy.sh`; any migration of hosting/orchestration (D5 in the blueprint); any change to how `.env` or VAPID keys are stored/synced in a multi-service topology.

### INV-17: Every user-triggered frontend action logs via dbg()

- **Rule**: any user-triggered or automated frontend action must emit a timecoded `dbg()` line so behavior is diagnosable from the console. Output is off in production by default: `dbg()`/`verify()` success lines print only when `localStorage.stc_debug === '1'` (set it and reload to diagnose in the field); `verify()` failures always print regardless of the flag.
- **Why**: this is the primary field-diagnosis mechanism for a static-site + service-worker app with no server-side client telemetry; several documented incidents (notification-click routing, meetup cancellation) were diagnosed via `dbg()`/`verify()` traces plus server-side `[SWLOG]`/`[PUSH-ACK]` logging.
- **Verify**: no automated test enforces this; verify by grep for user-facing event handlers lacking a `dbg()` call, or by setting `localStorage.stc_debug = '1'` and exercising a flow to confirm console output.
- **Constrains**: all new frontend code, regardless of which framework replaces `render.py`'s inline JS or `chat.html`; any migration of frontend logging conventions.

---

## How to use

Any PR touching an area listed in a "Constrains" field must state in its description which INV numbers it was checked against. If a PR knowingly breaks an invariant (e.g. as part of a deliberate, documented migration step), it must say so explicitly and link to the ADR or plan that accounts for the consequence: silent violation is never acceptable.
