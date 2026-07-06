# Pre-Deploy Review — Confirmed Findings & Triage

All findings below survived Round 3 adversarial verification (CONFIRMED unless noted).

## CRITICAL (6)
| ID | Finding | File | Triage |
|----|---------|------|--------|
| C1 | Arbitrary file deletion via unvalidated top-level `media_url` (all messages, not just E2EE); delete_message unlinks victim's file | chat_ws.py:1361-1370,1683-1702 | FIX NOW |
| C2 | SSRF via DNS rebinding (TOCTOU) in link-preview fetch — validated IP not pinned to connection | chat_ws.py:141-269 | FIX NOW (moderate) |
| C3 | E2EE DM silently downgrades to plaintext on any transient key-fetch error (not just 404) | chat.html:2493-2523,4314-4320 | FLAG (crypto + UX policy) |
| C4a | `</script>` breakout XSS — artist name/URL json.dumps'd into inline `<script>TT_ARTISTS` | render.py:1518,94-113 | FIX NOW |
| C5 | SSRF via push subscription `endpoint` (no scheme/host allowlist) → server POSTs to internal/metadata URLs | chat_api.py:1494-1508, api.py:794-821 | FIX NOW |
| C6 | Google OAuth handlers make BLOCKING network calls in async def + no rate limit → event-loop stall DoS | chat_api.py:259-367 | FIX NOW |

## HIGH (24)
| ID | Finding | File | Triage |
|----|---------|------|--------|
| C4b | Unescaped innerHTML for artist name/photo/link in timetable popup + bio modal (DOM XSS) | render.py:2111,2457-2466 | FIX NOW |
| H1 | Session token embedded in WS URL path → logged in uvicorn access log (7-day hijack) | chat.html:1036 | FLAG (transport redesign) |
| H2 | Magic-link verify token in URL path → logged; no rate limit | chat_api.py:428,450-477 | FIX NOW (rate limit) + FLAG (logging) |
| H3 | Client never sends device_fingerprint → fingerprint ban half is dead code (ban evasion) | chat.html auth sites | FLAG (client feature + privacy) |
| H5 | E2EE media files never cleaned by TTL purge or ban/mute mass-delete (unbounded disk) | chat_db.py:886-920,1256-1287 | FIX NOW |
| H6 | Broadcast storm — typing/reactions/join/leave have no rate limiting | chat_ws.py:1444-1457,1601-1665,580-643 | FIX NOW |
| H7 | Push scheduler dedup skipped by partial-loop network exception → duplicate pushes | api.py:335-402 | FIX NOW |
| H9 | Word filter has no substring match on message content (whole-word only) | chat_moderation.py:116-132 | FIX NOW |
| H10 | Partial AI-moderation-layer failure not fail-closed (blocks only if BOTH raise) | chat_moderation.py:460-475 | FIX NOW |
| H11 | room_history serves not-yet-moderated/rejected messages to joiners; no retraction | chat_db.py:810-824 + chat_ws.py:1148 | FLAG (needs status column — shared w/ H15) |
| H12 | Auto-bans (strike/mute/AI) ban only frozen identity, not linked providers (evasion) | chat_moderation.py:354-489 | FIX NOW |
| H13 | Unbounded request body buffered before size check (disk/CPU exhaustion) | chat_api.py:1136,1233,1330 | FIX NOW |
| H14 | reports/strikes ON DELETE CASCADE destroys moderation audit trail on user delete | chat_db.py:181-199 | FLAG (schema migration) |
| H15 | Untracked moderation asyncio tasks killed on deploy → message left un-moderated & served | chat_ws.py:1423 | FLAG (needs status column — shared w/ H11) |
| H16 | No memory/CPU limits on app container | docker-compose.yml | FIX NOW |
| H17 | deploy.sh health-check failure doesn't exit non-zero or roll back | deploy.sh:109-143 | FIX NOW (exit 1) |
| H19 | Scraper aborts whole run on TBA slot (uncaught FK IntegrityError; INSERT OR IGNORE doesn't suppress FK) | scrape.py:187 | FIX NOW |
| H20 | Admin Users tab shows unverified E2EE report snapshot without the unverified banner | chat_db.py:1405-1411 + admin.html:491 | FIX NOW |
| H21 | E2EE device-key cache never refreshes on DM open → offline peer's new device silently locked out | chat.html:4335-4347 | FLAG (crypto) |
| H22 | Profile changes (name/avatar/color) frozen inside already-open WS connections | chat_ws.py:1024-1030 | FLAG (design) |
| H23 | Self-service account deletion doesn't close live WS connections (admin path does) | chat_api.py:494-516 | FIX NOW |
| H24 | report_message: no rate limit, no size cap, pending reports never purged (flooding) | chat_ws.py:1714-1770 | FIX NOW |
| H25 | Meetup creation rate limit bypassable via REST endpoint | chat_api.py:954-982 | FIX NOW |
| H26 | webpush() called with no timeout → thread-pool exhaustion via hostile endpoint | chat_ws.py:465, api.py:361 | FIX NOW |

## MEDIUM (downgraded in verification)
- H8: pushsubscriptionchange repairs only chat subscription; lineup self-heals on next visit — DOWNGRADED to MEDIUM.

## Proposed split
- **FIX NOW (17):** C1, C2, C4a, C4b, C5, C6, H2(rate-limit), H5, H6, H7, H9, H10, H12, H13, H16, H17, H19, H20, H23, H24, H25, H26
- **FLAG FOR HUMAN (9):** C3 (E2EE fail-open), H1 (WS token transport), H2/H1 logging exposure, H3 (device fingerprint), H11+H15 (moderation status column), H14 (schema migration), H21 (E2EE key cache), H22 (profile propagation), H8 (MEDIUM)
