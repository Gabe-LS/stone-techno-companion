import Link from "next/link";
import styles from "./page.module.css";

export default function NotFound() {
  return (
    <div className={styles.page}>
      <h1 className={styles.title}>Page not found</h1>
      <p className={styles.body}>This page does not exist.</p>
      <p className={styles.note}>
        <Link href="/">Back to the home page</Link>
      </p>
    </div>
  );
}
