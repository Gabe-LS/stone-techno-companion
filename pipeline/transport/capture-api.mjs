// Regenerates the Tram 107 / NE2 schedule for /transport.
// Run when event dates change (edit the dates array below), then deploy the
// JSON via git pull (no rebuild). Requires Node 18+. Output goes to
// server/static/timetable-transport.json.
import { writeFileSync } from 'fs';

// Call the VRR EFA departure monitor API directly from Node.js
// This bypasses CORS and lets us set date/time parameters

const ZOLLVEREIN_ID = '20009206';
const ESSEN_HBF_ID = '20009289';

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

const transportJson = {
  route: { from: 'Zollverein', to: 'Essen Hbf', fromId: ZOLLVEREIN_ID, toId: ESSEN_HBF_ID },
  stop: { lat: 51.486095, lng: 7.046062 },
  days: daysFrom(outbound),
  reverse: {
    route: { from: 'Essen Hbf', to: 'Zollverein', fromId: ESSEN_HBF_ID, toId: ZOLLVEREIN_ID },
    stop: { lat: 51.449732, lng: 7.012213 },  // Essen Hbf (inbound departure stop)
    days: daysFrom(inbound),
  },
};

writeFileSync(new URL('../../server/static/timetable-transport.json', import.meta.url), JSON.stringify(transportJson));
console.log('\nJSON saved to server/static/timetable-transport.json');
