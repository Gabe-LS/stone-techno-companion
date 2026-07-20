"use client";

import { useEffect, useMemo, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import styles from "./MethodPicker.module.css";
import LiveBoard from "./LiveBoard";
import { BusIcon, CarIcon, ChevronDownIcon, PinIcon, PlaneIcon, TrainIcon, TransitIcon } from "./icons";
import Pill from "../ui/Pill";
import Button from "../ui/Button";
import ExternalLink from "../ui/ExternalLink";
import { dbg, setDbgTag } from "../../lib/debug";
import {
  LOCAL_TRANSIT_METHOD_ID,
  festivalDateWindow,
  getNow,
  gettingThereItemName,
  inferCountryFromLanguage,
  isWithinFestivalWindow,
  linkTargetsDuesseldorf,
  routeSlugFor,
  sortItemsByCountryBoost,
} from "../../lib/transport/logic";
import type {
  Direction,
  GettingThereData,
  GettingThereItem,
  GettingThereMethod,
  RouteKey,
  TimetableData,
} from "../../lib/transport/types";

// Unified method picker: ONE top-level tab bar (Train | Plane | Car | Bus |
// Local transit) replaces the old two-section layout (live boards on top, a
// separate collapsible "Getting there" section below). See
// docs/getting-there-design.md, "Decision: unified method layout".

interface MethodPickerProps {
  // From the initial ?route= resolution done server-side in page.tsx.
  initialRoute: RouteKey;
  initialDirection: Direction;
  // Non-null only when the URL carried an EXPLICIT, recognized ?route= slug
  // -- route slugs win over ?method=, and only that case can be resolved
  // server-side (it needs no fetched data). Null means "resolve client-side
  // once getting-there.json + timetable-transport.json have loaded", via
  // methodParam (?method=) or, failing that, the festival-window smart
  // default.
  initialMethodId: string | null;
  methodParam: string | null;
  dusExpandedInitial: boolean;
  dateOverride: string | null;
  timeOverride: string | null;
}

function track(event: string, data?: Record<string, unknown>) {
  const w = window as unknown as { umami?: { track: (e: string, d?: Record<string, unknown>) => void } };
  if (w.umami) w.umami.track(event, data);
}

function methodIcon(id: string) {
  switch (id) {
    case "train":
      return <TrainIcon />;
    case "plane":
      return <PlaneIcon />;
    case "car":
      return <CarIcon />;
    case "bus":
      return <BusIcon />;
    case LOCAL_TRANSIT_METHOD_ID:
      return <TransitIcon />;
    default:
      return <PinIcon />;
  }
}

function isInternalLink(href: string): boolean {
  return href.startsWith("/");
}

function ItemLink({ item }: { item: GettingThereItem }) {
  const name = gettingThereItemName(item);
  const internal = isInternalLink(item.link);

  function onLinkClick() {
    dbg("[TRANSPORT] getting-there item link click ->", item.link, name);
  }

  if (internal) {
    // No item in current data links internally except the DUS row, which is
    // handled separately (expand-inline, not a link) -- kept for parity
    // with any future internal getting-there link. Internal navigation
    // never leaves the site, so it renders as a Button (DESIGN-STANDARDS.md
    // #2: "if it stays on the page, it looks like a button"), not the
    // underlined ExternalLink style.
    return (
      <span className={styles.itemLinkWrap}>
        <Button href={item.link} onClick={onLinkClick}>
          {item.link_label}
        </Button>
      </span>
    );
  }
  return (
    <span className={styles.itemLinkWrap}>
      <ExternalLink href={item.link} onClick={onLinkClick}>
        {item.link_label}
      </ExternalLink>
    </span>
  );
}

// Train/Car/Bus (and any future non-live-board method): plain curated rows,
// country-boosted ordering, no special-casing of any item.
function MethodItemsPanel({ method, visitorCountry }: { method: GettingThereMethod; visitorCountry: string | null }) {
  const ranked = useMemo(() => sortItemsByCountryBoost(method.items, visitorCountry), [method.items, visitorCountry]);
  if (ranked.length === 0) return <p className={styles.emptyState}>No options listed yet.</p>;
  return (
    <ul className={styles.items}>
      {ranked.map(({ item, boosted }) => (
        <li key={`${gettingThereItemName(item)}|${item.link}`} className={`${styles.item} ${boosted ? styles.itemBoosted : ""}`}>
          <div className={styles.itemTop}>
            <span className={styles.itemName}>{gettingThereItemName(item)}</span>
            {item.duration_hint && <span className={styles.itemDuration}>{item.duration_hint}</span>}
          </div>
          <p className={styles.itemSummary}>{item.summary}</p>
          {item.notes && <p className={styles.itemNotes}>{item.notes}</p>}
          <ItemLink item={item} />
        </li>
      ))}
    </ul>
  );
}

// Plane: curated rows, but any item whose link targets the Duesseldorf
// itinerary (the airport row) expands inline into the live board instead of
// linking out. CGN/DTM (and anything else with a plain outbound link) stay
// coarse rows, same as Train/Car/Bus.
function PlanePanel({
  method,
  visitorCountry,
  dusExpanded,
  onDusToggle,
  dusInitialDirection,
  dateOverride,
  timeOverride,
}: {
  method: GettingThereMethod;
  visitorCountry: string | null;
  dusExpanded: boolean;
  onDusToggle: () => void;
  dusInitialDirection: Direction;
  dateOverride: string | null;
  timeOverride: string | null;
}) {
  const ranked = useMemo(() => sortItemsByCountryBoost(method.items, visitorCountry), [method.items, visitorCountry]);
  if (ranked.length === 0) return <p className={styles.emptyState}>No options listed yet.</p>;
  return (
    <ul className={styles.items}>
      {ranked.map(({ item, boosted }) => {
        const expandable = linkTargetsDuesseldorf(item.link);
        return (
          <li key={`${gettingThereItemName(item)}|${item.link}`} className={`${styles.item} ${boosted ? styles.itemBoosted : ""}`}>
            <div className={styles.itemTop}>
              <span className={styles.itemName}>{gettingThereItemName(item)}</span>
              {item.duration_hint && <span className={styles.itemDuration}>{item.duration_hint}</span>}
            </div>
            <p className={styles.itemSummary}>{item.summary}</p>
            {item.notes && <p className={styles.itemNotes}>{item.notes}</p>}
            {expandable ? (
              <>
                <Button
                  className={styles.expandBtn}
                  aria-expanded={dusExpanded}
                  onClick={onDusToggle}
                >
                  {item.link_label}
                  <span className={`${styles.chevron} ${dusExpanded ? styles.chevronOpen : ""}`}>
                    <ChevronDownIcon />
                  </span>
                </Button>
                {dusExpanded && (
                  <div className={styles.embeddedBoard}>
                    <LiveBoard
                      route="duesseldorf"
                      initialDirection={dusInitialDirection}
                      dateOverride={dateOverride}
                      timeOverride={timeOverride}
                      embedded
                    />
                  </div>
                )}
              </>
            ) : (
              <ItemLink item={item} />
            )}
          </li>
        );
      })}
    </ul>
  );
}

export default function MethodPicker({
  initialRoute,
  initialDirection,
  initialMethodId,
  methodParam,
  dusExpandedInitial,
  dateOverride,
  timeOverride,
}: MethodPickerProps) {
  const router = useRouter();
  const pathname = usePathname();

  const [gtData, setGtData] = useState<GettingThereData | null>(null);
  const [gtLoadError, setGtLoadError] = useState(false);
  const [ttData, setTtData] = useState<TimetableData | null>(null);
  const [ttLoadError, setTtLoadError] = useState(false);

  const [activeMethodId, setActiveMethodId] = useState<string | null>(initialMethodId);
  const [dusExpanded, setDusExpanded] = useState(dusExpandedInitial);
  const [visitorCountry, setVisitorCountry] = useState<string | null>(null);

  useEffect(() => {
    setDbgTag("transport");
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetch("/getting-there.json")
      .then((r) => r.json())
      .then((d: GettingThereData) => {
        if (cancelled) return;
        setGtData({ ...d, methods: [...d.methods].sort((a, b) => a.position - b.position) });
      })
      .catch(() => {
        if (!cancelled) setGtLoadError(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Fetched separately from LiveBoard's own copy, purely to compute the
  // smart-default festival window before any board mounts (only the active
  // tab's panel -- and therefore its LiveBoard -- is ever mounted, so this
  // component can't wait for a LiveBoard to tell it the window).
  useEffect(() => {
    let cancelled = false;
    fetch("/timetable-transport.json")
      .then((r) => r.json())
      .then((d: TimetableData) => {
        if (!cancelled) setTtData(d);
      })
      .catch(() => {
        if (!cancelled) setTtLoadError(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // navigator.language rarely changes mid-session, but the 'languagechange'
  // event is the correct external system to subscribe to rather than
  // reading it once unconditionally.
  useEffect(() => {
    function apply() {
      setVisitorCountry(inferCountryFromLanguage(typeof navigator !== "undefined" ? navigator.language : null));
    }
    apply();
    if (typeof window === "undefined") return;
    window.addEventListener("languagechange", apply);
    return () => window.removeEventListener("languagechange", apply);
  }, []);

  // Resolve the active method when the URL didn't carry an explicit ?route=
  // (that case is already resolved server-side into initialMethodId). This
  // MUST be committed into activeMethodId state exactly once, rather than
  // left as a value re-derived from methodParam on every render: methodParam
  // is a page PROP, and it changes as soon as syncUrl() rewrites the URL
  // (e.g. expanding the Plane tab's Duesseldorf row drops ?method= from the
  // URL in favor of ?route=) -- a purely derived value would then silently
  // re-resolve using the new (now-absent) methodParam and flip back to the
  // smart default out from under the user. The guard below (activeMethodId
  // !== null bails out) makes this a one-time reconciliation of async-loaded
  // data (gtData/ttData) into local state, which can't be done synchronously
  // during render since it depends on in-flight fetches completing.
  const knownMethodIds = useMemo(() => (gtData ? gtData.methods.map((m) => m.id) : []), [gtData]);
  const dataSettled = (gtData !== null || gtLoadError) && (ttData !== null || ttLoadError);
  useEffect(() => {
    if (activeMethodId !== null) return;
    if (!dataSettled) return;

    const methodParamValid = Boolean(
      methodParam && (knownMethodIds.includes(methodParam) || methodParam === LOCAL_TRANSIT_METHOD_ID),
    );
    if (methodParamValid && methodParam) {
      dbg("[TRANSPORT] method resolved from ?method= ->", methodParam);
      // One-time reconciliation of the async-loaded getting-there.json into
      // state, see the comment above.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setActiveMethodId(methodParam);
      return;
    }

    const now = getNow(dateOverride, timeOverride);
    const window_ = ttData ? festivalDateWindow(ttData) : null;
    const inWindow = window_ ? isWithinFestivalWindow(now, window_) : false;
    const smartId = inWindow ? LOCAL_TRANSIT_METHOD_ID : "train";
    dbg(
      "[TRANSPORT] smart default ->",
      smartId,
      inWindow ? "(within festival window)" : "(outside festival window)",
    );
    setActiveMethodId(smartId);
  }, [activeMethodId, dataSettled, knownMethodIds, methodParam, ttData, dateOverride, timeOverride]);

  const resolvedMethodId = activeMethodId;

  const tabs = useMemo(() => {
    const fromData = gtData ? gtData.methods.map((m) => ({ id: m.id, label: m.label })) : [];
    return [...fromData, { id: LOCAL_TRANSIT_METHOD_ID, label: "Local transit" }];
  }, [gtData]);

  function syncUrl(nextMethodId: string, nextDusExpanded: boolean) {
    const sp = new URLSearchParams(window.location.search);
    sp.delete("route");
    sp.delete("method");
    if (nextMethodId === LOCAL_TRANSIT_METHOD_ID) {
      sp.set("route", routeSlugFor("zollverein", "outbound"));
    } else if (nextMethodId === "plane" && nextDusExpanded) {
      sp.set("route", routeSlugFor("duesseldorf", "outbound"));
    } else {
      sp.set("method", nextMethodId);
    }
    const qs = sp.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    window.dispatchEvent(new Event("stc:transport-route-change"));
  }

  function onMethodClick(id: string) {
    dbg("[TRANSPORT] method tab click ->", id);
    track("transport-method", { method: id });
    const nextDusExpanded = id === "plane" ? dusExpanded : false;
    setActiveMethodId(id);
    setDusExpanded(nextDusExpanded);
    syncUrl(id, nextDusExpanded);
  }

  function onDusToggle() {
    const next = !dusExpanded;
    dbg("[TRANSPORT] DUS board toggle ->", next ? "expanded" : "collapsed");
    track("transport-dus-toggle", { expanded: next });
    setDusExpanded(next);
    syncUrl("plane", next);
  }

  const activeMethod = gtData?.methods.find((m) => m.id === resolvedMethodId);

  // Title for panels with no live board (LiveBoard sets its own title while
  // mounted, for both Local transit and an expanded Plane/DUS board).
  useEffect(() => {
    if (!resolvedMethodId) return;
    if (resolvedMethodId === LOCAL_TRANSIT_METHOD_ID) return;
    if (resolvedMethodId === "plane" && dusExpanded) return;
    document.title = `${activeMethod?.label ?? resolvedMethodId} · Transport`;
  }, [resolvedMethodId, dusExpanded, activeMethod]);

  return (
    <div className={styles.page}>
      <h1 className={styles.heading}>Transport</h1>

      {(gtData || gtLoadError) && (
        <div className={styles.methodTabs} role="tablist" aria-label="Transport method">
          {tabs.map((tab) => (
            <Pill
              key={tab.id}
              tier="primary"
              role="tab"
              aria-selected={tab.id === resolvedMethodId}
              active={tab.id === resolvedMethodId}
              onClick={() => onMethodClick(tab.id)}
            >
              {methodIcon(tab.id)}
              {tab.label}
            </Pill>
          ))}
        </div>
      )}

      <div className={styles.panel}>
        {resolvedMethodId === LOCAL_TRANSIT_METHOD_ID && (
          <LiveBoard
            route="zollverein"
            initialDirection={initialRoute === "zollverein" ? initialDirection : "outbound"}
            dateOverride={dateOverride}
            timeOverride={timeOverride}
          />
        )}

        {resolvedMethodId === "plane" && activeMethod && (
          <PlanePanel
            method={activeMethod}
            visitorCountry={visitorCountry}
            dusExpanded={dusExpanded}
            onDusToggle={onDusToggle}
            dusInitialDirection={initialRoute === "duesseldorf" ? initialDirection : "outbound"}
            dateOverride={dateOverride}
            timeOverride={timeOverride}
          />
        )}

        {resolvedMethodId && resolvedMethodId !== LOCAL_TRANSIT_METHOD_ID && resolvedMethodId !== "plane" && activeMethod && (
          <MethodItemsPanel method={activeMethod} visitorCountry={visitorCountry} />
        )}
      </div>
    </div>
  );
}
