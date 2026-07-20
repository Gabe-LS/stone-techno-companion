import type { Metadata } from "next";
import { LOCAL_TRANSIT_METHOD_ID, explicitRouteSlug, resolveRouteSlug } from "../../lib/transport/logic";
import MethodPicker from "../../components/transport/MethodPicker";

// Static placeholder, overwritten client-side on load and on every direction
// toggle to "<from> -> <to> * Transport" (docs/parity/transport.md #229).
// Kept a static string here (not derived from the request's ?route=) on
// purpose, for parity with the legacy page's own pre-JS <title>.
export const metadata: Metadata = {
  title: "107 / NE2 — Zollverein → Hbf",
};

interface TransportPageProps {
  searchParams: Promise<{ route?: string; method?: string; date?: string; time?: string }>;
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
