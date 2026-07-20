"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import Link from "next/link";
import styles from "./GettingThere.module.css";
import { BusIcon, CarIcon, ChevronDownIcon, ExternalLinkIcon, PinIcon, PlaneIcon, TrainIcon } from "./icons";
import { dbg } from "../../lib/debug";
import { gettingThereItemName, inferCountryFromLanguage, sortItemsByCountryBoost } from "../../lib/transport/logic";
import type { GettingThereData, GettingThereItem, GettingThereMethod } from "../../lib/transport/types";

// Curated, durable "getting there" content -- see docs/getting-there-design.md.
// Deliberately separate from the live departure boards above: different
// decision timing (booked days ahead, not read in the moment), so it's
// fetched once, has no polling, and carries no realtime state.

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
    default:
      return <PinIcon />;
  }
}

function isInternalLink(href: string): boolean {
  return href.startsWith("/");
}

function ItemRow({ item, boosted }: { item: GettingThereItem; boosted: boolean }) {
  const name = gettingThereItemName(item);
  const internal = isInternalLink(item.link);

  function onLinkClick() {
    dbg("[GETTING-THERE] item link click ->", item.link, name);
  }

  return (
    <li className={`${styles.item} ${boosted ? styles.itemBoosted : ""}`}>
      <div className={styles.itemTop}>
        <span className={styles.itemName}>{name}</span>
        {item.duration_hint && <span className={styles.itemDuration}>{item.duration_hint}</span>}
      </div>
      <p className={styles.itemSummary}>{item.summary}</p>
      {item.notes && <p className={styles.itemNotes}>{item.notes}</p>}
      {internal ? (
        <Link href={item.link} className={styles.itemLink} onClick={onLinkClick}>
          {item.link_label}
        </Link>
      ) : (
        <a href={item.link} className={styles.itemLink} target="_blank" rel="noopener noreferrer" onClick={onLinkClick}>
          {item.link_label}
          <ExternalLinkIcon />
        </a>
      )}
    </li>
  );
}

function MethodPanel({ method, visitorCountry }: { method: GettingThereMethod; visitorCountry: string | null }) {
  const ranked = useMemo(
    () => sortItemsByCountryBoost(method.items, visitorCountry),
    [method.items, visitorCountry],
  );
  if (ranked.length === 0) return <p className={styles.emptyState}>No options listed yet.</p>;
  return (
    <ul className={styles.items}>
      {ranked.map(({ item, boosted }) => (
        <ItemRow key={`${gettingThereItemName(item)}|${item.link}`} item={item} boosted={boosted} />
      ))}
    </ul>
  );
}

export default function GettingThere() {
  const [data, setData] = useState<GettingThereData | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [activeMethodId, setActiveMethodId] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(true);
  const [visitorCountry, setVisitorCountry] = useState<string | null>(null);
  const bodyId = useId();
  // Once the visitor manually toggles the section, the breakpoint-driven
  // default below stops overriding their choice on further resizes.
  const manuallyToggledRef = useRef(false);

  // Collapsed by default on mobile so the live boards stay the visual star
  // of the page (docs/getting-there-design.md); full on desktop. Same
  // "measure + subscribe" convention as DayTabs' compact-tab measurement:
  // applied once on mount and kept in sync with the matchMedia listener
  // rather than set unconditionally inside the effect body.
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mq = window.matchMedia("(min-width: 769px)");
    function apply() {
      if (manuallyToggledRef.current) return;
      setExpanded(mq.matches);
    }
    apply();
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
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

  useEffect(() => {
    let cancelled = false;
    fetch("/getting-there.json")
      .then((r) => r.json())
      .then((d: GettingThereData) => {
        if (cancelled) return;
        const sorted = [...d.methods].sort((a, b) => a.position - b.position);
        setData({ ...d, methods: sorted });
        if (sorted.length > 0) setActiveMethodId(sorted[0].id);
      })
      .catch(() => {
        if (!cancelled) setLoadError(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function toggleExpanded() {
    const next = !expanded;
    dbg("[GETTING-THERE] section toggle ->", next ? "expanded" : "collapsed");
    manuallyToggledRef.current = true;
    setExpanded(next);
  }

  function onMethodClick(id: string) {
    dbg("[GETTING-THERE] method tab click ->", id);
    setActiveMethodId(id);
  }

  if (loadError || (data && data.methods.length === 0)) return null;

  const activeMethod = data?.methods.find((m) => m.id === activeMethodId) ?? data?.methods[0];

  return (
    <div className={styles.section}>
      <button
        type="button"
        className={styles.headerBtn}
        onClick={toggleExpanded}
        aria-expanded={expanded}
        aria-controls={bodyId}
      >
        <h2 className={styles.heading}>Getting there</h2>
        <span className={styles.chevron}>
          <ChevronDownIcon />
        </span>
      </button>

      {expanded && (
        <div id={bodyId} className={styles.body}>
          {!data ? null : (
            <>
              <div className={styles.methodTabs}>
                {data.methods.map((m) => (
                  <button
                    key={m.id}
                    type="button"
                    className={`${styles.methodTab} ${m.id === activeMethod?.id ? styles.methodTabActive : ""}`}
                    onClick={() => onMethodClick(m.id)}
                  >
                    {methodIcon(m.id)}
                    {m.label}
                  </button>
                ))}
              </div>
              {activeMethod && <MethodPanel method={activeMethod} visitorCountry={visitorCountry} />}
            </>
          )}
        </div>
      )}
    </div>
  );
}
