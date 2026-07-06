## Media Upload Security Review

Reviewed `server/chat_api.py` (upload/serving endpoints), `server/chat_ws.py` (moderation pipeline, media URL validation), and `server/chat/chat.html` (client upload flow) against the OWASP File Upload Cheat Sheet checklist.

---

**[HIGH] server/chat_api.py:1136, 1233, 1330 — unbounded request body buffered before size check**

`data = await file.read()` in `upload_avatar`, `upload_image`, and `upload_video` reads the *entire* multipart body before the code checks `len(data) > 500KB / 5MB / 100MB`. Confirmed via repo search: no ASGI/Starlette middleware caps body size (`server/api.py` has no middleware registered), no Caddyfile with a `request_body`/max-size directive exists in the repo, and Caddy is managed externally on the VPS per the README. `_check_upload_rate` (chat_api.py:92-98) only throttles call *rate* (10/min per user) — it does nothing to cap the size of a single call or the number of concurrent in-flight calls.

PoC: an authenticated user opens several concurrent connections to `/chat/api/upload/video` (or any of the three endpoints), each streaming a multi-GB body. Starlette will spool each to a temp file until the read completes, before the 100MB check ever fires — disk/CPU exhaustion with a single valid session.

Remediation: reject based on `Content-Length` header before reading, and/or read in bounded chunks with an early abort once the limit is exceeded, and/or set a body-size cap at the Caddy layer.

---

**[MEDIUM] server/chat_api.py:1140-1146, 1244-1251 — `unlimited=True` decompression-bomb bypass applied to every image format, not just HEIC**

Both `upload_avatar` and `upload_image` do:
```python
try:
    img = pyvips.Image.new_from_buffer(data, "")
except pyvips.Error:
    img = pyvips.Image.new_from_buffer(data, "", unlimited=True)
```
`pyvips.Error` here is exactly libvips's own built-in guard against oversized/decompression-bomb images tripping. The code blanket-retries with `unlimited=True` for *any* format that trips it (JPEG/PNG/WebP/GIF/HEIF alike), not only HEIC as the naming/intent suggests. The only remaining safety net is the app's own `width*height > 10_000_000 / 40_000_000` check performed afterward — this does mitigate the primary risk since `img.width`/`img.height` are header-level reads (no full decode yet), but it removes libvips's per-codec tuned protection for every format, leaving a single blunt pixel-count threshold as the sole defense-in-depth layer.

Remediation: only pass `unlimited=True` when the failure is specifically from the HEIF/HEIC loader (e.g. detect via magic bytes or a narrower except), not as a catch-all retry for every `pyvips.Error`.

---

**[MEDIUM] server/chat_api.py:1224-1476 + server/chat_ws.py:770-812 — media is publicly servable before moderation runs, and orphaned uploads are never cleaned up**

`upload_image`/`upload_video` fully process and write the file to `chat/uploads/` and return its URL *before* any chat message exists. `/chat/uploads/{filename}` (chat_api.py:2212-2230) serves it to anyone with the URL from that instant on. Moderation only happens later, inside `_moderate_and_broadcast` (chat_ws.py:770), triggered only if/when the client sends a `send_message` WS event referencing the URL. If moderation rejects it, the file *is* correctly unlinked (chat_ws.py:799-811) — but if the client never sends the message at all (closes tab, network drop, abandoned flow), the file is never referenced by any message, moderation never runs on it, and it is never deleted. The startup sweep only cleans `chat/tmp/` (chat_api.py:2236-2244), not `chat/uploads/`, and there's no TTL/orphan sweep for unreferenced uploads.

PoC: `POST /chat/api/upload/video` with an unmoderated video, never call `send_message` — the processed video sits in `chat/uploads/` forever, fully public at its (unguessable but permanent) URL, having never passed word-filter/OpenAI/GPT moderation.

Remediation: track pending (unattached) uploads and expire them from disk after a short window (e.g. a few minutes) if no message references them.

---

**[MEDIUM] server/chat_api.py:1128-1196 — avatar upload has no server-side resize/crop enforcement**

The client (`submitAvatarCrop` in chat.html) always crops to 128x128 before calling `/chat/api/upload/avatar`, but the server only rejects if `img.width * img.height > 10_000_000` (~3162x3162) — it never resizes or crops. A direct API call with a valid session cookie (bypassing the browser UI) can upload and store a full-resolution photo as an "avatar," which is then re-served at full size wherever `avatar_url` is fetched, and stored in the `avatars` BLOB table far beyond the intended 128x128 footprint documented in CLAUDE.md.

Remediation: always resize/center-crop server-side to 128x128 regardless of input dimensions, rather than trusting the client crop.

---

**[LOW] server/chat_api.py:1321-1476 — video bytes are served as-is, never re-encoded (unlike images)**

Images are always re-processed through pyvips end-to-end (stripping metadata/payloads, per CLAUDE.md). Videos are only probed with `ffprobe` for duration/stream metadata, then `shutil.move`d into the public `uploads/` directory unmodified (chat_api.py:1385-1387) — the original bytes, including any data hidden in atoms/boxes that ffprobe doesn't inspect, are served verbatim and also fed directly into `ffmpeg` for frame extraction (chat_api.py:1406-1425), i.e. a full C media parser runs against fully attacker-controlled bytes. `X-Content-Type-Options: nosniff` + strict CSP on the serving route (chat_api.py:2226-2227) substantially limit browser-side exploitation of any embedded payload, which keeps this at LOW rather than higher.

Remediation (defense-in-depth, not a blocker): consider re-muxing (`ffmpeg -c copy`) or fully re-encoding video server-side to strip anything outside recognized stream data.

---

Not flagged (verified clean): path traversal in filenames (tokens are `secrets.token_hex(16)`, serving regex `^[a-f0-9]{32}\.(webp|mp4)$` — chat_api.py:2210, chat_ws.py:67), MIME confusion (actual bytes are parsed via pyvips/ffprobe, not trusted from headers), media-URL injection into messages (`_UPLOAD_URL_RE` gate in chat_ws.py:1267), rate-limiter multi-worker bypass (confirmed single uvicorn worker, no `--workers` flag, single container replica), and moderation-copy cleanup (properly unlinked in `_moderate_and_broadcast`'s `finally` block regardless of outcome).
