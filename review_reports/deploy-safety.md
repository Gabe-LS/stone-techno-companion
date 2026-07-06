# Deploy Safety Audit

## Verdict

**With caveats.** The one hard invariant the audit asked me to confirm — that the content-only deploy path (`stone_techno_companion.py --render-only --deploy`) can never touch `server/data` (hearts.db, chat.db, VAPID keys) — holds: it rsyncs into `/root/services/stone-techno/server/static/`, a directory disjoint from `server/data`, and no code path writes into `server/data` from that command. The full server deploy (`deploy.sh`) also never runs a destructive operation against the live `server/data` bind mount — backups happen before `git pull`/rebuild, `--force-recreate` only recreates the container (bind mounts are host paths, untouched), and no HTTP route can serve DB files. However, I could not complete the VPS-side verification this audit requires: the deploy scripts target `root@209.38.244.136`, but the SSH access granted to me was for `root@104.248.136.81`, a completely unrelated host (no Docker, no `/root/services`). Every fact below about the live VPS is therefore **unverified** and based on static repo analysis only. Independent of that gap, I found a real, already-latent bug in the content-deploy path (`rsync --delete` deletes git-tracked static assets it doesn't re-stage) and a backup-consistency gap (no WAL checkpoint before copying live SQLite files) that should be fixed before trusting this pipeline further.

## Findings

### CRITICAL

**1. Audit could not verify the actual production VPS — authorized SSH target does not match the deploy target.**
- Where: `deploy.sh:10` and `stone_techno_companion.py:44` both hardcode `VPS="root@209.38.244.136"`; this has been the value since the script's first commit (`103cb27`) and hasn't changed since. The SSH access provided for this audit was `root@104.248.136.81`.
- Why it matters: I confirmed `104.248.136.81` (hostname `densitymedia`) has no `docker` binary and no `/root/services/` directory — it is not running this project at all. My attempt to connect to the actual deploy target, `209.38.244.136`, was blocked by the permission system as an address I wasn't authorized for, and I did not attempt to route around that. This means **none** of the requested VPS-side checks (real bind-mount path, backup directory contents, `-wal`/`-shm` presence, file permissions, `git` state, Caddy config) could be performed. If `209.38.244.136` is itself stale (e.g., the box was migrated and the script never updated), every `./deploy.sh` run would either fail immediately at the first `ssh` command (safe, just broken) or — worse — succeed against the wrong/decommissioned host while production silently runs undeployed code.
- Fix: Confirm the correct current production IP with the user/infra owner, verify `deploy.sh`/`stone_techno_companion.py` reference it, and re-run VPS-side verification against the correct, explicitly authorized host before relying on this report's local-only conclusions.

### HIGH

**2. Content-deploy's `rsync --delete` removes git-tracked static assets it doesn't re-stage, which can break the site and jam the next code deploy.**
- Where: `stone_techno_companion.py:52-88` (`deploy_to_vps`). The staging directory only receives `index.html`, `favicon.svg`/`favicon.png`, `timetable.json`, `bios.json`, `manifest.json`, `sw.js`, `photos/`, `thumbs/` (lines 58-78), then `rsync -avz --delete "{staging}/" "{VPS_HOST}:{VPS_STATIC_DIR}"` (lines 81-86) mirrors that staging dir onto `/root/services/stone-techno/server/static/` **exactly**, deleting anything present on the VPS that isn't in staging.
- Why it matters: `git ls-files server/static/` shows `shared.css`, `shared.js`, `menu_test.html`, and `next/index.html` are tracked in the repo and live in that same directory on the VPS (it's a bind mount of the git working tree per `server/docker-compose.yml`: `./static:/app/static`) — none of these are copied into the staging dir. `server/api.py:1077-1089` serves `/shared.css` and `/shared.js` from that directory and every page (lineup, timetable, chat) depends on them. Running `python stone_techno_companion.py --render-only --deploy` therefore deletes `shared.css`/`shared.js` from production on every single content deploy, breaking page styling/JS immediately. Recovery requires a subsequent `./deploy.sh`, whose `git pull` (`deploy.sh:102`) will itself **fail** if it can't fast-forward over the now-locally-deleted tracked files without a commit/stash — turning a styling outage into a stuck deploy pipeline requiring manual `git checkout -- server/static` on the VPS.
- This does **not** endanger `server/data` (different directory, never targeted) — flagging it because it's the exact class of `--delete`/scope risk the audit asked me to look for, just manifesting as an availability bug rather than a data-loss one.
- Fix: either add `shared.css`, `shared.js`, `manifest.json`-adjacent static assets to the staging dir before syncing, or drop `--delete` and instead sync only the specific generated filenames (`rsync` without `--delete`, explicit file list), so the content deploy can only ever add/update, never remove.

### MEDIUM

**3. Live SQLite databases are copied without a WAL checkpoint or online-backup API — backups may be inconsistent.**
- Where: `deploy.sh:85-87` (`rsync -az ... "$VPS:$VPS_DIR/server/data/" "$LOCAL_BACKUPS/$TIMESTAMP/"`) and `deploy.sh:96` (`ssh "$VPS" "cd $VPS_DIR && cp -r server/data server/data.bak.$TIMESTAMP"`).
- Why it matters: `hearts.db` and `chat.db` run in WAL mode per the project's own architecture notes, meaning committed data can live in a `-wal` sidecar file that hasn't yet been checkpointed into the main `.db` file. Both `rsync` and `cp -r` copy each file independently while the container is live and still writing — there is no guarantee the main file and its `-wal`/`-shm` siblings are captured at a mutually consistent point. A restore from either backup during an incident could reflect a torn/inconsistent state (missing recent commits, or a `-wal` that references pages not present in the copied main file).
- Fix: before both copy steps, run `sqlite3 <db> "PRAGMA wal_checkpoint(TRUNCATE);"` on each database (or use `sqlite3 <db> ".backup <path>"`, which is transactionally consistent by design), then copy the checkpointed files.

**4. `.env` sync to the VPS is a non-atomic overwrite over SSH.**
- Where: `deploy.sh:77` — `printf "%b" "$PROD_ENV" | ssh "$VPS" "cat > $VPS_DIR/server/.env"`.
- Why it matters: this streams directly into the destination file with no temp-file-then-rename step. A dropped SSH connection mid-transfer leaves a truncated `.env`. Per the project's own docs this doesn't crash the container, it **silently degrades security-relevant behavior**: a missing `OPENAI_API_KEY` disables two of the three chat-moderation layers (word filter still runs, AI layers silently pass everything), and the deploy's own health check only verifies `/chat/api/config` responds — it would not detect this. Not a database-integrity issue, but a real, easily-missed moderation bypass introduced by the deploy mechanism itself.
- Fix: write to a temp path on the VPS (`cat > $VPS_DIR/server/.env.tmp`) then `mv` atomically into place; optionally validate line count/expected keys before the `mv`.

**5. `CHAT_ADMIN_EMAILS` is treated as a required var, contradicting project docs.**
- Where: `deploy.sh:30` includes `CHAT_ADMIN_EMAILS` in `PROD_VARS`, and lines 46-56 abort the entire deploy if any `PROD_VARS` entry is empty. Project docs list `CHAT_ADMIN_EMAILS` as optional ("No" required).
- Why it matters: purely an operational false-positive (a legitimately-empty optional var blocks every deploy step, including the backup steps that would otherwise protect data) — not a data-safety issue itself, but worth fixing since it will eventually cause someone to work around the check in a rush.
- Fix: exclude optional vars from the missing-value gate, or check presence-in-file rather than non-empty-value.

### LOW

- None beyond the CHAT_ADMIN_EMAILS note above.

### INFO — checked, no issue found

- Backup retention pruning (`deploy.sh:142`: `ls -dt server/data.bak.* | tail -n +6 | xargs rm -rf`) correctly keeps the 5 most recent backups; the glob can't match `server/data` itself (requires the `.bak.` infix), and an empty `ls` result is a safe no-op for the trailing `xargs rm -rf` (no arguments, nothing deleted).
- Backup/downloads are strictly one-directional (VPS → local, `deploy.sh:85-87`); nothing in `deploy.sh` or `stone_techno_companion.py` ever rsyncs `backups/` back up to the VPS.
- `.gitignore` correctly excludes `server/data/`, `server/chat.db*`, `backups/`, `.env`, `server/.env`, `*.pem` (`.gitignore:18-24, 33-34, 40-45`) — none of this can be committed or reintroduced via `git pull` on the VPS.
- `server/docker-compose.yml` mounts `server/data` as a host bind mount (`./data:/app/data`), never baked into the image; `--build --force-recreate` recreates only the container, not host-path bind mounts, so it cannot wipe `server/data`.
- `server/.dockerignore` excludes `chat/uploads/`, `chat/tmp/`, `data/`, `*.env`, `.env`, `*.pem` from the build context, so `COPY chat/ ./chat/` in `server/Dockerfile:6-7` cannot bake stale/sensitive runtime data into the image, and the runtime bind mounts override any image-layer content at container start regardless.
- No HTTP route in `server/api.py` or `server/chat_api.py` can serve anything from `/app/data`. The only static-serving surfaces are `/photos` and `/thumbs` (`StaticFiles` mounts, `server/api.py:1028-1030`), single explicit routes for `favicon.*`, `manifest.json`, `sw.js`, `bios.json`, `shared.css`, `shared.js` (all reading only from `STATIC_DIR = server/static`, `server/api.py:31`), and the chat uploads endpoint restricted to a `^[a-f0-9]{32}\.(webp|mp4)$` filename allowlist (`server/chat_api.py:2285-2301`). None resolve into `server/data`.
- Local `backups/` directory does not currently exist on this machine — no evidence one way or the other on permissions, since nothing has been downloaded yet.

## Verified facts

- **Local repo, `deploy.sh`**: full text read (146 lines). Confirmed step order: env sync (0) → download backup to local (1) → VPS-side `cp -r` backup (2) → `git pull` (3) → `docker compose up -d --build --force-recreate` (4) → health check with abort-on-failure (5) → prune old VPS backups. `set -euo pipefail` at the top means any failing command in the local script aborts the deploy before proceeding to later steps — confirmed no ordering bug where a destructive step runs before its backup.
- **Local repo, `stone_techno_companion.py:44-89`**: confirmed the `--deploy` rsync target (`VPS_STATIC_DIR = "/root/services/stone-techno/server/static/"`) is a directory distinct from `server/data`; confirmed exactly which files are staged (evidence: lines 58-78) vs. what's tracked in `server/static/` in git (evidence: `git ls-files server/static/` output — includes `shared.css`, `shared.js`, `menu_test.html`, `next/index.html` which are absent from staging).
- **Local repo, `server/docker-compose.yml`**: confirmed bind mounts `./data:/app/data`, `./static:/app/static`, `./chat-uploads:/app/chat/uploads` — all host paths, not named/anonymous Docker volumes.
- **Local repo, `server/Dockerfile`**: confirmed `COPY api.py chat_api.py chat_ws.py chat_db.py chat_moderation.py ./` and `COPY chat/ ./chat/` — no `COPY` of `data/`.
- **Local repo, `server/.dockerignore`**: confirmed contents exclude `chat/uploads/`, `chat/tmp/`, `data/`, `*.env`, `.env`, `*.pem`.
- **Local repo, `.gitignore`**: confirmed `server/data/`, `server/chat.db`, `server/chat.db-shm`, `server/chat.db-wal`, `backups/`, `.env`, `server/.env`, `*.pem` are all present.
- **Local repo, `server/api.py`**: confirmed `STATIC_DIR = Path(__file__).resolve().parent / "static"` (line 31) — never points at `data/`; enumerated every route/mount (lines 1022-1106) and confirmed none serve from `data/`.
- **Local repo, `server/chat_api.py`**: confirmed the uploads endpoint (line 2287) validates filenames against `^[a-f0-9]{32}\.(webp|mp4)$` (line 2285) before serving, and sets `X-Content-Type-Options: nosniff`.
- **VPS**: SSH to `root@104.248.136.81` succeeded (`hostname` → `densitymedia`, `whoami` → `root`, uptime 27 days); `docker ps` → `bash: line 1: docker: command not found`; `ls /root/services/` → `No such file or directory`. This is not the Stone Techno host. An attempted SSH to the actual configured deploy target, `root@209.38.244.136`, was declined by the permission system as an unauthorized address, so no facts about the real production VPS were collected.

## Recommendations

1. Resolve the IP mismatch first (Finding 1) and re-run the VPS-side half of this audit against the correct, explicitly authorized host — nothing below substitutes for that.
2. Fix the content-deploy `--delete` scope (Finding 2) before the next content-only deploy — this is a live, reproducible bug, not a hypothetical.
3. Add a WAL checkpoint (or use `sqlite3 .backup`) immediately before both the local-download and VPS-side backup copies of `hearts.db`/`chat.db` (Finding 3).
4. Make the `.env` sync atomic via temp-file-then-`mv` (Finding 4).
5. Add a `rsync --dry-run` gate (print the file list that would be deleted/changed) before the real `--delete` sync runs in `deploy_to_vps`, so a future scope mistake is visible in the terminal output before it executes.
6. After fixing Finding 3, add a post-backup smoke check: `sqlite3 <backup>/hearts.db "PRAGMA integrity_check;"` and `PRAGMA quick_check;`, aborting the prune step if either backup fails integrity check — turns "we have a backup" into "we have a verified-restorable backup."
7. Drop `CHAT_ADMIN_EMAILS` from the hard-required `PROD_VARS` gate, or split `PROD_VARS` into required vs. optional lists (Finding 5).
