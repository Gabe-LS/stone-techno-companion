# Stage 2 monorepo restructure plan

Design document for the Stage 2 "Foundations" workstream (`docs/roadmap.md` section
3.2, "Monorepo restructure"), implementing the decision in `docs/adr/0007-repo-and-hosting-shape.md`
(Accepted 2026-07-13: monorepo, `apps/web` / `services/companion` / `services/data` /
`packages/`, vertical scaling on one bigger VPS).

This document is design only. No files are moved and no code is edited as part of
writing it. It is the map the actual restructure PR(s) follow.

## 0. Grounding

Every claim about a breaking path below was checked against the current tree
(`git ls-files`, `ls`, `grep`) as of 2026-07-13, not assumed from `CLAUDE.md` alone.
Citations are file:line. Where `CLAUDE.md` and the real file disagreed on a detail,
the real file wins and the discrepancy is called out.

---

## 1. Target layout

Per ADR 0007's decision (monorepo, scoped to team-owned code: pretix/Medusa/Payload/
Meilisearch stay upstream, run as their own containers, never vendored):

```
.
├── apps/
│   └── web/                       # Next.js app. Empty placeholder at Stage 2 exit,
│                                  # per ADR 0007's own framing; Stage 3 fills it in
│                                  # (docs/roadmap.md 3.3). Package.json, minimal
│                                  # next.config, README stub, .gitignore for
│                                  # .next/node_modules. No real UI yet.
│
├── services/
│   ├── companion/                 # today's server/, moved as-is (git mv, history
│   │   │                          # preserved)
│   │   ├── api.py
│   │   ├── chat_api.py
│   │   ├── chat_db.py
│   │   ├── chat_moderation.py
│   │   ├── chat_ws.py
│   │   ├── chat/                  # chat.html, admin.html, blocklist.txt, etc.
│   │   ├── static/                # shared.css, shared.js, sw.js, manifest.json,
│   │   │                          # pages/, vendor/, symlinks (see 3.5)
│   │   ├── Dockerfile
│   │   ├── docker-compose.yml
│   │   ├── entrypoint.sh
│   │   ├── requirements.txt
│   │   ├── seed_chat_db.py
│   │   ├── generate_vapid_keys.py
│   │   ├── .dockerignore
│   │   └── data/                  # gitignored runtime: hearts.db, chat.db, vapid*.pem
│   │
│   └── data/                      # today's pipeline/, moved as-is (git mv)
│       ├── stone_techno_companion.py
│       ├── fetch_videos.py
│       ├── scraper/                # db.py, render.py, scrape.py, images.py,
│       │                          # timetable_json.py, overrides.toml, icons/, qrcode.min.js
│       ├── transport/              # capture-api.mjs
│       ├── lineup.db               # gitignored runtime
│       └── output/                 # gitignored runtime: lineup.html, bios.json,
│                                   # timetable.json, photos/, thumbs/
│
├── packages/
│   ├── design-tokens/              # ported FROM services/companion/static/shared.css
│   │                               # at Stage 3 scaffold time (roadmap 3.3); at
│   │                               # Stage 2 this can stay a placeholder or be
│   │                               # skipped until Stage 3 actually needs it: see
│   │                               # non-goals (section 5)
│   └── api-types/                  # shared TS types generated from the two OpenAPI
│                                   # specs (companion API, new lineup JSON read API),
│                                   # per roadmap 3.2's "Formalize API contracts"
│                                   # workstream. Placeholder at Stage 2 exit unless
│                                   # the JSON API workstream lands first.
│
├── tests/                          # STAYS top-level. See 1.1 for the reasoning.
├── monitoring/                     # STAYS top-level, unchanged. See 1.2.
├── docs/                           # STAYS top-level, unchanged.
├── backups/                        # STAYS top-level (gitignored, deploy.sh writes here)
├── logs/                           # STAYS top-level (gitignored, deploy.sh writes here)
├── deploy.sh                       # STAYS top-level, edited in place (see section 3)
├── monitor.sh                      # STAYS top-level, edited in place (see section 3)
├── CLAUDE.md                       # STAYS top-level, needs a per-package split: flagged
│                                   # as a Stage 2 follow-up, not blocking (section 3.8)
└── README.md                       # STAYS top-level, edited in place
```

### 1.1 Where `tests/` lands: stays top-level

**Decision: `tests/` stays at the repo root. It does not split into
`services/companion/tests/` and `services/data/tests/`.**

Reasoning, grounded in what `tests/` actually contains today:

- `git ls-files tests/` shows the 315 pytest tests are **entirely chat/transport
  (companion-side)**: `test_chat_db.py`, `test_chat_api.py`, `test_chat_ws.py`,
  `test_chat_moderation.py`, `test_chat_admin_roles.py`, `test_transport.py`,
  `test_notifications.py`. There are **zero pytest tests for `pipeline/` /
  `services/data`** in the current tree (no `test_scrape.py`, `test_render.py`,
  `test_db.py`, etc. exist). So the premise that "tests span pipeline and server
  code" is not literally true today: the split is really "chat/companion tests"
  plus a handful of standalone Playwright/browser scripts that also target the
  companion server (`tests/e2ee_browser_check.py`, `tests/verify_push_both.py`,
  `tests/notif_badge_browser_check.py`, `tests/transport_*_check.py`,
  `tests/notif_e2e/`), plus `tests/stress_test/` (companion-side load test) and
  `tests/c1_fail_closed_check.py` / `p2_downgrade_check.py`.
- Every one of those test files already does its own `sys.path.insert(0, ... / "server")`
  to reach the companion modules (see section 3.6, the single largest mechanical
  fix the move requires, regardless of where `tests/` physically lives). Moving
  `tests/` into `services/companion/tests/` doesn't remove that path-injection
  pattern, it just shortens the relative path in each file.
- `CLAUDE.md`'s own suite-count framing ("315 core tests: 241 chat + 20 transport")
  and the roadmap's CI workstream ("CI runs the existing 315 pytest tests plus the
  Playwright harnesses") both treat `tests/` as one gating unit, run with one
  `python -m pytest tests/ -v` command from the repo root. Splitting it now would
  require rewriting that single invocation into a matrix (companion suite, plus a
  data suite that doesn't exist yet, plus a future apps/web e2e suite) for no
  present benefit, since there is no data-service test suite to house separately yet.
- Keeping `tests/` top-level also matches the actual near-term trajectory: Stage
  2's own workstream says "stand up the new e2e layer for the Next.js front" as an
  addition, and Stage 3 adds parity-doc-driven e2e tests per surface. A single
  top-level `tests/` (with `tests/apps-web/` or similar added later) scales better
  as a third service (`apps/web`) joins than three fully split `tests/` trees that
  each need their own root detection and CI job.
- Cost of staying top-level: `tests/` importing companion modules across the
  `services/companion` boundary is a minor layering smell (a top-level test dir
  reaching into a service's internals via `sys.path`), but this is exactly the
  status quo today (`server/` and `tests/` are already siblings, not nested), so
  the restructure changes nothing about that relationship: it only renames the
  sibling from `server/` to `services/companion/`.

**Follow-up, not blocking Stage 2**: if `services/data` grows an actual pytest suite
later (for example `test_render.py` while `render.py` still exists mid-Stage-3, or
contract tests for the new JSON read API), the natural seam is `tests/data/`
alongside `tests/` today's flat files, not a new nested `services/data/tests/`. Keep
one pytest rootdir and one `python -m pytest tests/ -v` invocation as the single
gating command for as long as that remains practical.

### 1.2 Where `monitoring/`, `docs/`, `deploy.sh`, `monitor.sh` land: all stay top-level

- **`monitoring/`**: stays top-level, unchanged. It is deployment/ops tooling for
  the whole platform (the QNAP Container Station setup that runs `monitor.sh`), not
  owned by any one service. Nothing inside it references `server/` or `pipeline/`
  by path (verified: `monitoring/qnap/run-monitor.sh` only curls a GitHub raw URL
  for `monitor.sh` itself, see section 3.3), so this move is free.
- **`docs/`**: stays top-level, unchanged. Cross-cutting by definition (ADRs,
  roadmap, invariants, parity docs, runbook): splitting it per-service would
  scatter the exact cross-service reasoning ADR 0007 itself argues a monorepo
  exists to keep together.
- **`deploy.sh`**: stays top-level. It already orchestrates both `server/.env`
  sync and `pipeline/`-produced content deploy in one script (`deploy.sh:19`,
  `LOCAL_ENV="server/.env"`; the content-deploy path is a separate command,
  `pipeline/stone_techno_companion.py --render-only --deploy`, that this script's
  own final echo references at `deploy.sh:374`). A cross-service deploy script
  belongs at the root that spans the services it deploys, not inside one of them.
- **`monitor.sh`**: stays top-level, and this is load-bearing, not just tidy:
  `monitoring/qnap/run-monitor.sh` fetches it from GitHub by a hardcoded raw
  path: `https://raw.githubusercontent.com/Gabe-LS/stone-techno-companion/main/monitor.sh`.
  If `monitor.sh` moved to, say, `monitoring/monitor.sh` or `services/companion/monitor.sh`,
  this URL would 404 silently (the script's own fallback is "use the cached copy,"
  so a move would not fail loudly, it would just quietly stop picking up fixes
  until someone noticed). Keeping it at the exact path `monitor.sh` at repo root
  is a zero-cost way to make the whole monitoring pipeline immune to this
  restructure. Do not move `monitor.sh`.

---

## 2. Complete move map

"Stays" means path and contents unchanged. "git mv" means a tracked move, history
preserved via `git mv` (or `git mv` per-file for a whole subtree). All gitignored/
runtime paths are listed so `.gitignore` can be updated to match.

| Current path | New path | Notes |
|---|---|---|
| `pipeline/` (whole tree) | `services/data/` | `git mv pipeline services/data` |
| `pipeline/scraper/` | `services/data/scraper/` | moves with parent |
| `pipeline/transport/` | `services/data/transport/` | moves with parent; `capture-api.mjs` needs a path-literal fix, section 3.4 |
| `pipeline/stone_techno_companion.py` | `services/data/stone_techno_companion.py` | needs two literal-path fixes, section 3.1/3.2 |
| `pipeline/fetch_videos.py` | `services/data/fetch_videos.py` | no changes needed (self-relative `DB_PATH`) |
| `pipeline/scraper/overrides.toml` | `services/data/scraper/overrides.toml` | no changes |
| `pipeline/lineup.db` (gitignored) | `services/data/lineup.db` | `.gitignore` entries `pipeline/lineup.db*` become `services/data/lineup.db*` |
| `pipeline/output/` (gitignored) | `services/data/output/` | `.gitignore` entry `pipeline/output/` becomes `services/data/output/` |
| `server/` (whole tree) | `services/companion/` | `git mv server services/companion` |
| `server/api.py` | `services/companion/api.py` | one comment-only reference to `pipeline/transport/...`, cosmetic fix optional (section 3.9) |
| `server/chat_api.py` | `services/companion/chat_api.py` | one hardcoded `"pipeline"` literal to fix, section 3.1 |
| `server/chat_db.py`, `chat_moderation.py`, `chat_ws.py` | `services/companion/...` | no path changes needed (self-relative or pure imports) |
| `server/chat/` | `services/companion/chat/` | moves with parent |
| `server/static/` | `services/companion/static/` | moves with parent; symlinks inside need re-pointing, section 3.5 |
| `server/static/pages/` | `services/companion/static/pages/` | moves with parent |
| `server/static/vendor/maplibre/` | `services/companion/static/vendor/maplibre/` | moves with parent |
| `server/static/vendor/dop-cache/` (gitignored) | `services/companion/static/vendor/dop-cache/` | `.gitignore` entry updated |
| `server/Dockerfile` | `services/companion/Dockerfile` | no internal changes needed (all `COPY` paths are already relative to build context, section 3.2) |
| `server/docker-compose.yml` | `services/companion/docker-compose.yml` | no internal changes needed (bind mounts `./data`, `./static`, `./chat-uploads` are already relative to the compose file's own directory, section 3.2) |
| `server/.dockerignore` | `services/companion/.dockerignore` | no changes |
| `server/entrypoint.sh` | `services/companion/entrypoint.sh` | no changes (paths are container-internal `/app/...`) |
| `server/requirements.txt` | `services/companion/requirements.txt` | no changes |
| `server/seed_chat_db.py` | `services/companion/seed_chat_db.py` | uses a `SERVER_DIR`-relative default, verify it is still self-relative (no absolute literal found) |
| `server/generate_vapid_keys.py` | `services/companion/generate_vapid_keys.py` | prints a `/app/data/...` container-internal path only, no change |
| `server/data/` (gitignored) | `services/companion/data/` | `.gitignore` entry `server/data/` becomes `services/companion/data/` |
| `server/chat-uploads/` (gitignored) | `services/companion/chat-uploads/` | `.gitignore` entries updated |
| `server/chat/uploads/`, `server/chat/tmp/` (gitignored) | `services/companion/chat/uploads/`, `.../tmp/` | `.gitignore` entries updated |
| `server/chat.db*` (gitignored, legacy path) | `services/companion/chat.db*` | `.gitignore` entries updated (note: the current DB actually lives under `server/data/chat.db` per `chat_db.py:17`; the root-level `server/chat.db*` gitignore lines look like a stale, defensive leftover, flagged, not touched by this plan) |
| `server/static/bios.json`, `index.html`, `photos`, `thumbs`, `timetable.json` (gitignored symlinks) | `services/companion/static/...` (same names) | symlink targets re-pointed, section 3.5; `.gitignore` entries updated |
| `tests/` | `tests/` (stays) | see section 1.1; internal `sys.path` literals fixed, section 3.6 |
| `monitoring/` | `monitoring/` (stays) | no changes |
| `docs/` | `docs/` (stays) | this plan is one of its files |
| `backups/` (gitignored) | `backups/` (stays) | unchanged; `deploy.sh` already writes here by relative path from repo root |
| `logs/` (gitignored) | `logs/` (stays) | unchanged |
| `deploy.sh` | `deploy.sh` (stays, edited) | every `server/`-literal path inside becomes `services/companion/`, section 3.2 |
| `monitor.sh` | `monitor.sh` (stays, edited) | `VPS_DIR/server/...` literals become `VPS_DIR/services/companion/...`, section 3.3 |
| `CLAUDE.md` | `CLAUDE.md` (stays, NOT edited by this plan) | flagged as a required follow-up PR, section 3.8 |
| `README.md` | `README.md` (stays, edited) | path references updated in the execution steps, section 4 |
| `docs/runbook.md` | `docs/runbook.md` (stays, edited) | `server/`-literal paths in the incident procedures need the same rename, section 3.8 |
| new: `apps/web/` | n/a | created empty per ADR 0007, Stage 3 fills in |
| new: `packages/` | n/a | created as placeholder(s); populated starting Stage 3 (design tokens) and whenever the OpenAPI-types workstream lands |

---

## 3. Everything that breaks, and its fix

Every item below was found by grepping the actual file, not inferred from `CLAUDE.md`.

### 3.1 `services/companion/chat_api.py`: hardcoded `"pipeline"` literal

`server/chat_api.py:152`:
```python
lineup_db = Path(__file__).resolve().parent.parent / "pipeline" / "lineup.db"
```
This is `_load_site_short()`'s fallback lookup of the event's short name for page
titles, used "for production, where lineup.db is not inside the container" (comment
at `server/chat_api.py:146`). Today `Path(__file__).resolve().parent.parent` is the
repo root (`server/` and `pipeline/` are siblings under it), so `/ "pipeline" /
"lineup.db"` resolves correctly.

After the move, `services/companion/chat_api.py`'s `parent.parent` is `services/`
(one level shallower than the repo root, since `services/companion` is now two
levels deep instead of one). The literal must change from `"pipeline"` to `"data"`:
```python
lineup_db = Path(__file__).resolve().parent.parent / "data" / "lineup.db"
```
This still resolves correctly because `services/companion` and `services/data` are
siblings under `services/`, the same relationship `server/` and `pipeline/` had
under the repo root: only the sibling's name changes.

### 3.2 `services/data/stone_techno_companion.py`: two hardcoded path literals

`pipeline/stone_techno_companion.py:45`:
```python
VPS_STATIC_DIR = "/root/services/stone-techno/server/static/"
```
This is the absolute VPS path the `--deploy` rsync ships content to (`deploy_to_vps()`,
`pipeline/stone_techno_companion.py:52-111`). Must become:
```python
VPS_STATIC_DIR = "/root/services/stone-techno/services/companion/static/"
```
This is the one hardcoded path in the whole codebase that encodes the VPS's own
directory layout, and it must change in lockstep with whatever the VPS worktree's
new layout actually is post-restructure (see section 4's point-of-no-return step).

`pipeline/stone_techno_companion.py:66`:
```python
server_static = Path(__file__).resolve().parent.parent / "server" / "static"
```
This is where `deploy_to_vps()` picks up `manifest.json`, `sw.js`, `shared.css`,
`shared.js` to stage alongside the rendered lineup HTML before rsyncing. Same fix
pattern as 3.1: `parent.parent` becomes `services/` after the move, so the literal
changes from `"server"` to `"companion"`:
```python
server_static = Path(__file__).resolve().parent.parent / "companion" / "static"
```

Everything else in this file is self-relative (`PROJECT_ROOT = Path(__file__).resolve().parent`,
`DB_PATH = PROJECT_ROOT / "lineup.db"`, `DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output"`,
`pipeline/stone_techno_companion.py:41-44`) and needs no change: moving the whole
file with its parent directory preserves these.

### 3.3 `deploy.sh`: pervasive `server/`-literal paths, and the VPS-side compose location

`deploy.sh` hardcodes `VPS_DIR="/root/services/stone-techno"` (`deploy.sh:17`) and
then builds every VPS-side path by string concatenation with a literal `server/`
segment. Every one of these must become `services/companion/`:

- `deploy.sh:53`: `ssh "$VPS" "cd $VPS_DIR/server && docker compose up -d --build --force-recreate"` (rollback path)
- `deploy.sh:64-65`: rollback-instructions echo referencing `$VPS_DIR/server/data.bak.<timestamp>` and `$VPS_DIR/server/.env.bak.<timestamp>`
- `deploy.sh:158-162`: the atomic `.env` sync (`$VPS_DIR/server/.env.tmp`, `$VPS_DIR/server/.env`, `$VPS_DIR/server/.env.bak.$TIMESTAMP`)
- `deploy.sh:174,178,193`: VAPID preflight reading `$VPS_DIR/server/data/vapid_private.pem`
- `deploy.sh:210,212,218`: the `VACUUM INTO` snapshot step globbing `$VPS_DIR/server/data/*.db` and `$VPS_DIR/server/data/*.pem`
- `deploy.sh:224-225`: rsync pulling the snapshot down from `$VPS:$VPS_DIR/server/data-snap.$TIMESTAMP/`
- `deploy.sh:250`: rsync pulling `$VPS:$VPS_DIR/server/chat-uploads/`
- `deploy.sh:258-259`: renaming the snapshot to `$VPS_DIR/server/data.bak.$TIMESTAMP`
- `deploy.sh:281,284,295,297-298`: the first-deploy chat.db seed step, reading local `server/data/chat.db` and writing the seed to `$VPS_DIR/server/data/chat.db`
- `deploy.sh:319`: the main deploy's `docker compose up -d --build --force-recreate`, run from `$VPS_DIR/server`
- `deploy.sh:347`: backup pruning, listing `$VPS_DIR/server/data.bak.*` and `$VPS_DIR/server/.env.bak.*`
- `deploy.sh:19`: `LOCAL_ENV="server/.env"` (local-side, repo-root-relative, becomes `services/companion/.env`)
- `deploy.sh:374`: the closing usage-echo referencing `python pipeline/stone_techno_companion.py --render-only --deploy`, becomes `python services/data/stone_techno_companion.py --render-only --deploy` (cosmetic but should still be fixed since it's the documented invocation)

This is the single largest concentration of literal-path breakage in the repo: every
one of these must be updated, and the VPS worktree itself must have already been
restructured to match before this script is run again (see section 4's
point-of-no-return step: this is not a script-only fix, it requires the VPS's git
checkout to also be `git mv`'d into the new layout, since `deploy.sh` never
recreates directory structure on the VPS, it only `git pull`s/`git reset --hard`s
an existing worktree and assumes its subdirectories already exist).

### 3.4 `services/data/transport/capture-api.mjs`: relative path literal changes depth

`pipeline/transport/capture-api.mjs:326`:
```js
writeFileSync(new URL('../../server/static/timetable-transport.json', import.meta.url), ...)
```
Today, `pipeline/transport/` is two path segments deep (`pipeline`, `transport`), so
`../../` walks back to the repo root, then descends into `server/static/...`. After
the move, `services/data/transport/` is three segments deep (`services`, `data`,
`transport`), so escaping to the repo root needs one more `../`, and the sibling
directory name changes too:
```js
writeFileSync(new URL('../../../services/companion/static/timetable-transport.json', import.meta.url), ...)
```
The adjacent `console.log` at `pipeline/transport/capture-api.mjs:327` ("JSON saved
to server/static/timetable-transport.json") is a user-facing message, not a path
literal, but should be updated too for accuracy.

This is the general shape of every relative-path breakage in this restructure:
anything currently one level shallower (`pipeline/X`, `server/X`, both one segment
under root) becomes one level deeper (`services/data/X`, `services/companion/X`, two
segments under root), so any `../`-style relative path crossing between the two
trees needs exactly one extra `../` in addition to the renamed segment. The two
symlink cases in 3.5 hit the same rule.

### 3.5 Symlinks in `services/companion/static/` need re-pointing (extra `../`, same rule as 3.4)

Confirmed via `find -type l`, these five symlinks exist in `server/static/` today:

```
server/static/index.html      -> ../../pipeline/output/lineup.html
server/static/bios.json       -> ../../pipeline/output/bios.json
server/static/timetable.json  -> ../../pipeline/output/timetable.json
server/static/photos          -> ../../pipeline/output/photos
server/static/thumbs           -> ../../pipeline/output/thumbs
```
`server/static/` is two segments deep (`server`, `static`), so `../../` reaches the
repo root, then descends into `pipeline/output/...`. After the move,
`services/companion/static/` is three segments deep, so each symlink needs a third
`../` and the renamed sibling:

```
services/companion/static/index.html      -> ../../../services/data/output/lineup.html
services/companion/static/bios.json       -> ../../../services/data/output/bios.json
services/companion/static/timetable.json  -> ../../../services/data/output/timetable.json
services/companion/static/photos          -> ../../../services/data/output/photos
services/companion/static/thumbs           -> ../../../services/data/output/thumbs
```
These are gitignored (`.gitignore`: `server/static/bios.json`, `index.html`,
`photos`, `thumbs`, `timetable.json`), so `git mv` will not touch them at all: they
are local build artifacts recreated by `pipeline/stone_techno_companion.py --render-only`.
Concretely: after moving `server/` and `pipeline/`, the existing symlinks in any
local working tree will point at a now-nonexistent relative path (still
`../../pipeline/output/...`, which no longer resolves) until the next
`--render-only` run recreates them at the new relative depth, but only if the
render code that creates these symlinks is itself relative-safe. This needs a
one-line check during execution (section 4, verification gate) that the symlink-
creation code in `render.py`/`stone_techno_companion.py` computes its target via
`Path` objects relative to the moved files' own new locations, not a copy-pasted
string literal. If it's a literal, it has the same fix as 3.4.

### 3.6 Every test file's `sys.path.insert(..., "server")` literal

Confirmed via grep, the following files hardcode the literal segment `"server"` when
adding the companion service's directory to `sys.path` so they can `import chat_db`,
`import chat_ws`, etc. directly (there is no installed package, so this is the only
way these modules resolve):

- `tests/test_chat_db.py:10`
- `tests/test_chat_api.py:13`
- `tests/test_chat_admin_roles.py:14`
- `tests/test_chat_moderation.py:10`
- `tests/test_chat_ws.py:12`
- `tests/test_transport.py:15`
- `tests/test_notifications.py:18`
- `tests/e2ee_browser_check.py:41` (`SERVER_DIR = REPO_ROOT / "server"`)
- `tests/notif_badge_browser_check.py:43` (`SERVER_DIR = REPO_ROOT / "server"`)
- `tests/notif_e2e/harness.py:59` (`SERVER_DIR = REPO_ROOT / "server"`): this one
  additionally `shutil.copytree(SERVER_DIR, scratch_server, ...)`s the entire
  companion tree into an isolated scratch directory per test run
  (`tests/notif_e2e/harness.py:174`), so it is not just an import-path fix. The copy
  target naming and any assumptions the harness makes about what's inside that
  copied tree (for example, `data/` being created under it at `harness.py:175`)
  need re-verifying against the new `services/companion` contents, though the copy
  logic itself is directory-name-agnostic once the source literal is fixed.
- `tests/transport_duesseldorf_check.py:22`, `transport_reverse_check.py:18`,
  `transport_routes_check.py:24`, `transport_duesseldorf_realtime_check.py:16` (all
  `Path.cwd() / "server"`, which is cwd-relative rather than `__file__`-relative:
  these already assume they're invoked from the repo root, so the fix is the same
  literal rename, no behavior change)

Every one of these becomes `"services/companion"` (or `.parent.parent /
"services" / "companion"` where the path is built in two segments). This is
mechanical and total: every test file that talks to the companion server needs this
one-line fix, and it is the highest file-count breakage in the whole restructure
(14 files), though each individual fix is a single literal string.

`tests/stress_test/run.py:2400` also defaults `--db` to `server/data/chat.db` (a CLI
argument default, not an import path); it should become `services/companion/data/chat.db`
for the documented VPS-invocation example in `CLAUDE.md`'s Stress Test section to
keep working, though it is not itself broken (it is a default that can always be
overridden with `--db`).

### 3.7 What does NOT break (verified, not assumed)

- **`services/companion/Dockerfile`**: `docker-compose.yml` builds it with `build: .`
  from inside `server/` (soon `services/companion/`), and every `COPY` instruction
  (`server/Dockerfile`: `COPY requirements.txt .`, `COPY api.py chat_api.py ... ./`,
  `COPY chat/ ./chat/`, `COPY entrypoint.sh /app/entrypoint.sh`) is already relative
  to that build context. Moving the whole directory changes nothing here.
- **`services/companion/docker-compose.yml`**: all three bind mounts (`./data:/app/data`,
  `./static:/app/static`, `./chat-uploads:/app/chat/uploads`, `server/docker-compose.yml`)
  are relative to the compose file's own directory, which moves with it. No edit
  needed.
- **`services/companion/entrypoint.sh`**: all paths are container-internal absolute
  paths (`/app/data`, `/app/chat/uploads`, `/app/static/vendor/dop-cache`,
  `server/entrypoint.sh`), unaffected by the host-side repo restructure.
- **`services/companion/api.py`'s `DB_PATH`** (`server/api.py:30`,
  `Path(__file__).resolve().parent / "data" / "hearts.db"`) and
  **`services/companion/chat_db.py`'s `CHAT_DB_PATH`** (`server/chat_db.py:15-17`,
  same `__file__`-relative pattern, overridable via `CHAT_DB_PATH` env var) are both
  self-relative to their own file, so they need no change.
- **`services/data/fetch_videos.py`'s `DB_PATH`** (`pipeline/fetch_videos.py:18`,
  `Path(__file__).resolve().parent / "lineup.db"`) is self-relative, no change.
- **`services/companion/seed_chat_db.py`'s `--source` default** (`server/seed_chat_db.py:58`,
  `str(SERVER_DIR / "data" / "chat.db")`): needs verifying that `SERVER_DIR` in that
  file is itself `__file__`-relative rather than a repeated literal; if it is
  (consistent with every other companion module's pattern), no change needed.

### 3.8 Documentation that needs its own follow-up (not blocking Stage 2's code moves, but tracked)

- **`CLAUDE.md`**: contains dozens of `server/`- and `pipeline/`-prefixed paths
  throughout (Quick Reference commands, the Key Files table, the Environment
  Variables table, the Deploy Checklist, etc.), effectively the whole file assumes
  today's layout. Per the blueprint's own philosophy of doing large near-mechanical
  edits as one dedicated pass rather than folding them into a functional PR, this
  plan recommends a separate, immediately-following PR that does nothing but
  rewrite `CLAUDE.md`'s paths (and likely splits it: the doc itself implies a
  per-package split once the restructure lands). Note as a Stage 2 follow-up, per
  this task's brief; do not attempt inline in the moves PR, since a wrong path in a
  doc doesn't break CI the way a wrong path in `deploy.sh` does, and mixing
  "mechanical file moves" with "prose rewrite of a 90 KB doc" in one PR makes the
  moves PR much harder to review.
- **`docs/runbook.md`**: also has several `server/`-literal paths in its incident
  procedures (`docs/runbook.md:41,75,76,93,100`, for example "`server/.env`",
  "`server/data.bak.*`", "`server/data/`", "`server/static/`"). Same follow-up PR as
  CLAUDE.md: these are runbook commands an on-call human copy-pastes during an
  incident, so they must be accurate, but they are prose/instructions, not code
  that executes, so they don't block the Stage 2 exit criteria the way `deploy.sh`
  does.
- **`README.md`**: has the same class of references (`README.md:21,35,48,101,118,123,129,138,146,153,238,243,259-261`).
  Since this is the first file a new contributor reads, this plan's execution order
  (section 4) does update `README.md` inline as part of the moves PR (unlike
  `CLAUDE.md`/`runbook.md`, which are large enough to warrant their own pass): it is
  a small, mechanical, low-risk find-and-replace given its size.

### 3.9 Low-risk cosmetic reference (not required to fix, flagged for completeness)

`server/api.py:1452` has a comment referencing `pipeline/transport/capture-api.mjs`
by path. It's prose inside a docstring/comment describing where the transport JSON
regenerates from, not executable, so it doesn't break anything, but the moves PR
should fix it in the same pass as the file it lives in since it's a one-line diff
adjacent to code already being touched.

---

## 4. Execution order

Each step below is designed to leave the repo in a working, deployable state before
the next step starts. Every step that can be verified is verified before moving on.

**Step 0. Pre-flight.** Confirm the working tree is clean (`git status`), the full
test suite is currently green (`python -m pytest tests/ -v`), and note the exact
`lineup.html` byte output of a `--render-only` run (see step 4) as the pre-move
baseline for the identical-output diff.

**Step 1. `git mv` the two big trees, no reference fixes yet.**
```
git mv pipeline services/data
git mv server services/companion
```
Commit this alone, with a message that says only "moved, references not yet fixed."
This isolates the history-preserving move from the logic changes in the next steps,
so `git log --follow` on any moved file still works cleanly and a reviewer can see
"pure rename" as one commit and "path-literal fixes" as the next. The repo is
intentionally broken between here and step 3 (imports and hardcoded paths do not
resolve yet). This is fine locally as long as nothing is deployed from this state,
but it must not be pushed as a standalone deployable state to any shared branch
other than the feature branch this whole restructure lives on.

**Step 2. Fix every reference found in section 3, in dependency order.**

1. `services/companion/chat_api.py` (`"pipeline"` to `"data"`, section 3.1)
2. `services/data/stone_techno_companion.py` (two literals, section 3.2)
3. `services/data/transport/capture-api.mjs` (extra `../` plus rename, section 3.4)
4. Re-point the five symlinks in `services/companion/static/` (section 3.5), or
   confirm they self-heal on the next `--render-only` (verify the symlink-creation
   code path before assuming this)
5. `deploy.sh` (every literal in section 3.3)
6. `monitor.sh` (the `$VPS_DIR/server/...` literals: grep confirmed lines at
   `monitor.sh:174,198`, referencing `$VPS_DIR/server/chat-uploads` and
   `$VPS_DIR/server/data/*.db`)
7. All 14 test files in section 3.6 (the `"server"` to `"services/companion"` literal)
8. `README.md` (all path references, per section 3.8's "fix inline" call)
9. `server/api.py`'s now-moved comment at (new path) `services/companion/api.py`
   (section 3.9, optional but cheap)

Commit this as one logical "fix references after move" commit, or a small number of
commits grouped by concern (one for deploy/monitor ops scripts, one for tests, one
for app code), but not split so finely that any individual commit leaves the repo
non-functional.

**Step 3. Run the full test suite.**
```
python -m pytest tests/ -v
```
Must be green with the same pass count as the step-0 baseline (315). Any failure at
this point is either a missed literal from section 3 or a new one this plan didn't
catch: fix and re-run before proceeding. This is the first hard gate.

**Step 4. `--render-only` identical-output diff.**
```
python services/data/stone_techno_companion.py --render-only --no-photos
diff <(sha256sum services/data/output/lineup.html) <(echo "<step-0 baseline hash>")
```
Confirms the pipeline itself, now living at its new path, produces byte-identical
output to the pre-move baseline captured in step 0. Also confirms the symlinks in
`services/companion/static/` resolve (open `services/companion/static/index.html`
and check it's not a dangling link).

**Step 5. Local dev smoke test.** Bring up the full server locally per `CLAUDE.md`'s
existing instructions, adjusted for the new path:
```
cd services/companion && set -a && source .env && set +a && \
  uvicorn api:app --port 64728 --ssl-keyfile certs/localhost+1-key.pem --ssl-certfile certs/localhost+1.pem
```
Open `https://localhost:64728/line-up` and `https://localhost:64728/chat`, confirm
both load, confirm a chat message round-trips, and confirm the lineup page's lazy
`bios.json` fetch works (proves the symlink chain end to end, not just that it
exists on disk).

**Step 6. `monitor.sh --test-alert` path check.**
```
./monitor.sh --test-alert
```
Run locally, not on the VPS (this is a local dry-run of the alerting path, not the
SSH-based VPS-internal checks, which require the VPS to already be restructured,
see step 8). Confirms the script's own top-level path (`monitor.sh` unchanged
location, section 1.2) still resolves and the alert fires. The SSH-based internal
checks (`$VPS_DIR/services/companion/data/*.db`, etc.) cannot be fully verified
until step 8's VPS-side restructure has happened; note this explicitly rather than
claiming a false green here.

**Step 7. Deploy dry-run.**
```
./deploy.sh --dry-run
```
Confirms every literal path fixed in step 2's item 5 resolves without error in
dry-run mode. Dry-run mode should surface a wrong
`$VPS_DIR/services/companion/...` path as a clear SSH/rsync error rather than a
silent no-op: verify this is actually true of the dry-run implementation before
trusting it as a gate, since a dry-run that skips the SSH calls entirely would not
have caught the path bugs this whole plan exists to prevent.

### 4.1 The point of no return: shipping the new layout to the VPS

This is step 8, and it is irreversible in a way none of the steps above are.

The VPS at `/root/services/stone-techno` is a `git`-tracked worktree that `deploy.sh`
operates on via `git reset --hard` / `git pull` (`deploy.sh:47-53` for rollback,
`deploy.sh:268` for normal deploy) plus `docker compose up -d --build --force-recreate`
run from inside `$VPS_DIR/server` (soon `$VPS_DIR/services/companion`). None of
`deploy.sh`'s git operations create new top-level directories; they operate on an
existing worktree whose tracked files are wherever the checked-out commit says they
are. So the first deploy that ships a commit containing the `git mv` is the one
where:

- The VPS's `git pull`/`git reset --hard` will correctly move the tracked files to
  their new paths (git handles this natively, no manual `mkdir`/`mv` needed on the
  VPS side), but:
- Every VPS-side untracked, gitignored runtime path (`server/data/*.db`,
  `server/data/vapid_private.pem`, `server/chat-uploads/`) does not move with a
  `git reset --hard`, because git never touches untracked files. These must be
  moved on the VPS manually, in the same maintenance window, before
  `docker compose up` runs against the new `docker-compose.yml` at its new path.
  Otherwise the container starts with empty `data/` and `chat-uploads/` directories
  at the new location, which looks like (and would behave as) total data loss for
  hearts.db/chat.db/VAPID keys/uploaded media, even though the actual bytes are
  still sitting untouched at the old path.
- Concretely, the VPS-side sequence for this one deploy must be, in order: (a) the
  normal pre-deploy backup/snapshot steps `deploy.sh` already does, unaffected by
  this since they run before the code update; (b) `git fetch` plus `reset --hard`
  to the new commit, which moves tracked files; (c) a manual, scripted `mv` of
  `server/data` to `services/companion/data`, `server/chat-uploads` to
  `services/companion/chat-uploads`, and any leftover `server/chat/uploads`/
  `server/chat/tmp` if present, run over SSH before the compose step; (d)
  `docker compose up -d --build --force-recreate` from the new
  `services/companion/` path; (e) the existing health check.
- This means `deploy.sh` itself needs a one-time, deploy-specific migration step
  added for exactly this deploy: a small guard such as
  `if [ -d "$VPS_DIR/server/data" ] && [ ! -d "$VPS_DIR/services/companion/data" ];
  then mv ...; fi`, or a fully manual SSH session run once, outside the script,
  immediately before the automated deploy. Either is acceptable, but the plan must
  pick one explicitly before step 8, not improvise it live against production.
  This is the one piece of section 3 that is genuinely novel to write (not just a
  literal-rename), because `deploy.sh` has never before had to move data across a
  directory rename.
- **Rollback for this specific step**: `./deploy.sh --rollback <pre-move-tag>` resets
  the VPS code back to the pre-move commit (`deploy.sh`'s existing `git reset
  --hard` path, `deploy.sh:52`), but it does not reverse a manual data directory
  move, and it does not revert `docker-compose.yml`'s bind-mount paths back
  automatically in a way that matches wherever the data physically ended up. If the
  manual data move in (c) above already happened and then a rollback is triggered,
  the operator must also manually move the data back (`services/companion/data`
  to `server/data`, etc.) before or as part of the rollback's `docker compose up`,
  or the rolled-back (old-layout) container will start looking for `server/data`
  that is no longer there. Practical mitigation: don't delete/rename the old
  `server/` directory's data on the VPS, copy it instead of moving it, so the
  pre-move paths still exist as a fallback for one deploy cycle. Clean them up
  only after the new layout has been confirmed healthy for some period (for
  example, through the next scheduled backup prune). This turns an irreversible
  manual step into a reversible one, at the cost of temporarily doubled disk usage
  for the data directories only.
- Everything before step 8 (local restructure, test suite, render diff, local dev
  smoke test, dry-run) is fully reversible with an ordinary `git revert`/reset of
  the feature branch, since nothing has touched the VPS yet.

**Step 9. Full `./deploy.sh` to production**, per this plan's own verification gate
(section 6), as the final acceptance criterion for the whole restructure.

---

## 5. Non-goals

- **No code rewrites.** `render.py`, `chat_ws.py`, `scrape.py`, etc. are moved
  verbatim. Nothing about their internal logic changes as part of this plan.
- **No dependency changes.** `requirements.txt`, the Dockerfile's installed system
  packages, Python/Node versions: all unchanged.
- **No behavior changes.** The identical-output diff in section 4 step 4 and section
  6 exists specifically to prove this. If the diff is not byte-identical, that is a
  bug in the restructure, not an intentional improvement smuggled in.
- **Next.js scaffolding is out of scope for this plan.** `apps/web/` is created
  empty (or with the barest possible placeholder: a `package.json` and nothing
  that renders) per ADR 0007's own framing ("empty placeholder until Stage 3").
  Building the actual Next.js app, the nav component (ADR 0002), or any UI is Stage
  3 work per `docs/roadmap.md` section 3.3, not this document.
- **No `packages/` content is required for Stage 2 exit** beyond the directory
  existing. The roadmap's own Stage 2 exit criteria list "OpenAPI specs exist...
  at least one real endpoint is being served from the new JSON API" as a separate
  workstream from the monorepo restructure itself. This plan is scoped to the
  restructure workstream only (`docs/roadmap.md` 3.2's first bullet), not the API-
  contracts or dev-infrastructure workstreams listed alongside it. Those need their
  own design pass once this restructure has landed, since `packages/api-types`
  cannot be usefully populated before the OpenAPI specs it's generated from exist.
- **No CLAUDE.md/runbook.md rewrite in this PR.** Tracked as an explicit follow-up
  (section 3.8), not silently deferred.

---

## 6. Verification gate

All of the following must pass, in this order, before the restructure is considered
complete. This is the same sequence as section 4's steps 3, 4, 6, and 9, restated
here as the single acceptance checklist:

1. **`python -m pytest tests/ -v` green.** Same 315-test count as the pre-move
   baseline (`CLAUDE.md`'s documented "241 chat + 20 transport" plus whatever the
   admin-roles/moderation/db/ws/api suites contribute to that total; the number
   itself doesn't change, only where the code being tested lives).
2. **`--render-only` pipeline run producing identical output.** Diff the generated
   `lineup.html` (and, for completeness, `bios.json` and `timetable.json`, since all
   three are produced by the same run and are equally cheap to hash) before and
   after the move, byte-for-byte. Any difference is a bug introduced by the move,
   not an acceptable side effect.
3. **`monitor.sh --test-alert` path check**, run locally, confirming the alert path
   fires correctly with `monitor.sh` still at its unchanged top-level location
   (section 1.2). The SSH-based VPS-internal checks are separately re-verified after
   step 8/9's VPS restructure, since they depend on the VPS's own directory layout
   matching.
4. **A successful `./deploy.sh` to production, as the final acceptance criterion.**
   This includes: the pre-deploy backup/snapshot/VAPID-preflight steps completing
   normally (proving `deploy.sh`'s literal-path fixes from section 3.3 are
   correct), the one-time VPS-side data-directory migration from section 4.1
   completing without error, `docker compose up -d --build --force-recreate`
   succeeding from the new `services/companion/` path, and the post-deploy health
   check passing. Only once this has run clean is Stage 2's monorepo-restructure
   workstream done; the remaining Stage 2 workstreams (OpenAPI contracts,
   `docker compose` local dev stack, CI) are separate follow-on work per
   `docs/roadmap.md` 3.2, not part of this plan's scope (section 5).
