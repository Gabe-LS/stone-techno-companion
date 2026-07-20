// Pure logic ported from services/companion/static/pages/transport.html's
// inline <script>. Kept framework-free (no React, no browser APIs beyond
// plain Date/Math) so it runs identically on the server (initial route
// resolution in app/transport/page.tsx) and in the client component, and so
// it can be unit-exercised directly. See docs/parity/transport.md sections
// 1, 4, 6, 7 for the behavior this ports.

import type {
  Direction,
  RealtimeEntry,
  RouteKey,
  TimetableData,
  TransportBaseBlock,
  TransportDay,
  TransportViewBlock,
} from "./types";

// --- Section 1: routing -----------------------------------------------

export const DEFAULT_ROUTE: RouteKey = "zollverein";
export const DEFAULT_DIRECTION: Direction = "outbound";

// ?route= encodes both the itinerary and the direction as one slug.
// Legacy aliases (duesseldorf-essen, essen-duesseldorf, bare duesseldorf)
// are kept even though no UI control surfaces them.
export const ROUTE_SLUGS: Record<string, [RouteKey, Direction]> = {
  "zollverein-essen": ["zollverein", "outbound"],
  "essen-zollverein": ["zollverein", "inbound"],
  "dus-airport-essen": ["duesseldorf", "outbound"],
  "essen-dus-airport": ["duesseldorf", "inbound"],
  "duesseldorf-essen": ["duesseldorf", "outbound"],
  "essen-duesseldorf": ["duesseldorf", "inbound"],
  duesseldorf: ["duesseldorf", "outbound"],
};

export interface ResolvedRoute {
  route: RouteKey;
  direction: Direction;
}

// An unrecognized slug (or none) falls back to the default, exactly like no
// param at all (docs/parity/transport.md #26).
export function resolveRouteSlug(slug: string | null | undefined): ResolvedRoute {
  const resolved = slug ? ROUTE_SLUGS[slug] : undefined;
  if (!resolved) return { route: DEFAULT_ROUTE, direction: DEFAULT_DIRECTION };
  return { route: resolved[0], direction: resolved[1] };
}

// Canonical slug for a (route, direction) pair, for URL sync + nav highlighting.
export function routeSlugFor(route: RouteKey, direction: Direction): string {
  if (route === "duesseldorf") {
    return direction === "inbound" ? "essen-dus-airport" : "dus-airport-essen";
  }
  return direction === "inbound" ? "essen-zollverein" : "zollverein-essen";
}

// --- Date/time helpers ---------------------------------------------------

export interface ParsedDate {
  day: number;
  month: number;
  year: number;
}

// "DD.MM.YYYY" -> {day, month, year}
export function parseDate(dateStr: string): ParsedDate {
  const p = dateStr.split(".");
  return { day: parseInt(p[0], 10), month: parseInt(p[1], 10), year: parseInt(p[2], 10) };
}

// "HH:MM" -> minutes since midnight
export function depToMinutes(dep: string): number {
  const p = dep.split(":");
  return parseInt(p[0], 10) * 60 + parseInt(p[1], 10);
}

export interface NowInfo {
  year: number;
  month: number;
  day: number;
  hours: number;
  minutes: number;
  nowMinutes: number;
}

// Mirrors the legacy page's getNow(): a ?date=DD.MM.YYYY&time=HH:MM URL
// override (both must be present) makes every time-relative computation
// deterministic for tests; otherwise the real wall clock is used.
// docs/parity/transport.md #115.
export function getNow(dateOverride?: string | null, timeOverride?: string | null): NowInfo {
  let now: Date;
  if (dateOverride && timeOverride) {
    const dp = dateOverride.split(".");
    const tp = timeOverride.split(":");
    now = new Date(
      parseInt(dp[2], 10),
      parseInt(dp[1], 10) - 1,
      parseInt(dp[0], 10),
      parseInt(tp[0], 10),
      parseInt(tp[1], 10),
    );
  } else {
    now = new Date();
  }
  return {
    year: now.getFullYear(),
    month: now.getMonth() + 1,
    day: now.getDate(),
    hours: now.getHours(),
    minutes: now.getMinutes(),
    nowMinutes: now.getHours() * 60 + now.getMinutes(),
  };
}

export function isSameCalendarDate(d: ParsedDate, now: NowInfo): boolean {
  return d.day === now.day && d.month === now.month && d.year === now.year;
}

export function findTodayIndex(days: TransportDay[], now: NowInfo): number {
  for (let i = 0; i < days.length; i++) {
    if (isSameCalendarDate(parseDate(days[i].date), now)) return i;
  }
  return 0;
}

// --- Section 5: walk time --------------------------------------------------

export function haversineKm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const R = 6371;
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLon = ((lon2 - lon1) * Math.PI) / 180;
  const a =
    Math.sin(dLat / 2) * Math.sin(dLat / 2) +
    Math.cos((lat1 * Math.PI) / 180) *
      Math.cos((lat2 * Math.PI) / 180) *
      Math.sin(dLon / 2) *
      Math.sin(dLon / 2);
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

export const WALK_SPEED_KMH = 4.5;

export function fmtNum(n: number): string {
  return n.toLocaleString("de-DE");
}

export function fmtDist(meters: number): string {
  if (meters < 1000) return fmtNum(Math.round(meters)) + " m";
  return fmtNum(parseFloat((meters / 1000).toFixed(1))) + " km";
}

// --- Section 4: realtime cache key -----------------------------------------

export function rtKey(line: string, scheduledTime: string, scheduledDate: string): string {
  return line + "|" + scheduledTime + "|" + scheduledDate;
}

function realtimeDirection(rt: RealtimeEntry): string {
  return "direction" in rt && typeof rt.direction === "string" ? rt.direction : "";
}

// --- Section 7: departure row rendering ------------------------------------

export interface RowSource {
  dep: string;
  line: string;
  direction: string;
  platform?: string | null;
  arr?: string | null;
  badge?: string;
  nextDay?: boolean;
}

export interface RowViewModel {
  key: string;
  dep: RowSource;
  rt?: RealtimeEntry;
  effectiveMin: number;
  isCanceled: boolean;
  isPast: boolean;
  isTooLate: boolean;
  isNext: boolean;
  hasDelay: boolean;
  isOnTime: boolean;
}

interface RawEntry {
  dep: RowSource;
  rt?: RealtimeEntry;
  effectiveMin: number;
  isCanceled: boolean;
}

/**
 * Builds the sorted, per-row state for one day panel: static departures plus
 * (today only) realtime spillover, sorted by effective minute, with
 * is-past/is-next/is-too-late/is-canceled/hasDelay/isOnTime computed exactly
 * as the legacy renderPanel() does. Pure and side-effect free so it can be
 * unit tested and reused between render and the auto-scroll effect.
 */
export function buildRows(
  dayData: TransportDay,
  isToday: boolean,
  nowMin: number,
  realtimeCache: Record<string, RealtimeEntry>,
  realtimeSpillover: RealtimeEntry[],
  walkMinutes: number | null,
): RowViewModel[] {
  const raw: RawEntry[] = [];

  for (const dep of dayData.departures) {
    const offset = dep.nextDay ? 1440 : 0;
    const depMin = depToMinutes(dep.dep) + offset;
    const rt = realtimeCache[rtKey(dep.line, dep.dep, dayData.date)];
    const isCanceled = Boolean(rt && rt.status === "CANCELED");
    let effectiveMin = depMin;
    if (rt && rt.real && !isCanceled) {
      let rtMin = depToMinutes(rt.real) + offset;
      if (depMin - rtMin > 720) rtMin += 1440;
      effectiveMin = rtMin;
    }
    raw.push({ dep, rt, effectiveMin, isCanceled });
  }

  if (isToday) {
    for (const sp of realtimeSpillover) {
      const spDep: RowSource = {
        dep: sp.scheduled,
        line: sp.line,
        direction: realtimeDirection(sp),
        platform: "platform" in sp ? sp.platform ?? undefined : undefined,
      };
      const spEffective = sp.real ? depToMinutes(sp.real) : depToMinutes(sp.scheduled);
      raw.push({ dep: spDep, rt: sp, effectiveMin: spEffective, isCanceled: sp.status === "CANCELED" });
    }
  }

  raw.sort((a, b) => a.effectiveMin - b.effectiveMin);

  let nextFound = false;
  const rows: RowViewModel[] = [];

  raw.forEach((entry, i) => {
    const { dep, rt, effectiveMin, isCanceled } = entry;
    const hasDelay = Boolean(rt && typeof rt.delay === "number" && rt.delay > 0 && !isCanceled);
    const isOnTime = Boolean(rt && rt.realtime && rt.delay === 0 && !isCanceled);

    let isPast = false;
    let isTooLate = false;
    let isNext = false;

    if (isToday) {
      if (isCanceled) {
        isPast = true;
      } else if (effectiveMin < nowMin) {
        isPast = true;
      } else if (!nextFound) {
        if (walkMinutes !== null) {
          const leaveBy = effectiveMin - walkMinutes;
          if (leaveBy < nowMin) {
            isTooLate = true;
          } else {
            isNext = true;
            nextFound = true;
          }
        } else {
          isNext = true;
          nextFound = true;
        }
      }
    }

    rows.push({
      key: `${dep.line}|${dep.dep}|${i}`,
      dep,
      rt,
      effectiveMin,
      isCanceled,
      isPast,
      isTooLate,
      isNext,
      hasDelay,
      isOnTime,
    });
  });

  return rows;
}

// Right-column countdown/walk text priority (#182): realtime countdown wins,
// else the raw walk-derived minutes-to-departure, else nothing.
export function countdownText(row: RowViewModel, isToday: boolean, nowMin: number, walkMinutes: number | null): string | null {
  const rt = row.rt;
  if (rt && "countdown" in rt && rt.countdown != null && isToday && !row.isPast && !row.isCanceled) {
    return `arr. in ${rt.countdown} min`;
  }
  if (walkMinutes !== null && !row.isPast && isToday) {
    const minsLeft = row.effectiveMin - nowMin;
    if (minsLeft > 0) return `${minsLeft} min`;
  }
  return null;
}

// --- Route/direction -> data block resolution (curBase/curView in legacy) --

// The selected route's forward block: the Duesseldorf block if that route is
// chosen (and present in the data), else the top-level Zollverein block.
export function curBaseBlock(data: TimetableData, route: RouteKey): TransportBaseBlock {
  return route === "duesseldorf" && data.duesseldorf ? data.duesseldorf : data;
}

// The active "view" ({route, stop, days}): the reverse block when inbound,
// else the forward block itself.
export function curViewBlock(data: TimetableData, route: RouteKey, direction: Direction): TransportViewBlock {
  const base = curBaseBlock(data, route);
  return direction === "inbound" && base.reverse ? base.reverse : base;
}

// Destination text: strip the FIRST occurrence only of the literal "Essen "
// substring (String.replace with a string argument, not a regex, matches
// legacy exactly -- docs/parity/transport.md #179).
export function stripEssenPrefix(direction: string): string {
  return direction.replace("Essen ", "");
}

// day-tab date formatting (#152): dots -> slashes, abbreviated drops the year.
export function slashDate(date: string): string {
  return date.replace(/\./g, "/");
}

export function shortDate(date: string): string {
  return slashDate(date)
    .split("/")
    .slice(0, 2)
    .join("/");
}
