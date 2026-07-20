"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import styles from "./Nav.module.css";

type NavItem = {
  label: string;
  href: string;
};

type NavGroup = {
  id: string;
  label: string;
  items: NavItem[];
};

// The four destinations every page links back to (docs/roadmap.md section 3.3,
// nav workstream input (c): "signed-in desktop chat gets real nav back to
// Line-up / Timetable / Transport" — generalized here to every page showing
// all four, closing that gap everywhere at once, not just on chat.
const PRIMARY_NAV: NavItem[] = [
  { label: "Line-up", href: "/line-up" },
  { label: "Timetable", href: "/timetable" },
  { label: "Chat", href: "/chat" },
  { label: "Transport", href: "/transport" },
];

// Mobile menu is grouped (design input (d)). Only one group exists today;
// more groups (e.g. "Account") can be appended here without touching the
// render logic below.
const NAV_GROUPS: NavGroup[] = [{ id: "pages", label: "Pages", items: PRIMARY_NAV }];

function isActive(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}

function NavBadge({ count }: { count: number }) {
  if (count <= 0) return null;
  return (
    <span className={styles.badge} aria-hidden="true">
      {count > 99 ? "99+" : count}
    </span>
  );
}

export default function Nav() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  // Placeholder unread count for the mobile menu badge. Wired to a real
  // source once chat unread counts are surfaced to the Next.js front
  // (docs/roadmap.md section 3.3 nav workstream input (b)).
  const unreadCount = 0;

  const hamburgerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  // Close the mobile menu on route change. Adjusted during render (React's
  // recommended pattern for resetting state on a prop/derived-value change)
  // rather than in an effect, which would cause an extra cascading render.
  const [prevPathname, setPrevPathname] = useState(pathname);
  if (pathname !== prevPathname) {
    setPrevPathname(pathname);
    setOpen(false);
  }

  // Close the mobile menu if the viewport crosses into desktop width, same
  // as the legacy cmd-bar/chat menu behavior (matchMedia change listener).
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 768px)");
    const onChange = () => setOpen(false);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  // Escape closes with focus return; Tab is trapped inside the panel while
  // open; background scroll is locked while the overlay is up.
  useEffect(() => {
    if (!open) return;

    const panel = panelRef.current;
    const focusables = panel
      ? Array.from(panel.querySelectorAll<HTMLElement>('a[href], button:not([disabled])'))
      : [];
    focusables[0]?.focus();

    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        closeMenu();
        return;
      }
      if (e.key === "Tab" && focusables.length > 0) {
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    }

    document.addEventListener("keydown", onKeyDown);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [open]);

  function closeMenu() {
    setOpen(false);
    hamburgerRef.current?.focus();
  }

  const hamburgerLabel = unreadCount > 0 ? `Menu, ${unreadCount} unread` : "Menu";

  return (
    <header className={styles.header}>
      <nav className={styles.bar} aria-label="Main navigation">
        <Link href="/" className={styles.brand}>
          Stone Techno
        </Link>

        <div className={styles.desktopNav}>
          {PRIMARY_NAV.map((item) => {
            const active = isActive(pathname, item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={active ? `${styles.navLink} ${styles.navLinkActive}` : styles.navLink}
                aria-current={active ? "page" : undefined}
              >
                {item.label}
              </Link>
            );
          })}
        </div>

        <button
          type="button"
          ref={hamburgerRef}
          className={styles.hamburgerBtn}
          aria-label={hamburgerLabel}
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-controls="nav-menu-panel"
          onClick={() => setOpen((o) => !o)}
        >
          <span className={styles.hamburgerIcon} aria-hidden="true">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <line x1="3" y1="6" x2="21" y2="6" />
              <line x1="3" y1="12" x2="21" y2="12" />
              <line x1="3" y1="18" x2="21" y2="18" />
            </svg>
          </span>
          <NavBadge count={unreadCount} />
        </button>
      </nav>

      {open && (
        <>
          <div className={styles.overlay} onClick={closeMenu} />
          <div
            id="nav-menu-panel"
            ref={panelRef}
            className={styles.panel}
            role="dialog"
            aria-modal="true"
            aria-label="Navigation menu"
          >
            {NAV_GROUPS.map((group) => (
              <div key={group.id} className={styles.group}>
                <h2 className={styles.groupLabel} id={`nav-group-${group.id}`}>
                  {group.label}
                </h2>
                <ul className={styles.groupList} aria-labelledby={`nav-group-${group.id}`}>
                  {group.items.map((item) => {
                    const active = isActive(pathname, item.href);
                    return (
                      <li key={item.href}>
                        <Link
                          href={item.href}
                          className={active ? `${styles.mobileLink} ${styles.mobileLinkActive}` : styles.mobileLink}
                          aria-current={active ? "page" : undefined}
                          onClick={() => setOpen(false)}
                        >
                          {item.label}
                        </Link>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))}
          </div>
        </>
      )}
    </header>
  );
}
