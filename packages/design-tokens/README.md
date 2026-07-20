# packages/design-tokens

Design tokens shared between the legacy `services/companion/static/shared.css`
world and the Next.js front end (`apps/web`), per `docs/roadmap.md` section 3.3
("Scaffold + design tokens + nav").

`tokens.css` ports the `:root` custom properties from `shared.css` — colors,
spacing, radius, shadows, z-index, font scale, header height — with the exact
same variable names, so both worlds stay visually in sync until `render.py` is
retired. It does NOT port component/utility CSS (`.cmd-bar`, `.hamburger`,
`.toast`, etc.); those stay page-specific until each surface is ported.

If a token value changes, update it in both `tokens.css` and `shared.css`
until the migration completes.

## Usage

`apps/web` imports the file directly by relative path in its root layout:

```ts
import "../../packages/design-tokens/tokens.css";
```

No build step, no package manager wiring — it's plain CSS. This may move to a
proper workspace package (with a `package.json` and an npm/pnpm workspace
entry) once Stage 2's monorepo tooling work lands; that is out of scope here.
