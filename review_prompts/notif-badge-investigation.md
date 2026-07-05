# Investigation: DM notification + unread badge regressions after multi-device E2EE v2

You are a READ-ONLY investigator. You have Read, Glob, Grep only. You CANNOT run
anything (no Bash, no tests, no server). Do not claim any verification you did not
do by reading code. Your diagnosis is a hypothesis that the orchestrator will
falsify with a runtime repro before any fix — cite exact file:line evidence for
every claim so it can be checked.

## Project context

Repo root: this working directory. Festival companion app. Chat system:
- `server/chat_ws.py` — WebSocket server: message broadcast, push trigger for
  offline/idle members, `badge_counts` on connect, `badge_update` events, E2EE
  content gating (generic push previews for DMs).
- `server/chat_api.py` — REST + auth + push subscription endpoints +
  `POST /chat/api/push/idle` (sendBeacon idle signal) + E2EE key endpoints.
- `server/chat_db.py` — chat.db schema/queries incl. `room_memberships`
  (`last_read_at`), unread counts, `chat_push_subscriptions`, `e2ee_device_keys`.
- `server/chat/chat.html` — entire chat frontend, single file: WS client, DM list
  rendering, unread red dot + count badges, client E2EE (v2 envelopes
  `{e2ee, v:2, sd, ct, keys}`), push subscription registration.
- `server/static/sw.js` — service worker: push display, notificationclick
  navigation (local-first: Cache Storage `stc-push`/`_push_navigate`, postMessage +
  focus, openWindow fallback), push acks. UNCHANGED in the regression window.
- Docs: `docs/e2ee-multidevice.md` (v2 spec), `docs/e2ee-dev.md` (v1),
  `CLAUDE.md` sections "Push Notifications" and "Chat System".

Multi-device E2EE v2 landed recently (commit "Implement multi-device E2EE v2").
DM notifications were browser-verified WORKING immediately before that work, at
the commit "DM list live-refresh on new DM". The file
`review_prompts/notif-regression.diff` in this repo is the full git diff of
server/chat_ws.py, server/chat_api.py, server/chat/chat.html between that
known-good commit and HEAD (server/static/sw.js had no changes). Read it — the
regressions are almost certainly introduced or exposed by this diff, but also
read the surrounding current code for context; pre-existing latent bugs newly
exposed by E2EE payloads count too.

## Reported symptoms (user setup)

Three users/devices: user "outlook" on Zen browser (Mac), user "Gabbo" on Brave
(Mac) AND on iOS PWA (same account, two devices). "outlook" sends a DM to
"Gabbo". Message DOES arrive live in both Brave and the iOS PWA. But:

1. **No desktop notification in Brave** when the Brave tab is in the background.
   (Expected: backgrounded tab fires visibilitychange -> sendBeacon idle signal ->
   server treats user as idle -> web push -> OS notification.)
2. **iOS push notification arrives, but tapping it opens the app at the default
   Line-up page** instead of navigating to the chat room/message. This exact
   symptom class was previously fixed (see CLAUDE.md "iOS notification click" —
   unique tag per push derived from the push URL; push URL includes
   `/chat/msg/{id}` for scroll-to-message). Suspect the E2EE generic-preview
   path builds a different payload (missing/short URL, reused tag, missing msg id).
3. **Unread indicators unreliable in the PWA**: the red dot and unread-count
   badge on the DM row do not always appear. Specifically: if the PWA was fully
   closed, opening it and navigating to chat shows NO dot and NO unread badge for
   the DM that has unread messages. (Expected: server sends `badge_counts` on WS
   connect covering ALL joined rooms including DM rooms; DM list rendering should
   show them.)

Note symptom 1 caveat: with the Brave tab merely backgrounded (not closed), the
WS is still connected. Check both the idle path (is the user eligible for push?)
and whether push is correctly suppressed/sent for users with open-but-hidden
tabs, and whether the E2EE envelope broke anything in the push-eligibility or
payload-construction code path for DMs.

## Your tasks

For EACH of the three symptoms:
1. Trace the complete relevant code path in CURRENT code (server trigger ->
   payload -> SW -> client render), with file:line references.
2. Identify the defect(s): what exactly breaks, why, and why it worked before
   the E2EE v2 diff. Point to the specific lines in
   `review_prompts/notif-regression.diff` that introduced or exposed it.
3. Rate confidence (high/medium/low) and state what runtime observation would
   confirm or falsify your diagnosis (e.g. "server log line X should be absent",
   "badge_counts frame on connect will lack room R").
4. Propose a minimal fix direction (no code edits — you have no write access).

Also flag any ADDITIONAL notification/badge defects you find in the diff even if
not matching a reported symptom.

## Hard rules

- Read-only. No edits, no claimed test runs.
- Every claim needs file:line evidence from current files (diff hunks may be
  cited additionally, by hunk header).
- If two plausible causes exist for a symptom, report both ranked.
- Do not pad: no code-style commentary, no unrelated refactor suggestions.

## Required final report format (markdown)

Write your ENTIRE final response as the report:

```
# Findings

## Symptom 1: no desktop push in backgrounded Brave
- Code path: ...
- Defect: ... (file:line, diff hunk)
- Confidence: high|medium|low
- Falsification test: ...
- Fix direction: ...

## Symptom 2: iOS notification tap lands on Line-up
(same structure)

## Symptom 3: missing unread dot/badge in PWA after cold start
(same structure)

## Additional defects
- ...

## Open questions / what I could not determine statically
- ...
```
