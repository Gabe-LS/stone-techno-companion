# Pre-deployment review: deploy pipeline, Docker, config, and operational readiness

You are a read-only reviewer checking that this festival companion app will actually come up healthy in production and survive operation. You CANNOT run any commands — Bash is not available and will fail. Do not claim to have run or tested anything. Cite findings as `file:line` with quoted snippets.

## Scope

- `deploy.sh`, `server/Dockerfile`, `server/docker-compose.yml`
- `server/api.py` — startup sequence, env var reads, static routes, catch-all route, health endpoint
- `server/chat_api.py` — env var reads, mount order
- `CLAUDE.md` — the "Deploy Checklist" and "Environment Variables" sections: verify every claim against the code
- `.gitignore`, `server/.env` DO NOT read `.env` itself (denied) — instead Grep the codebase for `os.environ`/`os.getenv` and compare the full set of variable names against the CLAUDE.md table

## Focus checklist

1. Env var completeness: every `os.getenv`/`os.environ` in server code vs the CLAUDE.md table. Missing-required behavior: does the app crash loudly at startup or limp along silently (e.g., moderation silently disabled if OPENAI_API_KEY unset)?
2. Startup ordering: chat mount before catch-all, VAPID consistency check, DB creation/permissions on fresh volume, `chat/uploads/` + `chat/tmp/` creation, timezone data in the Docker image.
3. Dockerfile: system deps present (ffmpeg, ffprobe, libvips), Python version match, layer that would break on rebuild, anything copied that shouldn't be (secrets, dev certs, .env, chat.db, uploaded media, diag/ dirs).
4. docker-compose: volume mounts for data persistence (hearts.db, chat.db, vapid pem, uploads — is `chat/uploads/` volume-mounted or lost on rebuild?), restart policy, port exposure.
5. deploy.sh: backup correctness, failure handling (does a failed health check roll back or leave the site down?), git pull on dirty tree, prune logic.
6. Static routes in api.py: every file the frontend fetches has an explicit route (bios.json, manifest.json, sw.js, favicon, shared.css, shared.js, photos, thumbs) — Grep the generated HTML/chat.html for fetched paths and verify each resolves. Catch-all rejecting /chat*.
7. Logging: is anything sensitive logged (tokens, emails, message content)? Is log volume sane for production (the codebase has extensive dbg logging)?
8. SQLite in Docker: WAL files on volume mounts, `secure_delete`, concurrent access from scheduler + WS + REST in one process — any second-process access (stress test docs mention pointing at the same db)?
9. Anything in the repo that would leak into the image or deployment that shouldn't (test artifacts, diag/, notif-diag/, review_reports/, .bak files).

## Hard rules

- Read-only: Read, Glob, Grep only. NEVER read `.env` files.
- Evidence-based findings only.

## Required final report format (this is your entire final message)

```
# Findings: deploy-ops

## [SEVERITY: CRITICAL|HIGH|MEDIUM|LOW] <one-line title>
- Where: file:line
- Evidence: <short quoted snippet>
- Impact: <production consequence>
- Fix: <concrete minimal change>
```

End with `## Verified clean` — one line per checklist area found sound. If nothing found, say so explicitly.
