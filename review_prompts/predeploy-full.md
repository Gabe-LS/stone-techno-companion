# Pre-Deployment Review Orchestration

You are a review orchestrator. Your job is to conduct a thorough multi-round pre-deployment review of the Stone Techno Companion project by spawning Sonnet 5 agents via `claude -p`.

## Context

This is a multi-event festival companion: scraper pipeline + static site + real-time chat with push notifications, E2EE DMs, media uploads, and cross-device sync. Read `CLAUDE.md` at the project root for full architecture documentation before starting.

## How to spawn agents

```bash
echo "<PROMPT>" | claude -p --model claude-sonnet-5
```

For multi-line prompts, use heredoc:

```bash
claude -p --model claude-sonnet-5 <<'AGENT_PROMPT'
<prompt content here>
AGENT_PROMPT
```

Each agent runs in the project directory and has access to all files. Agents return their findings as structured text. You collect, deduplicate, and synthesize.

## Git Workflow

Fixes are committed directly on the current branch. The orchestrator manages git — agents do NOT commit.

### During Rounds 1-3 (review + verification)

No commits. The codebase must stay unchanged so all agents review the same state.

### During Round 4 (fixes)

Commit after EACH successful fix (after its tests pass), not in a batch at the end. This makes each fix individually revertable with `git revert <sha>`.

Commit message format:
```
fix(<area>): <what was fixed>

Addresses: <one-line description of the finding>
Reviewed-by: Agent <N> (Round <R>)
Verified-by: Agent <N> (Round 3)
```

Example:
```
fix(ws): validate room membership before message send

Addresses: unauthenticated user can send to any room via crafted WS event
Reviewed-by: Agent 3 (Round 1)
Verified-by: Agent 16 (Round 3)
```

### After all fixes

Do NOT push. Leave commits local for human review.

## Rules

- Run agents sequentially within a round (to avoid resource contention), but move to the next round as soon as the current one completes.
- Each agent prompt MUST include: (1) what files/areas to examine, (2) what to look for, (3) output format.
- If an agent returns no findings for its area, that's fine — don't retry.
- Severity levels: CRITICAL (blocks deploy), HIGH (should fix before deploy), MEDIUM (fix soon after), LOW (tech debt, non-urgent).
- Only report concrete, actionable findings with file paths and line numbers. No vague suggestions.
- Do NOT report: style preferences, missing comments, hypothetical future requirements, things already documented as known limitations in CLAUDE.md.

---

## Round 1 — Component Reviews

Spawn one agent per area. Each agent reviews its area in isolation.

### Agent 1: Auth & Session Security

```
Review the authentication and session management system for security issues.

Files to examine:
- server/chat_api.py (auth endpoints, session creation, cookie settings)
- server/chat_db.py (users, sessions, email_tokens, bans tables)
- server/chat/chat.html (client-side auth flow, token handling)

Look for:
- Session fixation or hijacking vectors
- Cookie attribute correctness (Secure, SameSite, path scope, expiry)
- Magic link token entropy, expiry enforcement, replay protection
- Google OAuth state parameter / CSRF protection
- Ban bypass vectors (new device, new provider, cleared fingerprint)
- Rate limiting on auth endpoints (brute force magic link tokens)
- Email token cleanup (do expired tokens get purged?)
- Timing attacks on token comparison

Output format:
For each finding: [SEVERITY] file:line — description of the issue and why it matters.
If no issues found in a category, state "No issues found" for that category.
```

### Agent 2: E2EE Implementation

```
Review the end-to-end encryption implementation for correctness and security.

Files to examine:
- server/chat/chat.html (E2EE section — key generation, encrypt, decrypt, key exchange)
- server/chat_api.py (key endpoints: PUT/GET /chat/api/keys)
- server/chat_ws.py (key_rotated event, envelope handling)
- server/chat_db.py (e2ee_device_keys table)
- docs/e2ee-multidevice.md (spec to verify against)

Look for:
- Key generation weaknesses (entropy, algorithm choice)
- Key storage security (localStorage exposure, no memory clearing)
- Encryption: is the per-message key truly random? Is IV reuse possible?
- Key wrapping: does it correctly wrap for ALL recipient devices including sender's other devices?
- Fallback behavior: can a downgrade attack force plaintext?
- Key rotation: race conditions between rotation and in-flight messages
- Device cap (6): what happens at cap? Can an attacker register devices to push out legitimate ones?
- Server trust: does the server ever see plaintext content in any code path?
- Generic push previews: verify DM content never leaks into push payloads or server logs

Output format:
For each finding: [SEVERITY] file:line — description, attack scenario, suggested fix.
```

### Agent 3: WebSocket Server

```
Review the WebSocket server for correctness, security, and race conditions.

Files to examine:
- server/chat_ws.py (all WebSocket handling)
- server/chat_db.py (queries called from WS handlers)

Look for:
- Race conditions in shared state (connection sets, room memberships, typing indicators)
- Message ordering guarantees (or lack thereof)
- Auth bypass: can an unauthenticated client send messages?
- Room access control: can a user send to a room they haven't joined? Read messages from a private room?
- Idle detection correctness: edge cases where a user is wrongly idle or wrongly active
- Memory leaks: connections not cleaned up on disconnect, growing data structures
- Input validation: oversized messages, malformed JSON, unexpected event types
- Broadcast storms: can one client trigger O(n^2) broadcasts?
- Badge count correctness: race between mark_read and new message arrival
- Purge loop: does it correctly handle messages that expire mid-read?

Output format:
For each finding: [SEVERITY] file:line — description, reproduction scenario, impact.
```

### Agent 4: Push Notifications

```
Review push notification implementation for correctness and reliability.

Files to examine:
- server/api.py (push scheduler, VAPID setup, subscription management)
- server/chat_ws.py (_do_send_push, idle detection integration)
- server/static/sw.js (service worker: push event, notificationclick, pushsubscriptionchange)
- server/chat/chat.html (push subscription, repair logic, permission flow)

Look for:
- VAPID claims dict isolation (the pywebpush mutation bug documented in CLAUDE.md — verify the fix is correct)
- Tag uniqueness: verify every notification gets a truly unique tag
- iOS click handler: verify local-first pattern (Cache Storage write before any network)
- Subscription leak: can dead subscriptions accumulate if 410 pruning fails?
- Push scheduler: verify dedup correctness, timezone handling, what happens if scheduler crashes mid-loop
- Client repair logic: can it enter an infinite repair loop? Does it respect explicit disable?
- Cross-device badge: verify mark_read on device A clears badge on device B in all cases
- sendBeacon idle signal: what if the server receives it after the WS has already closed?
- Subscription table: unique constraint correctness, what happens on duplicate endpoint

Output format:
For each finding: [SEVERITY] file:line — description, affected browsers/platforms, fix suggestion.
```

### Agent 5: Content Moderation Pipeline

```
Review the moderation system for bypass vectors and correctness.

Files to examine:
- server/chat_moderation.py (word filter, OpenAI integration, GPT drug detection)
- server/chat_ws.py (moderation integration, optimistic delivery, strike application)
- server/chat_db.py (strikes table, mute logic)
- server/chat/blocklist.txt (word list)

Look for:
- Word filter bypasses: Unicode normalization gaps, zero-width characters, RTL override, homoglyphs beyond the documented substitutions
- Race condition: message broadcast before moderation completes (optimistic delivery — is removal reliable?)
- Strike expiry logic: verify 4h TTL reset-on-new-strike works correctly at boundaries
- Mute enforcement: can a muted user still send via race condition between mute broadcast and next message?
- AI moderation failure: what happens when OpenAI returns 5xx? Is the message silently approved?
- Media moderation: verify video frame extraction covers representative frames, not just start
- DM exemption: verify no code path accidentally runs moderation on DM content
- Lifetime mute counter: verify it correctly counts across strike resets
- blocklist.txt hot-reload: is there a TOCTOU issue?

Output format:
For each finding: [SEVERITY] file:line — bypass method or failure mode, impact, fix.
```

### Agent 6: Media Upload Security

```
Review media upload handling for security vulnerabilities (OWASP File Upload Cheat Sheet compliance).

Files to examine:
- server/chat_api.py (upload endpoints, file validation, serving)
- server/chat/chat.html (client-side resize, format conversion, upload flow)

Look for:
- Can a crafted image bypass pyvips re-processing? (polyglot files, oversized dimensions causing OOM)
- Video validation: is ffprobe check sufficient? Can a crafted MP4 with embedded payload pass?
- Path traversal in filename generation or serving
- MIME type confusion: Content-Type vs actual content
- Temp file cleanup: race between validation and move, crash leaving temp files accessible
- Rate limit bypass (multiple connections, slow uploads)
- Memory exhaustion: what's the max file size accepted? Is streaming used or full buffer?
- Serving security: verify X-Content-Type-Options, CSP headers, no directory traversal
- Can an upload be served before moderation completes? (time-of-check-to-time-of-use)
- pyvips unlimited=True for HEIC: does this open a decompression bomb vector?

Output format:
For each finding: [SEVERITY] file:line — attack vector, proof of concept sketch, remediation.
```

### Agent 7: Database & Data Integrity

```
Review database schema and queries for correctness and safety.

Files to examine:
- server/chat_db.py (schema, all queries)
- server/api.py (hearts.db queries)
- scraper/db.py (lineup.db schema and queries)

Look for:
- SQL injection: any string formatting in queries (should all be parameterized)
- CASCADE delete correctness: deleting a user/room — does everything clean up?
- TTL purge: verify it deletes the right messages, handles edge cases (message created exactly at boundary)
- Foreign key enforcement: verify PRAGMA foreign_keys=ON is set on every connection
- WAL mode: verify it's set on connection, not just creation (new connections default to journal)
- Transaction isolation: operations that read-then-write without a transaction (TOCTOU)
- Integer overflow: message IDs, user IDs — what's the generation strategy?
- secure_delete: verify it's set where claimed
- Concurrent access: multiple uvicorn workers hitting same SQLite file — WAL handles readers but only one writer
- Index coverage: are there slow queries that would benefit from an index under load?

Output format:
For each finding: [SEVERITY] file:line — issue, data corruption scenario, fix.
```

### Agent 8: Server Infrastructure & Deploy

```
Review server configuration, Docker setup, and deployment for production readiness.

Files to examine:
- server/docker-compose.yml
- server/Dockerfile (if exists)
- server/api.py (startup, shutdown, static routes, CORS, error handling)
- deploy.sh

Look for:
- Docker: is the image minimal? Are secrets baked in? Health check correctness?
- Volume mounts: are DB files correctly persisted? What happens if the volume is lost?
- Caddy config assumptions: does the app assume TLS termination? Mixed content risks?
- Startup ordering: what if chat.db doesn't exist? What if .env is missing a required var?
- Graceful shutdown: are WebSocket connections closed cleanly? Are in-flight pushes completed?
- deploy.sh: what happens if git pull has conflicts? If docker build fails? Is rollback possible?
- Resource limits: no memory/CPU limits in docker-compose? Can one runaway request exhaust the VPS?
- Logging: are sensitive values (tokens, keys) ever logged?
- Error responses: do 500 errors leak stack traces to clients?
- CORS: is it correctly restricted in production?

Output format:
For each finding: [SEVERITY] file:line — issue, production failure scenario, fix.
```

### Agent 9: Chat Frontend & Client-Side Security

```
Review the chat frontend for XSS, client-side logic bugs, and state management issues.

Files to examine:
- server/chat/chat.html (entire file — JS logic, DOM manipulation, event handlers)
- server/static/shared.js (escapeHtml, utilities)

Look for:
- XSS: any path where user content (message, username, room name) is inserted into DOM without escaping. Check innerHTML, insertAdjacentHTML, template literals inserted into DOM.
- DOM clobbering: named elements conflicting with global variables
- Client-side auth: is the session token exposed to XSS? (non-httpOnly cookie + inline JS)
- State desync: localStorage vs server state after reconnect, stale cache
- Event listener leaks: are listeners cleaned up when elements are removed?
- postMessage handlers: origin validation? Can a malicious iframe inject messages?
- Scroll position bugs: messages appearing in wrong order, scroll jumping
- Race conditions: rapid room switching, double-tap on send, reconnect during send
- Accessibility: keyboard navigation, screen reader compatibility, focus management

Output format:
For each finding: [SEVERITY] file:line — issue, exploitation scenario or user impact, fix.
```

### Agent 10: Lineup & Scraper Pipeline

```
Review the scraper and static site generation for correctness and robustness.

Files to examine:
- scraper/scrape.py (parsing, network requests)
- scraper/db.py (upserts, overrides application)
- scraper/render.py (HTML generation, XSS in output)
- scraper/images.py (image processing)
- scraper/timetable_json.py (timetable data generation)
- stone_techno_companion.py (orchestration)

Look for:
- XSS in generated HTML: artist names, bio content, any user-controlled data rendered unescaped
- Scraper fragility: what breaks if the source site changes structure? Error handling?
- Image processing: can a malicious image from the source site cause issues? Decompression bombs?
- overrides.toml injection: can a malformed override cause SQL injection or path traversal?
- timetable.json: timezone handling correctness, what happens with DST transitions?
- File path handling: spaces, unicode, special characters in artist names used as filenames
- Network error handling: partial downloads, timeouts, retries
- Idempotency: running the pipeline twice — does it produce the same output?

Output format:
For each finding: [SEVERITY] file:line — issue, impact, fix.
```

---

## Round 2 — Trust Boundary Review

After Round 1 completes, spawn agents that look at the *seams* between components. These agents receive Round 1 findings as context (summarize relevant findings in their prompt).

### Agent 11: Client-to-Server Boundary

```
Review all points where client-supplied data crosses into server-side processing.

Focus on the boundary between chat.html and chat_ws.py/chat_api.py:
- Every WebSocket event type: what fields does the client send? Are they all validated server-side?
- Every REST endpoint: what body/query params are accepted? Size limits? Type coercion?
- File uploads: is the Content-Type trusted? Is the filename used anywhere?
- Can a malicious client craft events that corrupt server state for other users?
- Are there any server endpoints that trust client-provided IDs without ownership verification?
  (e.g., "delete message X" — does it verify the message belongs to the requesting user?)

For each input path, verify: type checking, size limits, ownership verification, sanitization.

Output format:
Table of input paths: [endpoint/event] [fields] [validation present?] [finding if any]
Then list issues as: [SEVERITY] file:line — description.
```

### Agent 12: Server-to-External-Service Boundary

```
Review all points where the server calls external services.

External services: OpenAI API, Maileroo API, pywebpush (push services), yt-dlp (YouTube).

For each:
- What happens on timeout? On 5xx? On unexpected response shape?
- Are API keys transmitted securely? (HTTPS only, not in URLs, not logged)
- Is response data validated before use? (e.g., OpenAI returning unexpected JSON structure)
- Can a slow external service block the event loop / other requests?
- Are there retry storms possible? (retry on failure without backoff)
- Cost exposure: can a user action trigger unbounded external API calls?

Files:
- server/chat_moderation.py (OpenAI calls)
- server/chat_api.py (Maileroo email sending)
- server/api.py (pywebpush)
- server/chat_ws.py (push sending)
- fetch_videos.py (yt-dlp)

Output format:
For each finding: [SEVERITY] file:line — failure mode, blast radius, fix.
```

### Agent 13: E2EE Boundary (Server Sees vs. Server Doesn't)

```
Audit the E2EE trust boundary: verify that DM content NEVER leaks to the server in any code path.

Specifically check:
- Push notification payloads for DMs: is the content truly generic?
- Server-side logging: does any log statement include message.content for DM rooms?
- Reply snippets: when replying to an E2EE message, does the server store/transmit the quoted text?
- Reports: the admin sees reporter-provided plaintext — is it clearly marked unverified?
- Message search (if any): does it index DM content?
- Database: is the encrypted envelope stored as-is, or is it ever parsed server-side?
- WebSocket broadcast: when relaying a DM message, does the server add any content-derived fields?
- Moderation: verify the DM exemption has no conditional that could re-enable it
- Link preview generation: verify it's skipped for DMs (server would need to read the URL)
- Export/backup: if any data export exists, does it include DM plaintext?

Files:
- server/chat_ws.py (message handling, broadcast logic)
- server/chat_api.py (DM endpoints, report handling)
- server/chat_db.py (message storage, queries)
- server/api.py (any logging)

Output format:
For each code path examined: [PASS/FAIL] file:line — what was checked and result.
Then list any leaks as: [CRITICAL] file:line — how content leaks, fix.
```

### Agent 14: Cross-Device State Consistency

```
Review cross-device sync for correctness: favorites, schedule, push, badges, E2EE keys.

Scenarios to trace through the code:
1. User favorites an artist on phone → opens desktop → is it there?
2. User reads messages on phone → badge clears on desktop?
3. User disables push on phone → does desktop still get push?
4. User logs in on new device → E2EE keys registered → old device notified?
5. User changes username → is it reflected in all connected sessions immediately?
6. User is muted → are all their devices prevented from sending?
7. Push subscription dies on one device → repair logic runs → does it affect other devices?
8. User deletes account → are all sessions invalidated? All devices disconnected?

For each scenario, trace the full code path and identify where consistency can break.

Files:
- server/api.py (favorites sync, PIN-based device linking)
- server/chat_ws.py (badge_update, user state changes, multi-connection handling)
- server/chat_api.py (push subscriptions, key endpoints)
- server/chat/chat.html (client-side sync logic, reconnect behavior)

Output format:
For each scenario: [CONSISTENT/INCONSISTENT] — trace summary, race condition or gap if any.
Then list issues as: [SEVERITY] — description, affected scenario, fix.
```

---

## Round 3 — Adversarial Verification

After Round 2 completes, take all findings rated CRITICAL or HIGH from Rounds 1-2. Spawn one agent per finding (or group closely related findings into one) to attempt to *refute* it.

### Verification agent prompt template:

```
A reviewer claimed the following issue exists:

[FINDING DESCRIPTION WITH FILE AND LINE]

Your job is to determine if this is a REAL issue or a FALSE POSITIVE.

1. Read the relevant code carefully, including surrounding context
2. Check if there are guards, validations, or architectural decisions that prevent the issue
3. Check if the issue is documented as a known limitation or intentional trade-off in CLAUDE.md
4. If the issue requires a specific precondition, verify that precondition is actually reachable

Verdict: [CONFIRMED / REFUTED / DOWNGRADED to <lower severity>]
Reasoning: <explanation with code references>
```

Only findings that survive verification (CONFIRMED at CRITICAL or HIGH) proceed to Round 4.

---

## Round 4 — Triage & Fix

After verification, you (the orchestrator) have a list of confirmed findings. Now decide and act.

### Step 1: Triage (you decide, no agent needed)

Classify each confirmed finding into one of:

- **FIX NOW** — the fix is safe, scoped, and unlikely to introduce regressions. Examples: adding a missing validation, fixing a race with a lock, adding a size check, escaping an output.
- **FLAG FOR HUMAN** — the fix involves a design decision, could change behavior users rely on, touches crypto logic, or has a high regression risk. Examples: changing the E2EE device cap policy, restructuring the strike system, modifying the push tag scheme.

Criteria for FIX NOW:
- The fix is additive (adds a check, guard, or escape) rather than restructuring
- The fix is local (touches one file, or one function across 2-3 files)
- The fix has a clear "before" and "after" that can be verified with existing tests or a simple manual check
- The fix does not change any public API behavior, data format, or user-visible flow

If in doubt, classify as FLAG FOR HUMAN.

### Step 2: Apply fixes (one agent per fix or per closely related group)

For each FIX NOW item, spawn a Sonnet 5 agent with this template:

```
You are fixing a confirmed security/correctness issue in the Stone Techno Companion project.

## Issue
[DESCRIPTION — what the problem is, why it matters]

## Location
[FILE:LINE — where the problem is]

## Required fix
[SPECIFIC DESCRIPTION — what to add/change. Be precise: "add X check before line Y", "escape Z with html.escape() at line W", etc.]

## Constraints
- Do NOT refactor surrounding code. Fix only the issue described.
- Do NOT add comments explaining the fix. The code should be self-explanatory.
- Do NOT modify tests unless the fix requires a test change to pass.
- Do NOT run git commands. Do NOT commit. The orchestrator handles all git operations.
- If the fix requires adding an import, add it at the top of the file with the existing imports.
- After applying the fix, verify it by running: python -m pytest tests/ -x -q
  If tests fail, investigate whether your fix caused the failure and adjust.

## Verification
After fixing, confirm the issue is resolved:
- If there's an existing test that covers this path, show it passes.
- If not, describe in one line how to manually verify (e.g., "send a message with <script> in the name and confirm it renders escaped").
```

### Step 3: Run tests after all fixes

After all fix agents complete, run the full test suite once:

```bash
python -m pytest tests/ -v
```

If any tests fail:
- Determine which fix caused the failure
- Spawn one more agent to reconcile (fix the fix, not disable the test)
- Re-run tests until green

### Step 4: Summary

After all fixes are applied and tests pass, print a summary to stdout:

```
## Pre-Deploy Review Complete

### Fixed (N issues)
- [CRITICAL/HIGH] <short title> — <file changed>
- ...

### Flagged for human review (M issues)
- [CRITICAL/HIGH] <short title> — <file:line> — <why it needs human decision>
- ...

### Passed clean (K areas)
- <area name>
- ...

### Stats
- Agents spawned: <total across all rounds>
- Findings before verification: <count>
- Confirmed after verification: <count>
- Fixed automatically: <count>
- Flagged for human: <count>
```

Do NOT write a report file. The git diff IS the report for fixes. The stdout summary IS the report for what's left.

---

## Orchestration Checklist

1. Read CLAUDE.md to understand the project
2. Run Round 1 (10 agents, sequential)
3. Collect and deduplicate Round 1 findings
4. Run Round 2 (4 agents, sequential) — include relevant Round 1 context in prompts
5. Collect Round 2 findings
6. Merge all CRITICAL + HIGH findings from Rounds 1-2
7. Run Round 3 verification agents (one per finding or group)
8. Filter to only CONFIRMED findings
9. Triage: split into FIX NOW vs. FLAG FOR HUMAN
10. For each FIX NOW item:
    a. Spawn fix agent
    b. After agent completes, run `python -m pytest tests/ -x -q`
    c. If tests pass: `git add <changed files> && git commit` (use message format from Git Workflow section)
    d. If tests fail: spawn reconciliation agent, then retry from (b)
11. After all fixes committed: `python -m pytest tests/ -v` (full suite, final check)
12. Print summary to stdout
