import styles from "./Badge.module.css";

interface BadgeProps {
  count: number;
  className?: string;
}

/** Numbered badge, hidden entirely when count is 0 or less. Caps display at "99+". */
export default function Badge({ count, className }: BadgeProps) {
  if (count <= 0) return null;
  return (
    <span className={className ? `${styles.badge} ${className}` : styles.badge} aria-hidden="true">
      {count > 99 ? "99+" : count}
    </span>
  );
}
