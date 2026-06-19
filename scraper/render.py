from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

from .scrape import format_followers

ICONS_DIR = Path(__file__).resolve().parent / "icons"


def _load_icon(name: str) -> str:
    path = ICONS_DIR / f"{name}-square-round.svg"
    if path.exists():
        svg = path.read_text(encoding="utf-8").strip()
        if "<?xml" in svg:
            svg = svg[svg.index("<svg") :]
        svg = svg.replace('width="24"', 'width="18"').replace(
            'height="24"', 'height="18"'
        )
        if "width=" not in svg:
            svg = svg.replace("<svg", '<svg width="18" height="18"', 1)
        return svg
    return ""


SVG_IG = _load_icon("instagram")
SVG_SC = _load_icon("soundcloud")
SVG_SP = _load_icon("spotify")
SVG_LT = _load_icon("linktree")
SVG_YT = _load_icon("youtube")


def _format_date_heading(date_str: str) -> str:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return f"{dt.strftime('%A')}, {dt.strftime('%B')} {dt.day}, {dt.year}"


def _format_short_date(date_str: str) -> str:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return f"{dt.strftime('%A')}, {dt.strftime('%B')} {dt.day}"


def _format_other_slots(
    all_slots: list[dict], current_date: str, current_period: str
) -> str | None:
    others = []
    for slot in all_slots:
        if slot["date"] == current_date and slot["period"] == current_period:
            continue
        same_day = slot["date"] == current_date
        slot_name = (
            "daytime (12:00–23:59)"
            if slot["period"] == "day"
            else "nighttime (23:00–07:00)"
        )
        if same_day:
            label = slot_name
        else:
            label = f"{_format_short_date(slot['date'])} {slot_name}"
        if slot.get("location_name"):
            label += f" @ {slot['location_name']}"
        others.append(label)
    if not others:
        return None
    return "Also playing " + " · ".join(others)


def render_output_html(
    title: str,
    ordered_sections: list[dict],
    assignments: dict[str, list[dict]],
    locations: dict[str, dict],
) -> str:
    def esc(text: str | None) -> str:
        return html.escape(text or "")

    parts: list[str] = []
    parts.append("<!DOCTYPE html>")
    parts.append('<html lang="en">')
    parts.append("<head>")
    parts.append('  <meta charset="UTF-8">')
    parts.append(
        '  <meta name="viewport" content="width=device-width, initial-scale=1.0">'
    )
    parts.append(f"  <title>{esc(title)}</title>")
    parts.append("  <style>")
    parts.append("""
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; line-height: 1.5; max-width: 960px; margin: 0 auto; padding: 0 24px; color: #111; background: #fff; }
    h1 { margin-bottom: 32px; font-size: 2em; position: sticky; top: 28px; background: #fff; z-index: 30; padding: 12px 0 8px; border-bottom: 2px solid #222; }
    section.date-section { margin-bottom: 48px; }
    h2 { position: sticky; top: 96px; background: #fff; z-index: 20; padding: 10px 0 8px; margin-bottom: 8px; font-size: 1.5em; border-bottom: 1px solid #ccc; }
    h3.period-heading { position: sticky; top: 150px; background: #fff; z-index: 10; padding: 8px 0 6px; margin: 24px 0 12px; font-size: 1.15em; color: #333; text-transform: uppercase; letter-spacing: 0.05em; border-bottom: none; }
    .fade-after::after { content: ''; position: absolute; left: 0; right: 0; top: 100%; height: 36px; background: linear-gradient(to bottom, rgba(255,255,255,1) 0%, rgba(255,255,255,0.9) 20%, rgba(255,255,255,0.75) 35%, rgba(255,255,255,0.5) 55%, rgba(255,255,255,0.15) 78%, rgba(255,255,255,0) 100%); pointer-events: none; opacity: 0; transition: opacity 0.15s; }
    .fade-after.stuck::after { opacity: 1; }
    h4.location-heading { position: sticky; top: 190px; background: #fff; z-index: 10; font-size: 1em; padding: 6px 0 4px; margin: 16px 0 8px; color: #555; border-bottom: 1px solid #eee; }
    h4.location-heading small { font-weight: normal; color: #999; }
    ul.artist-list { list-style: none; padding: 0; margin: 0; }
    li.artist-item { display: flex; align-items: center; gap: 16px; padding: 12px; margin-bottom: 8px; background: #f9f9f9; border-radius: 8px; border: 1px solid #eee; }
    .artist-photo { width: 120px; height: 120px; object-fit: cover; border-radius: 6px; flex-shrink: 0; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
    .photo-placeholder { width: 120px; height: 120px; flex-shrink: 0; }
    .artist-info { flex: 1; min-width: 0; }
    .artist-name { font-weight: 700; font-size: 1.15em; display: block; margin-bottom: 3px; }
    .artist-schedule { color: #888; font-size: 0.85em; display: block; margin-bottom: 6px; }
    .links { display: flex; flex-wrap: wrap; column-gap: 18px; row-gap: 4px; align-items: center; }
    .links a { display: inline-flex; align-items: center; gap: 5px; text-decoration: none; color: #555; font-size: 0.72em; padding: 3px 0; min-width: 72px; font-variant-numeric: tabular-nums; }
    .links a:hover { color: #111; }
    .links a svg { flex-shrink: 0; }
    .missing { color: #aaa; font-size: 0.8em; }
    @media (max-width: 480px) {
      body { padding: 0 12px; }
      .cmd-bar { font-size: 0.7em; }
      h1 { font-size: 1.5em; padding: 8px 0 6px; top: 28px; }
      h2 { font-size: 1.2em; padding: 6px 0; top: 78px; }
      h3.period-heading { font-size: 1em; padding: 6px 0 4px; top: 118px; margin: 16px 0 8px; }
      h4.location-heading { top: 152px; }
      li.artist-item { gap: 10px; padding: 10px; }
      .artist-photo { width: 72px; height: 72px; border-radius: 4px; }
      .photo-placeholder { width: 72px; height: 72px; }
      .artist-name { font-size: 1em; }
      .artist-schedule { font-size: 0.75em; margin-bottom: 4px; }
      .links { column-gap: 8px; row-gap: 0; }
      .links a { font-size: 0.68em; min-width: 72px; gap: 3px; }
      .links a svg { width: 14px; height: 14px; }
      .heart-btn svg { width: 18px; height: 18px; }
      .share-bar { font-size: 0.8em; padding: 8px 12px; }
      .modal-content { padding: 20px 16px; max-width: 320px; }
      .modal-tabs { flex-direction: column; }
      .sync-qr-section { display: none; }
    }
    .heart-btn { background: none; border: none; cursor: pointer; padding: 6px; flex-shrink: 0; align-self: flex-start; margin-top: 2px; }
    .heart-btn svg { fill: none; stroke: #ccc; stroke-width: 2; transition: fill 0.15s, stroke 0.15s; width: 22px; height: 22px; }
    .heart-btn:hover:not(.active) svg { stroke: #ddd; }
    .heart-btn.active svg { fill: #e53e3e; stroke: #e53e3e; }
    .cmd-bar { position: sticky; top: 0; z-index: 40; background: #111; color: #fff; display: flex; align-items: stretch; height: 28px; font-size: 0.75em; }
    .cmd-bar button { background: none; color: #999; border: none; cursor: pointer; padding: 0; font-size: 1em; white-space: nowrap; flex: 1; text-align: center; transition: color 0.1s; letter-spacing: 0.03em; }
    .cmd-bar button:hover { color: #fff; }
    .cmd-bar button:focus { outline: none; }
    .cmd-bar button.active { color: #e53e3e; }
    .cmd-bar .sep { color: #333; margin: 0; display: flex; align-items: center; }
    .filter-active .artist-item:not(.hearted) { display: none; }

    /* --- Modals --- */
    .mo { display:none; position:fixed; top:0; left:0; right:0; bottom:0; z-index:100; background:rgba(0,0,0,.4); backdrop-filter:blur(4px); -webkit-backdrop-filter:blur(4px); touch-action:none; overscroll-behavior:none; }
    .mo.open { display:flex; justify-content:center; align-items:flex-start; padding-top:16vh; }
    .mo-box { background:#fff; border-radius:14px; padding:24px; width:320px; max-width:calc(100vw - 48px); text-align:center; color:#111; box-shadow:0 8px 24px rgba(0,0,0,.12); position:relative; }
    .mo-box h3 { margin:0 0 6px; font-size:1em; font-weight:600; }
    .mo-box .sub { font-size:.8em; color:#999; margin:0 0 14px; }
    .mo-box .link-field { display:block; background:#f5f5f5; padding:12px 14px; border-radius:8px; font-size:.82em; font-family:inherit; word-break:break-all; color:#333; cursor:pointer; transition:background .15s; margin:0; border:none; }
    .mo-box .link-field:hover { background:#eee; }
    .mo-box .link-field.copied { background:#d4edda; }
    .mo-box canvas { display:block; margin:10px auto; border-radius:6px; }
    .mo-box .or-line { display:flex; align-items:center; gap:10px; margin:10px 0; }
    .mo-box .or-line hr { flex:1; border:none; border-top:1px solid #e0e0e0; }
    .mo-box .or-line span { color:#bbb; font-size:.78em; }
    .mo-box .tabs { display:flex; gap:3px; margin-bottom:14px; border-radius:8px; border:1px solid #e0e0e0; padding:3px; background:#f5f5f5; }
    .mo-box .tabs button { flex:1; background:transparent; border:none; padding:7px 4px; cursor:pointer; font-size:.8em; color:#888; border-radius:5px; transition:color .15s,background .15s; }
    .mo-box .tabs button:focus { outline:none; }
    .mo-box .tabs button:hover:not(.on) { background:#eee; color:#555; }
    .mo-box .tabs button.on { background:#111; color:#fff; }
    .mo-box .pane { display:none; }
    .mo-box .pane.on { display:block; }
    .mo-box .lbl { font-size:.82em; color:#333; text-align:left; margin:0 0 4px; }
    .mo-box .steps { counter-reset:s; }
    .mo-box .steps p { text-align:left; font-size:.8em; color:#333; margin:5px 0; padding-left:16px; }
    .mo-box .steps p::before { content:counter(s) ". "; counter-increment:s; font-weight:600; }
    .pin { display:flex; gap:5px; justify-content:center; margin:10px 0; }
    .pin span, .pin input { width:28px; height:36px; text-align:center; font-size:1.2em; font-weight:700; border:1px solid #ddd; border-radius:5px; background:#f5f5f5; color:#111; line-height:36px; display:block; }
    .pin input { padding:0; caret-color:#111; }
    .pin input:focus { outline:none; border-color:#111; background:#fff; }
    .mo-box .btn { background:#111; color:#fff; border:none; padding:7px 18px; border-radius:5px; cursor:pointer; font-size:.82em; margin-top:8px; }
    .mo-box .btn:hover { background:#333; }
    .mo-box .btn:focus { outline:none; }
    .qr-wrap { display:block; }
    @media (max-width:480px) { .qr-wrap { display:none; } .mo-box .tabs { flex-direction:column; } .mo.open { padding-top:10vh; } }
    @media (max-height:500px) { .mo.open { padding-top:5vh; } }
    """)
    parts.append("  </style>")
    parts.append("</head>")
    parts.append("<body>")
    parts.append('  <div class="cmd-bar" id="cmd-bar">')
    parts.append('    <button onclick="openShareModal()">Share</button>')
    parts.append('    <span class="sep">|</span>')
    parts.append('    <button onclick="openSyncModal()">Sync</button>')
    parts.append('    <span class="sep">|</span>')
    parts.append(
        '    <button onclick="toggleFilter(this)" id="btn-filter">My Picks</button>'
    )
    parts.append("  </div>")
    parts.append(f"  <h1>{esc(title)}</h1>")

    # Share modal
    parts.append('  <div class="mo" id="m-share" onclick="closeMo(this)">')
    parts.append('    <div class="mo-box" onclick="event.stopPropagation()">')
    parts.append("      <h3>Share With Friends</h3>")
    parts.append(
        '      <p class="sub">Friends can view your picks. Click the link to copy it.</p>'
    )
    parts.append(
        '      <div class="link-field" id="share-link" onclick="copyLink(this)"></div>'
    )
    parts.append("    </div>")
    parts.append("  </div>")

    # Sync modal
    pin_inputs = "".join(
        f'<input class="pin-input" type="text" inputmode="numeric" maxlength="1" autocomplete="off" data-i="{i}"/>'
        for i in range(6)
    )
    parts.append('  <div class="mo" id="m-sync" onclick="closeMo(this)">')
    parts.append('    <div class="mo-box" onclick="event.stopPropagation()">')
    parts.append("      <h3>Sync Your Devices</h3>")
    parts.append('      <div class="tabs">')
    parts.append(
        '        <button class="on" onclick="syncTab(\'send\',this)">Send to another device</button>'
    )
    parts.append(
        "        <button onclick=\"syncTab('recv',this)\">Receive from another device</button>"
    )
    parts.append("      </div>")
    parts.append('      <div class="pane on" id="p-send">')
    parts.append('        <div class="qr-wrap">')
    parts.append('          <p class="lbl">Scan this QR with your other device:</p>')
    parts.append('          <canvas id="sync-qr" width="180" height="180"></canvas>')
    parts.append('          <div class="or-line"><hr><span>or</span><hr></div>')
    parts.append("        </div>")
    parts.append('        <p class="lbl">On your other device:</p>')
    parts.append('        <div class="steps">')
    parts.append("          <p>Open <strong>stonetechno.deftlab.dev</strong></p>")
    parts.append("          <p>Click <strong>Sync</strong></p>")
    parts.append("          <p>Click <strong>Receive from another device</strong></p>")
    parts.append("          <p>Enter the code shown below</p>")
    parts.append("        </div>")
    parts.append('        <div class="pin" id="pin-display"></div>')
    parts.append("      </div>")
    parts.append('      <div class="pane" id="p-recv">')
    parts.append('        <p class="lbl">On your other device:</p>')
    parts.append('        <div class="steps">')
    parts.append("          <p>Click <strong>Sync</strong></p>")
    parts.append("          <p>Click <strong>Send to another device</strong></p>")
    parts.append("        </div>")
    parts.append('        <p class="lbl" style="margin-top:10px">On this device:</p>')
    parts.append('        <div class="steps"><p>Enter the code</p></div>')
    parts.append(f'        <div class="pin" id="pin-input">{pin_inputs}</div>')
    parts.append('        <button class="btn" onclick="submitPin()">Connect</button>')
    parts.append("      </div>")
    parts.append("    </div>")
    parts.append("  </div>")

    def _link(href: str, svg: str, label: str = "") -> str:
        txt = f"{svg} {esc(label)}" if label else svg
        return f'<a href="{esc(href)}" target="_blank" rel="noopener noreferrer" title="{esc(label)}">{txt}</a>'

    def render_artist_card(a: dict, cur_date: str, cur_period: str) -> None:
        name = a.get("name") or ""
        photo_local = a.get("photo_local")
        ig = a.get("instagram")
        sc = a.get("soundcloud")
        sp = a.get("spotify")
        lt = a.get("linktree")
        yt = a.get("youtube")
        ig_f = format_followers(a.get("ig_followers"))
        sc_f = format_followers(a.get("sc_followers"))
        sp_l = format_followers(a.get("spotify_listeners"))
        schedule = _format_other_slots(a.get("all_slots", []), cur_date, cur_period)

        artist_id = a.get("overlay_id", "")
        parts.append(
            f'      <li class="artist-item" data-artist-id="{esc(artist_id)}">'
        )
        if photo_local:
            parts.append(
                f'        <img class="artist-photo" src="photos/{esc(photo_local)}" alt="{esc(name)}" width="120" height="120" loading="lazy">'
            )
        else:
            parts.append('        <div class="photo-placeholder"></div>')
        parts.append('        <div class="artist-info">')
        parts.append(f'        <span class="artist-name">{esc(name)}</span>')
        if schedule:
            parts.append(
                f'        <span class="artist-schedule">{esc(schedule)}</span>'
            )
        parts.append('        <div class="links">')
        if ig:
            parts.append(f"          {_link(ig, SVG_IG, ig_f or '')}")
        if sc:
            parts.append(f"          {_link(sc, SVG_SC, sc_f or '')}")
        if sp:
            parts.append(f"          {_link(sp, SVG_SP, sp_l or '')}")
        if lt:
            parts.append(f"          {_link(lt, SVG_LT)}")
        if yt:
            parts.append(f"          {_link(yt, SVG_YT)}")
        if not ig and not sc and not sp and not lt and not yt:
            parts.append('          <span class="missing">No links</span>')
        parts.append("        </div>")
        parts.append("        </div>")
        parts.append(
            '        <button class="heart-btn" onclick="toggleHeart(this)" aria-label="Add to favorites" aria-pressed="false"><svg viewBox="0 0 24 24"><path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/></svg></button>'
        )
        parts.append("      </li>")

    dates_seen: list[str] = []
    sections_by_date: dict[str, list[dict]] = {}
    for sec in ordered_sections:
        sections_by_date.setdefault(sec["date"], []).append(sec)
        if sec["date"] not in dates_seen:
            dates_seen.append(sec["date"])

    for date_str in dates_seen:
        date_heading = _format_date_heading(date_str)
        parts.append('  <section class="date-section">')
        parts.append(f"    <h2>{esc(date_heading)}</h2>")

        for sec in sections_by_date[date_str]:
            timestamp = sec["key"]
            is_night = sec["period"] == "night"
            time_range = "23:00 – 07:00" if is_night else "12:00 – 23:59"
            period_label = f"{sec['period'].capitalize()} ({time_range})"
            artists = assignments.get(timestamp, [])

            h3_cls = "period-heading" if is_night else "period-heading fade-after"
            parts.append(f'    <h3 class="{h3_cls}">{esc(period_label)}</h3>')

            if not artists:
                parts.append("    <p>No artists found.</p>")
                continue

            if is_night:
                by_loc: dict[str | None, list[dict]] = {}
                for a in artists:
                    by_loc.setdefault(a.get("location_id"), []).append(a)

                for loc_id, loc_artists in by_loc.items():
                    loc = locations.get(loc_id) if loc_id else None
                    if loc:
                        desc = (
                            f" <small>{esc(loc['description'])}</small>"
                            if loc.get("description")
                            else ""
                        )
                        parts.append(
                            f'    <h4 class="location-heading fade-after">{esc(loc["name"])}{desc}</h4>'
                        )
                    parts.append('    <ul class="artist-list">')
                    for a in loc_artists:
                        render_artist_card(a, sec["date"], sec["period"])
                    parts.append("    </ul>")
            else:
                parts.append('    <ul class="artist-list">')
                for a in artists:
                    render_artist_card(a, sec["date"], sec["period"])
                parts.append("    </ul>")

        parts.append("  </section>")

    parts.append("  <script>")
    parts.append("""
    // Sticky gradient observer
    document.querySelectorAll('.fade-after').forEach(el => {
      const top = parseFloat(getComputedStyle(el).top) || 0;
      const s = document.createElement('div');
      s.style.cssText = 'height:1px;width:0;pointer-events:none;visibility:hidden;margin-bottom:-1px;position:relative;top:-' + top + 'px';
      el.parentNode.insertBefore(s, el);
      new IntersectionObserver(([e]) => {
        el.classList.toggle('stuck', e.intersectionRatio === 0);
      }, {threshold: 0}).observe(s);
    });

    // Hearts
    const API = '/api';
    let editCode = localStorage.getItem('stc_edit_code');
    let shareCode = localStorage.getItem('stc_share_code');
    let localPicks = new Set(JSON.parse(localStorage.getItem('stc_picks') || '[]'));
    let readOnly = false;
    let filterActive = false;

    // Blur buttons after click, ESC to close modals
    document.addEventListener('click', e => { if (e.target.matches('button')) e.target.blur(); });
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape') { document.querySelectorAll('.modal-overlay.visible').forEach(m => m.classList.remove('visible')); document.body.classList.remove('modal-open'); }
    });

    function saveLocal() {
      localStorage.setItem('stc_picks', JSON.stringify([...localPicks]));
      updateUI();
    }

    function updateUI() {
      const btn = document.getElementById('btn-filter');
      const n = localPicks.size;
      const label = filterActive ? 'Show All' : 'My Picks';
      btn.textContent = n ? label + ' (' + n + ')' : label;
      document.querySelectorAll('.artist-item').forEach(li => {
        li.classList.toggle('hearted', localPicks.has(li.dataset.artistId));
      });
    }

    function applyHearts() {
      document.querySelectorAll('.heart-btn').forEach(btn => {
        const id = btn.closest('[data-artist-id]').dataset.artistId;
        const active = localPicks.has(id);
        btn.classList.toggle('active', active);
        btn.setAttribute('aria-pressed', active);
      });
      updateUI();
    }

    function toggleFilter(btn) {
      filterActive = !filterActive;
      document.body.classList.toggle('filter-active', filterActive);
      btn.classList.toggle('active', filterActive);
      updateUI();
      updateGroupVisibility();
    }

    function updateGroupVisibility() {
      document.querySelectorAll('ul.artist-list').forEach(ul => {
        const hasVisible = filterActive
          ? ul.querySelector('.artist-item.hearted') !== null
          : true;
        ul.style.display = hasVisible ? '' : 'none';
        const prev = ul.previousElementSibling;
        if (prev && (prev.matches('h3') || prev.matches('h4'))) {
          prev.style.display = hasVisible ? '' : 'none';
        }
      });
      document.querySelectorAll('section.date-section').forEach(sec => {
        const hasVisible = filterActive
          ? sec.querySelector('.artist-item.hearted') !== null
          : true;
        sec.style.display = hasVisible ? '' : 'none';
      });
      document.querySelectorAll('h3.period-heading').forEach(h3 => {
        if (!filterActive) { h3.style.display = ''; return; }
        let el = h3.nextElementSibling;
        let found = false;
        while (el && !el.matches('h3') && !el.matches('h2')) {
          if (el.querySelector && el.querySelector('.artist-item.hearted')) { found = true; break; }
          el = el.nextElementSibling;
        }
        h3.style.display = found ? '' : 'none';
      });
    }

    async function ensureSession() {
      if (editCode) return;
      try {
        const res = await fetch(API + '/session', {method: 'POST'});
        if (!res.ok) return;
        const data = await res.json();
        editCode = data.edit_code;
        shareCode = data.share_code;
        localStorage.setItem('stc_edit_code', editCode);
        localStorage.setItem('stc_share_code', shareCode);
        connectWS(editCode);
      } catch {}
    }

    async function toggleHeart(btn) {
      if (readOnly) return;
      const li = btn.closest('[data-artist-id]');
      const id = li.dataset.artistId;
      const adding = !localPicks.has(id);

      if (adding) localPicks.add(id); else localPicks.delete(id);
      btn.classList.toggle('active', adding);
      btn.setAttribute('aria-pressed', adding);
      li.classList.toggle('hearted', adding);
      saveLocal();

      await ensureSession();
      if (!editCode) return;

      try {
        const method = adding ? 'POST' : 'DELETE';
        const res = await fetch(API + '/session/' + editCode + '/pick/' + id, {method});
        if (!res.ok && res.status !== 204) throw new Error();
      } catch {
        if (adding) localPicks.delete(id); else localPicks.add(id);
        btn.classList.toggle('active', !adding);
        btn.setAttribute('aria-pressed', !adding);
        li.classList.toggle('hearted', !adding);
        saveLocal();
      }
    }

    async function loadFromServer(code) {
      try {
        const res = await fetch(API + '/session/' + code);
        if (!res.ok) return;
        const data = await res.json();
        localPicks = new Set(data.picks);
        readOnly = data.readonly;
        if (data.edit_code) { editCode = data.edit_code; localStorage.setItem('stc_edit_code', editCode); }
        if (data.share_code) { shareCode = data.share_code; localStorage.setItem('stc_share_code', shareCode); }
        saveLocal();
        applyHearts();
        if (readOnly) document.querySelectorAll('.heart-btn').forEach(b => b.style.display = 'none');
      } catch {}
    }

    async function reconcile() {
      if (!editCode || readOnly) return;
      try {
        const res = await fetch(API + '/session/' + editCode);
        if (!res.ok) return;
        const data = await res.json();
        const serverPicks = new Set(data.picks);
        for (const id of localPicks) {
          if (!serverPicks.has(id)) fetch(API + '/session/' + editCode + '/pick/' + id, {method: 'POST'}).catch(() => {});
        }
        for (const id of serverPicks) localPicks.add(id);
        saveLocal();
        applyHearts();
      } catch {}
    }

    // WebSocket real-time sync
    let _ws = null;
    function connectWS(code) {
      if (_ws) { try { _ws.close(); } catch {} }
      if (!code) return;
      const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
      _ws = new WebSocket(proto + '//' + location.host + '/ws/' + code);
      _ws.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);
          if (data.picks) {
            localPicks = new Set(data.picks);
            saveLocal();
            applyHearts();
            if (data.readonly !== undefined) {
              readOnly = data.readonly;
              if (readOnly) document.querySelectorAll('.heart-btn').forEach(b => b.style.display = 'none');
            }
          }
        } catch {}
      };
      _ws.onclose = () => { setTimeout(() => { if (editCode || shareCode) connectWS(code); }, 2000); };
    }

    // Modal helpers
    // Scroll lock (PQINA technique — works on iOS Safari)
    function _blockTouch(e) { e.preventDefault(); }
    function openMo(id) {
      const mo = document.getElementById(id);
      mo.classList.add('open');
      mo.addEventListener('touchmove', _blockTouch, {passive:false});
    }
    function closeMo(el) {
      const mo = el.closest ? el.closest('.mo') : el;
      mo.removeEventListener('touchmove', _blockTouch);
      mo.classList.remove('open');
    }
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape') {
        const open = document.querySelectorAll('.mo.open');
        if (open.length) { open.forEach(m => { m.removeEventListener('touchmove', _blockTouch); m.classList.remove('open'); }); }
      }
    });
    document.addEventListener('click', e => { if (e.target.matches('button')) e.target.blur(); });

    function loadQR(id, url) {
      const c = document.getElementById(id);
      if (!c) return;
      const img = new Image();
      img.crossOrigin = 'anonymous';
      img.onload = () => { c.getContext('2d').drawImage(img, 0, 0, c.width, c.height); };
      img.src = 'https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=' + encodeURIComponent(url);
    }

    // Share modal
    function openShareModal() {
      if (!shareCode) { alert('Heart an artist first.'); return; }
      document.getElementById('share-link').textContent = 'https://stonetechno.deftlab.dev/?code=' + shareCode;
      openMo('m-share');
    }
    function copyLink(el) {
      navigator.clipboard.writeText(el.textContent);
      el.classList.add('copied');
      const t = el.textContent; el.textContent = 'Copied!';
      setTimeout(() => { el.textContent = t; el.classList.remove('copied'); }, 1500);
    }

    // Sync modal
    async function openSyncModal() {
      await ensureSession();
      if (!editCode) { alert('Heart an artist first.'); return; }
      const d = document.getElementById('pin-display');
      d.innerHTML = '';
      for (const ch of editCode) { const s = document.createElement('span'); s.textContent = ch; d.appendChild(s); }
      loadQR('sync-qr', 'https://stonetechno.deftlab.dev/?code=' + editCode);
      openMo('m-sync');
    }
    function syncTab(t, btn) {
      btn.closest('.tabs').querySelectorAll('button').forEach(b => b.classList.remove('on'));
      btn.classList.add('on');
      document.getElementById('p-send').classList.toggle('on', t === 'send');
      document.getElementById('p-recv').classList.toggle('on', t === 'recv');
      if (t === 'recv') { const f = document.querySelector('#pin-input input'); if (f) f.focus(); }
    }

    // Pin inputs
    document.querySelectorAll('#pin-input input').forEach(inp => {
      inp.addEventListener('input', e => {
        const v = e.target.value.replace(/\\D/g, '');
        e.target.value = v.slice(0, 1);
        const i = +e.target.dataset.i;
        if (v && i < 5) { const nx = e.target.parentElement.querySelector('[data-i="'+(i+1)+'"]'); if (nx) nx.focus(); }
        if (v && i === 5) submitPin();
      });
      inp.addEventListener('keydown', e => {
        if (e.key === 'Backspace' && !e.target.value) {
          const i = +e.target.dataset.i;
          if (i > 0) { const pv = e.target.parentElement.querySelector('[data-i="'+(i-1)+'"]'); if (pv) { pv.value=''; pv.focus(); } }
        }
      });
      inp.addEventListener('paste', e => {
        e.preventDefault();
        const t = (e.clipboardData.getData('text')||'').replace(/\\D/g,'').slice(0,6);
        const all = document.querySelectorAll('#pin-input input');
        for (let j=0; j<t.length&&j<6; j++) all[j].value=t[j];
        if (t.length===6) submitPin(); else if (t.length) all[Math.min(t.length,5)].focus();
      });
    });
    function submitPin() {
      const code = Array.from(document.querySelectorAll('#pin-input input')).map(i=>i.value).join('');
      if (code.length!==6) return;
      loadFromServer(code);
      closeMo(document.getElementById('m-sync'));
      document.querySelectorAll('#pin-input input').forEach(i=>i.value='');
    }

    // Init
    (async () => {
      const p = new URLSearchParams(location.search);
      const c = p.get('code');
      if (c) { await loadFromServer(c); connectWS(c); }
      else if (editCode) { await reconcile(); connectWS(editCode); }
      applyHearts();
    })();
    """)
    parts.append("  </script>")
    parts.append("</body>")
    parts.append("</html>")
    return "\n".join(parts)
