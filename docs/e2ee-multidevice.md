# Multi-Device E2EE for DMs (v2) — Implementation Spec

## Context

The v1 E2EE design (docs/e2ee-dev.md, fully implemented and verified) assumes one key pair per user: the server stores a single public key, and the client derives one static ECDH shared key per DM pair. In reality the app allows concurrent sessions on multiple devices (Mac browser + iOS PWA), and v1 degenerates to last-device-wins: the most recent device to upload a key is the only one that can read the conversation; every other device of the same user renders `[Encrypted message]`, and messages SENT from a stale device are unreadable by the counterpart.

v2 makes every device a first-class crypto endpoint, WhatsApp-style, minus the parts an ephemeral 60-minute-TTL chat does not need.

## Goals / Non-Goals

Goals:
- Every device of both DM participants can decrypt every message sent AFTER that device registered — including the sender's own other devices.
- No private key ever leaves a device. No key escrow, no key sync.
- v1's trust model, fallback rules (fail-closed, keyless-peer plaintext fallback), moderation/report/push adaptations, and UI indicators carry over unchanged.

Non-goals (explicit):
- History for newly added devices. A device registered at time T shows `[Encrypted message]` for anything sent before T. With the 60-minute TTL this window self-cleans. No device-linking ceremony, no phone-signs-companion trust chain, no encrypted history transfer.
- Forward secrecy / ratcheting (unchanged from v1).
- Key verification between users (unchanged from v1: server MITM is accepted).
- Group room encryption (DMs only, unchanged).

## Protocol v2

### Device identity

Each browser profile IS a device. On first E2EE init the client generates:
- `e2ee_device_id` — 32 hex chars from `crypto.getRandomValues` (stored in localStorage next to `e2ee_keypair`).
- The P-256 ECDH key pair, exactly as v1.

Tabs in the same browser share localStorage and therefore ARE the same device (the existing v1 multi-tab race handling and `storage`-event adoption carry over verbatim, now also covering `e2ee_device_id`). Logout clears both `e2ee_device_id` and `e2ee_keypair`; a re-login creates a NEW device. Server-side device rows are never deleted on logout (v1 Key Lifecycle rule unchanged) — stale devices are pruned by inactivity (below).

### Message-key wrapping (the core change)

v1 encrypted the content once with a static pair key. v2 encrypts the content once with a random per-message key, then wraps that key once per target device:

1. Sender generates a random 256-bit message key `mk` (`crypto.getRandomValues`).
2. Content is encrypted once: `ct = AES-256-GCM(mk, iv1, plaintext)`.
3. Target devices = ALL device keys of BOTH participants (peer's devices + sender's own devices, including the sending device itself — uniform decrypt path after reload).
4. For each target device D: `kek_D = HKDF-SHA256(ECDH(sender_device_private, D.public), salt=room_id, info="e2ee-dm-v2")`; `wrapped_D = AES-256-GCM(kek_D, iv_D, mk)`.
5. Envelope (in the existing `content` TEXT field):

```json
{
  "e2ee": true,
  "v": 2,
  "sd": "<sender_device_id>",
  "ct": "<b64(iv1 || ciphertext)>",
  "keys": { "<device_id>": "<b64(iv_D || wrapped_mk)>", ... }
}
```

`sd` is required: the recipient derives the unwrap KEK from their own private key + the SENDER DEVICE's public key, so they must know which of the sender's devices produced the message.

Decrypt on device X: parse envelope; if `keys[X]` is absent, render the `[Encrypted message]` sentinel (device predates... postdates the message — the accepted no-history rule). Otherwise: resolve the sender's device list (msg.user_id + `sd`), derive `kek`, unwrap `mk`, decrypt `ct`.

Size math (why wrapping instead of N full ciphertexts): each keys entry is b64(12+32+16) = 80 chars + ~45 chars JSON overhead ≈ 125 chars/device. At the device cap (12 total across both users) that is ~1.5 KB of key material regardless of message length, vs N full copies of the content.

### v1 backward compatibility

v1 envelopes (`"v": 1`, single `ct`, no `keys`) still exist inside the TTL window at deploy time. The client RETAINS the v1 decrypt code path (static pair key from own private + peer's key) but it must operate ONLY on an in-memory cached peer key from before the deploy: after the schema migration below, `GET /keys/{user}` returns the v2 `devices` array (no `public_key` field), so the v1 path must NEVER fetch — attempting to would parse `undefined` and throw. In practice the path is live only for a tab that was open across the deploy with the peer key already cached; after any reload it is dead code, every v1 envelope renders the `[Encrypted message]` sentinel (no retry), and the path may be deleted in a post-TTL cleanup. With the 60-minute TTL this affects at most one hour of history and is accepted. Do not build a compatibility key-mapping layer.

## Server

### Schema — `server/chat_db.py`

Replace the single-key table with a per-device table (new table + migration, using the existing patterns):

```sql
CREATE TABLE IF NOT EXISTS e2ee_device_keys (
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device_id  TEXT NOT NULL,
    public_key TEXT NOT NULL,   -- JWK JSON string, same validation as v1
    created_at TEXT NOT NULL,
    last_seen  TEXT NOT NULL,
    PRIMARY KEY (user_id, device_id)
);
```

Migration in `_migrate_chat_db()`: create the table if missing, then `DROP TABLE IF EXISTS e2ee_keys` (v1 rows cannot be mapped to a device; clients re-upload under a device_id on next load; the at-most-one-hour of old envelopes degrade per the compat rule above). Idempotent.

Functions (replace the v1 pair):
- `upsert_e2ee_device_key(db, user_id, device_id, public_key_jwk)` — INSERT OR REPLACE, sets/refreshes `last_seen`. Returns whether the (device_id -> public_key) mapping CHANGED (new device, or same device with a different key) so the caller knows whether to broadcast.
- `get_e2ee_device_keys(db, user_id) -> list[dict]` — all devices for a user, ordered by created_at.
- `prune_stale_devices(db, user_id, max_devices=6, max_age_days=7)` — called inside upsert: delete rows with `last_seen` older than max_age_days, then if still over max_devices delete oldest-`last_seen` rows beyond the cap. Keeps fanout bounded.

### API — `server/chat_api.py`

- `PUT /chat/api/keys` — body `{"device_id": "<32 hex>", "public_key": "<JWK>"}`. Validate device_id (`^[a-f0-9]{32}$`, 422 otherwise) and the JWK exactly as v1. Upsert + prune. If the mapping changed (per upsert return): broadcast `key_rotated` to (a) the other participant of each of the user's DM rooms (v1 behavior, including first upload), AND (b) the user's OWN other connections with `room_id: null` — sibling devices must invalidate their cached device list for this user so their next send fans out to the new device. Same-key re-upload (every page load) refreshes `last_seen` and stays silent.
- `GET /chat/api/keys/{user_id}` — returns `{"user_id": ..., "devices": [{"device_id", "public_key", "created_at"}, ...]}`; 404 ONLY when the user has zero devices (preserves the v1 keyless-peer fallback semantics).
- `/dms` `other_has_key` — true when the peer has at least one device key (adjust the JOIN to the new table; EXISTS subquery or LEFT JOIN + GROUP BY, keep it simple).

### WebSocket — `server/chat_ws.py`

- `_is_e2ee_content` unchanged (checks `"e2ee": true` — matches v1 and v2).
- Content length allowance: the v2 envelope adds ~125 chars per device slot on top of the v1 overhead, and — unlike v1 — the overhead applies to EVERY message type, not just text (a 12-device image envelope is ~1,600 chars, dangerously close to the current 2,000-char non-text limit). Apply the E2EE allowance regardless of msg_type: for `is_e2ee_msg`, text allows `max(msg_char_limit * 2, 4000) + 2000` and non-text allows `2000 + 2000`. Add a code comment stating the coupling explicitly: the device cap (12 total) x ~125 chars/slot must stay well under the +2000 headroom — raising the cap requires raising the allowance. Group-room rejection, media-URL skip, push previews, reply snippets, report gating: all unchanged (they key on `_is_e2ee_content`).
- `key_rotated` event shape gains the `room_id: null` self-notification variant (see API above). No other WS changes.

## Client — `server/chat/chat.html`

### Identity & init

- `E2EE.init()`: additionally load-or-generate `e2ee_device_id` (localStorage, 32 hex). Include it in the storage-event adoption and the pre-setItem race re-check (a losing tab must adopt BOTH the winner's key pair and device_id atomically — store them under the two existing keys but write device_id first, keypair second, and adopt on the keypair storage event which reads both). The storage-event handler must clear the v2 caches (`_deviceLists`, `_keks`) in addition to any v1 leftovers — after adoption the tab's device identity changed, so every cached list and derived KEK is suspect.
- `uploadPublicKey()`: sends `{device_id, public_key}`. Retry/failure behavior unchanged.
- Logout: remove `e2ee_device_id` too; clear all new caches.

### Caches (replace v1's `_peerKeys`/`_sharedKeys`)

- `_deviceLists`: user_id -> Promise<[{device_id, publicKey: CryptoKey}]> (fetch `GET /keys/{user}` once, import all JWKs). Pending-promise semantics, delete on rejection — same discipline as v1.
- `_keks`: `${room_id}:${user_id}:${device_id}` -> Promise<CryptoKey> (derived KEK per counterpart device). Cheap to derive; cache is an optimization only.
- On `key_rotated {user_id, room_id}`: delete `_deviceLists[user_id]` and clear `_keks` ENTIRELY (`_keks = {}`). Do not attempt a per-user scan of the composite `${room}:${user}:${device}` keys — it is error-prone (the `room_id: null` self-notification has no room to key on) and KEK re-derivation is cheap; the cache is an optimization only. If `room_id` is non-null also `_unencryptedRooms.delete(room_id)` and the existing open-room UI refresh. The `user_id` may be the CURRENT user (sibling device change) — handle identically; no special casing needed if `_deviceLists` keys by user_id including self.

### Encrypt

`E2EE.encrypt(roomId, peerUserId, plaintext)`:
1. Resolve `_deviceLists[peerUserId]` and `_deviceLists[currentUser.id]` (own list INCLUDES this device — if the fetch is missing this device, e.g. upload hasn't landed yet, add the local key locally so self-readability never races the upload).
2. Random 32-byte `mk`; `ct = AES-GCM(mk, iv, plaintext)`.
3. For each device across both lists: derive KEK (own private + device public, HKDF salt=roomId info="e2ee-dm-v2"), wrap `mk`.
4. Emit the v2 envelope. Failure of ANY single device wrap: log via dbg and SKIP that device (a corrupt stored JWK must not block the whole send); failure of the content encryption itself: abort send with toast (v1 rule).

INTERFACE CHANGE — update the caller: v1 `encrypt()` returns a bare base64 string and `sendChatMessage()` wraps it (`outgoingContent = JSON.stringify({e2ee: true, v: 1, ct: ciphertext})`). v2 `encrypt()` returns the COMPLETE envelope JSON string; the caller must use the return value as `outgoingContent` directly and the v1 wrapper line must be removed — leaving it produces a double-wrapped envelope that fails decryption everywhere.

Keyless peer (GET 404): unchanged v1 fallback rules (`_unencryptedRooms`, fail-closed `_roomEncrypted`).

### Decrypt

`_decryptMessageContent` handles three shapes:
- v2 (`keys` present): as per Protocol. Missing own device slot -> sentinel WITHOUT the retry loop (a missing slot is deterministic, not a stale-cache condition; retrying would refetch pointlessly — mark `_decryptFailed` and also a distinct `_noSlot` flag for the styling/debug, render the same sentinel).
- v2 unwrap/decrypt error: existing retry guard (`_decryptRetried`), invalidating `_deviceLists[sender_user_id]` + related `_keks` before the single retry (sender may have re-keyed the same device_id).
- v1 (`ct` without `keys`): legacy static-pair decrypt, best effort; failure -> sentinel, no retry.

### UI

No new UI. Lock icons/banners key on `other_has_key`/`_unencryptedRooms` exactly as v1. (A later, separate feature may add a device-count indicator; out of scope.)

## Edge cases

- **Own upload races first send**: encrypt() must not depend on the server already knowing this device (step 1's local-key injection covers it). The message is still readable by the peer (their slots are present) and by this device (local slot); the sender's OTHER devices only gain readability once the upload lands — acceptable, sub-second.
- **Peer adds a device mid-conversation**: peer's `key_rotated` (room_id variant) invalidates `_deviceLists[peer]`; next send fans out to the new device. Messages sent before that are unreadable on the new device by design.
- **Device cap eviction**: an evicted (stale) device silently stops receiving new slots; if the user returns to it, its next `uploadPublicKey` re-registers it (mapping changed -> broadcast) and conversation resumes forward. Old messages: sentinel, per the no-history rule.
- **Clock-free pruning**: `last_seen` compares ISO timestamps already used throughout chat_db; no new time infrastructure.
- **Envelope tampering**: `keys` maps are attacker-visible metadata (device counts). Accepted: v1 already leaks message timing/size; device count adds little. GCM auth protects `ct` and each wrap independently.
- **Report flow**: unchanged — reporter submits decrypted plaintext; `unverified=1` (v1 semantics; nothing in v2 changes what the server can verify).

## Tests

- `tests/test_chat_db.py`: device-key CRUD, upsert returns changed/unchanged correctly, CASCADE, prune (age + cap, eviction order), migration drops `e2ee_keys`.
- `tests/test_chat_api.py`: PUT with device_id (validation: bad hex, wrong length, missing), GET returns device array / 404 on zero devices, `key_rotated` broadcast on new device + on re-key + self-notification with room_id null + silence on same-key re-upload, `/dms` other_has_key with the new table.
- `tests/test_chat_ws.py`: v2 envelope accepted in DM within the new length allowance; still rejected in group rooms; `_is_e2ee_content` on a v2 envelope.
- Browser verification (`tests/e2ee_browser_check.py`) — this is the acceptance gate, extended or accompanied by a dedicated script with THREE contexts: Alice (1 device) and Bob (2 devices = 2 separate browser contexts logged into the same account):
  1. Both Bob devices upload distinct device keys (DB shows 2 rows for Bob).
  2. Alice -> Bob: BOTH Bob devices decrypt and display the plaintext.
  3. Bob(device 1) -> Alice: Alice decrypts AND Bob(device 2) decrypts (sibling readability — the original bug).
  4. Bob(device 2) -> Alice: same, mirrored.
  5. DB: v2 envelopes with `keys` slots for all 3 devices; no plaintext leakage (nonce scan).
  6. New-device-no-history: a THIRD Bob context created after messages exist shows the sentinel for old messages and decrypts new ones.
  7. Existing v1 checks still pass where applicable (the 13-check script must be updated for the new key API shapes it queries — e.g. `e2ee_keys` table references become `e2ee_device_keys`).
  8. Sender-device-pruned: decrypting a message whose `sd` is no longer in the sender's device list renders the sentinel, not an error (mirror of the missing-own-slot case).
  9. Envelope size: a v2 image/video envelope at the full 12-device cap fits within the non-text allowance (assert the actual length, documenting the breakeven).
  10. Self-decrypt after reload: a device sends a message, the page reloads (in-memory keys gone, localStorage pair intact), and the device decrypts its OWN message from history — this exercises the self-slot `ECDH(own_private, own_public)` deriveBits call end to end in a real browser.

## Deploy

Single deploy unit: schema migration + API + client ship together (the client's key upload shape and the GET response shape change simultaneously). Old clients (open tabs from before deploy) degrade in two distinct ways until reloaded:
- Their key upload 422s (missing device_id) -> `E2EE.available = false` -> unencrypted sends with the existing toast, for rooms not yet marked encrypted.
- For PREVIOUSLY-ENCRYPTED DMs the fail-closed rule blocks sends entirely: the old client's peer-key fetch parses the new `devices`-array response, throws, and `_roomEncrypted` forbids the plaintext fallback — the user sees the encryption-error toast and cannot send in that room until reload.
Both self-heal on reload. Acceptable for the dev-stage rollout; the deploy message must say "Reload the page if DM encryption shows an error."
