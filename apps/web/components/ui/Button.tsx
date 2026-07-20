import type { AnchorHTMLAttributes, ButtonHTMLAttributes, ReactNode } from "react";
import styles from "./Button.module.css";

type ButtonOwnProps = {
  children: ReactNode;
  className?: string;
};

type ButtonAsButton = ButtonOwnProps &
  Omit<ButtonHTMLAttributes<HTMLButtonElement>, "className"> & { href?: undefined };

type ButtonAsAnchor = ButtonOwnProps &
  Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "className"> & { href: string };

export type ButtonProps = ButtonAsButton | ButtonAsAnchor;

/**
 * The app's one solid-fill button style (DESIGN-STANDARDS.md #2). Renders
 * an <a> when `href` is given (an in-page control that happens to navigate,
 * styled as a button because it never leaves the site -- see ExternalLink
 * for the "leaves the site" style instead), otherwise a <button type="button">.
 */
export default function Button(props: ButtonProps) {
  const { children, className, ...rest } = props;
  const merged = className ? `${styles.button} ${className}` : styles.button;

  if ("href" in props && props.href !== undefined) {
    const { href, ...anchorRest } = rest as Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "className">;
    return (
      <a href={href} className={merged} {...anchorRest}>
        {children}
      </a>
    );
  }

  const buttonRest = rest as Omit<ButtonHTMLAttributes<HTMLButtonElement>, "className">;
  return (
    <button type="button" className={merged} {...buttonRest}>
      {children}
    </button>
  );
}
