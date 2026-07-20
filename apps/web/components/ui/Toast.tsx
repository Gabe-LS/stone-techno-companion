import styles from "./Toast.module.css";

interface ToastProps {
  message: string;
  visible: boolean;
}

/**
 * App-wide toast notification, style only (see CLAUDE.md "Conventions" for
 * the word-based duration behavior, wired up per-caller when first used).
 */
export default function Toast({ message, visible }: ToastProps) {
  return (
    <div className={`${styles.toast} ${visible ? styles.show : ""}`} role="status" aria-live="polite">
      {message}
    </div>
  );
}
