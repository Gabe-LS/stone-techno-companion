import styles from "./page.module.css";

export default function Home() {
  return (
    <div className={styles.page}>
      <h1 className={styles.title}>Stone Techno Companion</h1>
      <p className={styles.body}>
        This is the Stage 3 Next.js front end scaffold. Line-up, Timetable, and Chat
        still live on the current site; they are ported here one surface at a time
        (see <code>docs/roadmap.md</code> section 3.3).
      </p>
      <p className={styles.note}>Transport is next: see the Transport tab above.</p>
    </div>
  );
}
