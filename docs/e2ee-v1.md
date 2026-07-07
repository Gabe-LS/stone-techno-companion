# E2EE for DMs — Implementation Plan

## Context

The chat system currently stores and transmits all DM content as plaintext. The server reads DM content for moderation (word filter + OpenAI omni-moderation + GPT drug detection), push notification previews, reply snippet resolution, and link preview generation. The goal is to add end-to-end encryption so that only the two DM participants can read message content, accepting that server-side moderation for DMs is replaced by user reporting.

Messages TTL at 60 minutes, there's no multi-device support (single session per user), and the app runs over HTTPS/WSS — all of which simplify the design.

Note on code references: this branch changes `chat_ws.py` frequently, so this plan cites **function names**, not line numbers. Grep for the function.

## Crypto Protocol

- **Key agreement**: ECDH over **P-256** via Web Crypto API (`{name:'ECDH', namedCurve:'P-256'}`). P-256 has been in every browser's Web Crypto for ~a decade — no feature detection, no fallback UI, no version cutoff. X25519 was considered and rejected: Chrome only enabled it by default in 133 (Feb 2025), which would make browser support the binding constraint, and since this design has no key verification (server MITM is already accepted), X25519's marginal benefits over P-256 buy nothing here
- **Symmetric encryption**: AES-256-GCM (authenticated encryption, built into Web Crypto)
- **Key derivation**: ECDH `deriveBits` → HKDF-SHA256 with room_id as salt and `"e2ee-dm-v1"` as info → AES-256-GCM key. Produces a unique AES key per DM pair. Since room_id is server-assigned, a new DM room between the same pair (e.g. after block/unblock) derives a fresh key — this gives key isolation per DM session (not forward secrecy in the cryptographic sense, which would require ephemeral keys per message)
- **No sender binding**: both participants derive the *same* symmetric key, so a ciphertext is not cryptographically bound to its sender — either party could have produced it. Sender identity is server-assigned at the WS layer, which is fine for this trust model, but it bounds what a reported "snapshot" can ever prove: even a hypothetical cryptographic verification scheme couldn't attribute a message to one participant
- **No external dependencies**: everything via `crypto.subtle` (requires secure context — already guaranteed by HTTPS/WSS)
- **No key verification**: users cannot verify each other's public keys (no safety numbers / QR codes). The server could theoretically MITM by swapping keys. Acceptable for a festival chat with 60-min TTL — key verification is a non-goal for v1
- **Private keys in localStorage**: any XSS vulnerability leaks the private key and all derivable shared keys. IndexedDB with `extractable: false` would be stronger but complicates key handling. Acceptable for v1 given 60-min message TTL and the existing CSP — but E2EE here protects at-rest content (DB rows, backups), not against a compromised client
- **No replay protection beyond TLS**: AES-GCM with random 96-bit IVs prevents ciphertext reuse, but there's no sequence number or timestamp in the authenticated data. TLS handles transport integrity and messages TTL at 60 min, so this is a non-issue in practice

## Key Lifecycle (design decision)

**The server never deletes a public key on logout or session expiry.** Deleting it would cause peers without a cached key to get a 404 and silently downgrade the conversation to plaintext mid-thread — a downgrade the server (or logout timing) could force. And deletion doesn't actually inform peers with a *cached* key, who would keep encrypting to the dead key regardless.

Instead:
- **Client on explicit logout**: `localStorage.removeItem('e2ee_keypair')` — the private key is destroyed (privacy on shared devices). Must happen in the existing logout flow (`/logout` call in chat.html) alongside session cleanup
- **Server**: public key persists. Messages encrypted to it while the user is logged out become permanently undecryptable (private half destroyed) — they render as `[Encrypted message]` after re-login and expire via the 60-min TTL. This black-hole window is the accepted trade-off; it is strictly better than a silent plaintext downgrade
- **Re-login**: client generates a fresh key pair, `PUT /chat/api/keys` overwrites the old public key, and `key_rotated` (Phase 5.1) converges all peers to the new key
- **User deletion**: `ON DELETE CASCADE` removes the key row — no explicit delete function needed

**Fail-closed rule**: once a room has sent or received at least one encrypted message in this page session, the client never sends plaintext in it. If key material becomes unavailable then, block the send with an error toast. Plaintext fallback (Phase 2.9) applies only to rooms that were never encrypted (peer has no key at all — old client, stress test bot).

## Files to Modify

| File | Changes |
|---|---|
| `server/chat_db.py` | New `e2ee_keys` table, 2 new functions, `reports.unverified` column, DM creation sets `is_moderated=False`, DM moderation-off migration (ships with Phase 2+3, not Phase 1) |
| `server/chat_api.py` | `PUT/GET /chat/api/keys` endpoints with JWK validation, `key_rotated` broadcast on re-keying |
| `server/chat_ws.py` | `_is_e2ee_content` helper, reject E2EE envelopes outside DMs, E2EE content length allowance, media URL check skip for E2EE DMs, generic DM push/badge previews (both push paths), reply snippet E2EE handling, defense-in-depth link preview skip, report accepts client-provided plaintext (E2EE messages only), `key_rotated` event handling |
| `server/chat/chat.html` | E2EE module (key gen, storage, encrypt/decrypt, promise-based caching, multi-tab healing), send/receive hooks, DM peer resolution, reply resolution on client, report flow, `key_rotated` cache invalidation, UI indicators (lock icon, banner, decryption failure styling), logout key cleanup |
| `server/chat/admin.html` | DM report warning banner (keyed on `reports.unverified`) + reporter/reported history, encrypted DM room placeholder |
| `tests/test_chat_db.py` | Tests for key CRUD + cascade |
| `tests/test_chat_api.py` | Tests for key endpoints + JWK validation |
| `tests/test_chat_ws.py` | Tests for skipped moderation, non-DM envelope rejection, generic push (both paths), report gating, `key_rotated` broadcast |

---

## Phase 1: Key Infrastructure

**Goal**: Every user gets an ECDH key pair. Public keys stored on server, fetchable by peers. No encryption yet — purely plumbing. **Truly no behavior change** — the DM moderation migration ships with Phase 2+3, not here.

### 1.1 Database — `server/chat_db.py`

New table in `init_chat_db()` (after `chat_push_subscriptions` block), using the existing `CREATE TABLE IF NOT EXISTS` pattern:

```sql
CREATE TABLE IF NOT EXISTS e2ee_keys (
    user_id    TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    public_key TEXT NOT NULL,   -- JWK JSON string
    created_at TEXT NOT NULL
);
```

New column via the existing add-column migration pattern in `_migrate_chat_db()`:

```
reports: ("unverified", "INTEGER NOT NULL DEFAULT 0")
```

Set to 1 when a report's snapshot came from the reporter instead of the server (Phase 3.5) — the admin UI keys its warning styling on this column rather than joining `rooms` (which may be gone: meetup expiry destroys rooms).

**Do NOT add the `UPDATE rooms SET is_moderated = 0 WHERE type='dm'` migration in this phase.** `_moderate_and_broadcast()` already skips moderation when `is_moderated` is false, so running that UPDATE would disable DM moderation immediately — while messages are still plaintext. It ships with Phase 2+3 (see 3.1).

Two new functions:
- `upsert_e2ee_key(db, user_id, public_key_jwk)` — INSERT OR REPLACE
- `get_e2ee_key(db, user_id) -> str | None` — single user lookup

(No batch fetch — nothing consumes it. No delete function — CASCADE covers user deletion, and per the Key Lifecycle section keys are never deleted on logout.)

### 1.2 REST Endpoints — `server/chat_api.py`

- `PUT /chat/api/keys` — authenticated, body `{"public_key": "<JWK>"}`, validates JWK, calls `upsert_e2ee_key()`, returns 204. Server must validate the JWK before storing: `kty == "EC"`, `crv == "P-256"`, `x` and `y` present and valid base64url of 32 bytes each, no `d` field (private key). Reject with 422 on invalid input — a user uploading garbage would cause `crypto.subtle.importKey` to throw for every peer that tries to derive a shared key with them. Broadcast `key_rotated` to active DM peers whenever the stored key changes — including the FIRST upload (see Phase 5.1). First uploads matter: a peer can open the DM (and latch into unencrypted fallback) while this user is still in profile setup, before any key exists; the broadcast is what unlatches them. Only a byte-identical re-upload (every login with a cached key pair) stays silent
- `GET /chat/api/keys/{user_id}` — authenticated, returns `{"user_id", "public_key", "created_at"}` or 404

### 1.3 Client Key Management — `server/chat/chat.html`

New `E2EE` object with:
- `init()` — load key pair from `localStorage('e2ee_keypair')` or generate new P-256 ECDH key pair (`['deriveBits']` usage), store as JWK pair
- `getPublicKeyJwk()` — export public key
- `uploadPublicKey()` — PUT to `/chat/api/keys`

Call `E2EE.init()` + `E2EE.uploadPublicKey()` after WebSocket `identify` succeeds (after `currentUser` is set, around the existing login flow). Wrap `init()` in try/catch — if `crypto.subtle` is unavailable or `generateKey` throws, set `E2EE.available = false` (DMs behave as pre-E2EE; with P-256 this should never happen in a secure context, so no dedicated banner UI is needed). `uploadPublicKey()` must retry on failure (3 attempts, 2s backoff) — if all retries fail, show a persistent toast "Encryption setup failed" and set `E2EE.available = false` so DMs fall back to unencrypted with the user aware.

**Multi-tab race**: two tabs initializing on first login can both generate key pairs; the last `PUT` wins, and the losing tab holds a private key that doesn't match the server-stored public key — peers' messages would fail to decrypt in that tab until reload (`key_rotated` does not heal this: it invalidates *peer* caches, not your own mismatched pair). Fix:
- `init()` re-reads `localStorage` immediately before `setItem` — if a key pair appeared since the initial check, adopt it and skip generation
- Listen for the `storage` event on `e2ee_keypair`: when another tab overwrites it, re-import the stored pair and clear all derived-key caches (`_sharedKeys`, `_peerKeys`)

Logout: in the existing logout flow, `localStorage.removeItem('e2ee_keypair')` and clear the in-memory caches. The server-side key is intentionally left in place (see Key Lifecycle).

### 1.4 Tests

- `test_chat_db.py`: upsert, get, overwrite, CASCADE on user delete
- `test_chat_api.py`: PUT/GET endpoints, 404 for missing user, auth required, JWK validation (reject wrong kty/crv, missing/short x or y, present `d`)

### 1.5 Verification

- After login, `localStorage` contains `e2ee_keypair`
- `e2ee_keys` table has row with user's public key
- GET `/chat/api/keys/{user_id}` returns the key
- Two tabs logged in simultaneously on a fresh profile end up with the same key pair (check localStorage in both)
- Logout clears `e2ee_keypair` locally; the server row remains
- Existing DMs and rooms work exactly as before; DM moderation still active

---

## Phase 2: Encrypt / Decrypt Core

**Goal**: Client encrypts DM content before sending, decrypts on receipt. Server passes ciphertext unchanged. Backward-compatible with existing plaintext messages.

### 2.1 Shared Key Derivation — `server/chat/chat.html`

Add to `E2EE` object:
- `_deriveSharedKey(peerPublicKey, roomId)` — ECDH `deriveBits` → HKDF-SHA256 (salt = roomId, info = `"e2ee-dm-v1"`) → AES-256-GCM key
- `getSharedKey(roomId, peerUserId)` — cached key lookup, fetches peer's public key via `GET /chat/api/keys/{peerUserId}` on first use. Must use a pending-promise cache: store the in-flight `Promise` in the cache map (not just the resolved value), so concurrent calls for the same peer coalesce into one fetch instead of firing duplicate requests
- Internal caches: `_sharedKeys` (room_id -> Promise\<AES key\>), `_peerKeys` (user_id -> Promise\<CryptoKey\>). Cache the Promise itself — `await` it on every call. On fetch/derivation error, delete the cache entry so the next call retries

### 2.2 Encrypt / Decrypt — `server/chat/chat.html`

- `encrypt(roomId, peerUserId, plaintext) -> base64` — AES-GCM with random 96-bit IV, output = base64(IV || ciphertext)
- `decrypt(roomId, peerUserId, base64) -> plaintext` — split IV + ciphertext, decrypt

### 2.3 Message Envelope Format

Encrypted messages use the existing `content` TEXT field:

```json
{"e2ee": true, "v": 1, "ct": "<base64(IV + ciphertext)>"}
```

The inner plaintext (before encryption) remains unchanged: `{"text":"..."}`, `{"url":"..."}`, etc. The `type` field stays the same for server-side routing.

### 2.4 Send Hook — `server/chat/chat.html`

Modify `sendChatMessage()` (make it `async`). All callers must be updated to `await` — this ripples into the keyboard handler (Enter key), send button click handler, and DM creation flow. Each caller needs `try/catch` around the `await` — async event handlers swallow errors silently otherwise:
- When `currentRoomType === 'dm'`, resolve peer user ID from `membersByRoom[currentRoom]` (filtering out `currentUser.id`)
- Encrypt `content` string -> wrap in E2EE envelope
- Optimistic render uses **plaintext** content locally; encrypted content sent over wire
- On encryption failure: show toast, do not send, do not render optimistic message
- Mark the room encrypted (`_roomEncrypted` set) on first successful encrypted send or first successful decrypt — this drives the fail-closed rule (2.9)

Peer user ID resolution: `_getDmPeerUserId(roomId)` — looks up `membersByRoom[roomId]`, falls back to `_pendingDmTarget`, falls back to DM list cache.

**First-message race**: when user A opens a new DM with user B, `find_or_create_dm` runs server-side and `room_history` arrives with `members` — but the client may try to encrypt before `membersByRoom` is populated. Guard: `sendChatMessage` must `await` the peer key before encrypting. If `membersByRoom[roomId]` is empty, wait for the `room_history` event (with a 5s timeout, then toast "Could not establish encryption").

### 2.5 Receive Hook — `server/chat/chat.html`

In `handleWSEvent()`:
- `case 'message'`: if `_isE2eeContent(data.content)`, decrypt before storing in `messagesByRoom`
- `case 'room_history'`: decrypt all E2EE messages in the batch

Helper: `_isE2eeContent(content)` — parse JSON, check `c.e2ee === true`

Decryption failure: replace content with `{"text": "[Encrypted message]"}` — graceful degradation (retry logic in Phase 5.1).

### 2.6 E2EE Content Detection + Non-DM Rejection — `server/chat_ws.py`

Add a helper `_is_e2ee_content(content: str) -> bool` that parses the JSON once and checks for the `e2ee` key. Use this helper everywhere the server needs to branch on E2EE (rejection, content length, reply snippets, push/badge preview) instead of fragile `startswith` prefix checks — if future code adds whitespace or reorders JSON keys, a prefix check breaks silently.

**Reject E2EE envelopes outside DM rooms.** In the `send_message` handler, before the length check: if `_is_e2ee_content(content)` and the room type is not `dm`, send `message_rejected` ("Encrypted messages are only supported in direct messages") and skip. Without this, anyone can send `{"e2ee":true,...}` to a public room and get the larger length allowance, an empty `text_for_moderation` (bypassing the word filter and both AI layers), and no duplicate check — a spam vector that sidesteps every text safeguard. One check closes the whole class.

Note: the server's existing `json.loads(content).get("text", "")` pattern already produces `""` for E2EE envelopes (no `text` key), so `text_for_moderation`, the duplicate-check input, and the `_moderate_and_broadcast` preview naturally resolve to empty strings. But this is *not* true everywhere — the deferred-push DB refetch and the report snapshot both fall back to raw content (fixed in 3.2 and 3.5).

### 2.7 Server Content Length — `server/chat_ws.py`

In the `send_message` handler, the content length limit is `msg_char_limit + 20` for text messages, 2000 for other types. The E2EE envelope adds ~40% overhead: base64 encoding (+33%), 12-byte IV, 16-byte GCM tag, and the `{"e2ee":true,"v":1,"ct":""}` wrapper (~30 chars). In the DM branch (after the non-DM rejection in 2.6): if `_is_e2ee_content(content)`, allow `max(max_content * 2, 4000)`. This covers the worst case (1000-char plaintext -> ~1400-char ciphertext + wrapper) with headroom. Non-DM rooms never reach this allowance.

### 2.8 Media Messages in DMs — `server/chat_ws.py` + `server/chat/chat.html`

The `send_message` handler validates image/video messages by extracting `url` from `content` and matching it against `_UPLOAD_URL_RE` — non-matching messages are **rejected** ("Invalid media URL"). An E2EE envelope has no `url` key, so without a fix every encrypted image/video DM is rejected server-side.

- **Server**: skip the `_UPLOAD_URL_RE` check when `_is_e2ee_content(content)`. This is safe to gate on the envelope alone because 2.6 already rejects E2EE envelopes outside DM rooms, so the skip can never apply to group rooms. The `allows_media` room check still works (it keys on `msg_type`, which stays plaintext)
- **Client**: the validation moves to render time — after decrypting an image/video message, validate the inner URL against the same pattern (`/chat/uploads/` + `[a-f0-9]{32}.(webp|mp4)`) before inserting it into the DOM. On mismatch, render the `[Encrypted message]` fallback. This matters: the decrypted URL is attacker-controlled by the peer, and it must never land in a `src` attribute unvalidated. (The uploads endpoint's own filename allowlist + `nosniff`/CSP headers are a second layer, not a substitute)

Note this weakens nothing the server relied on: the URL check was input hygiene, not access control — uploads are already served through the secure endpoint with its own allowlist.

### 2.9 Graceful Fallback (fail closed once encrypted)

If peer has no public key at all (never logged in on an E2EE-capable client, or a stress-test bot): `GET /keys/{user_id}` returns 404 -> send unencrypted with a toast "Encryption unavailable for this user". Track this per room (`_unencryptedRooms` set) to avoid showing the toast on every message.

Two rules that prevent this from becoming a downgrade vector:
- **Never downgrade an encrypted room**: if `_roomEncrypted` contains the room (any encrypted message sent or received this session), a key fetch failure blocks the send with an error toast instead of falling back to plaintext
- **`_unencryptedRooms` is not permanent**: remove the room from the set when a `key_rotated` event arrives for its peer (the peer just uploaded a key) — the next send retries encryption

The `_isE2eeContent` check makes all of this backward-compatible — old plaintext messages render normally.

### 2.10 Verification

- Two browsers, two users, send DM. Both see decrypted text.
- SQLite `messages.content` contains `{"e2ee":true,"v":1,"ct":"..."}` (ciphertext)
- All message types work (text, image, video, location) — image/video specifically confirms the 2.8 server-side URL-check skip and the client-side render-time URL validation
- Group rooms unchanged (no encryption); manually sending an `{"e2ee":true,...}` payload to a group room via WS is rejected with `message_rejected`
- Old plaintext DMs still render (backward compat)
- DM with a stress-test user (no key) falls back to plaintext with one toast; after that user uploads a key and `key_rotated` arrives, the next message is encrypted

---

## Phase 3: Server Adaptations

**Goal**: Disable moderation for DMs, generic push notifications, client-side reply resolution, updated report flow. **Deploy together with Phase 2** — otherwise moderation would reject ciphertext.

### 3.1 Disable Moderation for DMs — `server/chat_db.py`

In `find_or_create_dm()`, change `create_room(...)` to pass `is_moderated=False`.

Add the idempotent migration to `_migrate_chat_db()` **in this phase** (setting 0 to 0 is a no-op on re-runs):

```sql
UPDATE rooms SET is_moderated = 0 WHERE type = 'dm';
```

This ships in the same deploy as encryption, so DM moderation turns off at the same moment DM content becomes unreadable — no window where plaintext DMs are unmoderated.

No changes needed in `_moderate_and_broadcast()` — it already checks `is_moderated` and skips moderation when False.

### 3.2 Generic Push Notifications — `server/chat_ws.py` + `server/chat/chat.html`

Add a helper `_dm_preview(sender_name: str) -> tuple[str, str]` that returns `(sender_name, "Sent you a message")` — used by all push and badge logic to avoid divergence.

**Server — three code paths produce DM previews, all must be patched:**
- In `_moderate_and_broadcast()`, the `text_preview` passed to `_push_or_defer` and to the `badge_update` event is derived from the parsed `text` — already `""` for E2EE envelopes (safe but poor UX). Use `_dm_preview()` for DM rooms
- In `_do_send_push()`, the debounced-flush path (`_flush_push_later` → `_do_send_push` with empty `sender_name`) re-fetches the message from the DB and sets `text_preview = msg_row["content"] or ""` — the **raw content column**. For an E2EE message the push body would be the literal `{"e2ee":true,"v":1,"ct":"gK9x...` envelope. Apply `_dm_preview()` for DM rooms *after* this refetch fallback (or guard the fallback itself with `_is_e2ee_content`)

**Client** — two notification paths need patching in `handleWSEvent`:
- `case 'message'`: when `_isE2eeContent(data.content)`, set `preview = 'Sent you a message'` instead of attempting to parse `.text` from the envelope
- `case 'badge_update'`: `_queueBrowserNotification(data)` passes through `data.preview` from the server — after the server fix, this contains `"Sent you a message"` for DMs. The existing client fallback `data.preview || 'New message'` also handles the empty-string case, but the explicit server-side fix is cleaner

### 3.3 Reply Snippets — `server/chat_ws.py` + `server/chat/chat.html`

Server: In `_build_reply_snippet()` and `_format_message_for_history()`, if `_is_e2ee_content(content)`, set `reply_text = ""` — server can't extract text from ciphertext.

Client: After decrypting `room_history` messages, rebuild reply snippets by looking up the referenced message in the decrypted `messagesByRoom` data. Same for incoming `case 'message'` events. **The referenced message may be absent** (expired via TTL, or older than the loaded history page) — fall back to a quote reading "Encrypted message" rather than an empty or broken quote.

### 3.4 Link Previews — `server/chat_ws.py`

In `_moderate_and_broadcast()`, URL extraction runs on `text` (the parsed plaintext, not raw `content`). For E2EE envelopes, `text` is already `""` (no `text` key), so the existing `if msg_type == "text" and text:` guard short-circuits. Add an explicit `_is_e2ee_content(content)` check before the block anyway — defense-in-depth against future refactors that might change how `text` is derived.

### 3.5 Report Mechanism — `server/chat_ws.py` + `server/chat/chat.html`

Two server fixes in the `report_message` WS handler:

1. **Fix the existing snapshot fallback.** The current code builds snapshots with `json.loads(content).get("text", msg_row["content"])` — the default is the **raw content**, so an E2EE envelope would land verbatim in the snapshot. For E2EE content with no client-provided plaintext, snapshot as `"[encrypted message — no content provided]"` instead.
2. **Accept client plaintext, gated to E2EE messages only.** Accept optional `message_content` field, but use it **only when `_is_e2ee_content(msg_row["content"])`** — for plaintext messages (group rooms, legacy DMs) the server has ground truth and must ignore the client field, otherwise reporters could fabricate snapshots for messages the server can actually read. When client content is used, set `reports.unverified = 1`.

Client: Add `reportMessage(msgId)` function — shows reason picker action sheet, sends `{message_id, reason, message_content: m.content}` where `m.content` is the already-decrypted plaintext from `messagesByRoom`.

Trust model: a malicious reporter can fabricate the submitted plaintext (and sender binding doesn't exist cryptographically — see Crypto Protocol). The admin UI must make this unambiguous — render reports with `unverified = 1` with a distinct warning background and banner: "Content provided by reporter (unverified — server cannot read encrypted messages)". This should be visually distinct from regular report snapshots, not just a parenthetical note.

Enforcement guidance: admins should treat unverified DM reports as leads, not proof. For severe claims (threats, CSAM), act on the report but document that the content is unverifiable. For borderline claims, consider warning both parties or requesting a screenshot. The admin page should surface both the reporter's and the reported user's strike/report/ban history to help triangulate credibility from both sides.

### 3.6 Duplicate Check — `server/chat_ws.py`

The `send_message` handler parses `json.loads(content).get("text", "")` before calling `check_duplicate()` — for E2EE envelopes this returns `""`, and `check_duplicate()` returns `False` immediately for short text. Duplicate detection is effectively disabled for E2EE DMs — acceptable since it's a secondary anti-spam measure and DMs are unmoderated anyway. Text messages already share the general rate limiter (`check_rate_limit`), so DM spam is throttled even without duplicate detection. (Group rooms are unaffected because E2EE envelopes are rejected there — see 2.6.)

### 3.7 Verification

- Send profanity in a DM -> not blocked (moderation skipped)
- Immediate push notification shows "Sent you a message" (not content)
- **Debounced push** (send 2+ messages within the 10s/60s debounce window to an offline user) shows "Sent you a message" — not the raw envelope. This exercises the `_do_send_push` DB-refetch path
- Browser notification (tab hidden) shows "Sent you a message" (not empty or ciphertext)
- Reply to an E2EE message -> reply quote displays correctly; reply to an expired E2EE message shows the "Encrypted message" fallback quote
- DM messages have no link previews
- Report a DM message -> admin sees reporter-provided plaintext flagged unverified; report a **group room** message with a forged `message_content` field -> snapshot still shows the server's copy
- Group rooms: moderation, push previews, link previews all unchanged

---

## Phase 4: UI / UX

**Goal**: Visual indicators for E2EE, improved report UX.

### 4.1 Lock Icon in DM Header — `server/chat/chat.html`

In `openRoom()`, when `roomType === 'dm'`, prepend a lock SVG to the room title in the header.

### 4.2 E2EE Banner — `server/chat/chat.html`

When entering a DM room, prepend a centered system banner: "Messages are end-to-end encrypted. Only you and the other person can read them." If the room is in `_unencryptedRooms` (peer has no key), show instead: "Encryption unavailable for this user — messages are not end-to-end encrypted." The lock icon (4.1, 4.3) is also suppressed for such rooms — showing a lock on a plaintext conversation is worse than no indicator.

### 4.3 Lock in DM Sidebar — `server/chat/chat.html`

In `loadDMs()`, add a small lock icon next to each DM entry in the sidebar (suppressed for `_unencryptedRooms`, per 4.2).

### 4.4 Decryption Failure Styling — `server/chat/chat.html`

In `renderMessage()`, detect `[Encrypted message]` sentinel and apply distinct styling (italic, muted color, subtle background).

### 4.5 Verification

- Lock icon visible in DM header and sidebar; hidden for a DM with a keyless (stress-test) user, which shows the "not encrypted" banner instead
- Banner appears at top of DM messages
- Failed decryption renders distinctly
- No visual changes in group rooms

---

## Phase 5: Edge Cases & Hardening

### 5.1 Key Rotation on Session Loss

When `localStorage` is cleared or user logs in on new device:
- `E2EE.init()` generates new key pair, uploads new public key
- Old messages (encrypted with old key) fail to decrypt -> show `[Encrypted message]`
- With 60-min TTL, stale messages are rare

**Receiver cache invalidation** — peer's cached shared key becomes stale:
- On decryption failure, invalidate cache (`delete _sharedKeys[roomId]`, `delete _peerKeys[peerUserId]`), refetch key, retry once
- Retry guard: track retried message IDs in a `_decryptRetried` Set. On decryption failure, check: if `_decryptRetried.has(msgId)`, render `[Encrypted message]` immediately without refetching. Otherwise add to the set, invalidate cache, refetch, retry. This prevents refetch loops when the old ciphertext is genuinely unrecoverable. Clear the set when the room is closed (it's a per-session guard, not a permanent record)

**Sender cache invalidation** — without this, user A's cached copy of B's old public key causes A to encrypt messages that B (with a new key pair) can't decrypt. The message is silently lost (B sees `[Encrypted message]`, A thinks it was delivered):
- Server: when `PUT /chat/api/keys` receives a new key for user B, look up B's active DM rooms via `dm_participants`. For each room, send a `key_rotated` event to the other participant: `{"event": "key_rotated", "user_id": B, "room_id": room_id}`. Use the existing pattern: `from chat_ws import manager` (local import) + `await manager.send_to_user()` — `chat_api.py` already does this in ~10 endpoints
- Client: on `key_rotated`, invalidate `_peerKeys[userId]` and `_sharedKeys[roomId]`, and remove the room from `_unencryptedRooms` (the peer now has a key — retry encryption on next send, per 2.9). Next `encrypt()` call re-derives from the fresh key
- Offline peers miss the event, but their caches are in-memory per page load — a fresh page load fetches the current key anyway
- This is cheap (one DB query + one WS message per active DM) and eliminates the silent message loss window entirely

### 5.2 Key Cleanup on Block

When blocking a user, clear cached `_peerKeys[blockedUserId]` and associated `_sharedKeys[roomId]`. No server-side key deletion needed — block prevents message delivery.

### 5.3 Admin Panel — `server/chat/admin.html`

**Unverified report snapshots**: keyed on `reports.unverified` (not a room-type join — the room may already be deleted). Must be visually distinct from regular reports: warning-colored background (`--color-warning-bg`), banner text "Content provided by reporter (unverified)". Show both reporter's and reported user's strike/report/ban history alongside the snapshot.

**DM room browsing**: if the admin page has any "view room messages" capability (e.g. clicking a room in the Rooms tab), DM messages will be ciphertext. Show a placeholder: "Messages in this room are end-to-end encrypted and cannot be read by the server." Do not render the raw `{"e2ee":true,...}` envelope — it's confusing and leaks no useful information.

### 5.4 Media Files

Image/video files remain unencrypted on disk — only the URL in the `content` field is encrypted. Full media E2EE (encrypting the blob before upload) is out of scope for v1. The server can see DM media: it assigns upload URLs and processes files through pyvips/ffprobe, so it can correlate uploads to users regardless of URL encryption. Encrypting the URL in the message content prevents third parties with DB access from linking a media file to a specific DM conversation, but does not hide content from the server operator.

### 5.5 Existing Plaintext Messages

DMs sent before E2EE are plaintext in the DB. After Phase 2+3, they render normally (backward compat via `_isE2eeContent` check). No migration needed — they'll expire via TTL. The admin panel treats them as regular messages (no "pre-E2EE" flag needed since the content is readable).

### 5.6 Stress Test

`stress_test/run.py` exercises DMs. After E2EE, stress test DMs will send plaintext (no browser crypto available in the Python client). This still works due to backward compat (stress users never upload keys, so their DMs take the 2.9 fallback), but doesn't exercise the encryption path. Acceptable — E2EE correctness is a client-side concern tested via Playwright (Phase 2.10), not the stress test.

### 5.7 Test Coverage

- `test_chat_db.py`: e2ee_keys CRUD + CASCADE, `reports.unverified` default
- `test_chat_api.py`: key endpoints round-trip, auth, 404, JWK validation (reject wrong kty/crv, missing/short x or y, present `d` field)
- `test_chat_ws.py`: DM moderation skipped, E2EE envelope rejected in group rooms, generic push preview (including the `_do_send_push` refetch path), report with client content (used for E2EE, ignored for plaintext), E2EE content length allowance, media URL check skipped for E2EE DM messages (and still enforced for plaintext media), `_is_e2ee_content` helper, `key_rotated` broadcast on key upload (and no broadcast on same-key re-upload)

### 5.8 Verification

- Clear localStorage -> re-login -> new keys generated -> old DM messages show `[Encrypted message]` -> new messages encrypt/decrypt correctly
- User B re-keys (clear localStorage + re-login) -> user A receives `key_rotated` -> A's next message encrypts with B's new key -> B decrypts successfully (no silent message loss)
- User B logs out (server key retained) -> A can still send encrypted (messages land in the black hole, expire via TTL) -> no plaintext downgrade occurs
- Block a user -> cached keys cleared
- Admin page: DM room shows "Messages are encrypted" placeholder, unverified report shows warning banner
- Run full test suite: `python -m pytest tests/ -v`

---

## Deployment Sequencing

```
Phase 1 (Keys)     -> deploy alone, no behavior change (migration NOT included here)
Phase 2 + 3        -> deploy TOGETHER (encryption + server adaptations + DM moderation-off migration)
Phase 4 (UI)       -> deploy any time after Phase 2+3
Phase 5 (Hardening)-> deploy any time after Phase 2+3
```

Phase 2 and 3 must ship together: without Phase 3, moderation would reject ciphertext as gibberish and strike users. The `is_moderated = 0` migration ships with them — shipping it earlier (as in a previous revision of this plan) would silently disable DM moderation while DMs are still plaintext.
