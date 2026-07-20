import type { ButtonHTMLAttributes, ReactNode } from "react";
import styles from "./IconButton.module.css";

interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "className"> {
  children: ReactNode;
  ariaLabel: string;
  className?: string;
}

/** A round icon-only hit target with the standard hover/active/focus chrome. */
export default function IconButton({ children, ariaLabel, className, ...rest }: IconButtonProps) {
  return (
    <button
      type="button"
      className={className ? `${styles.iconButton} ${className}` : styles.iconButton}
      aria-label={ariaLabel}
      {...rest}
    >
      {children}
    </button>
  );
}
