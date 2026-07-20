// Shapes mirror docs/api/companion-openapi.yaml (transport tag) and
// services/companion/static/timetable-transport.json exactly. See
// docs/parity/transport.md section 2 (static schedule) and section 3
// (realtime response shapes).

export type RouteKey = "zollverein" | "duesseldorf";
export type Direction = "outbound" | "inbound";

export interface TransportRouteInfo {
  from: string;
  to: string;
  fromId?: string;
  toId?: string;
}

export interface TransportStop {
  lat: number;
  lng: number;
}

// One entry in a day's static `departures` array.
export interface StaticDeparture {
  dep: string; // HH:MM
  arr?: string;
  line: string;
  direction: string;
  duration?: number;
  platform?: string;
  badge?: string; // "re" | "s", Duesseldorf-only
  nextDay?: boolean;
}

export interface TransportDay {
  day: string; // "Friday", etc.
  date: string; // DD.MM.YYYY (dots)
  departures: StaticDeparture[];
}

export interface TransportViewBlock {
  route: TransportRouteInfo;
  stop: TransportStop;
  days: TransportDay[];
}

export interface TransportBaseBlock extends TransportViewBlock {
  reverse?: TransportViewBlock;
}

export interface TimetableData extends TransportBaseBlock {
  duesseldorf?: TransportBaseBlock;
}

// Realtime departures response (GET /api/transport/departures)
export interface RealtimeEntryBase {
  line: string;
  scheduled: string;
  scheduledDate: string;
  realtime: boolean;
  real?: string;
  delay?: number;
  status?: string;
}

export interface ZollvereinRealtimeEntry extends RealtimeEntryBase {
  direction: string;
  platform?: string;
  countdown?: number;
}

export interface DuesseldorfRealtimeEntry extends RealtimeEntryBase {
  platform?: string | null;
  trainNumber?: string | null;
  arr?: string | null;
  arrReal?: string;
  arrDelay?: number;
}

export type RealtimeEntry = ZollvereinRealtimeEntry | DuesseldorfRealtimeEntry;

export interface DeparturesResponse {
  departures: RealtimeEntry[];
  ts: string;
  stale?: boolean;
}

export interface WalkResponse {
  distanceM: number;
  durationS: number;
}
