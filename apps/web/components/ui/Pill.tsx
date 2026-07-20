import type { ButtonHTMLAttributes, ReactNode } from "react";
import styles from "./Pill.module.css";

interface PillProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "className"> {
  children: ReactNode;
  tier?: "primary" | "secondary";
  active?: boolean;
  className?: string;
}

/**
 * Tab pill (DESIGN-STANDARDS.md #2): two tiers, active state is always a
 * solid fill inversion -- never an underline. `tier="primary"` is the page
 * method picker; `tier="secondary"` is an in-panel day/period picker.
 */
export default function Pill({ children, tier = "primary", active = false, className, ...rest }: PillProps) {
  const tierClass = tier === "primary" ? styles.primary : styles.secondary;
  const classes = [styles.pill, tierClass, active ? styles.active : "", className ?? ""].filter(Boolean).join(" ");
  return (
    <button type="button" className={classes} {...rest}>
      {children}
    </button>
  );
}
