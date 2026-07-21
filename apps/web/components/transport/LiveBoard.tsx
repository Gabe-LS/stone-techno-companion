"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import styles from "./LiveBoard.module.css";
import DayTabs from "./DayTabs";
import DepartureList from "./DepartureList";
import { ArrowRightIcon, DirectionSwapIcon } from "./icons";
import Button from "../ui/Button";
import IconButton from "../ui/IconButton";
import { dbg } from "../../lib/debug";
import {
  buildRows,
  curBaseBlock,
  curViewBlock,
  fmtDist,
  fmtNum,
  findTodayIndex,
  getNow,
  haversineKm,
  isSameCalendarDate,
  parseDate,
  routeSlugFor,
  rtKey,
  WALK_SPEED_KMH,
} from "../../lib/transport/logic";
import type {
  Direction,
  RealtimeEntry,
  RouteKey,
  TimetableData,
  TransportDay,
  TransportViewBlock,
} from "../../lib/transport/types";

const POLL_INTERVAL_MS = 90000;
const LOCAL_RENDER_INTERVAL_MS = 30000;

interface LiveBoardProps {
  // Fixed for the lifetime of this component -- unlike the pre-restructure
  // TransportBoard, a LiveBoard no longer switches between itineraries
  // itself (that's now the method picker's job, one level up). It only
  // toggles inbound/outbound within this one itinerary.
  route: RouteKey;
  initialDirection: Direction;
  dateOverride: string | null;
  timeOverride: string | null;
  // True when mounted inline inside the Plane tab's Duesseldorf airport row
  // rather than as the whole "Local transit" tab panel -- drops the
  // page-level max-width/margin and the sticky positioning, both of which
  // only make sense when this board owns the top of the viewport.
  embedded?: boolean;
}

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

// Umami analytics: no-ops if the script failed to load / is blocked, exactly
// like the legacy page's local track() helper. docs/parity/transport.md #225.
function track(event: string, data?: Record<string, unknown>) {
  const w = window as unknown as { umami?: { track: (e: string, d?: Record<string, unknown>) => void } };
  if (w.umami) w.umami.track(event, data);
}

export default function LiveBoard({ route, initialDirection, dateOverride, timeOverride, embedded = false }: LiveBoardProps) {
  const router = useRouter();
  const pathname = usePathname();

  const [data, setData] = useState<TimetableData | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [direction, setDirection] = useState<Direction>(initialDirection);
  const [activeDay, setActiveDay] = useState(0);

  const [realtimeCache, setRealtimeCache] = useState<Record<string, RealtimeEntry>>({});
  const [realtimeSpillover, setRealtimeSpillover] = useState<RealtimeEntry[]>([]);
  const [realtimeActive, setRealtimeActive] = useState(false);
  const [liveUpdatedText, setLiveUpdatedText] = useState("");
  const [, setTick] = useState(0);

  const [walkMinutes, setWalkMinutes] = useState<number | null>(null);
  const [lastGps, setLastGps] = useState<{ lat: number; lng: number } | null>(null);
  const [bannerText, setBannerText] = useState("Walk time unknown");
  const [locateLabel, setLocateLabel] = useState("Locate me");
  const [locateDisabled, setLocateDisabled] = useState(false);

  const [stuck, setStuck] = useState(false);

  // Refs mirror the latest state for the persistent 90s/30s intervals, which
  // are created once on mount (see effect below) and must always read the
  // CURRENT direction/activeDay/data, not whatever was current when the
  // interval was created.
  const directionRef = useRef(direction);
  const activeDayRef = useRef(activeDay);
  const dataRef = useRef(data);
  const realtimeActiveRef = useRef(realtimeActive);
  // Refs must not be written during render (React 19 rule) -- sync them
  // right after every commit instead. The 90s/30s intervals below only ever
  // read these asynchronously (well after the render that set them), so a
  // post-commit sync is not a meaningful timing change from an inline
  // assignment.
  useEffect(() => {
    directionRef.current = direction;
    activeDayRef.current = activeDay;
    dataRef.current = data;
    realtimeActiveRef.current = realtimeActive;
  });

  const stickyRef = useRef<HTMLDivElement>(null);
  const nextRowEl = useRef<HTMLLIElement | null>(null);

  // --- Realtime fetch (section 3/4) --------------------------------------

  const fetchRealtime = useCallback(
    async (fetchDirection: Direction, dayData: TransportDay | undefined) => {
      if (!dayData) return;
      const now = getNow(dateOverride, timeOverride);
      const isToday = isSameCalendarDate(parseDate(dayData.date), now);
      if (!isToday) {
        setRealtimeActive(false);
        return;
      }

      const timeStr = `${pad2(now.hours)}:${pad2(now.minutes)}`;
      const dateStr = `${pad2(now.day)}.${pad2(now.month)}.${now.year}`;

      try {
        const res = await fetch(
          `/api/transport/departures?date=${encodeURIComponent(dateStr)}&time=${encodeURIComponent(timeStr)}&dir=${fetchDirection}&route=${route}&limit=5`,
        );
        const result: { departures?: RealtimeEntry[]; ts: string } = await res.json();
        if (!result.departures) return;

        setRealtimeActive(true);

        const ts = new Date(result.ts);
        setLiveUpdatedText(
          `Updated ${pad2(ts.getDate())}/${pad2(ts.getMonth() + 1)}/${ts.getFullYear()} at ${pad2(ts.getHours())}:${pad2(ts.getMinutes())}`,
        );

        const additions: Record<string, RealtimeEntry> = {};
        const spillover: RealtimeEntry[] = [];
        for (const rt of result.departures) {
          additions[rtKey(rt.line, rt.scheduled, rt.scheduledDate)] = rt;
          if (rt.scheduledDate && rt.scheduledDate !== dateStr && rt.real) spillover.push(rt);
        }
        // Cache accumulates across polls (only wiped on direction toggle);
        // spillover is replaced wholesale each fetch. docs/parity/transport.md #117-118.
        setRealtimeCache((prev) => ({ ...prev, ...additions }));
        setRealtimeSpillover(spillover);
      } catch {
        setRealtimeActive(false);
      }
    },
    [route, dateOverride, timeOverride],
  );

  // --- Initial data load ---------------------------------------------------

  useEffect(() => {
    let cancelled = false;
    fetch("/timetable-transport.json")
      .then((r) => r.json())
      .then((d: TimetableData) => {
        if (cancelled) return;
        if (route === "duesseldorf" && !d.duesseldorf) {
          setLoadError(true);
          return;
        }
        setData(d);
        const view = curViewBlock(d, route, initialDirection);
        const now = getNow(dateOverride, timeOverride);
        const idx = findTodayIndex(view.days, now);
        setActiveDay(idx);
        void fetchRealtime(initialDirection, view.days[idx]);
      })
      .catch(() => {
        if (!cancelled) setLoadError(true);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // --- Persistent polling: 90s network re-fetch, 30s local re-render ------

  useEffect(() => {
    const pollId = setInterval(() => {
      if (!dataRef.current) return;
      const view = curViewBlock(dataRef.current, route, directionRef.current);
      void fetchRealtime(directionRef.current, view.days[activeDayRef.current]);
    }, POLL_INTERVAL_MS);

    const renderId = setInterval(() => {
      if (dataRef.current && !realtimeActiveRef.current) setTick((t) => t + 1);
    }, LOCAL_RENDER_INTERVAL_MS);

    return () => {
      clearInterval(pollId);
      clearInterval(renderId);
    };
  }, [route, fetchRealtime]);

  // --- Walk time (section 5) ------------------------------------------------

  const fetchWalkRoute = useCallback(async (lat: number, lng: number, walkDirection: Direction) => {
    try {
      const res = await fetch(
        `/api/transport/walk?lat=${encodeURIComponent(lat)}&lng=${encodeURIComponent(lng)}&dir=${walkDirection}&route=${route}`,
      );
      if (!res.ok) return null;
      const d = await res.json();
      if (d.distanceM == null) return null;
      return { distanceM: d.distanceM as number, durationS: d.durationS as number };
    } catch {
      return null;
    }
  }, [route]);

  const updateLocation = useCallback(
    async (lat: number, lng: number, viewDirection: Direction, view: TransportViewBlock, fallbackStop: { lat: number; lng: number }) => {
      setLastGps({ lat, lng });
      setBannerText("Calculating route...");

      const result = await fetchWalkRoute(lat, lng, viewDirection);
      if (result) {
        const mins = result.durationS ? Math.ceil(result.durationS / 60) : Math.ceil((result.distanceM / 1000 / WALK_SPEED_KMH) * 60);
        setWalkMinutes(mins);
        setBannerText(`${fmtDist(result.distanceM)} / ${fmtNum(mins)} min to ${view.route.from}`);
        setLocateLabel("Update");
        return;
      }

      const stop = view.stop || fallbackStop;
      const distKm = haversineKm(lat, lng, stop.lat, stop.lng);
      if (distKm > 20) {
        setWalkMinutes(null);
        setBannerText("You are too far from the stop");
      } else {
        const mins = Math.ceil((distKm / WALK_SPEED_KMH) * 60);
        setWalkMinutes(mins);
        setBannerText(`~${fmtDist(distKm * 1000)} / ~${fmtNum(mins)} min to ${view.route.from}`);
      }
      setLocateLabel("Update");
    },
    [fetchWalkRoute],
  );

  const onLocateClick = useCallback(() => {
    dbg("[TRANSPORT] locate-me click");
    if (typeof navigator === "undefined" || !navigator.geolocation) {
      setBannerText("Geolocation not supported");
      return;
    }
    setLocateLabel("...");
    setLocateDisabled(true);
    track("transport-locate");

    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setLocateDisabled(false);
        if (!data) return;
        const view = curViewBlock(data, route, direction);
        void updateLocation(pos.coords.latitude, pos.coords.longitude, direction, view, data.stop);
      },
      () => {
        setLocateDisabled(false);
        setLocateLabel("Retry");
        setBannerText("Location access denied");
      },
      { enableHighAccuracy: true, timeout: 10000 },
    );
  }, [data, route, direction, updateLocation]);

  // --- Direction toggle (section 1/4/5) -------------------------------------

  const toggleDirection = useCallback(() => {
    if (!data) return;
    const base = curBaseBlock(data, route);
    if (!base.reverse) return;

    const newDirection: Direction = direction === "outbound" ? "inbound" : "outbound";
    const newSlug = routeSlugFor(route, newDirection);
    dbg("[TRANSPORT] toggle direction ->", newDirection);
    track("transport-direction", { route: newSlug });

    const newView = curViewBlock(data, route, newDirection);
    const newActiveDay = activeDay >= newView.days.length ? 0 : activeDay;

    setDirection(newDirection);
    setActiveDay(newActiveDay);
    // A stale cache entry from the other direction could otherwise
    // coincidentally match by line+time+date, so drop it entirely on switch.
    setRealtimeCache({});
    setRealtimeSpillover([]);
    setRealtimeActive(false);

    // Always keep the URL in sync, embedded or not -- when embedded (the
    // Duesseldorf board expanded inline under the Plane tab), the route
    // param is already present (that's what triggered the expansion), so
    // this just updates its direction half; when swapping collapses the
    // board back to "essen-*" the param stays valid and shareable either way.
    const sp = new URLSearchParams(window.location.search);
    sp.set("route", newSlug);
    sp.delete("method");
    router.replace(`${pathname}?${sp.toString()}`, { scroll: false });
    window.dispatchEvent(new Event("stc:transport-route-change"));

    if (lastGps) {
      void updateLocation(lastGps.lat, lastGps.lng, newDirection, newView, data.stop);
    } else {
      setWalkMinutes(null);
      setBannerText("Walk time unknown");
      setLocateLabel("Locate me");
    }

    void fetchRealtime(newDirection, newView.days[newActiveDay]);
  }, [data, route, direction, activeDay, lastGps, pathname, router, updateLocation, fetchRealtime]);

  const onTabClick = useCallback(
    (idx: number, tabText: string) => {
      dbg("[TRANSPORT] day tab click ->", idx, tabText);
      track("transport-day", { day: tabText });
      setActiveDay(idx);
      if (data) {
        const view = curViewBlock(data, route, direction);
        void fetchRealtime(direction, view.days[idx]);
      }
    },
    [data, route, direction, fetchRealtime],
  );

  // --- Sticky header "stuck" state (fade-after gradient) --------------------

  useEffect(() => {
    if (embedded) return;
    const el = stickyRef.current;
    if (!el || !el.parentNode) return;
    const sentinel = document.createElement("div");
    sentinel.style.cssText = "height:0;width:0;pointer-events:none;visibility:hidden;position:relative;";
    el.parentNode.insertBefore(sentinel, el);

    function place() {
      sentinel.style.top = "-" + (parseFloat(getComputedStyle(el as HTMLDivElement).top) || 0) + "px";
    }
    place();
    window.addEventListener("resize", place);

    const observer = new IntersectionObserver(
      (entries) => setStuck(entries[0].intersectionRatio === 0),
      { threshold: 0 },
    );
    observer.observe(sentinel);

    return () => {
      window.removeEventListener("resize", place);
      observer.disconnect();
      sentinel.remove();
    };
  }, [embedded]);

  // --- Title (section 10) ---------------------------------------------------

  const view: TransportViewBlock | null = data ? curViewBlock(data, route, direction) : null;

  // Tab title: owned entirely by generateMetadata in app/transport/page.tsx,
  // derived from the URL on every soft navigation. No client-side
  // document.title here: it loses the race against Next's metadata re-apply
  // (the stale-title-after-swap bug).

  // --- Auto-scroll to the "next" departure row (section 6) -------------------

  const days = view?.days ?? [];
  const dayData = days[activeDay];
  const now = getNow(dateOverride, timeOverride);
  const isToday = dayData ? isSameCalendarDate(parseDate(dayData.date), now) : false;
  const rows = useMemo(
    () => (dayData ? buildRows(dayData, isToday, now.nowMinutes, realtimeCache, realtimeSpillover, walkMinutes) : []),
    [dayData, isToday, now.nowMinutes, realtimeCache, realtimeSpillover, walkMinutes],
  );

  useEffect(() => {
    if (!nextRowEl.current) return;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const target = nextRowEl.current;
    const id = setTimeout(() => {
      const sticky = stickyRef.current;
      const stickyH = sticky ? sticky.offsetHeight + (parseFloat(getComputedStyle(sticky).top) || 0) : 0;
      const targetTop = window.scrollY + target.getBoundingClientRect().top;
      window.scrollTo({ top: targetTop - stickyH - 30, behavior: reduced ? "auto" : "smooth" });
    }, 100);
    return () => clearTimeout(id);
  }, [rows]);

  const showReverse = Boolean(data && curBaseBlock(data, route).reverse);

  return (
    <div className={embedded ? `${styles.page} ${styles.embedded}` : styles.page}>
      <div
        ref={stickyRef}
        className={`${embedded ? styles.stickyEmbedded : styles.stickyTop} ${embedded ? "" : styles.fadeAfter} ${stuck ? styles.stuck : ""}`}
      >
        <div className={styles.routeTitle}>
          <div className={styles.routeTitleMain}>
            <span>
              {view ? view.route.from : "Zollverein"}{" "}
              <span className={styles.routeArrow}>
                <ArrowRightIcon />
              </span>{" "}
              {view ? view.route.to : "Essen Hbf"}
            </span>
            {showReverse && (
              <IconButton className={styles.dirToggle} variant="inline" ariaLabel="Show the opposite direction" onClick={toggleDirection}>
                <DirectionSwapIcon />
              </IconButton>
            )}
          </div>
        </div>

        <div className={styles.locationBanner}>
          <span className={styles.locationText}>{bannerText}</span>
          <Button className={styles.locateBtn} onClick={onLocateClick} disabled={locateDisabled}>
            {locateLabel}
          </Button>
        </div>

        <DayTabs days={days} activeDay={activeDay} onSelect={onTabClick} />

        {/* Rendered only when viewing today: on any other day realtime can
            never activate, and the empty row was a 14px ghost gap between
            the day tabs and the list (DESIGN-STANDARDS.md section 4). On
            today it stays mounted even while inactive so the sticky header
            does not jump when the LIVE label appears. */}
        {isToday && (
          <div className={`${styles.liveIndicator} ${realtimeActive ? styles.active : ""}`}>
            <span className={styles.liveDot} />
            <span className={styles.liveUpdated}>{liveUpdatedText || " "}</span>
          </div>
        )}
      </div>

      <div>
        {loadError ? (
          <div className={styles.emptyState}>Could not load timetable data.</div>
        ) : dayData && dayData.departures.length === 0 ? (
          <div className={styles.emptyState}>No departures available</div>
        ) : dayData ? (
          <DepartureList
            rows={rows}
            route={route}
            isToday={isToday}
            nowMin={now.nowMinutes}
            walkMinutes={walkMinutes}
            nextRowRef={(el) => {
              nextRowEl.current = el;
            }}
          />
        ) : null}
      </div>
    </div>
  );
}
