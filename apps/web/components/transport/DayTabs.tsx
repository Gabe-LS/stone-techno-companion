"use client";

import { useEffect, useRef, useState } from "react";
import styles from "./LiveBoard.module.css";
import { shortDate, slashDate } from "../../lib/transport/logic";
import type { TransportDay } from "../../lib/transport/types";

interface DayTabsProps {
  days: TransportDay[];
  activeDay: number;
  onSelect: (index: number, tabText: string) => void;
}

// Narrow viewport (or the 4-day airport board): abbreviate the day name and
// drop the year so the tabs never wrap or crowd. Measured per render against
// the actual tab count, so both the 3-day tram and 4-day airport boards
// adapt independently. docs/parity/transport.md #154.
export default function DayTabs({ days, activeDay, onSelect }: DayTabsProps) {
  const barRef = useRef<HTMLDivElement>(null);
  const [compact, setCompact] = useState(false);

  useEffect(() => {
    const bar = barRef.current;
    if (!bar) return;

    function measure() {
      const el = barRef.current;
      if (!el) return;
      const n = el.children.length;
      if (!n) return;
      // Threshold raised from 96 to 160 when the tabs went single-line
      // (DESIGN-STANDARDS.md #2 Controls, "Fri 10/07" style): the old value
      // was tuned for the two-line stacked layout (day name on its own row,
      // date on the row below), where each row only needed to fit half the
      // content. On one line, "Friday 10/07/2026" needs meaningfully more
      // width per tab before it stops overflowing its pill.
      setCompact(el.clientWidth / n < 160);
    }

    measure();
    window.addEventListener("resize", measure);
    const observer = new ResizeObserver(measure);
    observer.observe(bar);
    return () => {
      window.removeEventListener("resize", measure);
      observer.disconnect();
    };
  }, [days.length]);

  return (
    <div ref={barRef} className={`${styles.dayTabBar} ${compact ? styles.compact : ""}`}>
      {days.map((d, i) => {
        const dateSlash = slashDate(d.date);
        const dateShort = shortDate(d.date);
        return (
          <button
            key={d.date}
            type="button"
            className={`${styles.dayTab} ${i === activeDay ? styles.active : ""}`}
            onClick={(e) => onSelect(i, e.currentTarget.textContent?.trim() ?? d.day)}
          >
            <span>
              <span className={styles.dFull}>{d.day}</span>
              <span className={styles.dAbbr}>{d.day.slice(0, 3)}</span>
            </span>
            <span className={styles.dayTabCount}>
              <span className={styles.dtFull}>{dateSlash}</span>
              <span className={styles.dtAbbr}>{dateShort}</span>
            </span>
          </button>
        );
      })}
    </div>
  );
}
