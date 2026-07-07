// Regenerates the Tram 107 / NE2 schedule for /transport.
// Run when event dates change (edit the dates array below), then deploy the
// JSON via git pull (no rebuild). Requires Node 18+. Output goes to
// server/static/timetable-transport.json.
import { writeFileSync } from 'fs';

// Call the VRR EFA departure monitor API directly from Node.js
// This bypasses CORS and lets us set date/time parameters

const ZOLLVEREIN_ID = '20009206';

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
async function buildDayTimetable(date, nextDate, label, dayName) {
  const allDeps = new Map();

  const [dayData, nightData] = await Promise.all([
    fetchDepartures(ZOLLVEREIN_ID, date, '0400', 500),
    fetchDepartures(ZOLLVEREIN_ID, nextDate, '0000', 50),
  ]);

  const calDateNum = date.split('.').reverse().join('');
  const nextDateNum = nextDate.split('.').reverse().join('');

  for (const dep of [...(dayData.departureList || []), ...(nightData.departureList || [])]) {
    const line = dep.servingLine;
    if (line.number !== '107' && line.number !== 'NE2') continue;

    const dir = line.direction;
    if (!dir.includes('Bredeney') && !dir.includes('Hauptbahnhof')) continue;

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

const allData = {};

for (const { date, next, label, day } of dates) {
  console.log(`\nQuerying ${day} ${label}...`);
  const trips = await buildDayTimetable(date, next, label, day);
  allData[label] = { label, day, trips };
  const lines = {};
  for (const t of trips) lines[t.line] = (lines[t.line] || 0) + 1;
  console.log(`  ${trips.length} connections (${Object.entries(lines).map(([k, v]) => `${k}: ${v}`).join(', ')})`);
  console.log(`  First: ${trips[0]?.depTime}, Last: ${trips[trips.length-1]?.depTime}`);
}

// Build HTML
let html = `<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Zollverein → Essen Hbf | 107 &amp; NE2</title>
<style>
  :root { --bg: #fafafa; --card: #fff; --border: #e0e0e0; --accent: #0057a8; --text: #222; --muted: #777; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: var(--bg); color: var(--text); padding: 1.5rem; max-width: 960px; margin: 0 auto; }
  h1 { font-size: 1.4rem; margin-bottom: .2rem; }
  .subtitle { color: var(--muted); margin-bottom: 1.5rem; font-size: .9rem; }
  .day-section { margin-bottom: 2.5rem; }
  .day-header { display: flex; align-items: baseline; gap: .7rem; padding: .5rem .8rem; background: var(--accent); color: #fff; border-radius: 6px 6px 0 0; }
  .day-name { font-size: 1.1rem; font-weight: 700; }
  .day-date { font-size: .9rem; opacity: .85; }
  .day-count { margin-left: auto; font-size: .8rem; opacity: .7; }
  table { width: 100%; border-collapse: collapse; background: var(--card); border: 1px solid var(--border); border-top: none; border-radius: 0 0 6px 6px; overflow: hidden; }
  th { background: #f0f4f8; text-align: left; padding: .45rem .7rem; font-size: .75rem; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: .03em; border-bottom: 2px solid var(--border); }
  td { padding: .35rem .7rem; font-size: .85rem; border-bottom: 1px solid #f0f0f0; white-space: nowrap; }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: #f7fafd; }
  .line-107 { display: inline-block; background: var(--accent); color: #fff; font-weight: 700; font-size: .78rem; padding: .12rem .45rem; border-radius: 4px; min-width: 2.2rem; text-align: center; }
  .line-NE2 { display: inline-block; background: #2c3e50; color: #ffe066; font-weight: 700; font-size: .78rem; padding: .12rem .45rem; border-radius: 4px; min-width: 2.2rem; text-align: center; }
  .time { font-variant-numeric: tabular-nums; font-weight: 500; }
  .dur { color: var(--muted); }
  .gap-row td { border-bottom: 2px dashed #ddd; }
  @media (max-width: 600px) { body { padding: .75rem; } td, th { padding: .3rem .4rem; font-size: .8rem; } }
</style>
</head>
<body>
<h1>Zollverein → Essen Hbf</h1>
<p class="subtitle">Straßenbahn 107 &amp; NachtExpress NE2 — direct connections (toward Bredeney/Hbf)</p>
`;

for (const { date, label } of dates) {
  const { day, trips } = allData[label];
  html += `<div class="day-section">
<div class="day-header">
  <span class="day-name">${day}</span>
  <span class="day-date">${label}</span>
  <span class="day-count">${trips.length} connections</span>
</div>
<table>
<thead><tr><th>Dep</th><th>Arr</th><th>Line</th><th>Direction</th><th>Dur</th><th>Plat</th></tr></thead>
<tbody>
`;

  let prevSortKey = null;
  for (const trip of trips) {
    const lineClass = trip.line === 'NE2' ? 'line-NE2' : 'line-107';
    const isGap = prevSortKey !== null && (trip.sortKey - prevSortKey) > 25;
    const gapClass = isGap ? ' class="gap-row"' : '';
    prevSortKey = trip.sortKey;

    html += `<tr${gapClass}>
  <td class="time">${trip.depTime}</td>
  <td class="time">~${trip.arrTime}</td>
  <td><span class="${lineClass}">${trip.line}</span></td>
  <td>${trip.direction}</td>
  <td class="dur">${trip.duration} min</td>
  <td>${trip.platform}</td>
</tr>
`;
  }

  html += `</tbody></table></div>\n`;
}

html += `</body></html>`;

// Legacy HTML output dropped: the app consumes only the JSON.

const transportJson = {
  route: { from: 'Zollverein', to: 'Essen Hbf', fromId: ZOLLVEREIN_ID, toId: '20009289' },
  stop: { lat: 51.486095, lng: 7.046062 },
  days: dates.map(({ label }) => {
    const { day, trips } = allData[label];
    return {
      day,
      date: label,
      departures: trips.map(t => ({
        dep: t.depTime,
        arr: t.arrTime,
        line: t.line,
        direction: t.direction,
        duration: t.duration,
        platform: t.platform,
      })),
    };
  }),
};

writeFileSync(new URL('../../server/static/timetable-transport.json', import.meta.url), JSON.stringify(transportJson));
console.log('JSON saved to server/static/timetable-transport.json');
