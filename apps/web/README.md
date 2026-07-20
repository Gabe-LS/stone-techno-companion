# apps/web

The Next.js front end for Stone Techno Companion (App Router, TypeScript). See
`docs/roadmap.md` section 3.3 for the Stage 3 plan this scaffold starts, and
`docs/adr/0001-nextjs-frontend.md` / `docs/adr/0002-menu-component-deferred.md`
for the decisions behind it.

Design tokens are ported from `services/companion/static/shared.css` into
`packages/design-tokens/tokens.css` and imported directly (see
`app/layout.tsx`) — same variable names, kept in sync by hand until
`render.py` is retired.

## Install

```bash
cd apps/web
npm install
```

## Run (dev)

```bash
npm run dev
```

Serves at `http://localhost:3000` by default. Check the port is free first
(`lsof -ti :3000`), per the project's port-check convention.

### Running alongside the companion backend

`next.config.ts` proxies `/api/*` and `/chat/api/*` to the FastAPI companion
backend in development only (production sits behind the same Caddy reverse
proxy as the backend, so no rewrite is needed there). Start the backend per
the root `CLAUDE.md` Quick Reference:

```bash
cd services/companion && set -a && source .env && set +a && \
  uvicorn api:app --port 64728 --ssl-keyfile certs/localhost+1-key.pem --ssl-certfile certs/localhost+1.pem
```

The backend serves `https://localhost:64728` over a self-signed mkcert
certificate. Browsers and `curl` trust it because mkcert installs its root CA
into the OS/browser trust store, but **Node.js does not read that store** —
`next dev`'s rewrite proxy will fail TLS verification against it unless told
about the CA explicitly. Two options, cleanest first:

```bash
# Option 1 (recommended): point Node at mkcert's root CA specifically
export NODE_EXTRA_CA_CERTS="$(mkcert -CAROOT)/rootCA.pem"
npm run dev

# Option 2 (blunt fallback, if mkcert's CAROOT isn't available): disable TLS
# verification for the whole dev process. Local dev only, never commit this.
NODE_TLS_REJECT_UNAUTHORIZED=0 npm run dev
```

If the backend runs on a different port, set `BACKEND_ORIGIN` before starting
`next dev` (e.g. `BACKEND_ORIGIN=https://localhost:9000 npm run dev`).

## Build

```bash
npm run build
```

Production build (no dev-only proxy rewrites are added).

## Routes so far

- `/` — placeholder home page
- `/transport` — stub for the transport page port (next surface up, per
  `docs/roadmap.md` section 3.3 and `docs/parity/transport.md`)

Line-up, Timetable, and Chat still live on the current site; the nav links to
their documented production paths (`/line-up`, `/timetable`, `/chat`) ahead of
those surfaces actually being ported here.
