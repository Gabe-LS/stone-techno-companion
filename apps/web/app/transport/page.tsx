import type { Metadata } from "next";
import { LOCAL_TRANSIT_METHOD_ID, explicitRouteSlug, resolveRouteSlug } from "../../lib/transport/logic";
import MethodPicker from "../../components/transport/MethodPicker";

interface TransportPageProps {
  searchParams: Promise<{ route?: string; method?: string; date?: string; time?: string }>;
}

// The tab title derives from the URL, nowhere else: Next re-applies metadata
// on every soft navigation (direction swap, method change both rewrite the
// query), so a client-side document.title would lose the race against it,
// which is exactly the stale-title bug this replaced. Station display names
// mirror timetable-transport.json's route.from/to for the two live-board
// itineraries; method labels mirror getting-there.json's labels.
const ROUTE_TITLES: Record<string, { from: string; to: string }> = {
  zollverein: { from: "Zollverein", to: "Essen Hbf" },
  duesseldorf: { from: "DUS Airport", to: "Essen Hbf" },
};
const METHOD_TITLES: Record<string, string> = {
  train: "Train",
  plane: "Plane",
  car: "Car",
  bus: "Bus",
  [LOCAL_TRANSIT_METHOD_ID]: "Local transit",
};

export async function generateMetadata({ searchParams }: TransportPageProps): Promise<Metadata> {
  const sp = await searchParams;
  const explicit = explicitRouteSlug(sp.route ?? null);
  if (explicit) {
    const names = ROUTE_TITLES[explicit.route];
    if (names) {
      const from = explicit.direction === "inbound" ? names.to : names.from;
      const to = explicit.direction === "inbound" ? names.from : names.to;
      return { title: `${from} → ${to} · Transport` };
    }
  }
  const methodLabel = sp.method ? METHOD_TITLES[sp.method] : undefined;
  return { title: methodLabel ? `${methodLabel} · Transport` : "Transport" };
}

export default async function TransportPage({ searchParams }: TransportPageProps) {
  const sp = await searchParams;

  // An EXPLICIT, recognized ?route= slug wins over ?method= and can be
  // resolved server-side (no fetch needed) -- so a direct link to a route
  // slug lands on the right tab with no flash. An absent or unrecognized
  // slug (garbage ?route= included -- "falls back to default exactly like
  // no param at all", docs/parity/transport.md #26) defers to ?method= or
  // the festival-window smart default, both resolved client-side once
  // getting-there.json / timetable-transport.json have loaded. See
  // docs/getting-there-design.md, "Decision: unified method layout".
  const explicit = explicitRouteSlug(sp.route ?? null);
  const { route, direction } = explicit ?? resolveRouteSlug(sp.route ?? null);
  const initialMethodId = explicit ? (explicit.route === "duesseldorf" ? "plane" : LOCAL_TRANSIT_METHOD_ID) : null;
  const dusExpandedInitial = explicit ? explicit.route === "duesseldorf" : false;

  return (
    <MethodPicker
      initialRoute={route}
      initialDirection={direction}
      initialMethodId={initialMethodId}
      methodParam={sp.method ?? null}
      dusExpandedInitial={dusExpandedInitial}
      dateOverride={sp.date ?? null}
      timeOverride={sp.time ?? null}
    />
  );
}
