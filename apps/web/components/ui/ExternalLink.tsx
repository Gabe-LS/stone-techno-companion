import type { AnchorHTMLAttributes, ReactNode } from "react";
import styles from "./ExternalLink.module.css";
import { ExternalLinkIcon } from "./icons";

interface ExternalLinkProps extends Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "className" | "target" | "rel" | "href"> {
  href: string;
  children: ReactNode;
  className?: string;
}

/**
 * A link that leaves the site (DESIGN-STANDARDS.md #2): underlined, always
 * with the external-link icon, always target="_blank" rel="noopener
 * noreferrer". The only underlined style in the app besides the Nav's
 * accent-underline active state (a different convention, see Nav.module.css).
 */
export default function ExternalLink({ href, children, className, ...rest }: ExternalLinkProps) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className={className ? `${styles.link} ${className}` : styles.link}
      {...rest}
    >
      {children}
      <ExternalLinkIcon />
    </a>
  );
}
