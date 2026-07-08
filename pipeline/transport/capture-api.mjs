// Regenerates the Tram 107 / NE2 schedule for /transport.
// Run when event dates change (edit the dates array below), then deploy the
// JSON via git pull (no rebuild). Requires Node 18+. Output goes to
// server/static/timetable-transport.json.
import { writeFileSync } from 'fs';

// Call the VRR EFA departure monitor API directly from Node.js
// This bypasses CORS and lets us set date/time parameters

const ZOLLVEREIN_ID = '20009206';
const ESSEN_HBF_ID = '20009289';
const DFLUG_ID = '20018488'; // Düsseldorf Flughafen Bf (airport station)

function toBerlinTime(dateObj) {
  const h = String(dateObj.hour).padStart(2, '0');
  const m = String(dateObj.minute).padStart(2, '0');
  return `${h}:${m}`;
}

async function fetchDepartures(stopId, date, time, limit = 100) {
  // date format: DD.MM.YYYY, time format: HHMM
  const url = `https://efa.vrr.de/vrr/XSLT_DM_REQUEST?` + new URLSearchParams({
    outputFormat: 'JSON',
    language: 'de',
    stateless: '1',
    type_dm: 'stop',
    name_dm: stopId,
    mode: 'direct',
    useRealtime: '1',
    itdDateDayMonthYear: date,
    itdTime: time,
    limit: String(limit),
  });

  const res = await fetch(url, {
    headers: {
      'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
      'Accept': 'application/json',
    }
  });

  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// Test: fetch departures from Zollverein on 10.07.2026 at 04:00
console.log('Testing EFA API directly from Node.js...');
try {
  const data = await fetchDepartures(ZOLLVEREIN_ID, '10.07.2026', '0400', 10);
  const deps = data.departureList;
  if (deps) {
    console.log(`Got ${deps.length} departures`);
    for (const dep of deps.slice(0, 5)) {
      const line = dep.servingLine;
      const dt = dep.dateTime;
      console.log(`  ${line.number} -> ${line.direction} at ${dt.hour}:${dt.minute}`);
    }
  } else {
    console.log('No departureList in response');
    console.log('Keys:', Object.keys(data));
    console.log('Preview:', JSON.stringify(data).substring(0, 500));
  }
} catch (e) {
  console.log('Direct EFA failed:', e.message);
  console.log('Trying alternative URL...');

  // Try the IFA proxy backend directly
  try {
    const data2 = await fetch(`https://ifa.ruhrbahn.de/departure/${ZOLLVEREIN_ID}`, {
      headers: { 'Accept': 'application/json' }
    }).then(r => r.json());
    console.log('IFA proxy works, departures:', data2.data?.departureList?.length);
  } catch (e2) {
    console.log('IFA proxy also failed:', e2.message);
  }
}

// If EFA works, build the full timetable
async function buildDayTimetable(stopId, termini, date, nextDate) {
  const allDeps = new Map();

  // One departure-monitor fetch returns the next N departures across ALL lines
  // at the stop, so a single limit=500 call from 04:00 only spans ~4h at a big
  // hub like Essen Hbf (confirmed empirically). Window the day in ~3h steps and
  // merge -- the dedup Map below collapses overlaps -- so both the small
  // Zollverein stop and the huge Essen Hbf get full-day coverage.
  const startTimes = ['0400', '0700', '1000', '1300', '1600', '1900', '2200'];
  const results = await Promise.all([
    ...startTimes.map((t) => fetchDepartures(stopId, date, t, 500)),
    fetchDepartures(stopId, nextDate, '0100', 500),
  ]);
  const allDepartures = results.flatMap((r) => r.departureList || []);

  const calDateNum = date.split('.').reverse().join('');
  const nextDateNum = nextDate.split('.').reverse().join('');

  for (const dep of allDepartures) {
    const line = dep.servingLine;
    if (line.number !== '107' && line.number !== 'NE2') continue;

    const dir = line.direction;
    if (!termini.some(function (x) { return dir.includes(x); })) continue;

    const dt = dep.dateTime;
    const depTime = toBerlinTime(dt);
    const key = `${dt.year}${String(dt.month).padStart(2, '0')}${String(dt.day).padStart(2, '0')} ${depTime}`;

    const depDateNum = `${dt.year}${String(dt.month).padStart(2, '0')}${String(dt.day).padStart(2, '0')}`;
    const depHour = parseInt(dt.hour);

    if (depDateNum < calDateNum) continue;
    if (depDateNum === calDateNum && depHour < 4) continue;
    if (depDateNum > nextDateNum) continue;
    if (depDateNum === nextDateNum && depHour >= 4) continue;

    if (allDeps.has(key)) continue;

    const durationMin = line.number === 'NE2' ? 13 : (depHour >= 7 && depHour < 20 ? 15 : 14);
    const depMinutes = parseInt(dt.hour) * 60 + parseInt(dt.minute);
    const arrMinutes = depMinutes + durationMin;
    const arrH = String(Math.floor(arrMinutes / 60) % 24).padStart(2, '0');
    const arrM = String(arrMinutes % 60).padStart(2, '0');

    allDeps.set(key, {
      depTime,
      arrTime: `${arrH}:${arrM}`,
      line: line.number,
      type: line.name,
      direction: dir,
      duration: durationMin,
      platform: dep.platform,
      sortKey: depDateNum === calDateNum ? depMinutes : depMinutes + 24 * 60,
    });
  }

  const sorted = [...allDeps.values()].sort((a, b) => a.sortKey - b.sortKey);
  return sorted;
}

const dates = [
  { date: '10.07.2026', next: '11.07.2026', label: '10.07.2026', day: 'Friday' },
  { date: '11.07.2026', next: '12.07.2026', label: '11.07.2026', day: 'Saturday' },
  { date: '12.07.2026', next: '13.07.2026', label: '12.07.2026', day: 'Sunday' },
];
// The Düsseldorf airport trains also cover Thursday (the arrival day before the
// festival); the Zollverein tram board stays Fri-Sun only.
const duesDates = [
  { date: '09.07.2026', next: '10.07.2026', label: '09.07.2026', day: 'Thursday' },
  ...dates,
];

const DIRECTIONS = {
  outbound: { stopId: ZOLLVEREIN_ID, termini: ['Bredeney', 'Hauptbahnhof'] },  // Zollverein -> Essen Hbf
  inbound: { stopId: ESSEN_HBF_ID, termini: ['Hanielstr', 'Gelsenkirchen'] },  // Essen Hbf -> Zollverein (both pass Zollverein)
};

async function buildDirection(cfg) {
  const out = {};
  for (const { date, next, label } of dates) {
    out[label] = { trips: await buildDayTimetable(cfg.stopId, cfg.termini, date, next) };
  }
  return out;
}

function daysFrom(built) {
  return dates.map(({ label, day }) => ({
    day,
    date: label,
    departures: built[label].trips.map(t => ({
      dep: t.depTime,
      arr: t.arrTime,
      line: t.line,
      direction: t.direction,
      duration: t.duration,
      platform: t.platform,
    })),
  }));
}

console.log('\n=== Outbound: Zollverein -> Essen Hbf ===');
const outbound = await buildDirection(DIRECTIONS.outbound);
for (const { label } of dates) console.log(`  ${label}: ${outbound[label].trips.length} connections`);

console.log('\n=== Inbound: Essen Hbf -> Zollverein ===');
const inbound = await buildDirection(DIRECTIONS.inbound);
for (const { label } of dates) console.log(`  ${label}: ${inbound[label].trips.length} connections`);

// --- Düsseldorf Airport -> Essen Hbf: regional trains via the VRR journey
// planner (XML_TRIP_REQUEST2). Direct trips only; ~4 journeys per request, so
// window the day in pages. VRR response times are UTC, so convert to Berlin. ---
const _BERLIN = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'Europe/Berlin', year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', hour12: false,
});
function berlinParts(iso) {
  const p = _BERLIN.formatToParts(new Date(iso));
  const g = (t) => p.find((x) => x.type === t).value;
  return { dateNum: `${g('year')}${g('month')}${g('day')}`, hh: g('hour'), mm: g('minute') };
}

function shortenHeadsign(name) {
  // "Hamm (Westf.) Hbf" -> "Hamm", "Düsseldorf Hbf" -> "Düsseldorf": drop the
  // parenthetical region and the trailing station-type so the terminus reads
  // cleanly on the board (the tram board's terminus label equivalent).
  return (name || '')
    .replace(/\s*\([^)]*\)/g, '')
    .replace(/\s+(Hauptbahnhof|Hbf|Bahnhof|Bhf|Bf)$/i, '')
    .trim();
}

async function fetchTripsPage(originId, destId, dateNum, time) {
  const params = {
    outputFormat: 'rapidJSON', language: 'en',
    name_origin: originId, type_origin: 'any',
    name_destination: destId, type_destination: 'any',
    itdDate: dateNum, itdTime: time, itdTripDateTimeDepArr: 'dep',
    coordOutputFormat: 'WGS84[dd.ddddd]', useRealtime: '0',
    routeType: 'LEASTTIME', maxChanges: '0',
    useProxFootSearchOrigin: 'false', useProxFootSearchDestination: 'false',
    version: '11.0.6.72',
  };
  const url = 'https://www.vrr.de/vrr-efa/XML_TRIP_REQUEST2?' + new URLSearchParams(params);
  const res = await fetch(url, { headers: { 'User-Agent': 'Mozilla/5.0', Accept: 'application/json' } });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function buildDuesseldorfDay(originId, destId, date, nextDate) {
  const dateNum = date.split('.').reverse().join('');
  const nextNum = nextDate.split('.').reverse().join('');
  const deps = new Map();
  let curDate = dateNum;
  let curTime = '0400';
  for (let page = 0; page < 50; page++) {
    let d;
    try { d = await fetchTripsPage(originId, destId, curDate, curTime); } catch (e) { break; }
    const js = d.journeys || [];
    if (!js.length) break;
    let lastIso = null;
    for (const j of js) {
      const f = j.legs[0];
      const l = j.legs[j.legs.length - 1];
      const depIso = f.origin && f.origin.departureTimePlanned;
      if (!depIso) continue;
      lastIso = depIso;
      if (j.interchanges !== 0) continue; // direct only
      const train = j.legs.find((x) => x.transportation && x.transportation.product);
      const line = (train && (train.transportation.disassembledName || train.transportation.number)) || '';
      if (!/^(RE|RB|S)/.test(line)) continue; // regional/S-Bahn only (skip long-distance ICE/IC train numbers)
      const b = berlinParts(depIso);
      const depH = parseInt(b.hh, 10);
      if (b.dateNum < dateNum) continue;
      if (b.dateNum === dateNum && depH < 4) continue;
      if (b.dateNum > nextNum) continue;
      if (b.dateNum === nextNum && depH >= 4) continue;
      if (deps.has(depIso)) continue;
      const arrIso = l.destination && l.destination.arrivalTimePlanned;
      const ab = arrIso ? berlinParts(arrIso) : null;
      deps.set(depIso, {
        dep: `${b.hh}:${b.mm}`,
        arr: ab ? `${ab.hh}:${ab.mm}` : '',
        line,
        badge: /^S/.test(line) ? 's' : 're',
        // The train's terminus/headsign (e.g. "Hamm", "Köln") -- helps identify
        // the train on the platform, like the tram board's terminus column.
        direction: shortenHeadsign(train.transportation.destination && train.transportation.destination.name) || '',
        duration: Math.round(j.legs.reduce((s, x) => s + (x.duration || 0), 0) / 60),
        platform: (f.origin.properties && f.origin.properties.platform) || '',
      });
    }
    if (!lastIso) break;
    const lb = berlinParts(lastIso);
    if (lb.dateNum > nextNum || (lb.dateNum === nextNum && parseInt(lb.hh, 10) >= 4)) break;
    let mi = parseInt(lb.mm, 10) + 1;
    let h = parseInt(lb.hh, 10);
    let Y = +lb.dateNum.slice(0, 4);
    let M = +lb.dateNum.slice(4, 6);
    let D = +lb.dateNum.slice(6, 8);
    if (mi >= 60) { mi -= 60; h++; }
    if (h >= 24) { h -= 24; const nd = new Date(Date.UTC(Y, M - 1, D + 1)); Y = nd.getUTCFullYear(); M = nd.getUTCMonth() + 1; D = nd.getUTCDate(); }
    curDate = `${Y}${String(M).padStart(2, '0')}${String(D).padStart(2, '0')}`;
    curTime = `${String(h).padStart(2, '0')}${String(mi).padStart(2, '0')}`;
  }
  const svc = (t) => { const [h, m] = t.dep.split(':').map(Number); const x = h * 60 + m; return x < 240 ? x + 1440 : x; };
  return [...deps.values()].sort((a, b) => svc(a) - svc(b));
}

console.log('\n=== DUS Airport -> Essen Hbf (regional) ===');
const duesDays = [];
for (const { date, next, label, day } of duesDates) {
  const trips = await buildDuesseldorfDay(DFLUG_ID, ESSEN_HBF_ID, date, next);
  console.log(`  ${label}: ${trips.length} direct regional trips`);
  duesDays.push({ day, date: label, departures: trips });
}

console.log('\n=== Essen Hbf -> DUS Airport (regional) ===');
const duesRevDays = [];
for (const { date, next, label, day } of duesDates) {
  const trips = await buildDuesseldorfDay(ESSEN_HBF_ID, DFLUG_ID, date, next);
  console.log(`  ${label}: ${trips.length} direct regional trips`);
  duesRevDays.push({ day, date: label, departures: trips });
}

const transportJson = {
  route: { from: 'Zollverein', to: 'Essen Hbf', fromId: ZOLLVEREIN_ID, toId: ESSEN_HBF_ID },
  stop: { lat: 51.486095, lng: 7.046062 },
  days: daysFrom(outbound),
  reverse: {
    route: { from: 'Essen Hbf', to: 'Zollverein', fromId: ESSEN_HBF_ID, toId: ZOLLVEREIN_ID },
    stop: { lat: 51.449732, lng: 7.012213 },  // Essen Hbf (inbound departure stop)
    days: daysFrom(inbound),
  },
  duesseldorf: {
    route: { from: 'DUS Airport', to: 'Essen Hbf', fromId: DFLUG_ID, toId: ESSEN_HBF_ID },
    stop: { lat: 51.291368, lng: 6.787158 },  // D-Flughafen Bf (departure stop)
    days: duesDays,
    reverse: {
      route: { from: 'Essen Hbf', to: 'DUS Airport', fromId: ESSEN_HBF_ID, toId: DFLUG_ID },
      stop: { lat: 51.449732, lng: 7.012213 },  // Essen Hbf (reverse departure stop)
      days: duesRevDays,
    },
  },
};

writeFileSync(new URL('../../server/static/timetable-transport.json', import.meta.url), JSON.stringify(transportJson));
console.log('\nJSON saved to server/static/timetable-transport.json');
