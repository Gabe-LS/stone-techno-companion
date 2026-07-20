import type { Metadata } from "next";
import { resolveRouteSlug } from "../../lib/transport/logic";
import TransportBoard from "../../components/transport/TransportBoard";

// Static placeholder, overwritten client-side on load and on every direction
// toggle to "<from> -> <to> * Transport" (docs/parity/transport.md #229).
// Kept a static string here (not derived from the request's ?route=) on
// purpose, for parity with the legacy page's own pre-JS <title>.
export const metadata: Metadata = {
  title: "107 / NE2 — Zollverein → Hbf",
};

interface TransportPageProps {
  searchParams: Promise<{ route?: string; date?: string; time?: string }>;
}

export default async function TransportPage({ searchParams }: TransportPageProps) {
  const sp = await searchParams;
  const { route, direction } = resolveRouteSlug(sp.route ?? null);

  return (
    <TransportBoard
      initialRoute={route}
      initialDirection={direction}
      dateOverride={sp.date ?? null}
      timeOverride={sp.time ?? null}
    />
  );
}
