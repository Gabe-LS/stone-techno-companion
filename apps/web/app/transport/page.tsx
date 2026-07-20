import type { Metadata } from "next";
import styles from "../page.module.css";

export const metadata: Metadata = {
  title: "Transport - Stone Techno Companion",
};

export default function TransportPage() {
  return (
    <div className={styles.page}>
      <h1 className={styles.title}>Transport</h1>
      <p className={styles.body}>
        The realtime departure board is coming next. This route is a placeholder
        for the Next.js port of <code>services/companion/static/pages/transport.html</code>
        (see <code>docs/parity/transport.md</code> for the full acceptance checklist),
        chosen as the first surface to port per <code>docs/roadmap.md</code> section 3.3.
      </p>
      <p className={styles.note}>Nothing here talks to the backend yet.</p>
    </div>
  );
}
