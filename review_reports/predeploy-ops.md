# Findings: deploy-ops

## [SEVERITY: CRITICAL] `.gitignore` doesn't cover real user data that CLAUDE.md claims is gitignored
- Where: `.gitignore:1-38` (entire file — no entries for `server/chat.db*`, `server/chat/uploads/`, `server/chat/tmp/`, `lineup.db.bak*`)
- Evidence: CLAUDE.md states under "Generated Artifacts (gitignored)": `server/data/` — runtime databases (hearts.db, chat.db)` and `server/chat/uploads/` — uploaded images/videos`. But the actual `.gitignore` only lists `server/data/`, `lineup.db`, `.env`/`server/.env`, `*.pem`, and stress-test paths. The current git status confirms the gap: `server/chat.db`, `server/chat.db-shm`, `server/chat.db-wal`, `server/chat/uploads/` (100+ real WebP/MP4 files), and `lineup.db.bak`/`lineup.db.bak-shm` are all sitting untracked (`??`), not ignored.
- Impact: A `git add -A` / `git add .` (or any tooling that doesn't cherry-pick files) commits the live chat database and every user-uploaded photo/video straight into git history — for an app whose entire chat design is built around ephemeral, privacy-first messaging (60-min TTL, `secure_delete=ON`, E2EE DMs). Once committed, this data is permanent in history regardless of later deletion.
- Fix: Add to `.gitignore`: `server/chat.db*`, `server/data/*.db*`, `server/chat/uploads/`, `server/chat/tmp/`, `server/chat-uploads/`, `lineup.db.bak*`.

## [SEVERITY: HIGH] Missing `.dockerignore` bakes local/runtime files into the image via `COPY chat/ ./chat/`
- Where: `server/Dockerfile:7` (`COPY chat/ ./chat/`); no `.dockerignore` anywhere in the repo (confirmed via glob for `**/.dockerignore` — zero results)
- Evidence: `server/Dockerfile:7` copies the entire `server/chat/` directory verbatim. That directory currently contains `server/chat/uploads/*.webp` / `*.mp4` (real media, ~100+ files) and `.DS_Store` on this machine, none of which are excluded from the build context.
- Impact: Anything present under `server/chat/uploads/` or `server/chat/tmp/` at build time (e.g. leftover files from a bare `uvicorn` run, per the "Local Development" instructions, or historical files on the VPS predating the `./chat-uploads` bind mount) gets baked into an image layer. The `./chat-uploads:/app/chat/uploads` volume mount (`docker-compose.yml:11`) only shadows this at runtime — the baked-in content still exists in the image's layer history (exposed if the image is ever pushed/inspected/exported), and it needlessly bloats every rebuild's build context.
- Fix: Add `server/.dockerignore` excluding `chat/uploads/`, `chat/tmp/`, `data/`, `.env`, `*.pem`, `__pycache__/`, `.DS_Store`.

## [SEVERITY: HIGH] Magic-link login reports success even when the email was never sent
- Where: `server/chat_api.py:413-440`
- Evidence:
  ```python
  maileroo_key = os.environ.get("MAILEROO_API_KEY")
  if maileroo_key:
      ...
  else:
      logger.warning("MAILEROO_API_KEY not set — email not sent")
  return {"sent": True}
  ```
  The `return {"sent": True}` is unconditional — it executes in the `else` branch too, after only logging a warning.
- Impact: If `MAILEROO_API_KEY` is ever unset/blank on the running container (e.g. VPS `.env` hand-edited and container restarted without going through `deploy.sh`), every email-based sign-in attempt silently no-ops server-side while the client is told "sent" and shows a "check your email" state. Combined with Google OAuth also being optional-config, this can silently take out the *only* auth path with no user-facing or operator-facing error (only a log line). CLAUDE.md's table lists `MAILEROO_API_KEY` as `Required: Yes`, so this failure mode isn't supposed to be reachable, but the code has no fail-fast guard against it.
- Fix: When `maileroo_key` is falsy, raise `HTTPException(500, ...)` instead of returning success, so the failure surfaces to the client.

## [SEVERITY: MEDIUM] `OPENAI_API_KEY` missing silently disables 2 of 3 moderation layers, no app-level fail-fast
- Where: `server/chat_moderation.py:211-224` (`_get_api_headers`, `check_openai_moderation`), `server/chat_moderation.py:278-280` (`check_content_detection`)
- Evidence: `check_openai_moderation`: `if not os.environ.get("OPENAI_API_KEY"): logger.warning(...); return None` — a `None` return is treated as "no violation found," identical to a message that passed moderation cleanly. Same pattern in `check_content_detection`.
- Impact: If this var is unset at container runtime, every group-room message only passes through the local word-filter (layer 1); the OpenAI omni-moderation and GPT-5.4-nano drug/spam detection layers (layers 2-3) silently pass everything. There's no startup check equivalent to `_check_vapid_key_consistency` (`server/api.py:399-433`) for this variable, so the app starts and runs normally with moderation quietly degraded. This is compensated for by `deploy.sh:30,46-56` (`OPENAI_API_KEY` is in `PROD_VARS` and the deploy aborts if it's blank in the source `.env`) — but that check only fires on the `./deploy.sh` path, not on a manual `.env` edit + `docker compose restart`.
- Fix: Add a startup check (alongside `_check_vapid_key_consistency`) that logs loudly (or refuses to serve moderated rooms) if `OPENAI_API_KEY` is unset, so degraded moderation isn't invisible outside the deploy script's pre-flight check.

## [SEVERITY: MEDIUM] Failed health check leaves the site down/broken with no rollback, and prunes backups regardless
- Where: `deploy.sh:104-135`
- Evidence:
  ```bash
  echo "[4/6] Rebuilding container..."
  run ssh "$VPS" "cd $VPS_DIR/server && docker compose up -d --build --force-recreate"
  ...
  echo "  Container: $STATUS (waiting 30s...)"
  ...
  echo "  WARNING: Chat API not responding!"
  ...
  run ssh "$VPS" "cd $VPS_DIR && ls -dt server/data.bak.* 2>/dev/null | tail -n +6 | xargs rm -rf"
  ```
- Impact: `docker compose up -d` returns success as soon as the container is *started*, independent of whether it then crashes (e.g. the documented fail-fast chat-module-import crash). Step 5 only prints warnings on a bad health/API check — it never restarts the previous image, reverts `.env`, or restores `server/data.bak.$TIMESTAMP`. The script then unconditionally exits 0 with "=== Deploy complete ===" printed even after a "WARNING: Chat API not responding!" line, and the backup-pruning step runs regardless of deploy success. Since `set -euo pipefail` only aborts on non-zero *command* exit codes, a "successfully started but crash-looping" container is not treated as a failure at all.
- Fix: Make step 5 set a failure flag and (a) skip the final "Deploy complete" success message, (b) optionally auto-restore `server/data.bak.$TIMESTAMP` and `git checkout` the previous commit + rebuild, or at minimum print explicit manual-rollback instructions and exit non-zero.

## [SEVERITY: MEDIUM] Group-chat message text is logged verbatim at INFO level for every message
- Where: `server/chat_ws.py:786`
- Evidence: `logger.info("[MOD] text=%r is_moderated=%s", text[:50], is_moderated)` — executes unconditionally in `_moderate_and_broadcast` for every message (moderated or not), writing the first 50 characters of the message body to the server log.
- Impact: This runs against Docker's default logging driver (`json-file`, no size cap configured — see next finding), which is not subject to the app's own privacy controls (60-minute message TTL, `PRAGMA secure_delete=ON` on the DB). Message content that's designed to expire from the database persists indefinitely in log files instead. For non-DM group rooms this is plaintext user content; this directly undercuts the "ephemeral chat" design goal described throughout CLAUDE.md's Chat System section.
- Fix: Drop the raw text from the log line (log length/hash instead), or gate it behind a debug flag that's off in production.

## [SEVERITY: LOW] No log rotation configured for the container
- Where: `server/docker-compose.yml:1-23` (no `logging:` block); combined with extensive per-event `logger.info` calls across `server/chat_api.py` (e.g. `:1501` SWLOG, `:1241` UPLOAD, `:1511` PUSH-ACK) and `server/chat_ws.py`
- Evidence: `docker-compose.yml` has no `logging.driver`/`options.max-size` section, so Docker's default `json-file` driver (unbounded unless the daemon has global `log-opts`) applies.
- Impact: Given `logging.basicConfig(level=logging.INFO, ...)` (`server/chat_api.py:82`) and the volume of INFO-level events per message/upload/push, logs can grow unbounded over a multi-day festival and fill VPS disk with no automatic cap.
- Fix: Add `logging: {driver: json-file, options: {max-size: "10m", max-file: "5"}}` to the service in `docker-compose.yml`.

## [SEVERITY: LOW] `site_short` page-title feature is silently non-functional in production
- Where: `server/chat_api.py:104-119` (`_load_site_short`), called from `mount_chat` at `server/chat_api.py:2155`
- Evidence: `lineup_db = Path(__file__).resolve().parent.parent / "lineup.db"` resolves to `/lineup.db` inside the container (`__file__` is `/app/chat_api.py`, `.parent.parent` is `/`). Neither the Dockerfile (`server/Dockerfile:6-7`, which only copies specific `.py` files and `chat/`) nor `docker-compose.yml`'s volumes (`./data`, `./static`, `./chat-uploads`) ever put `lineup.db` at that path. The `try/except Exception: pass` swallows the resulting failure with no log line.
- Impact: `/chat/api/config`'s `site_short` is always `None` in production, so — contrary to CLAUDE.md's "Page titles: ... short name from `events.short_name` in lineup DB, loaded at server startup" — the chat page title never gets the short-name suffix in the deployed environment, and there's no signal in the logs that this lookup is failing.
- Fix: Either mount/copy `lineup.db` into the container (e.g. via a bind mount matching how the lineup pipeline deploys it), or change the `except Exception: pass` to log a warning so the gap is visible, and correct the CLAUDE.md claim if this is intentionally lineup-DB-optional.

## [SEVERITY: LOW] Dockerfile runs the app as root
- Where: `server/Dockerfile:1-10`
- Evidence: No `USER` instruction anywhere in the Dockerfile; the process runs as root inside the container by default.
- Impact: Standard container-hardening gap — increases blast radius of any RCE-class bug (e.g. in the video/image processing pipeline that shells out to `ffmpeg`/`ffprobe`).
- Fix: Add a non-root user and `USER` directive, ensuring it still has write access to the mounted `./data`, `./static`, `./chat-uploads` volumes.

## Verified clean
- Startup ordering: `chat_api.mount_chat(app)` (`server/api.py:1005-1007`) registers chat routes before the catch-all `/{path:path}` (`server/api.py:1010-1019`), and the catch-all explicitly rejects any path starting with `chat` (`server/api.py:1014-1015`).
- VAPID key handling: production path (`/app/data/vapid_private.pem`) set consistently in `deploy.sh:64` and documented in CLAUDE.md; `_check_vapid_key_consistency` (`server/api.py:399-433`) runs at startup and logs pass/fail clearly.
- DB path consistency: `hearts.db` (`server/api.py:30`) and `chat.db` (`server/chat_db.py:12-15`, default `server/data/chat.db`) both resolve under `/app/data`, matching the `./data:/app/data` volume mount — survives rebuilds.
- Static asset routes: `bios.json`, `manifest.json`, `sw.js`, `shared.css`, `shared.js`, `favicon.svg/png`, `/photos`, `/thumbs` all have explicit FastAPI routes/mounts (`server/api.py:939-1002`) ahead of the catch-all.
- Dockerfile system deps: `ffmpeg` (includes `ffprobe`), `libvips-dev`, `curl` (needed by the compose healthcheck) all installed (`server/Dockerfile:3`); `tzdata` is in `requirements.txt:4`, so `zoneinfo` lookups (`Europe/Berlin`, per-event timezone) work despite the `-slim` base image normally lacking system tz data.
- Env var completeness: every `os.environ`/`os.getenv` read across `server/*.py` matches a name documented in CLAUDE.md's Environment Variables table — no undocumented variables found.
- `chat/uploads` and `chat/tmp` creation on a fresh volume: both are `mkdir(parents=True, exist_ok=True)`'d lazily in code (`server/chat_api.py:1205`, `:1286`, `:2129-2130`), and `chat/uploads` is properly volume-mounted (`./chat-uploads:/app/chat/uploads`) so it survives `--force-recreate` rebuilds.
- SQLite WAL hygiene: `purge_loop` runs `PRAGMA wal_checkpoint(TRUNCATE)` periodically (`server/chat_ws.py:1796-1797`) to bound WAL file growth on the mounted volume; `secure_delete=ON` is set on both DB connections.
