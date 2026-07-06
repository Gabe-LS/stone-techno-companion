## E2EE Review Findings

I read `docs/e2ee-multidevice.md` (the v2 spec) and then verified the implementation against it in `server/chat_db.py`, `server/chat_api.py`, `server/chat_ws.py`, and the client crypto code in `server/chat/chat.html` (via an Explore sub-agent to locate line ranges, then direct reads).

Note: Bash was broken all session (sandbox `mkdir` EPERM on the session-env dir, persisted even with the sandbox override), so line-number cross-checks relied on Read/Explore instead of grep — findings below are still verified against actual file contents.

---

**[CRITICAL] server/chat/chat.html:2493-2523 (sendChatMessage) + 4335-4400 (E2EE.getDeviceList/encrypt) — fail-closed guarantee is bypassed by any transient key-fetch failure, not just a genuine "peer has no key" 404**

`_fetchDeviceList` (line 4314) returns `null` only on a real 404. Any other failure — network timeout, 500, a dropped connection on bad festival wifi/cellular — is thrown as an `Error` (line 4320) and propagates out of `encrypt()` uncaught. The catch block in `sendChatMessage` (lines 2512-2522) treats a thrown exception *identically* to the legitimate "peer has no key" case: it checks `_roomEncrypted.has(roomId)`, and if false, silently falls through and sends the message as **plaintext** (`outgoingContent` was never reassigned from `content`), only showing a dismissible toast ("Encryption unavailable for this user").

`_roomEncrypted` (declared chat.html:405) is an in-memory `Set`, reset on every page load/logout, and is populated *only* by successfully decrypting a message this session (chat.html:1166/1170/1206/1210) or by a prior successful `encrypt()` call this session (line 2508) — never from the server-authoritative `other_has_key` flag already returned by `GET /chat/api/dms` (that flag only feeds `_unencryptedRooms`, chat.html:1867-1868). So for any DM opened fresh this session with no un-expired message history to decrypt (very common: DM TTL defaults to 24h per `chat_settings.dm_ttl_minutes`, and many festival conversations naturally have >24h gaps), a single transient failure of `GET /chat/api/keys/{peer}` causes a real, previously-encrypted conversation to send plaintext to the server with no blocking, no retry, and no strong warning — even though the peer genuinely has valid registered keys the whole time.

Attack scenario: a network-adjacent attacker (or a flaky mobile network, which is the default festival environment) delays/drops the one `GET /chat/api/keys/{peer}` request per send attempt. Every affected send downgrades silently to plaintext, defeating the "end-to-end encrypted" guarantee documented for DMs.

Suggested fix: distinguish "confirmed no key" (404) from "could not determine" (network/HTTP error) in `sendChatMessage`'s catch — on the latter, always fail-closed (block the send with an error, same as the `_roomEncrypted.has(roomId)` branch) rather than falling through to plaintext. Also seed `_roomEncrypted` from `other_has_key: true` in `loadDMs()`/`_loadMenuSection('dms')` (chat.html:1867, 2694) so the fail-closed check reflects server-known state, not just this session's decrypt history.

---

**[MEDIUM] server/chat/chat.html:4179-4223 (E2EE.init) — E2EE private key is stored as an extractable JWK in localStorage**

`crypto.subtle.generateKey(..., true, ['deriveBits'])` (line 4194-4196, `extractable: true`) and the resulting private key is exported to JWK and written to `localStorage.setItem('e2ee_keypair', ...)` (line 4219) in plaintext. Any XSS on the chat page (or a malicious browser extension with page access) can read `localStorage.e2ee_keypair` directly and exfiltrate the raw private key — not just for the current session, but retroactively (any past message the attacker also captured) and permanently going forward, since the same key pair persists indefinitely across reloads.

This is a materially higher-value target than typical XSS because it defeats the entire "no private key ever leaves a device" goal stated in the spec's Goals section.

Suggested fix: generate the key pair with `extractable: false` and persist the `CryptoKey` objects directly via IndexedDB (which supports structured-clone storage of non-extractable `CryptoKey`s) instead of exporting to JWK/localStorage. This preserves cross-reload persistence while making the raw key material unreadable to injected JS — `crypto.subtle.deriveBits`/`deriveKey` still work on a non-extractable key.

---

**[LOW] server/chat/chat.html:4382-4397 (E2EE.encrypt/wrapAll) — a message can be sent with an empty `keys` map if every device wrap fails**

`wrapAll` catches and swallows failures per-device (line 4390-4393, "a corrupt stored JWK must not block the whole send" — matches spec intent for isolated corrupt keys). But there's no check afterward that `keys` is non-empty before returning the envelope (line 4399). If every device wrap fails (e.g., all cached public keys are momentarily unusable, or a code regression in `_deriveKek`), the function still returns a well-formed v2 envelope with `keys: {}`. That message is then sent and stored, but is **undecryptable by anyone, including the sender's own reload** (no self-slot either) — with no error surfaced to the user beyond what already succeeded (message shows as sent).

Suggested fix: after `wrapAll` for both peer and own devices, if `Object.keys(keys).length === 0`, treat it the same as an encryption failure (throw, so the caller's existing fail-closed/toast logic applies) rather than returning a silently-broken envelope.

---

Everything else checked out against the spec and did not produce a reportable finding: key generation (P-256 ECDH + `crypto.getRandomValues`, correct entropy/algorithm), per-message key randomness and IV handling (fresh random `mk` and IV per message, fresh IV per device wrap — no reuse), key wrapping fan-out to all peer + own devices including self-injection before upload lands, device cap/pruning logic in `chat_db.py:1667-1688` (correctly bounded to the user's own account, no cross-user eviction path), server-side never touching DM plaintext (moderation, link previews, reply snippets, and push previews all gate on `_is_e2ee_content`/`is_moderated=False` correctly), and the `key_rotated` broadcast/cache-invalidation logic (both the DM-peer and self-notification variants match spec).
