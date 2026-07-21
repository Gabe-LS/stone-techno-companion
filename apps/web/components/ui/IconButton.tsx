import type { ButtonHTMLAttributes, MouseEvent, ReactNode } from "react";
import styles from "./IconButton.module.css";

interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "className"> {
  children: ReactNode;
  ariaLabel: string;
  className?: string;
  /** Glyph size: md (default), sm, or xs for subtle glyphs inline with text. */
  size?: "xs" | "sm" | "md";
  /** chrome (default): round hover/active chrome. inline: bare glyph that
      only recolors, sized relative to the neighboring text (0.52em). */
  variant?: "chrome" | "inline";
}

/** A round icon-only hit target with the standard hover/active/focus chrome. */
export default function IconButton({ children, ariaLabel, className, onClick, size = "md", variant = "chrome", ...rest }: IconButtonProps) {
  // Pointer clicks must not leave a lingering focus ring on the button
  // (event.detail > 0 only for real pointer activation; keyboard-triggered
  // clicks have detail 0 and keep their focus ring for accessibility).
  function handleClick(e: MouseEvent<HTMLButtonElement>) {
    if (e.detail > 0) e.currentTarget.blur();
    onClick?.(e);
  }
  const sizeCls = size === "xs" ? styles.sizeXs : size === "sm" ? styles.sizeSm : "";
  const cls = [styles.iconButton, variant === "inline" ? styles.variantInline : sizeCls, className || ""].filter(Boolean).join(" ");
  return (
    <button
      type="button"
      className={cls}
      aria-label={ariaLabel}
      onClick={handleClick}
      {...rest}
    >
      {children}
    </button>
  );
}
