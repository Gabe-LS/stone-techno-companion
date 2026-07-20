# "Getting there" — design

Status: implemented (v1). Companion doc to `docs/parity/transport.md` (the live
departure boards this section sits alongside). Data file:
`services/companion/static/getting-there.json`. UI:
`apps/web/components/transport/GettingThere.tsx`, mounted from
`apps/web/app/transport/page.tsx` below the existing live boards.

## 1. Core principle: match detail to decision timing

The `/transport` page already does one job very well: telling someone standing
at Zollverein or Essen Hbf *right now* which tram or train to catch, with a
live countdown. That is a **last-mile, in-the-moment** decision, and the
existing departure boards are correctly built around realtime data that goes
stale in 90 seconds.

"Getting there" (Amsterdam to Essen, or DUS to the venue) is a **different
kind of decision, made days or weeks ahead**, usually while comparing a
handful of options once and then booking. The content that decision needs is
categorically different from a departure board:

- **Coarse, not precise.** "Direct ICE, about 2 hours" is useful for choosing
  a travel method. "ICE 123 departs 14:07 from platform 3" is not just
  unnecessary, it is actively wrong within days as timetables shift — and this
  page has no realtime feed for long-distance rail the way it does for the
  regional Zollverein/DUS routes.
- **Durable, not live.** A row here should still be true in six months. No
  departure times, no platform numbers, no "next train in 12 minutes."
- **A pointer, not a planner.** The page's job is to narrow the field (which
  cities have a sane direct-ish route, which airport to fly into, where to
  park) and then hand off to the operator's own booking/journey-planning tool,
  which already does per-date search correctly. See section 6.

This is why "Getting there" and the live boards can coexist on one page
without competing: they answer different questions, at different decision
horizons, and the design of each follows from that.

## 2. Data model

```jsonc
{
  "event_id": "stone-techno-2026",
  "methods": [
    {
      "id": "train",              // "train" | "plane" | "car" | "bus" | ... — free text, not an enum
      "label": "Train",
      "position": 1,               // display order among methods
      "items": [
        {
          "origin": "Amsterdam",   // OR "title" for non-origin-shaped items (plane/car/bus rows)
          "summary": "One line, human-readable, coarse (see section 3).",
          "duration_hint": "~2h15 direct, ~3h with a change",
          "link": "https://www.nsinternational.com/en/germany/train-essen",
          "link_label": "Book via NS International",
          "countries": ["NL"],     // ISO 3166-1 alpha-2, for the personalization boost (section 4)
          "notes": "Optional extra line, e.g. caveats or transfer detail."
        }
      ]
    }
  ]
}
```

Both `origin` and `title` are accepted on an item (whichever reads more
naturally for that method — "Amsterdam" for a train row, "UNESCO Welterbe
Zollverein" for the car row); the UI displays whichever is present, `origin`
taking precedence if both are set. `duration_hint` and `notes` are optional
(`null` or omitted). `countries` is an optional array, empty/absent means the
row never gets a personalization boost (used for the plane/car/bus rows,
which are not tied to a specific home country).

**Multi-event from day one.** `event_id` is a top-level field even though v1
ships exactly one event's data, matching the multi-event goal in
`docs/roadmap.md` Stage 4. The file itself does not yet key on it (there is
one file, one event) — see section 5 for what changes when a second event
needs its own "Getting there" content.

## 3. Method sections are data-driven, not hardcoded

The UI has **no knowledge of which methods exist.** It renders one pill/tab
per entry in `methods`, in `position` order, using `label` as the visible
text and `id` only as a React key and for icon lookup (an unrecognized `id`
falls back to a generic pin icon rather than failing to render — a new
method never needs a matching code change to show up, only a nicer icon is a
nice-to-have follow-up). A festival with a ferry, a shared shuttle, or no car
route at all (city-center venue, no parking) renders exactly the methods
present in its own `getting-there.json`, nothing more.

This is enforced structurally, not just by convention: `GettingThere.tsx`
maps over `data.methods` with no `switch`/`if` on `id` anywhere in the render
path (see `apps/web/components/transport/GettingThere.tsx` — the only place
`method.id` is read for anything other than a React key is the icon lookup,
which has an explicit fallback case for unrecognized ids).

## 4. Content freshness rules

1. **No departure times, no platform numbers, no "next train."** Anything
   that changes with a timetable revision is out.
2. **Duration hints are ranges or rough figures**, sourced from the general
   shape of the route (direct vs. one change), not a specific train's
   schedule. "~2h15 direct, ~3h with a change," not "arrives 16:22."
3. **Every row carries a `link` to the operator's own site — the source of
   truth for anything that actually needs a real date.** The row's job is to
   get someone to the right search box, not to replace it. Links point at the
   operator's stable route-search entry point (e.g. `nsinternational.com`'s
   Essen page, `bahn.de`'s homepage/journey planner) rather than a
   timetable-encoded deep link, precisely because a deep link is exactly the
   kind of thing that rots — a booking-engine URL scheme can change on the
   operator's side with no warning, while the operator's front door does not.
4. **Static file, deployed like `timetable-transport.json`.** No pipeline
   regenerates this content; a human edits the JSON and it ships on the next
   `git pull` (see section 5). There is no automated freshness check today —
   this is a known limitation, not an oversight: automating "is this still
   true" would require a language-model or scraper step, and coarse claims
   drift slowly enough (a booking link outliving a URL redesign is the
   dominant failure mode, not "Amsterdam is no longer ~2h15 from Essen") that
   a human editing pass before each event is judged sufficient for v1.

## 5. Ownership (v1)

**A hand-maintained JSON file in the repo**
(`services/companion/static/getting-there.json`), deployed exactly like
`services/companion/static/timetable-transport.json`: it lives under the
`static/` tree, which is bind-mounted and git-tracked, so a content edit ships
via `git pull` with no container rebuild, no API-route change, no redeploy of
`apps/web`. This is a deliberate, minimal v1 — organizer self-service is a
separate, larger decision.

**This doc does not decide the DB-backed / organizer-dashboard question.**
That question is explicitly ADR 0004's territory (`docs/adr/0004-organizer-dashboard-owner.md`,
currently "Proposed," not yet decided) and `docs/roadmap.md` Stage 4's
multi-event data model workstream, which already names
`timetable-transport.json` itself as a file that migrates off hand-editing
into config-driven rows at that stage. "Getting there" content is the same
shape of problem (durable, festival-specific, edited occasionally, not by the
pipeline) and should migrate on the same timeline, into whatever surface ADR
0004 lands on (Payload collections is the current leaning, not yet decided).
**Nothing in this feature should be read as pre-deciding that ADR** — v1
intentionally reuses the exact mechanism `timetable-transport.json` already
uses today, so the migration path (if and when ADR 0004 resolves) is
identical for both files, not a new bespoke one invented here.

## 6. Explicit exclusions

- **Full door-to-door journey planning.** Not built — this page curates
  method-level options and hands off to the operator's own planner (NS
  International, bahn.de, SNCF Connect, Google Maps), which already solves
  per-date/per-time routing correctly. Duplicating that is a maintenance
  burden with no accuracy upside.
- **Live flight status/times.** Not built — flight schedules and delays are a
  live-data problem (like the tram/train boards), which is exactly what this
  section is designed to avoid. A traveler already has an airline app or
  boarding pass for that; this page only needs to say "DUS is the primary
  airport, here's how to get from there to the venue."
- **Taxi/rideshare pricing.** Not built — prices are volatile (surge pricing,
  fuel costs, seasonal demand) and this page has no way to keep them current.
  Nothing stops a future ride-sharing pillar (`docs/roadmap.md` Stage 4 lists
  "ride sharing" as a planned pillar) from covering this properly with live
  matching instead of a stale number.

## 7. Ordering personalization v1

Within each method's item list, rows whose `countries` array contains the
visitor's inferred country sort first (stable sort otherwise — ties keep
their `items` array order) and get a subtle highlight (left accent border +
slightly tinted background, not a badge or icon, so it reads as "this one's
for you" without shouting).

**Country inference (v1): browser language only.** The client reads
`navigator.language` (e.g. `"nl-NL"`, `"de-DE"`, `"en-GB"`) client-side (no
SSR — resolving it during render would require reading the `Accept-Language`
request header, and static-friendly consistency with the existing `/transport`
page's client-first-load pattern was preferred over a server-side branch for
this one heuristic). The region subtag is used directly when present
(`nl-NL` → `NL`); for the handful of common tags that ship without one
(`de`, `fr`, `nl`, `it`, `es`), a small fallback map assigns the obvious
country. Anything else (no match, unparseable, `navigator.language`
unavailable) simply personalizes nothing — every row keeps its data-file
order, which is itself a reasonable default (train rows are already ordered
closest/most-connected first in the JSON).

**This is a deliberately weak signal**, acknowledged up front: browser
language correlates with home country but is not it (a Dutch expat living in
Berlin with an `en-GB` OS locale gets no boost; a tourist with a `de-DE`
laptop language gets one they don't need). It is used anyway for v1 because
it requires zero user state and zero backend — a client-only heuristic that
degrades gracefully to "no personalization" is strictly better than nothing,
and the far more accurate signal already exists elsewhere in the product.

**Documented future upgrade, not built now:** the chat profile already
collects a real, user-confirmed `country` field (`CLAUDE.md` "Profile Setup"
— `users.country`, searchable dropdown, explicit selection, not inferred).
A logged-in visitor's chat-profile country is a strictly better signal than
their browser language and should supersede it once available. This is
**not implemented in v1** — `/transport` has no dependency on chat
auth/session state today, and wiring that in is a real scope increase (would
need `/transport` to read the chat session cookie, call a chat API for the
profile, handle signed-out visitors, etc.) that this feature does not need to
ship. Flagged here so it isn't rediscovered as a surprise gap later.

## 8. Future: affiliate links

Every row's `link` is currently a plain outbound URL with no tracking or
revenue attached. If the product ever wants to monetize outbound booking
clicks (train/flight affiliate programs exist for exactly this kind of
referral), the natural seam is the `link` field itself: swap the raw operator
URL for an affiliate-wrapped one per item, with `link_label` unchanged so the
visitor-facing copy doesn't need to change. This is **not built or decided
now** — no affiliate program is integrated, no tracking parameters are added,
and no consent/disclosure UI exists. Noted only so the schema isn't
accidentally designed in a way that would make this harder later (it isn't:
`link` is already just a URL string, nothing about the schema needs to change
to support this).

## 9. Facts verified vs. omitted

Every coarse claim in `getting-there.json` was checked against a live web
search on 2026-07-20 before being written down; anything that couldn't be
corroborated was left out entirely rather than guessed. See the companion
report in the implementing commit/PR for the specific sources used per row
(train durations, the Zollverein visitor address, airport transfer times).
Notably **omitted for lack of a solid source**: exact CGN→Essen and
DTM→Essen end-to-end minute counts (only the individual legs — airport to
that city's Hbf — were independently confirmed, and chaining them into one
"total minutes" figure would be exactly the kind of precise-sounding number
this doc's freshness rules argue against; the JSON instead describes the
transfer *shape* — "change at Köln Hbf," "shuttle to Dortmund Hbf" — and lets
the duration stay a hint, not a promise).

## Decision: unified method layout (2026-07-20)

The two-section page described above (live departure boards on top, this
section collapsed below) is retired in favor of ONE top-level method picker:
a single tab bar — Train | Plane | Car | Bus | Local transit — where exactly
one panel renders below it. `apps/web/components/transport/MethodPicker.tsx`
now owns the whole `/transport` page; `GettingThere.tsx` and the old
route-switching `TransportBoard.tsx` are gone, replaced by `MethodPicker.tsx`
and a route-fixed `LiveBoard.tsx` (the same live-board component, mounted
once full-panel under "Local transit" and once embedded inline under
"Plane").

- **Tabs are still data-driven**: `Train`/`Plane`/`Car`/`Bus` come from
  `getting-there.json`'s `methods` array exactly as before (section 3 above
  is unchanged). `Local transit` is appended after them — it has no curated
  items, only the live tram board, so it isn't and can't be represented in
  `getting-there.json`.
- **Plane's Duesseldorf row expands inline.** Any Plane item whose `link`
  resolves to the Duesseldorf route (currently just the DUS row) renders as
  an expand/collapse toggle instead of an outbound link; expanding it mounts
  the live airport board (`LiveBoard route="duesseldorf" embedded`) directly
  inside the row, both directions, swap included. CGN/DTM keep their plain
  outbound links, unchanged.
- **Smart default.** On load, if no explicit `?route=` or `?method=` is
  present, the page opens on **Local transit** during the festival window,
  else **Train**. The window is derived from `timetable-transport.json`
  itself — the earliest day present across both boards, minus one day (for a
  fly-in arriving the evening before), through the latest day present — not
  hardcoded. `apps/web/lib/transport/logic.ts`'s `festivalDateWindow()` /
  `isWithinFestivalWindow()` implement this; `MethodPicker.tsx` logs the
  decision via `dbg()`.
- **URL contract**: the existing `?route=` slugs (and every legacy alias)
  resolve exactly as before and now also select the right tab — a
  Zollverein-mapped slug opens Local transit, a Duesseldorf-mapped slug opens
  Plane with the board pre-expanded. The active method is independently
  shareable via `?method=` (e.g. `?method=car`). An explicit, recognized
  `?route=` always wins over `?method=` when both are present. An
  unrecognized `?route=` (or none at all) falls through to `?method=`, then
  to the smart default — this already matched the pre-existing "unrecognized
  route falls back to default exactly like no param at all" behavior
  (`docs/parity/transport.md` #26); it now simply means the default itself is
  smart-default-driven rather than hardcoded to the Zollverein board.
- **No change inside either live board.** Both directions, the swap icon,
  day tabs, realtime polling, and walk time all behave exactly as documented
  in `docs/parity/transport.md` — only the chrome around them (the old
  itinerary quick-switch buttons in the sticky header) is gone, since
  switching itineraries is now the top-level tab bar's job.
