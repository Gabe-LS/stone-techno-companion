# Festival Ops Runbook

Incident procedures for the live event. Facts verified against `deploy.sh`, `server/chat_moderation.py`, and the push invariants in CLAUDE.md (July 2026).

- **VPS**: `root@209.38.244.136`, app dir `/root/services/stone-techno`, container `stone-techno`
- **Site**: https://stonetechno.deftlab.dev — TLS terminated by Caddy on the VPS
- **Admin panel**: https://stonetechno.deftlab.dev/chat/admin

## Quick health check

```bash
./monitor.sh          # full check: HTTP + chat API JSON + static + TLS expiry + latency,
                      # container health/restarts, disk, memory, load, DB quick_check,
                      # log errors last hour. Exit 1 on any FAIL.
```

Run it hourly via cron (`--quiet` prints only when something needs attention):

```bash
crontab -e
0 * * * * cd "/Users/gabrielelosurdo/Documents/Developer/Scripts/Personal/Stone Techno Companion" && ./monitor.sh --quiet >> logs/monitor.log 2>&1
```

**Alerts**: any FAIL fires a phone push via ntfy.sh (topic `stc26-ops-2c8faa31e3be` — install the ntfy app and subscribe to it; the topic name is the only credential, don't share it) plus a macOS notification. Test with `./monitor.sh --test-alert`.

**Coverage gap**: the monitor runs on the Mac, so it stops when the Mac sleeps — during the festival, back it with an external uptime service. Recommended free setup (UptimeRobot or similar, 5-min interval): one HTTP check on `https://stonetechno.deftlab.dev/line-up` (expect 200) and one KEYWORD check on `https://stonetechno.deftlab.dev/chat/api/config` expecting `msg_char_limit` in the body (a plain 200 check is fooled by the catch-all serving HTML). Point its alerts at your email / the UptimeRobot app.

Manual equivalents when digging into a specific failure:

```bash
ssh root@209.38.244.136 "docker ps --filter name=stone-techno"          # container up + (healthy)?
ssh root@209.38.244.136 "docker exec stone-techno curl -sf http://localhost:8080/chat/api/config"  # chat API alive?
ssh root@209.38.244.136 "docker logs stone-techno --tail 200"           # look for [MOD], [PUSH], VAPID lines
```

## Push notifications not arriving

1. **Test on a Chromium browser first** (Chrome/Brave). FCM is the strict push service; iOS and Firefox working proves nothing about Chromium users.
2. Check the startup log for `VAPID key pair verified`. A mismatch error means the private key file and `VAPID_PUBLIC_KEY` in `.env` have drifted — every push silently fails until fixed. Do NOT regenerate keys casually: new keys invalidate every existing subscription.
3. Check logs for WebPush errors and 410 pruning (`docker logs stone-techno | grep -i push`). A 410 means the browser revoked the subscription (e.g. Brave "Forget me"); the row is auto-pruned and the client self-repairs on next page load.
4. Local reproduction: enable notifications on both lineup and chat in one Chromium profile, then `set -a && source server/.env && set +a && python tests/verify_push_both.py` — expects ONE endpoint, LIVE in both tables.

## Server down / unhealthy

```bash
ssh root@209.38.244.136
cd /root/services/stone-techno/server
docker compose ps
docker logs stone-techno --tail 200   # find the crash reason first
docker compose up -d                  # restart
```

Note: a failed chat module import crashes the server at startup **by design** (fail-fast). The crash reason is always in the logs — read them before restarting repeatedly. If the container is healthy but the site is unreachable, the problem is Caddy or DNS, not this app.

## Rollback / restore

- **Fast code rollback** (~2 min, use this when a deploy goes bad):

  ```bash
  ./deploy.sh --rollback <known-good-commit>
  ```

  Resets the VPS worktree to that commit, rebuilds the container, and health-checks it (container only — the target may predate the chat API). Code only: data and `.env` are untouched. Manual equivalent:

  ```bash
  ssh root@209.38.244.136
  cd /root/services/stone-techno
  git reset --hard <known-good-commit>
  cd server && docker compose up -d --build --force-recreate
  ```

  Known-good reference point: `868fda0` is the June 30 lineup-only build that ran in production until the first chat deploy (July 2026). Rolling back to it loses chat entirely but restores a proven-stable lineup site.

- **Slow path** (when the bad commit should also leave history): `git revert <bad-commit>` locally, push, run `./deploy.sh` — full backups + chat health check included.
- **Backups**: the VPS keeps 5 timestamped backups (`server/data.bak.*`); every `deploy.sh` run also downloads `server/data/` + `chat-uploads/` to local `backups/{timestamp}/` (each `.db` verified with `PRAGMA quick_check`).
- **DB restore**: stop the container, replace `server/data/chat.db` (or `hearts.db`) with the backup copy, delete any stale `-wal`/`-shm` files next to it, start the container.
- **.env restore**: `deploy.sh` keeps `.env.bak.{timestamp}` beside the live `.env` on the VPS.

## Moderation incidents

- **OpenAI API outage** (key set, API erroring): moderated rooms **fail closed** — every send is rejected with "Message could not be verified. Please try again." (no strikes issued). To users the chat looks broken. Confirm via `[MOD] OpenAI moderation error` in logs. Options: wait it out, or toggle `is_moderated` OFF for the main room in the admin panel — but that disables the word filter too, so only with active human moderation.
- **`OPENAI_API_KEY` missing** (misconfig, not outage): moderated rooms silently degrade to word-filter-only. Logged loudly at startup.
- **User rampage**: admin panel → Users → ban (or strike/mute). Bans cover all linked providers, delete the user's messages, and close their live sockets immediately.
- **Admin lockout**: the `X-Admin-Token: $CHAT_ADMIN_TOKEN` header (value in VPS `.env`) is the break-glass super-admin credential, independent of the cookie/role path. `CHAT_ADMIN_EMAILS` accounts can never be demoted or moderated.

## Magic-link email failures

`/chat/api/login` returning 500 means `MAILEROO_API_KEY` is missing or invalid. Check the Maileroo dashboard and quota (3,000/mo free tier). Google OAuth is unaffected — tell affected users to use "Sign in with Google" meanwhile. Also note per-address rate limit: 3 magic links/hour.

## Content updates mid-festival (lineup / timetable / bios)

```bash
python pipeline/stone_techno_companion.py --render-only --deploy   # rsync only, no container restart
```

Static pages (e.g. `/transport`) deploy via `git pull` on the VPS (bind-mounted `static/`), no rebuild.

## Map / POIs

POIs live ONLY in the MapTiler dataset (edit pins there; live within 120 s). If MapTiler is unreachable right after a container restart (cold cache), the map shows zero POIs. Break-glass: drop a `festival-pois.kml` (or `.kmz`/`.json`) export into the VPS `server/static/` — bind-mounted, picked up without a deploy.

## Disk pressure

`chat-uploads/` is the only unbounded growth vector (user media, TTL-purged automatically — expired files are unlinked). Container logs are capped (10 MB x 5). Check with `df -h` on the VPS.

## Client-side debugging in the field

The frontend's `dbg()` console logging is off by default in production. To diagnose a user-visible issue on any device: open the browser console, run `localStorage.setItem('stc_debug', '1')`, reload — full timecoded action logs appear. `localStorage.removeItem('stc_debug')` to turn off. `verify()` failures print regardless of the flag.

## Logs

**Sensitive-data policy: request paths are NEVER logged.** Magic-link tokens travel in `GET /chat/v/{token}` and session tokens in the WebSocket path `/ws/chat/{token}`, so the uvicorn access log and uvicorn INFO lines are disabled in the Dockerfile CMD (`--no-access-log --log-level warning`). The container log contains only app-level events — moderation scores/categories (never message text), push delivery diagnostics (endpoint prefix truncated to 60 chars), VAPID verification, warnings and errors. Do not add request-path logging back; if request tracing is ever needed, configure it in Caddy with explicit token redaction.

- **Live**: `docker logs stone-techno` on the VPS (json-file driver, capped 10 MB x 5).
- **Archived**: every `deploy.sh` run saves the outgoing container's log to local `logs/docker-{timestamp}.log` before `--force-recreate` destroys it (newest 15 kept).
- **Monitor history**: `logs/monitor.log` (hourly cron; only writes when something is non-OK; self-rotates at 512 KB keeping the newest 256 KB).
- **Backup retention** (backups are separate from logs, in `backups/`): VPS keeps the 5 newest `data.bak.*` and `.env.bak.*`; local `backups/` keeps the 15 newest deploy dirs. All pruned automatically by `deploy.sh`.

Useful filters:

```bash
docker logs stone-techno 2>&1 | grep -E 'ERROR|CRITICAL|Traceback'    # problems only
docker logs stone-techno 2>&1 | grep '\[MOD\]'                        # moderation decisions
docker logs stone-techno 2>&1 | grep -E '\[SWLOG\]|\[PUSH'            # push pipeline
docker logs stone-techno 2>&1 | grep -i vapid                        # push key verification
```

## Live settings (no deploy)

Admin panel → Settings (super-admin only): message char limit, DM/room/meetup TTLs. Room properties (moderation, read-only, media, TTL) per-room in the Rooms tab.
