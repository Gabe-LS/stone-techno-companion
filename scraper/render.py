from __future__ import annotations

import html
import uuid
from datetime import datetime
from pathlib import Path

from .scrape import format_followers

ICONS_DIR = Path(__file__).resolve().parent / "icons"


def _load_icon(name: str) -> str:
    path = ICONS_DIR / f"{name}-square-round.svg"
    if path.exists():
        svg = path.read_text(encoding="utf-8").strip()
        if "<?xml" in svg:
            idx = svg.find("<svg")
            if idx != -1:
                svg = svg[idx:]
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
    description = "Explore the Stone Techno 2026 line-up: artist profiles, social links, follower counts. Save your picks and share them with friends."
    parts.append(f'  <meta name="description" content="{esc(description)}">')
    parts.append(f'  <meta property="og:title" content="{esc(title)}">')
    parts.append(f'  <meta property="og:description" content="{esc(description)}">')
    parts.append('  <meta property="og:type" content="website">')
    parts.append(
        '  <meta property="og:url" content="https://stonetechno.deftlab.dev/">'
    )
    parts.append(
        '  <meta property="og:image" content="https://stonetechno.deftlab.dev/favicon.png">'
    )
    parts.append('  <meta name="twitter:card" content="summary">')
    parts.append(f'  <meta name="twitter:title" content="{esc(title)}">')
    parts.append(f'  <meta name="twitter:description" content="{esc(description)}">')
    parts.append(
        '  <meta name="twitter:image" content="https://stonetechno.deftlab.dev/favicon.png">'
    )
    parts.append(
        '  <script defer src="https://analytics.deftlab.dev/script.js" data-website-id="3ca133b8-9f1b-405f-9b29-04d615d9d08a"></script>'
    )
    import base64 as _b64

    favicon_b64 = _b64.b64encode((ICONS_DIR / "favicon.svg").read_bytes()).decode()
    parts.append(
        f'  <link rel="icon" type="image/svg+xml" href="data:image/svg+xml;base64,{favicon_b64}">'
    )
    parts.append("  <style>")
    parts.append("""
    *, *::before, *::after { box-sizing: border-box; }
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
    .photo-placeholder { width: 120px; height: 120px; flex-shrink: 0; background: #eee; border-radius: 6px; }
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
      .photo-placeholder { width: 72px; height: 72px; border-radius: 4px; }
      .artist-name { font-size: 1em; }
      .artist-schedule { font-size: 0.75em; margin-bottom: 4px; }
      .links { column-gap: 8px; row-gap: 0; }
      .links a { font-size: 0.68em; min-width: 72px; gap: 3px; }
      .links a svg { width: 14px; height: 14px; }
      .heart-btn svg { width: 18px; height: 18px; }
    }
    .heart-btn { background: none; border: none; cursor: pointer; padding: 6px; flex-shrink: 0; align-self: flex-start; margin-top: 2px; }
    .heart-btn svg { fill: none; stroke: #ccc; stroke-width: 2; transition: fill 0.15s, stroke 0.15s; width: 22px; height: 22px; }
    .heart-btn:hover:not(.active) svg { stroke: #999; }
    .heart-btn:focus:not(:focus-visible) { outline: none; }
    .heart-btn.active svg { fill: #e53e3e; stroke: #e53e3e; }
    .cmd-bar { position: sticky; top: 0; z-index: 40; background: #111; color: #fff; display: flex; align-items: stretch; height: 28px; font-size: 0.75em; }
    .cmd-bar button { background: none; color: #999; border: none; cursor: pointer; padding: 0; font-size: 1em; white-space: nowrap; flex: 1; text-align: center; transition: color 0.1s; letter-spacing: 0.03em; }
    .cmd-bar button:hover { color: #fff; }
    .cmd-bar button:focus-visible { outline: 1px solid #fff; outline-offset: -2px; }
    .cmd-bar button:focus:not(:focus-visible) { outline: none; }
    .cmd-bar button.active { color: #fff; }
    .cmd-bar .sep { color: #333; margin: 0; display: flex; align-items: center; }
    .filter-active .artist-item:not(.hearted) { display: none; }

    /* --- Modals --- */
    html.scroll-locked, html.scroll-locked body { overflow:hidden; }
    html.scroll-locked body { position:fixed; left:0; right:0; }
    .modal-overlay { display:none; position:fixed; inset:0; z-index:100; background:rgba(0,0,0,.4); padding:24px; }
    .modal-overlay.open { display:flex; justify-content:center; align-items:center; }
    .modal-box { background:#fff; border-radius:14px; padding:24px; width:420px; max-width:100%; text-align:center; color:#111; box-shadow:0 8px 24px rgba(0,0,0,.12); }
    .modal-box h3 { margin:0 0 6px; font-size:1em; font-weight:600; }
    .modal-box .sub { font-size:.8em; color:#999; margin:0 0 14px; }
    .modal-link { display:block; width:100%; background:#f5f5f5; padding:12px 14px; border-radius:8px; font-size:.82em; font-family:inherit; color:#333; cursor:pointer; transition:background .15s; margin:0; border:none; text-align:left; overflow:hidden; text-overflow:clip; white-space:nowrap; box-sizing:border-box; outline:none; }
    .modal-link:hover { background:#eee; }
    .modal-link.copied { background:#d4edda; text-align:center; }
    .modal-box canvas { display:block; margin:10px auto; border-radius:6px; }
    .modal-box .or-line { display:flex; align-items:center; gap:10px; margin:10px 0; }
    .modal-box .or-line hr { flex:1; border:none; border-top:1px solid #e0e0e0; }
    .modal-box .or-line span { color:#bbb; font-size:.78em; }
    .modal-box .tabs { display:flex; gap:3px; margin-bottom:14px; border-radius:8px; border:1px solid #e0e0e0; padding:3px; background:#f5f5f5; }
    .modal-box .tabs button { flex:1; background:transparent; border:none; padding:7px 4px; cursor:pointer; font-size:.8em; color:#888; border-radius:5px; transition:color .15s,background .15s; }
    .modal-box .tabs button:focus-visible { outline:1px solid #111; outline-offset:-2px; }
    .modal-box .tabs button:focus:not(:focus-visible) { outline:none; }
    .modal-box .tabs button:hover:not(.on) { background:#eee; color:#555; }
    .modal-box .tabs button.on { background:#111; color:#fff; }
    .modal-box .pane { display:none; }
    .modal-box .pane.on { display:block; }
    .modal-box .lbl { font-size:.82em; color:#333; text-align:left; margin:0 0 4px; }
    .modal-box .recv-lbl { font-size:.82em; color:#333; text-align:left; margin:10px 0 4px; }
    .modal-box .steps { counter-reset:s; }
    .modal-box .steps p { text-align:left; font-size:.8em; color:#333; margin:5px 0; padding-left:16px; }
    .modal-box .steps p::before { content:counter(s) ". "; counter-increment:s; font-weight:600; }
    .pin { display:flex; gap:5px; justify-content:center; margin:10px 0; }
    .pin span { width:28px; height:36px; font-size:1.2em; font-weight:700; border:1px solid #ddd; border-radius:5px; background:#f5f5f5; color:#111; display:flex; align-items:center; justify-content:center; line-height:1; }
    .sync-expiry { font-size:.75em; color:#999; text-align:center; margin:8px 0 0; }
    .sync-expiry a { color:inherit; text-decoration:underline; cursor:pointer; }
    .pin-wrap { position:relative; cursor:text; margin:10px 0; -webkit-tap-highlight-color:transparent; }
    .pin-wrap .pin { pointer-events:none; }
    .pin-wrap .pin span.active { border-color:#111; background:#fff; }
    .pin-wrap.focused .pin span.active:empty::after { content:''; width:2px; height:1.2em; background:#111; border-radius:1px; animation:blink 1s step-end infinite; }
    @keyframes blink { 0%,100% { opacity:1; } 50% { opacity:0; } }
    .pin-wrap .pin span.filled { color:#111; }
    .pin-real { position:absolute; inset:0; opacity:0; font-size:16px; width:100%; height:100%; border:none; padding:0; margin:0; -webkit-tap-highlight-color:transparent; }
    .modal-box .btn { background:#111; color:#fff; border:none; padding:7px 18px; border-radius:5px; cursor:pointer; font-size:.82em; margin-top:8px; }
    .modal-box .btn:hover { background:#333; }
    .modal-box .btn:focus-visible { outline:1px solid #111; outline-offset:2px; }
    .modal-box .btn:focus:not(:focus-visible) { outline:none; }
    .qr-wrap { display:block; }
    @media (max-width:480px) { .qr-wrap { display:none; } .modal-box .tabs { flex-direction:column; } }
    """)
    parts.append("  </style>")
    parts.append("</head>")
    parts.append("<body>")
    parts.append('  <div class="cmd-bar" id="cmd-bar">')
    parts.append(
        '    <button onmousedown="this.blur()" onclick="toggleFilter(this)" id="btn-filter">Show My Picks</button>'
    )
    parts.append('    <span class="sep">|</span>')
    parts.append(
        '    <button onmousedown="this.blur()" onclick="openShareModal()">Share My Picks</button>'
    )
    parts.append('    <span class="sep">|</span>')
    parts.append(
        '    <button onmousedown="this.blur()" onclick="openSyncModal()">Sync My Picks</button>'
    )
    parts.append("  </div>")
    parts.append(f"  <h1>{esc(title)}</h1>")

    # Share modal
    parts.append(
        '  <div class="modal-overlay" id="m-share" role="dialog" aria-modal="true" aria-labelledby="m-share-title">'
    )
    parts.append('    <div class="modal-box">')
    parts.append('      <h3 id="m-share-title">Share My Picks</h3>')
    parts.append(
        '      <p class="sub" style="color:inherit">Friends can view your picks. Click the link to copy it.</p>'
    )
    parts.append(
        '      <input type="text" readonly class="modal-link" id="share-link">'
    )
    parts.append("    </div>")
    parts.append("  </div>")

    # Sync modal
    parts.append(
        '  <div class="modal-overlay" id="m-sync" role="dialog" aria-modal="true" aria-labelledby="m-sync-title">'
    )
    parts.append('    <div class="modal-box">')
    parts.append('      <h3 id="m-sync-title">Sync Your Devices</h3>')
    parts.append('      <div class="tabs">')
    parts.append(
        '        <button type="button" class="on" onclick="syncTab(\'send\',this)">Send to another device</button>'
    )
    parts.append(
        '        <button type="button" onclick="syncTab(\'recv\',this)">Receive from another device</button>'
    )
    parts.append("      </div>")
    parts.append('      <div class="pane on" id="p-send">')
    parts.append('        <div id="sync-pending">')
    parts.append('          <div class="qr-wrap">')
    parts.append('            <p class="lbl">Scan this QR with your other device:</p>')
    parts.append(
        '            <canvas id="sync-qr" width="360" height="360" style="width:120px;height:120px"></canvas>'
    )
    parts.append('            <div class="or-line"><hr><span>or</span><hr></div>')
    parts.append("          </div>")
    parts.append('          <p class="lbl">On your other device:</p>')
    parts.append('          <div class="steps">')
    parts.append("            <p>Open <strong>stonetechno.deftlab.dev</strong></p>")
    parts.append("            <p>Click <strong>Sync My Picks</strong></p>")
    parts.append(
        "            <p>Click <strong>Receive from another device</strong></p>"
    )
    parts.append("            <p>Enter the code shown below</p>")
    parts.append("          </div>")
    parts.append('          <div class="pin" id="pin-display"></div>')
    parts.append('          <p class="sync-expiry" id="sync-expiry"></p>')
    parts.append("        </div>")
    parts.append(
        '        <div id="sync-done" style="display:none;text-align:center;padding:24px 0">'
    )
    parts.append(
        '          <svg viewBox="0 0 52 52" width="52" height="52"><circle cx="26" cy="26" r="24" fill="none" stroke="#4caf50" stroke-width="3"/><path fill="none" stroke="#4caf50" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" d="M15 27l7 7 15-15"/></svg>'
    )
    parts.append('          <p style="margin:12px 0 0">Device synced successfully</p>')
    parts.append("        </div>")
    parts.append("      </div>")
    parts.append('      <div class="pane" id="p-recv">')
    parts.append('        <p class="lbl">On your other device:</p>')
    parts.append('        <div class="steps">')
    parts.append("          <p>Click <strong>Sync</strong></p>")
    parts.append("          <p>Click <strong>Send to another device</strong></p>")
    parts.append("        </div>")
    parts.append('        <p class="recv-lbl">On this device:</p>')
    parts.append('        <div class="steps"><p>Enter the code</p></div>')
    pin_spans = "<span></span>" * 6
    parts.append(
        f'        <div class="pin-wrap" id="pin-wrap">'
        f'<div class="pin" id="pin-boxes">{pin_spans}</div>'
        f'<input class="pin-real" id="pin-input" type="text" inputmode="numeric" maxlength="6" autocomplete="off"/>'
        f"</div>"
    )
    parts.append(
        '        <button type="button" class="btn" onclick="submitPin()">Connect</button>'
    )
    parts.append("      </div>")
    parts.append("    </div>")
    parts.append("  </div>")

    def _link(href: str, svg: str, label: str = "") -> str:
        txt = f"{svg} {esc(label)}" if label else svg
        return f'<a href="{esc(href)}" target="_blank" rel="noopener noreferrer" title="{esc(label)}">{txt}</a>'

    def render_artist_card(
        a: dict, cur_date: str, cur_period: str, loc_id: str | None = None
    ) -> None:
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

        card_key = f"{a.get('overlay_id', '')}:{cur_date}:{cur_period}:{loc_id or ''}"
        artist_id = str(uuid.uuid5(uuid.NAMESPACE_URL, card_key))
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
                        render_artist_card(a, sec["date"], sec["period"], loc_id)
                    parts.append("    </ul>")
            else:
                parts.append('    <ul class="artist-list">')
                for a in artists:
                    render_artist_card(a, sec["date"], sec["period"])
                parts.append("    </ul>")

        parts.append("  </section>")

    qr_js = (ICONS_DIR.parent / "qrcode.min.js").read_text(encoding="utf-8")
    parts.append(f"  <script>{qr_js}</script>")
    parts.append("  <script>")
    parts.append("""
    // Sticky gradient observer
    document.querySelectorAll('.fade-after').forEach(el => {
      const top = parseFloat(getComputedStyle(el).top) || 0;
      const s = document.createElement('div');
      s.style.cssText = 'height:0;width:0;pointer-events:none;visibility:hidden;position:relative;top:-' + top + 'px';
      el.parentNode.insertBefore(s, el);
      new IntersectionObserver(([e]) => {
        el.classList.toggle('stuck', e.intersectionRatio === 0);
      }, {threshold: 0}).observe(s);
    });

    // Hearts
    const API = '/api';
    // Migrate old localStorage keys
    if (localStorage.getItem('stc_edit_code') && !localStorage.getItem('stc_session_id')) {
      localStorage.setItem('stc_session_id', localStorage.getItem('stc_edit_code'));
      localStorage.removeItem('stc_edit_code');
    }
    if (localStorage.getItem('stc_share_code') && !localStorage.getItem('stc_share_token')) {
      localStorage.setItem('stc_share_token', localStorage.getItem('stc_share_code'));
      localStorage.removeItem('stc_share_code');
    }
    let sessionId = localStorage.getItem('stc_session_id');
    let shareToken = localStorage.getItem('stc_share_token');
    let localPicks; try { localPicks = new Set(JSON.parse(localStorage.getItem('stc_picks') || '[]')); } catch { localPicks = new Set(); localStorage.removeItem('stc_picks'); }
    let readOnly = false;
    let filterActive = false;

    function saveLocal() {
      localStorage.setItem('stc_picks', JSON.stringify([...localPicks]));
      updateUI();
    }

    function updateUI() {
      const btn = document.getElementById('btn-filter');
      const n = localPicks.size;
      btn.textContent = 'Show My Picks';
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

    let _sessionPromise = null;
    async function ensureSession() {
      if (sessionId) return;
      if (_sessionPromise) return _sessionPromise;
      _sessionPromise = (async () => {
        try {
          const res = await fetch(API + '/session', {method: 'POST'});
          if (!res.ok) return;
          const data = await res.json();
          sessionId = data.session_id;
          shareToken = data.share_token;
          localStorage.setItem('stc_session_id', sessionId);
          localStorage.setItem('stc_share_token', shareToken);
          connectWS(sessionId);
          for (const id of localPicks) {
            fetch(API + '/session/' + sessionId + '/pick/' + id, {method: 'POST'}).catch(() => {});
          }
        } catch {}
        finally { _sessionPromise = null; }
      })();
      return _sessionPromise;
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
      if (!sessionId) return;

      try {
        const method = adding ? 'POST' : 'DELETE';
        const res = await fetch(API + '/session/' + sessionId + '/pick/' + id, {method});
        if (res.status === 404) {
          sessionId = null; shareToken = null;
          localStorage.removeItem('stc_session_id');
          localStorage.removeItem('stc_share_token');
          await ensureSession();
          return;
        }
        if (!res.ok && res.status !== 204) {
          if (adding) localPicks.delete(id); else localPicks.add(id);
          btn.classList.toggle('active', !adding);
          btn.setAttribute('aria-pressed', !adding);
          li.classList.toggle('hearted', !adding);
          saveLocal();
        }
      } catch {}
    }

    async function loadFromServer(code) {
      try {
        const res = await fetch(API + '/session/' + code);
        if (!res.ok) return;
        const data = await res.json();
        localPicks = new Set(data.picks);
        readOnly = data.readonly;
        if (!readOnly) {
          sessionId = data.session_id || null;
          shareToken = data.share_token || null;
          if (sessionId) localStorage.setItem('stc_session_id', sessionId); else localStorage.removeItem('stc_session_id');
          if (shareToken) localStorage.setItem('stc_share_token', shareToken); else localStorage.removeItem('stc_share_token');
          saveLocal();
        }
        applyHearts();
        if (readOnly) {
          document.querySelectorAll('.heart-btn').forEach(b => b.style.pointerEvents = 'none');
          filterActive = true;
          document.body.classList.add('filter-active');
          document.getElementById('btn-filter').style.display = 'none';
          updateGroupVisibility();
        } else {
          document.querySelectorAll('.heart-btn').forEach(b => { b.style.display = ''; b.style.pointerEvents = ''; });
        }
      } catch {}
    }

    async function reconcile() {
      if (!sessionId || readOnly) return;
      try {
        const res = await fetch(API + '/session/' + sessionId);
        if (res.status === 404) {
          sessionId = null; shareToken = null;
          localStorage.removeItem('stc_session_id');
          localStorage.removeItem('stc_share_token');
          await ensureSession();
          return;
        }
        if (!res.ok) return;
        const data = await res.json();
        const serverPicks = new Set(data.picks);
        const syncs = [];
        for (const id of localPicks) {
          if (!serverPicks.has(id)) syncs.push(fetch(API + '/session/' + sessionId + '/pick/' + id, {method: 'POST'}).catch(() => {}));
        }
        await Promise.all(syncs);
        for (const id of serverPicks) localPicks.add(id);
        saveLocal();
        applyHearts();
      } catch {}
    }

    // WebSocket real-time sync
    let _ws = null;
    let _wsDelay = 2000;
    function connectWS(code) {
      if (_ws) { try { _ws.close(); } catch {} }
      if (!code) return;
      const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
      _ws = new WebSocket(proto + '//' + location.host + '/ws/' + code);
      _ws.onopen = () => { _wsDelay = 2000; };
      _ws.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);
          if (data.sync_complete) {
            if (_syncTimer) { clearInterval(_syncTimer); _syncTimer = null; }
            document.getElementById('sync-pending').style.display = 'none';
            document.getElementById('sync-done').style.display = '';
          }
          if (data.picks) {
            localPicks = new Set(data.picks);
            saveLocal();
            applyHearts();
            if (data.readonly !== undefined) {
              readOnly = data.readonly;
              if (readOnly) {
                document.querySelectorAll('.heart-btn').forEach(b => b.style.pointerEvents = 'none');
                filterActive = true;
                document.body.classList.add('filter-active');
                document.getElementById('btn-filter').style.display = 'none';
                updateGroupVisibility();
              }
            }
          }
        } catch {}
      };
      _ws.onclose = (ev) => { if (ev.code === 1008) return; setTimeout(() => { const cur = sessionId || shareToken; if (cur === code) connectWS(code); }, _wsDelay + Math.random() * 1000); _wsDelay = Math.min(_wsDelay * 2, 60000); };
    }

    // Modal system
    let _modalTrigger = null;
    function _fitToViewport() {
      const m = document.querySelector('.modal-overlay.open');
      if (!m || !window.visualViewport) return;
      const box = m.querySelector('.modal-box');
      const vh = visualViewport.height;
      const ot = visualViewport.offsetTop;
      const bh = box.offsetHeight;
      box.style.transform = 'translateY(' + (ot + (vh - bh) / 2 - (window.innerHeight - bh) / 2) + 'px)';
    }
    function openDialog(id) {
      _modalTrigger = document.activeElement;
      document.body.style.top = '-' + window.scrollY + 'px';
      document.documentElement.classList.add('scroll-locked');
      document.getElementById(id).classList.add('open');
      if (window.visualViewport) {
        visualViewport.addEventListener('resize', _fitToViewport);
        visualViewport.addEventListener('scroll', _fitToViewport);
      }
    }
    function closeDialog(id) {
      if (window.visualViewport) {
        visualViewport.removeEventListener('resize', _fitToViewport);
        visualViewport.removeEventListener('scroll', _fitToViewport);
      }
      const m = document.getElementById(id);
      m.classList.remove('open');
      const box = m.querySelector('.modal-box');
      box.style.transform = '';
      if (_syncTimer) { clearInterval(_syncTimer); _syncTimer = null; }
      pinField.value = '';
      syncPinDisplay();
      const scrollY = document.body.style.top;
      document.body.style.top = '';
      document.documentElement.classList.remove('scroll-locked');
      window.scrollTo(0, parseInt(scrollY || '0') * -1);
      if (_modalTrigger) { _modalTrigger.blur(); _modalTrigger = null; }
    }
    document.querySelectorAll('.modal-overlay').forEach(ov => {
      ov.addEventListener('click', e => { if (e.target === ov) closeDialog(ov.id); });
      ov.addEventListener('touchmove', e => {
        if (!e.target.closest('.modal-box')) e.preventDefault();
      }, { passive: false });
    });
    document.addEventListener('keydown', e => {
      const modal = document.querySelector('.modal-overlay.open');
      if (!modal) return;
      if (e.key === 'Escape') { closeDialog(modal.id); return; }
      if (e.key !== 'Tab') return;
      const focusable = [...modal.querySelectorAll('button, input, [href], select, textarea, [tabindex]:not([tabindex="-1"])')];
      if (!focusable.length) return;
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    });

    function loadQR(id, url) {
      const c = document.getElementById(id);
      if (!c || typeof qrcode === 'undefined') return;
      const qr = qrcode(0, 'M');
      qr.addData(url);
      qr.make();
      const count = qr.getModuleCount();
      const size = c.width;
      const cellSize = size / count;
      const ctx = c.getContext('2d');
      ctx.clearRect(0, 0, size, size);
      ctx.fillStyle = '#fff';
      ctx.fillRect(0, 0, size, size);
      ctx.fillStyle = '#000';
      for (let r = 0; r < count; r++)
        for (let col = 0; col < count; col++)
          if (qr.isDark(r, col))
            ctx.fillRect(Math.round(col * cellSize), Math.round(r * cellSize), Math.ceil(cellSize), Math.ceil(cellSize));
    }

    // Share modal
    const shareLink = document.getElementById('share-link');
    shareLink.addEventListener('click', () => {
      shareLink.select();
      const url = shareLink.value;
      navigator.clipboard.writeText(url).then(() => {
        shareLink.classList.add('copied');
        shareLink.value = 'Copied!';
        setTimeout(() => { shareLink.value = url; shareLink.classList.remove('copied'); }, 1500);
      });
    });
    function openShareModal() {
      if (!shareToken) { alert('Heart an artist first.'); return; }
      shareLink.value = location.origin + '/?code=' + shareToken;
      openDialog('m-share');
    }

    // Sync modal
    let _syncTimer = null;
    async function generateSyncPin() {
      const d = document.getElementById('pin-display');
      const exp = document.getElementById('sync-expiry');
      const qr = document.getElementById('sync-qr');
      d.innerHTML = '';
      exp.textContent = '';
      if (qr) qr.getContext('2d').clearRect(0, 0, qr.width, qr.height);
      if (_syncTimer) { clearInterval(_syncTimer); _syncTimer = null; }
      try {
        const res = await fetch(API + '/session/' + sessionId + '/sync-pin', {method: 'POST'});
        if (!res.ok) return;
        const data = await res.json();
        for (const ch of data.pin) { const s = document.createElement('span'); s.textContent = ch; d.appendChild(s); }
        loadQR('sync-qr', location.origin + '/?sync=' + data.pin);
        const deadline = Date.now() + 300000;
        function tick() {
          const left = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
          if (left === 0) {
            clearInterval(_syncTimer); _syncTimer = null;
            d.innerHTML = '';
            if (qr) qr.getContext('2d').clearRect(0, 0, qr.width, qr.height);
            exp.innerHTML = 'QR code and PIN expired. <a onclick="generateSyncPin()">Generate new ones</a>';
            return;
          }
          if (left >= 60) { const m = Math.ceil(left / 60); exp.textContent = 'Valid for ' + m + ' min'; }
          else exp.textContent = 'Valid for ' + left + 's';
        }
        tick();
        _syncTimer = setInterval(tick, 1000);
      } catch {}
    }
    async function openSyncModal() {
      await ensureSession();
      if (!sessionId) { alert('Heart an artist first.'); return; }
      document.getElementById('sync-pending').style.display = '';
      document.getElementById('sync-done').style.display = 'none';
      document.querySelectorAll('#m-sync .tabs button').forEach(b => b.classList.remove('on'));
      document.querySelector('#m-sync .tabs button').classList.add('on');
      document.getElementById('p-send').classList.add('on');
      document.getElementById('p-recv').classList.remove('on');
      await generateSyncPin();
      openDialog('m-sync');
    }
    function syncTab(t, btn) {
      btn.closest('.tabs').querySelectorAll('button').forEach(b => b.classList.remove('on'));
      btn.classList.add('on');
      document.getElementById('p-send').classList.toggle('on', t === 'send');
      document.getElementById('p-recv').classList.toggle('on', t === 'recv');
    }

    // Pin input (hidden input + visual boxes)
    const pinField = document.getElementById('pin-input');
    const pinBoxes = [...document.querySelectorAll('#pin-boxes span')];
    function syncPinDisplay() {
      const val = pinField.value;
      const cursor = val.length >= 6 ? 5 : val.length;
      pinBoxes.forEach((b, i) => {
        b.textContent = val[i] || '';
        b.classList.toggle('filled', i < val.length);
        b.classList.toggle('active', i === cursor);
      });
    }
    pinField.addEventListener('input', () => {
      pinField.value = pinField.value.replace(/\\D/g, '').slice(0, 6);
      syncPinDisplay();
    });
    pinField.addEventListener('focus', () => { document.getElementById('pin-wrap').classList.add('focused'); syncPinDisplay(); });
    pinField.addEventListener('blur', () => { document.getElementById('pin-wrap').classList.remove('focused'); pinBoxes.forEach(b => b.classList.remove('active')); });
    document.getElementById('pin-wrap').addEventListener('click', () => pinField.focus());
    async function submitPin() {
      const pin = pinField.value.replace(/\\D/g, '');
      if (pin.length !== 6) return;
      closeDialog('m-sync');
      pinField.value = '';
      syncPinDisplay();
      await exchangeSyncPin(pin);
    }

    async function exchangeSyncPin(pin) {
      try {
        const res = await fetch(API + '/sync/' + pin, {method: 'POST'});
        if (!res.ok) return;
        const data = await res.json();
        localPicks = new Set(data.picks);
        readOnly = data.readonly;
        if (!readOnly) {
          sessionId = data.session_id || null;
          shareToken = data.share_token || null;
          if (sessionId) localStorage.setItem('stc_session_id', sessionId); else localStorage.removeItem('stc_session_id');
          if (shareToken) localStorage.setItem('stc_share_token', shareToken); else localStorage.removeItem('stc_share_token');
          saveLocal();
        }
        applyHearts();
        if (sessionId) connectWS(sessionId);
      } catch {}
    }

    // Init
    (async () => {
      const p = new URLSearchParams(location.search);
      const syncPin = p.get('sync');
      const c = p.get('code');
      if (syncPin) {
        history.replaceState(null, '', location.pathname);
        await exchangeSyncPin(syncPin);
      } else if (c) {
        history.replaceState(null, '', location.pathname);
        await loadFromServer(c); connectWS(c);
      }
      else if (sessionId) { await reconcile(); connectWS(sessionId); }
      else {
        try {
          const res = await fetch(API + '/me');
          if (res.ok) {
            const data = await res.json();
            localPicks = new Set(data.picks);
            sessionId = data.session_id;
            shareToken = data.share_token;
            localStorage.setItem('stc_session_id', sessionId);
            localStorage.setItem('stc_share_token', shareToken);
            saveLocal();
            connectWS(sessionId);
          }
        } catch {}
      }
      applyHearts();
    })();
    """)
    parts.append("  </script>")
    parts.append("</body>")
    parts.append("</html>")
    return "\n".join(parts)
