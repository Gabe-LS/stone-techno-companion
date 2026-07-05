# Findings: uploads-moderation

## [SEVERITY: CRITICAL] Avatar images are never scanned by any moderation layer
- Where: `server/chat_api.py:1121-1163` (`upload_avatar`)
- Evidence: the entire handler is `if not file.content_type.startswith("image/") ... data = await file.read() ... pyvips.Image.new_from_buffer(...) ... db.execute("INSERT OR REPLACE INTO avatars ...")` — no call to `check_openai_moderation` or any other moderation function. Confirmed by `grep moderat` over the whole file: the only moderation call near avatars is on the *text* username/display name (`chat_api.py:621-635`), never the image bytes.
- Impact: avatars are shown platform-wide (message bubbles, member lists, DM lists, admin panel) with no TTL — unlike chat images/videos, which pass through word-filter + OpenAI omni-moderation + GPT drug-detection before broadcast (`chat_ws.py:789-844`). A user can upload sexual/violent/illegal imagery as an avatar and it is visible to every user in every room indefinitely, completely bypassing the moderation pipeline the app relies on elsewhere (the app's own threshold table treats `sexual/minors` and `violence/graphic` as instant-ban categories for message images — that same check never runs on avatars).
- Fix: run the already-built `check_openai_moderation(text="", image_url=<data-uri-of-uploaded-avatar>)` on the processed avatar bytes before persisting, and reject/strike on a flag exactly as message images do.

## [SEVERITY: HIGH] Moderation-rejected images/videos are deleted from the DB but the file stays on disk and stays publicly servable forever
- Where: `server/chat_ws.py:795-806` vs. `server/chat_ws.py:1629-1641`
- Evidence: on moderation reject, `_moderate_and_broadcast` only does `db.execute("DELETE FROM messages WHERE id = ?", (msg["id"],))` — no unlink of the served file. Compare to the user-initiated `delete_message` handler, which explicitly does `(_UPLOADS_DIR / filename).unlink(missing_ok=True)` for the same file. The `finally` block at `chat_ws.py:973-987` only removes the `_mod*.webp` moderation copies, never `{token}.webp`/`{token}.mp4` itself.
- Impact: content that failed the strictest categories (`sexual/minors`, `violence/graphic`, drugs, etc.) remains a live, publicly fetchable file at `/chat/uploads/{token}.{ext}` (`chat_api.py:2133-2151`) permanently — it's no longer referenced by any message row, so the TTL purge job (`chat_db.py:860-892`, which only walks *existing* expired message rows) never reaches it either. The file is orphaned in the worst possible state: rejected-as-unsafe, yet undeletable by any existing cleanup path.
- Fix: in the `not mod_result["allowed"]` branch, also unlink the primary served file (same logic already used in `delete_message`).

## [SEVERITY: HIGH] `unlimited=True` pyvips fallback strips libvips' safety limits on completely untrusted bytes, before the size check runs
- Where: `server/chat_api.py:1132-1141` (avatar) and `server/chat_api.py:1211-1222` (`_process_image`)
- Evidence:
  ```
  try:
      img = pyvips.Image.new_from_buffer(data, "")
  except pyvips.Error:
      img = pyvips.Image.new_from_buffer(data, "", unlimited=True)
  if img.width * img.height > 10_000_000:   # (avatar) / 40_000_000 (image)
      raise ...
  ```
- Impact: libvips raises `pyvips.Error` as its own built-in defense against oversized/anomalous images. This code specifically catches that defense and retries with `unlimited=True`, which disables libvips' internal safety limits — for every image that trips the safety net, not just benign edge cases. The pixel-count check only runs *after* the (now unprotected) decode has already happened, so a crafted small-byte/huge-pixel file (classic decompression bomb) gets fully decoded under `unlimited=True` before being rejected, defeating the purpose of the size cap.
- Fix: don't retry with `unlimited=True` on user-supplied uploads; if the safe decode fails, reject the file. If legitimate large images must be supported, pre-validate declared dimensions/format cheaply (e.g. `pyvips.Image.new_from_buffer(..., access="sequential")` header-only probe or a library like `imagesize`) before allowing any unlimited decode.

## [SEVERITY: HIGH] No image format allowlist — any format `pyvips`/libvips can load is accepted, including SVG
- Where: `server/chat_api.py:1125-1126` (avatar) and `server/chat_api.py:1197-1198` (image)
- Evidence: `if not file.content_type or not file.content_type.startswith("image/"): raise HTTPException(400, ...)` is the only gate; anything that starts with `image/` (including client-supplied `image/svg+xml`) is passed straight into `pyvips.Image.new_from_buffer(data, "")`.
- Impact: if the deployed libvips build includes the rsvg/SVG loader (common on standard Linux builds), an attacker-supplied SVG (XML, not a raster format) is a plausible vector for SSRF (external `<image xlink:href="http://internal-host/...">` fetched by librsvg during rasterization) or resource exhaustion via crafted XML. Nothing in this code path restricts to actual raster formats.
- Fix: explicitly allowlist decoded formats (`img.get("vips-loader")` after decode, e.g. reject anything but `jpegload`/`pngload`/`webpload`/`heifload`), reject SVG/XML-capable loaders outright.

## [SEVERITY: HIGH] Optimistic message save creates a window where moderation-pending media/text is readable by other users via REST and WS history
- Where: `server/chat_ws.py:1332-1341` (message written to DB), `server/chat_ws.py:1381-1399` (moderation dispatched as a separate `asyncio.create_task`, after the row already exists and after `message_acked` was sent at `1343-1355`)
- Evidence: `server/chat_api.py:780-812` (`GET /chat/api/rooms/{room_id}/messages`) and `server/chat_ws.py:1132` (WS `room_history`) both call the same `get_room_messages` (`chat_db.py:782-796`), which selects **any** non-expired row with no notion of a pending/moderation state.
- Impact: between `create_message` and the moderation verdict returning (up to the ~5s httpx timeout used for the OpenAI calls in `chat_moderation.py:207`), any other member who calls the REST history endpoint or (re)joins the room over WS will see the message — including image/video URLs — even though it may seconds later be identified as `sexual/minors` or `violence/graphic` content and deleted. This is exactly the kind of exposure the instant-ban categories are meant to prevent.
- Fix: add a `moderation_status` column (`pending`/`allowed`/`rejected`), filter `get_room_messages`/room-history queries to `allowed` (and `pending` only for the sender), and only broadcast to others (which the code already does correctly) — but also gate the REST/WS *history* reads the same way.

## [SEVERITY: MEDIUM] Uploaded media that's never attached to a sent message is never cleaned up
- Where: `server/chat_api.py:1191-1267` (`upload_image`), `server/chat_api.py:1270-1425` (`upload_video`)
- Evidence: both endpoints write the served file (and moderation copies) to `chat/uploads/` unconditionally on success and return the URL to the client; there is no DB record linking an uploaded-but-unsent file to anything. `purge_expired_messages` (`chat_db.py:860-892`) only deletes files referenced by rows in `messages` that have expired — a file with no corresponding message row is invisible to it forever.
- Impact: any authenticated user can call `/chat/api/upload/image` or `/upload/video` repeatedly (bounded only by the 10/min rate limit) and abandon every upload without sending the message. Each call permanently writes a real file (up to 5MB image / 100MB video) that is immediately, publicly downloadable at `/chat/uploads/{token}.ext` and is never garbage-collected — straightforward unbounded disk growth and permanent hosting of unmoderated content.
- Fix: track uploaded-but-unsent files (e.g., a short-lived `pending_uploads` table with a create timestamp) and sweep/delete any that aren't attached to a message within a few minutes.

## [SEVERITY: MEDIUM] AI moderation silently fails open (word filter only) if `OPENAI_API_KEY` is unset or blank, with no startup check
- Where: `server/chat_moderation.py:219-224`, `server/chat_moderation.py:278-280`, `server/chat_moderation.py:469-474`
- Evidence: `check_openai_moderation`/`check_content_detection` both `return None` (not an exception) when `OPENAI_API_KEY` is falsy. `moderate_message`'s only fail-closed path is `if ai_errored and drug_errored and os.environ.get("OPENAI_API_KEY"):` — which requires the key to be *present* to trigger. If the key is missing/empty, both AI checks return `None` (no error), the condition is `False`, and `moderate_message` falls through to `return {"allowed": True}` for every message.
- Impact: unlike the VAPID key, which has a loud startup consistency check (`_check_vapid_key_consistency` per project docs), there is no equivalent startup validation for `OPENAI_API_KEY`. A missing/typo'd env var in production silently disables both AI moderation layers for every group room — only the local word filter keeps working — with just a one-time `logger.warning` on first use, easy to miss in a busy log stream.
- Fix: fail loudly at server startup if `OPENAI_API_KEY` is unset (mirroring the VAPID key check), rather than relying on runtime per-call warnings.

## [SEVERITY: MEDIUM] Link-preview SSRF guard is vulnerable to DNS-rebinding (TOCTOU)
- Where: `server/chat_ws.py:141-167` (`_is_safe_preview_url`) and `server/chat_ws.py:212-220` (`_fetch_og_preview`)
- Evidence: `_is_safe_preview_url` resolves the hostname once via `socket.getaddrinfo` and checks the resolved IP is not private/loopback/link-local/reserved. The actual fetch, `client.get(url, ...)` at line 217-220, is a completely separate httpx call that performs its own independent DNS resolution when it opens the connection.
- Impact: an attacker who controls DNS for a domain (trivial — any domain they own) can return a public IP for the validation lookup and then a private/internal IP (e.g. `127.0.0.1`, cloud metadata `169.254.169.254`) for the resolution httpx performs moments later, bypassing the SSRF guard entirely. This is a well-known bypass pattern for exactly this kind of "validate-then-fetch" check.
- Fix: resolve the hostname once, validate the IP, then connect directly to that pinned IP (e.g. via `httpx.AsyncClient(transport=...)` with a custom resolver, or pass the IP in the URL with a `Host` header) rather than letting the HTTP client re-resolve.

## [SEVERITY: LOW] Link-preview fetch has no hard cap on response body size when `Content-Length` is absent
- Where: `server/chat_ws.py:231-237`
- Evidence: `cl = resp.headers.get("content-length"); if cl and cl.isdigit() and int(cl) > 1_000_000: return None` — this only rejects when the header is present and parseable. `body = resp.text` at line 237 unconditionally reads and buffers the entire response into memory before the `body[:100000]` truncation is applied.
- Impact: a malicious or compromised link target using chunked transfer-encoding (no `Content-Length`) can stream an arbitrarily large body, consuming server memory per preview fetch. Low severity given the 3s timeout bounds duration, but it's an easy gap to close.
- Fix: use `client.stream()` and cap bytes read (e.g., stop after 1MB) instead of trusting `Content-Length`.

## [SEVERITY: LOW] Avatar upload has no rate limiting, unlike image/video uploads
- Where: `server/chat_api.py:1121-1163`
- Evidence: `upload_image` (`1195`) and `upload_video` (`1274`) both call `_check_upload_rate(user["id"])`; `upload_avatar` does not call it anywhere in its body.
- Impact: minor — avatars are capped at 500KB and stored as a DB blob (not a disk file), so impact is bounded to repeated pyvips decode/DB write load rather than disk exhaustion, but it's an inconsistency with the rest of the upload surface and has no bound.
- Fix: call `_check_upload_rate(user["id"])` in `upload_avatar` too.

## Verified clean
- **Path traversal / filename allowlist on serving routes**: only one route serves `chat/uploads/` (`chat_api.py:2133-2151`), gated by `^[a-f0-9]{32}\.(webp|mp4)$`; no `StaticFiles` mount duplicates or bypasses it (confirmed via grep across `server/`, the only other `StaticFiles` mounts are `/photos` and `/thumbs`, unrelated to chat). Filenames are always server-generated via `secrets.token_hex(16)`, never derived from user input.
- **Video temp-file validation ordering**: `upload_video` writes to a `tempfile.mkstemp` in `chat/tmp/`, runs `ffprobe` (list-args, `timeout=10`, no `shell=True`) and only `shutil.move`s into the served `uploads/` directory after successful probe + duration check (`chat_api.py:1296-1337`). No route ever serves out of `chat/tmp/`.
- **Served-file headers**: both `/chat/api/avatar/{user_id}` and `/chat/uploads/{filename}` set `X-Content-Type-Options: nosniff` and `Content-Security-Policy: default-src 'none'`, and always serve with an explicit, narrow `media_type` (`image/webp` or `video/mp4`) — never `text/html`.
- **Avatar serving path traversal**: avatar is stored/read as a DB BLOB keyed by `user_id` via a parameterized query (`chat_api.py:1170-1172`), not a filesystem path — no traversal surface.
- **Command/argument injection**: all `ffprobe`/`ffmpeg` invocations use list-form `subprocess.run` args (no `shell=True`), operating only on server-generated temp/output paths — no user-controlled path or argument interpolation found.
- **Media forwarded to OpenAI moderation is always the reprocessed copy**: `_image_to_data_uri`/`_video_mod_frames` (`chat_ws.py:82-108`) read only the pyvips/ffmpeg-reprocessed `_mod*.webp` files, never raw uploaded bytes — consistent with stripping injected metadata/payloads before third-party transmission.
- **Moderation dispatch coverage for allowed content**: every `image`/`video` message in a moderated room is routed through `moderate_message` before broadcast to other users (`chat_ws.py:789-844`); DM exemption is intentional and documented (E2EE, server can't read content).
- **Upload rate limit mechanism**: single-process deployment confirmed (`Dockerfile` CMD has no `--workers` flag, defaults to 1), so the in-memory `_upload_rate`/`_push_debounce` dicts are not bypassable via multi-worker fan-out.
