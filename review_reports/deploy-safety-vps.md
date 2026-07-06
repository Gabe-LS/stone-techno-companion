# Deploy Safety Audit — VPS Verification (209.38.244.136)

## Verdict

The VPS state does **not** simply confirm the prior local-only report — it changes the picture substantially. `root@209.38.244.136` is confirmed correct and reachable (`/root/services/stone-techno` exists, live container running). But the deployed commit (`868fda0`, 2026-06-30 12:43) is **20 commits and ~5 days behind** `origin/main`, and critically it **predates the entire chat feature** (`chat_api.py`/`chat_ws.py`/`chat_db.py`/`chat_moderation.py` were first added in commit `8f5ba64`, hours later the same day). Production is running a pre-chat build: there is no `chat.db`, no chat-uploads mount, no E2EE, no moderation — chat has never gone live despite being fully built and tested locally. User data that *does* exist (`hearts.db`) is intact, WAL-mode, integrity-checked OK, actively accumulating real rows, correctly bind-mounted (not a Docker volume), not exposed by Caddy or any open port, and permissioned correctly. So for the data that exists today, the prior report's "safe" conclusion holds. But two things are worse than assumed: (1) **zero VPS-side backups exist** (`server/data.bak.*` — none), meaning `deploy.sh`'s backup step has apparently never fired here, so there is currently no on-host safety net; and (2) the known `rsync --delete` bug (prior HIGH finding #2) is confirmed **not yet fired but guaranteed to fire on the very next content-only deploy** — `render.py` unconditionally emits `<link href="/shared.css">`/`<script src="/shared.js">` into every generated page, those files were never staged for `--deploy`, and they don't exist on this VPS in any form (never even git-tracked at this stale commit). The site isn't broken today only because the currently-served HTML predates that CSS refactor.

## Findings

### CRITICAL

**1. Production is 20 commits stale and predates chat entirely — `deploy.sh` has not successfully shipped anything in ~5 days.**
- Evidence: `git log -1` on VPS → `868fda0` (2026-06-30 12:43:47). Local HEAD is `1a6aaca` (2026-07-05). `git log --oneline 868fda0..HEAD` → 20 commits. `git merge-base --is-ancestor 8f5ba64 868fda0` → false (chat commit is *after* the deployed commit). `server/*.py` on the VPS is only `api.py` + `generate_vapid_keys.py` — no `chat_api.py` etc. `server/data/` contains only `hearts.db` + `vapid_private.pem`, no `chat.db`.
- Impact: all of the chat/E2EE/moderation deploy-safety questions this audit was meant to answer are currently moot in production — nothing to endanger yet. But it also means the newer hardening (docker-compose mem/cpu limits, log rotation, `chat-uploads` mount, updated `.gitignore` secret exclusions) is likewise not live.

**2. The known `rsync --delete` bug (prior report Finding 2) is a live, loaded gun — confirmed not yet fired only because the site is stale, and guaranteed to fire on first contact.**
- Evidence: `scraper/render.py:273` and `:1525` unconditionally emit `<link rel="stylesheet" href="/shared.css">` and `<script src="/shared.js">` in every generated page. `stone_techno_companion.py`'s `deploy_to_vps` staging (lines 56-84) never copies `shared.css`/`shared.js`. On the VPS, `server/static/` has no `shared.css`/`shared.js` (`ls` confirms absence) — and unlike the prior report's framing, these were **never git-tracked at the deployed commit either** (`git ls-files server/static/` at VPS HEAD → only `favicon.png`, `favicon.svg`, `manifest.json`, `sw.js`; `shared.css` was first added in `c8117a4`, 2026-07-03, three days after the VPS commit). The currently-served `index.html` (mtime Jun 30 23:23) has zero references to `shared` (`grep -c shared` → 0) — production isn't broken today purely because it's running old HTML.
- Impact: this is worse than "deletes tracked files" — the next `python stone_techno_companion.py --render-only --deploy` run, from current local code, will push a new `index.html` referencing files that have *never once* existed on this VPS, breaking all styling/JS instantly, independent of whether `./deploy.sh` runs first or not.

### HIGH

**3. No VPS-side backups exist anywhere.**
- Evidence: `ls -la /root/services/stone-techno/server/` shows no `data.bak.*` entries at all; `ls -d data.bak.*` returns nothing.
- Impact: `deploy.sh`'s VPS-side `cp -r server/data server/data.bak.$TIMESTAMP` step has evidently never executed successfully against this host. There is currently no on-host recovery point if `server/data` is corrupted or lost mid-deploy — the local-backups half of the pipeline is untested against this real host, and the WAL-consistency concern (prior Finding 3) can't be evaluated empirically because there is nothing to inspect.

### MEDIUM

**4. VPS-checked-out `.gitignore` does not protect secrets/data — this only self-heals after the next successful `git pull`.**
- Evidence: `git check-ignore -v server/.env` on the VPS → exit 1, no match. VPS `.gitignore` content (`cat`) has no `.env`, `*.pem`, `data/`, `backups/`, or `chat.db` entries — those were added in later commits. `git status` correctly lists `server/.env`, `server/data/`, and generated static files as merely *untracked* (not protected-by-ignore).
- Impact: low today because `deploy.sh` never runs `git add`, but this checkout currently offers zero git-level protection against an operator accidentally staging secrets on this box; it will fix itself once a successful `deploy.sh` pulls the current `.gitignore`.

**5. Running `docker-compose.yml` and container config are stale relative to current HEAD.**
- Evidence: `git diff 868fda0..HEAD -- server/docker-compose.yml` shows the VPS is missing `mem_limit: 2g`, `cpus: 2.0`, `stop_grace_period: 30s`, the `./chat-uploads:/app/chat/uploads` mount, and the `logging: {max-size: 10m, max-file: 5}` block. `docker inspect stone-techno --format '{{json .HostConfig.LogConfig}}'` confirms only `{"max-file":"3","max-size":"10m"}` (Docker's implicit default, not the intended 5-file cap) is in effect.
- Impact: no immediate data risk, but log retention and resource limits documented as in place are not actually enforced on the running container until the next `--force-recreate`.

### LOW

**6. `.env` on the VPS is missing keys required by the current codebase and still uses the pre-rename VAPID variable name.**
- Evidence: `cut -d= -f1 server/.env` → only `VAPID_PRIVATE_KEY`, `VAPID_PUBLIC_KEY`, `VAPID_SUBJECT`, `OPENAI_API_KEY`. Missing: `MAILEROO_API_KEY`, `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`, `CHAT_ADMIN_EMAILS`. `VAPID_SUBJECT` has not yet been renamed to `VAPID_CLAIMS_EMAIL` per the project's own Deploy Checklist. `CHAT_BASE_URL` correctly absent.
- Impact: fully explained by the stale, pre-chat deployment — not a fresh bug — but confirms the Deploy Checklist's env changes have not yet been applied to this host and must happen before chat can go live here.

### INFO — checked, no issue found

- Bind mounts confirmed via `docker inspect .Mounts`: `/root/services/stone-techno/server/data` → `/app/data` (bind, rw), `/root/services/stone-techno/server/static` → `/app/static` (bind, rw). No named/anonymous volume shadowing either path.
- Caddy (`/root/services/caddy/Caddyfile`, live config) proxies `stonetechno.deftlab.dev` purely via `reverse_proxy stone-techno:8080` — no `root`/`file_server` directive in that block, so it cannot serve `server/data` or any backups as static files. No other vhost in the file references stone-techno paths.
- `ss -tlnp` shows no host-published port for the app (8080 only reachable inside the Docker `apps` network) — not directly internet-exposed; only `80`/`443` (Caddy) and unrelated services' ports (`8000`, `8033`, `2222`, `5050`, `5678`, `6379`) are listening, none touching stone-techno paths.
- `hearts.db`: `PRAGMA journal_mode` → `wal`, `PRAGMA integrity_check` → `ok`, live data present (`sessions`=49, `push_subscriptions`=3) — actively used, not stale despite the stale code.
- `server/data` ownership `root:root`, dir mode `755`; `vapid_private.pem` mode `600` (correctly restricted); `hearts.db` mode `644`. `.env` mode `600`, `root:root`.
- `-wal`/`-shm` sidecars absent at inspection time — consistent with a clean checkpoint (integrity check passed), not evidence of a problem.
- Disk: `df -h /` → 77G total, 29G used, 49G free (37%). `du -sh` on `server/data` (84K) and `server/static` (4.3M) — ample headroom; the keeps-5 backup policy poses no near-term disk-exhaustion risk given these sizes.
- Container: `Up 21 minutes (healthy)`, restart policy `unless-stopped`, `MaximumRetryCount: 0` (standard for `unless-stopped`).
- VPS is a shared multi-tenant box (umami, seafile, n8n, caroster, etc. also present) — no cross-service exposure of stone-techno paths observed.

## Verified facts

- **IP is correct**: `root@209.38.244.136` is genuinely the Stone Techno host (`/root/services/stone-techno` present, container running) — resolves the prior report's CRITICAL blocker.
- **Deployed commit is stale**: `868fda0` (2026-06-30), 20 commits behind `1a6aaca` (2026-07-05), predating chat entirely.
- **`server/data` bind mount matches repo assumption**: confirmed via `docker inspect`, exactly as `docker-compose.yml` (checked-out version) declares — `./data:/app/data`, host path, not a volume.
- **`chat.db` does not exist on this VPS** — refutes any assumption chat is live in production; nothing to verify re: E2EE/moderation data safety here yet.
- **No backups exist** — refutes the assumption that `deploy.sh`'s backup step has ever run successfully against this host; the WAL-consistency question from the prior report (Finding 3) remains theoretical, unexercised.
- **`rsync --delete` bug confirmed not-yet-triggered but live and guaranteed on next content deploy** — `shared.css`/`shared.js` absent both from disk and from git tracking at the deployed commit; current served HTML doesn't reference them (not broken yet), but current local `render.py` does (will break on next deploy).
- **`.gitignore` on VPS is pre-secrets-hardening** — `server/.env`, `server/data/`, `*.pem` are untracked only because nothing has ever `git add`ed them, not because they're ignored at this commit.
- **Caddy only reverse-proxies** — no static-file exposure path to `server/data` or backups exists.
- **No port exposure** — app container has no host-published port; only reachable via Caddy → Docker network.
- **`.env` reflects pre-chat variable set** — missing Maileroo/Google/Admin keys, `VAPID_SUBJECT` not yet renamed — consistent with, and caused by, the stale deployment.

## Recommendations

1. Treat the next deploy as a "first real chat rollout," not a routine update: run `./deploy.sh` (the full path, which does `git pull` + rebuild) **before** ever running `--render-only --deploy` again. That both applies the newer `docker-compose.yml` (chat-uploads mount, log caps, resource limits — Finding 5) via `--force-recreate`, and finally exercises the backup step this audit found has never fired (Finding 3).
2. Fix the `shared.css`/`shared.js` staging gap (Finding 2, and prior report's Finding 2) **before** that first deploy — either add them to `deploy_to_vps`'s staged file list in `stone_techno_companion.py`, or drop `--delete` per the prior report's fix. This is no longer a "will eventually break something" bug; it is scheduled to break styling on the very next content-only deploy run in either ordering.
3. Update `server/.env` locally per the project's own Deploy Checklist (rename `VAPID_SUBJECT`→`VAPID_CLAIMS_EMAIL`, add `MAILEROO_API_KEY`, `GOOGLE_CLIENT_ID`/`SECRET`, `CHAT_ADMIN_EMAILS`) before the next env sync — otherwise `deploy.sh`'s `PROD_VARS` gate (prior Finding 5) will either abort the deploy or ship an incomplete env that silently disables chat auth/moderation/push.
4. After the first successful `deploy.sh` run here, re-run this VPS checklist specifically for: `chat.db` creation and schema, `chat-uploads` bind mount presence, actual creation of `data.bak.*`, and `docker inspect` log-rotation config reading `max-file: 5` — several of today's findings (3, 4, 6) can only be confirmed fixed once that first real deploy has happened.
5. Keep the prior report's unresolved script-level fixes (WAL checkpoint before backup copies, atomic `.env` write via temp+`mv`) — they were not exercisable here since no backup/deploy has run against this host yet, but they'll matter starting with the very first one.
6. Until the next `git pull` lands the newer `.gitignore` on this box, avoid any manual `git add -A`/`git add .` here (Finding 4) — not a `deploy.sh` risk, but worth a one-line caution for anyone operating on this checkout by hand in the interim.
