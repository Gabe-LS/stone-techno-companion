"use client";

import styles from "./TransportBoard.module.css";
import { countdownText, stripEssenPrefix } from "../../lib/transport/logic";
import type { RouteKey } from "../../lib/transport/types";
import type { RowViewModel } from "../../lib/transport/logic";
import type { DuesseldorfRealtimeEntry } from "../../lib/transport/types";

interface DepartureListProps {
  rows: RowViewModel[];
  route: RouteKey;
  isToday: boolean;
  nowMin: number;
  walkMinutes: number | null;
  nextRowRef: (el: HTMLLIElement | null) => void;
}

export default function DepartureList({ rows, route, isToday, nowMin, walkMinutes, nextRowRef }: DepartureListProps) {
  return (
    <ul className={styles.depList}>
      {rows.map((row) => {
        const { dep, rt, isCanceled, isPast, isTooLate, isNext, hasDelay } = row;
        const classNames = [styles.depItem];
        if (isCanceled) classNames.push(styles.isCanceled);
        if (isPast) classNames.push(styles.isPast);
        if (isTooLate) classNames.push(styles.isTooLate);
        if (isNext) classNames.push(styles.isNext);

        const badgeKey = dep.badge || dep.line;
        const timeText = hasDelay && rt?.real ? rt.real : dep.dep;

        const dues = route === "duesseldorf" ? (rt as DuesseldorfRealtimeEntry | undefined) : undefined;
        const plat = dues?.platform || dep.platform;
        const subParts: string[] = [];
        if (plat) subParts.push(`Pl. ${plat}`);
        if (dues?.trainNumber) subParts.push(`#${dues.trainNumber}`);

        const arrDelayed = Boolean(dues?.arrReal && dues.arrDelay && dues.arrDelay > 0 && !isCanceled);
        const cd = countdownText(row, isToday, nowMin, walkMinutes);

        return (
          <li key={row.key} className={classNames.join(" ")} ref={isNext ? nextRowRef : undefined}>
            <div className={styles.depTimeCol}>
              <span className={`${styles.depTime} ${hasDelay && rt?.real ? styles.delayed : ""}`}>{timeText}</span>
            </div>

            <span className={`${styles.lineBadge} ${styles["l-" + badgeKey] ?? ""}`}>
              {dep.line}
              {rt && rt.realtime && !isCanceled && (
                <span className={`${styles.rtDot} ${hasDelay ? styles.red : styles.green}`} />
              )}
            </span>

            <div className={styles.depMiddle}>
              <span className={styles.depDest}>{stripEssenPrefix(dep.direction)}</span>
              {route === "duesseldorf" && subParts.length > 0 && (
                <span className={styles.depSub}>{subParts.join("  ·  ")}</span>
              )}
              {isCanceled && <span className={styles.cancelBadge}>Canceled</span>}
            </div>

            <div className={styles.depRight}>
              {route === "duesseldorf" && dep.arr && (
                <span className={`${styles.depArr} ${arrDelayed ? styles.delayed : ""}`}>
                  <span className={styles.depArrLabel}>arr </span>
                  {arrDelayed && dues?.arrReal ? dues.arrReal : dep.arr}
                </span>
              )}
              {cd &&
                (rt && "countdown" in rt && rt.countdown != null && isToday && !isPast && !isCanceled ? (
                  <span className={styles.depCountdown}>{cd}</span>
                ) : (
                  <span className={styles.depWalk}>{cd}</span>
                ))}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
