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
      h3.period-heading { font-size: 1em; padding: 6px 0 4px; top: 120px; margin: 16px 0 8px; }
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
    }
    .heart-btn { background: none; border: none; cursor: pointer; padding: 6px; flex-shrink: 0; align-self: flex-start; margin-top: 2px; }
    .heart-btn svg { fill: none; stroke: #ccc; stroke-width: 2; transition: fill 0.15s, stroke 0.15s; width: 22px; height: 22px; }
    .heart-btn:hover svg { stroke: #e53e3e; }
    .heart-btn.active svg { fill: #e53e3e; stroke: #e53e3e; }
    .cmd-bar { position: sticky; top: 0; z-index: 40; background: #111; color: #fff; display: flex; align-items: stretch; height: 28px; font-size: 0.75em; }
    .cmd-bar button { background: none; color: #999; border: none; cursor: pointer; padding: 0; font-size: 1em; white-space: nowrap; flex: 1; text-align: center; transition: color 0.1s; letter-spacing: 0.03em; }
    .cmd-bar button:hover { color: #fff; }
    .cmd-bar button:focus { outline: none; }
    .cmd-bar button.active { color: #e53e3e; }
    .cmd-bar .sep { color: #333; margin: 0; display: flex; align-items: center; }
    .modal-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.4); z-index: 100; align-items: center; justify-content: center; backdrop-filter: blur(4px); -webkit-backdrop-filter: blur(4px); }
    .modal-overlay.visible { display: flex; }
    .modal-content { background: #fff; padding: 28px; border-radius: 16px; text-align: center; max-width: 400px; width: 90%; color: #111; box-shadow: 0 8px 30px rgba(0,0,0,0.15); }
    .modal-content h3 { margin: 0 0 8px; font-size: 1.05em; font-weight: 600; }
    .modal-content p { font-size: 0.82em; color: #999; margin: 0 0 16px; line-height: 1.4; }
    .modal-content code { display: block; background: #f8f8f8; padding: 12px; border-radius: 8px; font-size: 1.3em; font-weight: 700; letter-spacing: 0.15em; border: none; margin: 12px 0; color: #111; }
    .modal-content code.share-link { font-size: 0.85em; font-weight: 500; letter-spacing: 0; word-break: break-all; padding: 14px 16px; color: #333; margin-bottom: 0; }
    .modal-content canvas { margin: 12px auto; display: block; border-radius: 8px; }
    .modal-content button { background: #111; color: #fff; border: none; padding: 8px 20px; border-radius: 6px; cursor: pointer; margin: 4px 3px 0; font-size: 0.85em; transition: background 0.1s; }
    .modal-content button:hover { background: #333; }
    .modal-content button:focus { outline: none; }
    .filter-active .artist-item:not(.hearted) { display: none; }
    .modal-close { position: absolute; top: 10px; right: 10px; background: #f0f0f0; border: none; font-size: 0.9em; color: #bbb; cursor: pointer; width: 24px; height: 24px; line-height: 24px; text-align: center; padding: 0; margin: 0; border-radius: 4px; }
    .modal-close:hover { background: #e0e0e0; color: #888; }
    .modal-content { position: relative; }
    .copyable { cursor: pointer; transition: background 0.15s; }
    .copyable:hover { background: #eee; }
    .copyable.copied { background: #d4edda; }
    .modal-tabs { display: flex; gap: 0; margin-bottom: 16px; border-radius: 8px; overflow: hidden; border: 1px solid #e0e0e0; }
    .modal-tabs button { flex: 1; background: #f8f8f8; border: none; padding: 9px; cursor: pointer; font-size: 0.82em; color: #888; transition: all 0.1s; }
    .modal-tabs button:focus { outline: none; }
    .modal-tabs button.active { background: #111; color: #fff; }
    .modal-panel { display: none; }
    .modal-panel.active { display: block; }
    .modal-content input[type="text"] { width: 100%; padding: 12px; font-size: 1.2em; border: 1px solid #e0e0e0; border-radius: 8px; text-align: center; letter-spacing: 0.15em; box-sizing: border-box; margin: 8px 0; font-weight: 600; }
    .modal-content input[type="text"]:focus { outline: none; border-color: #111; }
    .modal-content .step { text-align: left; font-size: 0.8em; color: #888; margin: 6px 0; padding-left: 18px; }
    .modal-content .step::before { content: counter(step) ". "; counter-increment: step; font-weight: 600; color: #111; }
    .modal-content .steps { counter-reset: step; }
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

    # Share modal (read-only link for friends)
    parts.append(
        '  <div class="modal-overlay" id="share-modal" onclick="closeModal(\'share-modal\')">'
    )
    parts.append('    <div class="modal-content" onclick="event.stopPropagation()">')
    parts.append("")
    parts.append("      <h3>Share With Friends</h3>")
    parts.append("      <p>Friends can view your picks. Click the link to copy it.</p>")
    parts.append(
        '      <code class="share-link copyable" id="share-link" onclick="copyShareLink(this)"></code>'
    )
    parts.append("    </div>")
    parts.append("  </div>")

    # Sync modal (read-write code for own devices)
    parts.append(
        '  <div class="modal-overlay" id="sync-modal" onclick="closeModal(\'sync-modal\')">'
    )
    parts.append('    <div class="modal-content" onclick="event.stopPropagation()">')
    parts.append("")
    parts.append("      <h3>Sync Your Devices</h3>")
    parts.append('      <div class="modal-tabs">')
    parts.append(
        '        <button class="active" onclick="switchSyncTab(\'send\', this)">Send to another device</button>'
    )
    parts.append(
        "        <button onclick=\"switchSyncTab('receive', this)\">Receive from another device</button>"
    )
    parts.append("      </div>")
    parts.append('      <div class="modal-panel active" id="sync-send">')
    parts.append('        <div class="steps">')
    parts.append(
        '          <p class="step">Open the lineup page on your other device</p>'
    )
    parts.append(
        '          <p class="step">Tap <strong>Sync</strong> then <strong>Receive</strong></p>'
    )
    parts.append('          <p class="step">Enter this code or scan the QR:</p>')
    parts.append("        </div>")
    parts.append('        <code id="sync-code"></code>')
    parts.append('        <canvas id="sync-qr"></canvas>')
    parts.append("        <div>")
    parts.append("          <button onclick=\"copyLink('sync')\">Copy Link</button>")
    parts.append("        </div>")
    parts.append("      </div>")
    parts.append('      <div class="modal-panel" id="sync-receive">')
    parts.append('        <div class="steps">')
    parts.append(
        '          <p class="step">On your other device, tap <strong>Sync</strong> then <strong>Send</strong></p>'
    )
    parts.append('          <p class="step">Enter the code shown on that device:</p>')
    parts.append("        </div>")
    parts.append(
        '        <input type="text" id="sync-input" placeholder="Enter sync code" maxlength="12" autocomplete="off" />'
    )
    parts.append("        <div>")
    parts.append('          <button onclick="applySyncCode()">Connect</button>')
    parts.append("        </div>")
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
      if (e.key === 'Escape') document.querySelectorAll('.modal-overlay.visible').forEach(m => m.classList.remove('visible'));
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

    function closeModal(id) { document.getElementById(id).classList.remove('visible'); }

    function loadQR(canvasId, url) {
      const canvas = document.getElementById(canvasId);
      canvas.width = 200; canvas.height = 200;
      const img = new Image();
      img.crossOrigin = 'anonymous';
      img.onload = () => { canvas.getContext('2d').drawImage(img, 0, 0, 200, 200); };
      img.src = 'https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=' + encodeURIComponent(url);
    }

    function openShareModal() {
      if (!shareCode) { alert('Heart an artist first to create your picks list.'); return; }
      const url = 'https://stonetechno.deftlab.dev/?code=' + shareCode;
      document.getElementById('share-link').textContent = url;
      document.getElementById('share-modal').classList.add('visible');
    }

    function copyShareLink(el) {
      navigator.clipboard.writeText(el.textContent);
      el.classList.add('copied');
      const orig = el.textContent;
      el.textContent = 'Copied!';
      setTimeout(() => { el.textContent = orig; el.classList.remove('copied'); }, 1500);
    }

    async function openSyncModal() {
      await ensureSession();
      if (!editCode) { alert('Heart an artist first to create your picks list.'); return; }
      const url = 'https://stonetechno.deftlab.dev/?code=' + editCode;
      document.getElementById('sync-code').textContent = editCode;
      loadQR('sync-qr', url);
      document.getElementById('sync-modal').classList.add('visible');
    }

    function switchSyncTab(tab, btn) {
      document.querySelectorAll('#sync-modal .modal-tabs button').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById('sync-send').classList.toggle('active', tab === 'send');
      document.getElementById('sync-receive').classList.toggle('active', tab === 'receive');
      if (tab === 'receive') document.getElementById('sync-input').focus();
    }

    function applySyncCode() {
      const input = document.getElementById('sync-input');
      const code = input.value.trim();
      if (!code) return;
      loadFromServer(code);
      closeModal('sync-modal');
      input.value = '';
    }

    function copyLink(type) {
      const code = type === 'share' ? shareCode : editCode;
      const url = 'https://stonetechno.deftlab.dev/?code=' + code;
      navigator.clipboard.writeText(url);
      event.target.textContent = 'Copied!';
      setTimeout(() => event.target.textContent = 'Copy Link', 1500);
    }

    (async () => {
      const params = new URLSearchParams(location.search);
      const urlCode = params.get('code');
      if (urlCode) { await loadFromServer(urlCode); }
      else if (editCode) { await reconcile(); }
      applyHearts();
      document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible' && editCode && !readOnly) reconcile();
      });
    })();
    """)
    parts.append("  </script>")
    parts.append("</body>")
    parts.append("</html>")
    return "\n".join(parts)
