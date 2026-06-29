from __future__ import annotations

import html
import json as _json
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
SVG_RA = _load_icon("ra")


def _svg_to_symbol(svg: str, symbol_id: str) -> str:
    import re as _re

    vb = _re.search(r'viewBox="([^"]*)"', svg)
    viewbox = vb.group(1) if vb else "0 0 24 24"
    inner = _re.sub(r"^<svg[^>]*>", "", svg)
    inner = _re.sub(r"</svg>$", "", inner)
    attrs = ""
    for attr in ("fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin"):
        m = _re.search(rf'{attr}="([^"]*)"', svg.split(">")[0])
        if m and attr == "fill" and m.group(1) == "none":
            attrs += f' {attr}="{m.group(1)}"'
        elif m and attr != "fill":
            attrs += f' {attr}="{m.group(1)}"'
    return (
        f'<symbol id="{symbol_id}" viewBox="{viewbox}"{attrs}>{inner.strip()}</symbol>'
    )


def _use_svg(symbol_id: str, **attrs: str) -> str:
    attr_str = "".join(f' {k}="{v}"' for k, v in attrs.items())
    return f'<svg{attr_str}><use href="#{symbol_id}"/></svg>'


def _format_date_heading(date_str: str) -> str:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return f"{dt.strftime('%A')}, {dt.strftime('%B')} {dt.day}, {dt.year}"


def _format_date_tab(date_str: str) -> str:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return f"{dt.strftime('%a')} {dt.day}"


def _parse_time(t: str) -> int:
    """Return minutes since midnight from an ISO time string."""
    dt = datetime.fromisoformat(t)
    return dt.hour * 60 + dt.minute


def _format_hhmm(minutes: int) -> str:
    h, m = divmod(minutes % 1440, 60)
    return f"{h:02d}:{m:02d}"


def _artists_json(group: list[dict], photos_prefix: str) -> str:
    return _json.dumps(
        [
            {
                "name": a.get("name", ""),
                "photo": photos_prefix + a["photo_local"]
                if a.get("photo_local")
                else "",
                "ig": a.get("instagram") or "",
                "sc": a.get("soundcloud") or "",
                "sp": a.get("spotify") or "",
                "lt": a.get("linktree") or "",
                "yt": a.get("youtube") or "",
                "ra": a.get("ra") or "",
                "igF": format_followers(a.get("ig_followers")) or "",
                "scF": format_followers(a.get("sc_followers")) or "",
                "spL": format_followers(a.get("spotify_listeners")) or "",
                "raF": format_followers(a.get("ra_followers")) or "",
            }
            for a in group
        ]
    )


def _slot_time_str(slot: dict) -> str:
    s = slot.get("start_time") or ""
    e = slot.get("end_time") or ""
    if s and e:
        sh = s.split("T")[1] if "T" in s else s
        eh = e.split("T")[1] if "T" in e else e
        return f"{sh}–{eh}"
    return ""


def _format_short_date_abbr(date_str: str) -> str:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return f"{dt.strftime('%a')}, {dt.strftime('%B')} {dt.day}"


def _format_artist_schedule(
    all_slots: list[dict], current_date: str, current_period: str
) -> tuple[str | None, str | None]:
    current_label = None
    other_labels = []
    for slot in all_slots:
        floor = slot.get("location_name") or ""
        time = _slot_time_str(slot)
        is_current = slot["date"] == current_date and slot["period"] == current_period
        if is_current:
            current_label = ", ".join(p for p in (floor, time) if p) or None
        else:
            same_day = slot["date"] == current_date
            if same_day:
                parts = [p for p in (floor, time) if p]
            else:
                parts = [
                    p for p in (_format_short_date_abbr(slot["date"]), floor, time) if p
                ]
            if parts:
                other_labels.append(", ".join(parts))
    also_label = ("Also " + " · ".join(other_labels)) if other_labels else None
    return current_label, also_label


def render_output_html(
    title: str,
    ordered_sections: list[dict],
    assignments: dict[str, list[dict]],
    locations: dict[str, dict],
    has_timetable: bool = False,
    photos_prefix: str = "photos/",
    floor_curators: dict[str, str] | None = None,
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
    description = "Your companion for Stone Techno 2026. Browse the lineup, explore the timetable, plan your schedule, and get notified before your sets start."
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
        '  <script defer src="https://analytics.deftlab.dev/script.js" data-website-id="8f79ad80-e080-421d-91c6-45b7bfc460d2" data-domains="stonetechno.deftlab.dev" data-auto-track="true" data-performance="true"></script>'
    )
    import base64 as _b64

    favicon_b64 = _b64.b64encode((ICONS_DIR / "favicon.svg").read_bytes()).decode()
    parts.append(
        f'  <link rel="icon" type="image/svg+xml" href="data:image/svg+xml;base64,{favicon_b64}">'
    )
    parts.append('  <link rel="manifest" href="/manifest.json">')
    parts.append('  <meta name="mobile-web-app-capable" content="yes">')
    parts.append(
        '  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">'
    )
    parts.append("  <style>")
    parts.append("""
    :root {
      --color-text: #111;
      --color-bg: #fff;
      --color-muted: #717171;
      --color-muted-icon: #888;
      --color-border: #e0e0e0;
      --color-accent: #e53e3e;
      --color-schedule: #4a90d9;
      --color-line-hour: #ccc;
      --color-line-half: #e8e8e8;
      --floor-eisbahn: #c6f9c5;
      --floor-grand-hall: #c5f9f1;
      --floor-koksofenbatterie: #c5d5f9;
      --floor-listening-floor: #e2c5f9;
      --floor-mischanlage: #f9c5e4;
      --floor-salzlager: #f9d3c5;
      --floor-werksschwimmbad: #f3f9c5;
      --font-2xl: 2em;
      --font-xl: 1.5em;
      --font-lg: 1.125em;
      --font-base: 1em;
      --font-sm: 0.875em;
      --font-xs: 0.75em;
    }
    *, *::before, *::after { box-sizing: border-box; }
    html { overscroll-behavior: none; }
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; line-height: 1.5; max-width: 960px; margin: 0 auto; padding: 0 24px; color: var(--color-text); background: var(--color-bg); }
    h1 { margin-bottom: 32px; font-size: var(--font-2xl); position: sticky; top: 28px; background: #fff; z-index: 30; padding: 12px 0 8px; border-bottom: 2px solid #222; }
    section.date-section { margin-bottom: 48px; }
    h2 { position: sticky; top: 96px; background: #fff; z-index: 20; padding: 10px 0 8px; margin-bottom: 8px; font-size: var(--font-xl); border-bottom: 1px solid #ccc; }
    h3.period-heading { position: sticky; top: 150px; background: #fff; z-index: 10; padding: 8px 0 6px; margin: 24px 0 12px; font-size: var(--font-lg); color: #333; text-transform: uppercase; letter-spacing: 0.05em; border-bottom: none; }
    .fade-after::after { content: ''; position: absolute; left: 0; right: 0; top: 100%; height: 36px; background: linear-gradient(to bottom, rgba(255,255,255,1) 0%, rgba(255,255,255,0.9) 20%, rgba(255,255,255,0.75) 35%, rgba(255,255,255,0.5) 55%, rgba(255,255,255,0.15) 78%, rgba(255,255,255,0) 100%); pointer-events: none; opacity: 0; transition: opacity 0.15s; }
    .fade-after.stuck::after { opacity: 1; }
    h4.location-heading { position: sticky; top: 190px; background: #fff; z-index: 10; font-size: var(--font-base); padding: 6px 0 4px; margin: 16px 0 8px; color: #555; border-bottom: 1px solid #eee; }
    h4.location-heading small { font-weight: normal; color: var(--color-muted); }
    ul.artist-list { list-style: none; padding: 0; margin: 0; }
    li.artist-item { display: flex; align-items: center; gap: 16px; padding: 12px; margin-bottom: 8px; background: #f9f9f9; border-radius: 8px; border: 1px solid #eee; }
    .artist-photo { width: 120px; height: 120px; object-fit: cover; border-radius: 6px; flex-shrink: 0; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
    .photo-placeholder { width: 120px; height: 120px; flex-shrink: 0; background: #eee; border-radius: 6px; }
    .artist-info { flex: 1; min-width: 0; }
    .artist-name { font-weight: 700; font-size: var(--font-lg); display: block; margin-bottom: 3px; }
    .artist-schedule { color: var(--color-muted); font-size: var(--font-sm); display: block; margin-bottom: 6px; }
    .artist-also { color: var(--color-muted); font-size: var(--font-xs); line-height: 1; margin-top: 4px; }
    .links { display: flex; flex-wrap: wrap; column-gap: 18px; row-gap: 4px; align-items: center; }
    .links a { display: inline-flex; align-items: center; gap: 5px; text-decoration: none; color: #555; font-size: var(--font-xs); padding: 3px 0; min-width: 72px; font-variant-numeric: tabular-nums; }
    .links a:hover { color: #111; }
    .links a svg { flex-shrink: 0; }
    .missing { color: var(--color-muted); font-size: var(--font-xs); }
    @media (max-width: 480px) {
      body { padding: 0 12px; }
      .cmd-bar { font-size: var(--font-xs); }
      h1 { font-size: var(--font-xl); padding: 8px 0 6px; top: 48px; }
      h2 { font-size: var(--font-xl); padding: 6px 0; top: 100px; }
      h3.period-heading { font-size: var(--font-base); padding: 6px 0 4px; top: 148px; margin: 16px 0 8px; }
      h4.location-heading { top: 176px; }
      li.artist-item { gap: 10px; padding: 10px; align-items: flex-start; flex-wrap: wrap; }
      .artist-also { margin-left: calc(-72px - 10px); width: calc(100% + 72px + 10px); margin-top: 10px; display: block; }
      .artist-photo { width: 72px; height: 72px; border-radius: 4px; margin-top: 2px; }
      .photo-placeholder { width: 72px; height: 72px; border-radius: 4px; margin-top: 2px; }
      .artist-name { font-size: var(--font-base); }
      .artist-schedule { font-size: var(--font-xs); margin-bottom: 4px; }
      .links { column-gap: 8px; row-gap: 0; }
      .links a { font-size: var(--font-xs); min-width: 72px; gap: 3px; }
      .links a svg { width: 14px; height: 14px; }
      .heart-btn svg { width: 18px; height: 18px; }
    }
    .heart-btn { background: none; border: none; cursor: pointer; padding: 6px; flex-shrink: 0; align-self: flex-start; margin-top: 2px; }
    .heart-btn svg { fill: none; stroke: var(--color-muted-icon); stroke-width: 2; transition: fill 0.15s, stroke 0.15s; width: 22px; height: 22px; }
    .heart-btn:hover:not(.active) svg { stroke: var(--color-muted-icon); }
    .heart-btn:focus:not(:focus-visible) { outline: none; }
    .heart-btn.active svg { fill: var(--color-accent); stroke: var(--color-accent); }
    .cmd-bar { position: sticky; top: 0; z-index: 40; background: #111; color: #fff; display: flex; align-items: stretch; justify-content: space-between; height: 28px; font-size: var(--font-xs); padding: 0 16px; }
    .cmd-group { display: flex; align-items: stretch; }
    .cmd-bar button { background: none; color: #aaa; border: none; cursor: pointer; padding: 0 16px; font-size: var(--font-base); white-space: nowrap; text-align: center; transition: color 0.1s; letter-spacing: 0.03em; }
    .cmd-bar button:hover { color: #fff; }
    .cmd-bar button:focus-visible { outline: 1px solid #fff; outline-offset: -2px; }
    .cmd-bar button:focus:not(:focus-visible) { outline: none; }
    .cmd-bar button.active { color: #fff; }
    .cmd-sep { width: 1px; background: #444; margin: 6px 16px; }
    .filter-active .artist-item:not(.hearted) { display: none; }
    .filter-active .tt-block:not(.hearted):not(:has(.tt-artist-row.hearted)) { opacity: 0.15; }

    /* --- Modals --- */
    html.scroll-locked, html.scroll-locked body { overflow:hidden; }
    html.scroll-locked body { position:fixed; left:0; right:0; }
    .modal-overlay { display:none; position:fixed; inset:0; z-index:100; background:rgba(0,0,0,.4); padding:24px; }
    .modal-overlay.open { display:flex; justify-content:center; align-items:center; }
    .modal-box { background:#fff; border-radius:14px; padding:24px; width:420px; max-width:100%; text-align:center; color:#111; box-shadow:0 8px 24px rgba(0,0,0,.12); }
    .modal-box h3 { margin:0 0 6px; font-size:var(--font-base); font-weight:600; text-wrap:balance; }
    .modal-box .sub { font-size:var(--font-xs); color:#888; margin:0 0 14px; text-wrap:balance; }
    .modal-link { display:block; width:100%; background:#f5f5f5; padding:12px 14px; border-radius:8px; font-size:var(--font-sm); font-family:inherit; color:#333; cursor:pointer; transition:background .15s; margin:0; border:none; text-align:left; overflow:hidden; text-overflow:clip; white-space:nowrap; box-sizing:border-box; outline:none; }
    .modal-link:hover { background:#eee; }
    .modal-link.copied { background:#d4edda; text-align:center; }
    .modal-box canvas { display:block; margin:10px auto; border-radius:6px; }
    .modal-box .or-line { display:flex; align-items:center; gap:10px; margin:10px 0; }
    .modal-box .or-line hr { flex:1; border:none; border-top:1px solid #e0e0e0; }
    .modal-box .or-line span { color:#bbb; font-size:var(--font-xs); }
    .modal-box .tabs { display:flex; gap:3px; margin-bottom:14px; border-radius:8px; border:1px solid #e0e0e0; padding:3px; background:#f5f5f5; }
    .modal-box .tabs button { flex:1; background:transparent; border:none; padding:7px 4px; cursor:pointer; font-size:var(--font-xs); color:#888; border-radius:5px; transition:color .15s,background .15s; }
    .modal-box .tabs button:focus-visible { outline:1px solid #111; outline-offset:-2px; }
    .modal-box .tabs button:focus:not(:focus-visible) { outline:none; }
    .modal-box .tabs button:hover:not(.on) { background:#eee; color:#555; }
    .modal-box .tabs button.on { background:#111; color:#fff; }
    .modal-box .pane { display:none; }
    .modal-box .pane.on { display:block; }
    .modal-box .lbl { font-size:var(--font-sm); color:#333; text-align:left; margin:0 0 4px; }
    .modal-box .recv-lbl { font-size:var(--font-sm); color:#333; text-align:left; margin:10px 0 4px; }
    .modal-box .steps { counter-reset:s; }
    .modal-box .steps p { text-align:left; font-size:var(--font-xs); color:#333; margin:5px 0; padding-left:20px; position:relative; }
    .modal-box .steps p::before { content:counter(s) ". "; counter-increment:s; font-weight:600; position:absolute; left:0; }
    .pin { display:flex; gap:5px; justify-content:center; margin:10px 0; }
    .pin span { width:28px; height:36px; font-size:var(--font-lg); font-weight:700; border:1px solid #ddd; border-radius:5px; background:#f5f5f5; color:#111; display:flex; align-items:center; justify-content:center; line-height:1; }
    .sync-expiry { font-size:var(--font-xs); color:#888; text-align:center; margin:8px 0 0; }
    .sync-expiry a { color:inherit; text-decoration:underline; cursor:pointer; }
    .pin-wrap { position:relative; cursor:text; margin:10px 0; -webkit-tap-highlight-color:transparent; }
    .pin-wrap .pin { pointer-events:none; }
    .pin-wrap .pin span.active { border-color:#111; background:#fff; }
    .pin-wrap.focused .pin span.active:empty::after { content:''; width:2px; height:1.2em; background:#111; border-radius:1px; animation:blink 1s step-end infinite; }
    @keyframes blink { 0%,100% { opacity:1; } 50% { opacity:0; } }
    .pin-wrap .pin span.filled { color:#111; }
    .pin-real { position:absolute; inset:0; opacity:0; font-size:16px; width:100%; height:100%; border:none; padding:0; margin:0; -webkit-tap-highlight-color:transparent; }
    .modal-box .btn { background:#111; color:#fff; border:none; padding:7px 18px; border-radius:5px; cursor:pointer; font-size:var(--font-sm); margin-top:8px; }
    .modal-box .btn:hover { background:#333; }
    .modal-box .btn:focus-visible { outline:1px solid #111; outline-offset:2px; }
    .modal-box .btn:focus:not(:focus-visible) { outline:none; }
    .qr-wrap { display:block; }
    @media (max-width:480px) { .qr-wrap { display:none; } .modal-box .tabs { flex-direction:column; } }
    """)
    if has_timetable:
        parts.append("""
    /* --- Timetable view --- */

    /* Filter bar */
    .filter-bar { position: sticky; top: 98px; z-index: 20; background: #fff; display: flex; align-items: center; justify-content: space-between; padding: 10px 0 8px; margin: 0.83em 0 8px; gap: 8px; border-bottom: 1px solid #ccc; }
    .day-tabs { display: flex; gap: 2px; }
    .period-tabs { display: flex; gap: 2px; }
    .day-tab, .period-tab { padding: 7px 14px; border: 1px solid #ddd; border-radius: 6px; background: #f5f5f5; color: var(--color-text); cursor: pointer; font-size: var(--font-sm); font-weight: 600; transition: background 0.15s, border-color 0.15s; }
    .day-tab:hover, .period-tab:hover { background: #eee; }
    .day-tab.active { background: #111; color: #fff; border-color: #111; }
    .period-tab.active { background: #333; color: #fff; border-color: #333; }

    /* Floor headers */
    .floor-header-bar { display: grid; position: sticky; top: 148px; z-index: 10; background: #fff; padding: 8px 0 6px; margin: 24px 0 12px; align-items: start; }
    .floor-header-bar::after { content: ''; position: absolute; left: 0; right: 0; top: 100%; height: 36px; background: linear-gradient(to bottom, rgba(255,255,255,1) 0%, rgba(255,255,255,0.9) 20%, rgba(255,255,255,0.75) 35%, rgba(255,255,255,0.5) 55%, rgba(255,255,255,0.15) 78%, rgba(255,255,255,0) 100%); pointer-events: none; opacity: 0; transition: opacity 0.15s; }
    .floor-header-bar.stuck::after { opacity: 1; }
    .floor-header { text-align: center; margin: 0 3px; background: none !important; }
    .floor-header > span:first-child { display: block; font-weight: 700; font-size: var(--font-sm); padding: 8px 12px; border-radius: 999px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .floor-curator { font-style: italic; font-size: var(--font-xs); color: var(--color-muted); margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

    /* Timetable grid */
    .timetable-panel { display: none; }
    .timetable-panel.active { display: block; }
    .timetable { display: grid; position: relative; margin-bottom: 4px; }
    .time-label { font-size: var(--font-xs); color: var(--color-muted); text-align: right; padding-right: 8px; line-height: 1; position: relative; top: calc(-0.5em + 1px); }
    .grid-line { grid-column: 2 / -1; border-top: 1px solid #ccc; pointer-events: none; }
    .grid-line.hour { border-top: 1px solid var(--color-line-hour); }
    .grid-line.half { border-top: 1px dashed var(--color-line-half); }

    /* Artist blocks */
    .tt-block { border-radius: 6px; margin: 5px 3px 4px; padding: 8px 10px; font-size: var(--font-sm); cursor: pointer; position: relative; display: flex; flex-direction: row; align-items: flex-start; border: 1px solid var(--color-border); transition: opacity 0.15s; min-height: 0; }
    .tt-text { width: 0; flex-grow: 1; display: flex; flex-direction: column; }
    .tt-block .tt-time-row { display: flex; align-items: center; justify-content: space-between; margin-bottom: 5px; }
    .tt-block .tt-time { font-size: var(--font-sm); color: var(--color-muted); white-space: nowrap; line-height: 1; }
    .tt-artist-row { display: flex; align-items: center; gap: 8px; margin-top: 6px; min-width: 0; }
    .tt-photo-wrap { position: relative; flex-shrink: 0; width: 34px; height: 34px; }
    .tt-photo { width: 34px; height: 34px; border-radius: 4px; object-fit: cover; display: block; }
    .tt-photo-placeholder { width: 34px; height: 34px; border-radius: 4px; background: #eee; }
    .tt-block .tt-name { font-weight: 700; font-size: var(--font-base); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; line-height: 1.3; min-width: 0; flex: 1; }

    /* Per-artist heart (bottom-right corner inside the photo) */
    .tt-photo-heart { position: absolute; bottom: -5px; right: -5px; background: rgba(255,255,255,0.85); border: none; cursor: pointer; padding: 2px; line-height: 0; border-radius: 50%; width: 18px; height: 18px; display: flex; align-items: center; justify-content: center; z-index: 1; }
    .tt-photo-heart svg { width: 12px; height: 12px; fill: none; stroke: var(--color-muted-icon); stroke-width: 2; transition: fill 0.15s, stroke 0.15s; }
    .tt-photo-heart.active svg { fill: var(--color-accent); stroke: var(--color-accent); }
    .tt-photo-heart:hover:not(.active) svg { stroke: var(--color-muted-icon); }

    /* Calendar icon */
    .tt-cal { background: none; border: none; cursor: pointer; padding: 0; line-height: 0; flex-shrink: 0; }
    .tt-cal svg { width: 16px; height: 16px; color: var(--color-muted-icon); transition: color 0.15s; }
    .tt-cal.active svg { color: var(--color-schedule); }
    .tt-cal:hover:not(.active) svg { color: var(--color-muted-icon); }
    .tt-block.scheduled { box-shadow: inset 0 0 0 2px var(--color-schedule); }
    .tt-ics { position: absolute; bottom: 4px; left: 8px; font-size: var(--font-xs); color: var(--color-muted); cursor: pointer; text-decoration: none; }
    .tt-ics:hover { color: #555; }
    .filter-schedule .tt-block:not(.scheduled) { opacity: 0.15; }

    /* Now line */
    .now-line { grid-column: 2 / -1; border-top: 2px solid #e53e3e; pointer-events: none; z-index: 8; position: relative; }
    .now-line::before { content: 'NOW'; position: absolute; left: -48px; top: -8px; font-size: 9px; font-weight: 700; color: #e53e3e; letter-spacing: 0.05em; }

    /* Floor colors */
    .floor-eisbahn { background: color-mix(in srgb, var(--floor-eisbahn) 88%, transparent); }
    .floor-grand-hall { background: color-mix(in srgb, var(--floor-grand-hall) 88%, transparent); }
    .floor-koksofenbatterie { background: color-mix(in srgb, var(--floor-koksofenbatterie) 88%, transparent); }
    .floor-listening-floor { background: color-mix(in srgb, var(--floor-listening-floor) 88%, transparent); }
    .floor-mischanlage { background: color-mix(in srgb, var(--floor-mischanlage) 88%, transparent); }
    .floor-salzlager { background: color-mix(in srgb, var(--floor-salzlager) 88%, transparent); }
    .floor-werksschwimmbad { background: color-mix(in srgb, var(--floor-werksschwimmbad) 88%, transparent); }
    .floor-unknown { background: rgba(243, 244, 246, 0.88); }
    .floor-header.floor-eisbahn > span:first-child { background: var(--floor-eisbahn); }
    .floor-header.floor-grand-hall > span:first-child { background: var(--floor-grand-hall); }
    .floor-header.floor-koksofenbatterie > span:first-child { background: var(--floor-koksofenbatterie); }
    .floor-header.floor-listening-floor > span:first-child { background: var(--floor-listening-floor); }
    .floor-header.floor-mischanlage > span:first-child { background: var(--floor-mischanlage); }
    .floor-header.floor-salzlager > span:first-child { background: var(--floor-salzlager); }
    .floor-header.floor-werksschwimmbad > span:first-child { background: var(--floor-werksschwimmbad); }

    /* Mobile table — hidden on desktop */
    .tt-table-wrap { display: none; }

    /* Artist detail popup */
    .tt-popup { position: fixed; z-index: 200; background: #fff; border-radius: 10px; box-shadow: 0 8px 24px rgba(0,0,0,0.18); padding: 16px; width: 320px; max-width: 90vw; visibility: hidden; opacity: 0; pointer-events: none; }
    .tt-popup.open { visibility: visible; opacity: 1; pointer-events: auto; }
    .tt-popup .popup-meta { font-size: var(--font-xs); color: var(--color-muted); margin-bottom: 10px; }
    .tt-popup .popup-artist { display: flex; gap: 14px; align-items: flex-start; margin-bottom: 10px; }
    .tt-popup .popup-artist:last-child { margin-bottom: 0; }
    .tt-popup .popup-photo { width: 80px; height: 80px; border-radius: 6px; object-fit: cover; flex-shrink: 0; margin-top: 2px; }
    .tt-popup .popup-photo-placeholder { width: 80px; height: 80px; border-radius: 6px; background: #eee; flex-shrink: 0; margin-top: 2px; }
    .tt-popup .popup-name { font-weight: 700; font-size: var(--font-base); }
    .tt-popup .links { display: flex; flex-wrap: wrap; column-gap: 24px; row-gap: 0; margin-top: 8px; }
    .tt-popup .links a { display: inline-flex; align-items: center; gap: 4px; text-decoration: none; color: #555; font-size: var(--font-xs); }
    .tt-popup .links a:hover { color: #111; }

    @media (max-width: 768px) {
      .floor-header > span:first-child { font-size: var(--font-xs); padding: 6px 2px; }
      .tt-block { font-size: var(--font-xs); padding: 6px 7px; margin: 2px; gap: 5px; }
      .day-tab { padding: 6px 10px; font-size: var(--font-xs); }
    }

    /* Hamburger menu (hidden on desktop) */
    .hamburger { display: none; }
    .view-label { display: none; }
    .cmd-dropdown { display: none; }
    .menu-overlay { display: none; }

    @media (max-width: 480px) {
      body { padding: 0 12px; }
      h1 { font-size: var(--font-xl); padding: 8px 0 6px; top: 48px; margin-bottom: 0; }

      /* Mobile cmd bar — 48px, full width, label left, hamburger right */
      .cmd-bar { height: 48px; padding: 0 16px; margin-left: -12px; margin-right: -12px; }
      .cmd-bar .cmd-group { display: none; }
      .cmd-bar .cmd-group-right { display: none !important; }
      .cmd-sep { display: none; }

      /* View label — left aligned, 16px */
      .cmd-bar { cursor: pointer; -webkit-tap-highlight-color: transparent; }
      .cmd-bar .view-label { color: #fff; font-size: 16px; font-weight: 600; letter-spacing: 0.02em; display: flex; align-items: center; height: 100%; }

      /* Hamburger — 48x48 tap target, 28px SVG */
      .hamburger { display: flex; align-items: center; justify-content: center; background: none; color: #fff; border: none; cursor: pointer; width: 48px; height: 48px; position: absolute; right: 4px; top: 0; -webkit-tap-highlight-color: transparent; }
      .hamburger svg { width: 24px; height: 24px; min-width: 24px; min-height: 24px; }

      /* Dropdown — full width, same bg as bar, 16px font matching label */
      .cmd-dropdown { position: fixed; top: 48px; left: 0; right: 0; z-index: 49; background: #111; flex-direction: column; box-shadow: 0 8px 24px rgba(0,0,0,0.4); }
      .cmd-dropdown.open { display: flex; }
      .cmd-dropdown button { background: none; color: #aaa; border: none; border-top: 1px solid #222; cursor: pointer; padding: 16px; font-size: 16px; font-family: inherit; text-align: left; -webkit-tap-highlight-color: transparent; }
      .cmd-dropdown button:active { background: #222; }
      .cmd-dropdown button.active { color: #fff; font-weight: 600; }

      /* Overlay behind dropdown (below hamburger) */
      .menu-overlay.open { display: block; position: fixed; top: 48px; left: 0; right: 0; bottom: 0; z-index: 47; background: rgba(0,0,0,0.3); }

      /* Photos bigger on mobile single-floor view */
      .tt-photo, .tt-photo-placeholder { width: 30px; height: 30px; border-radius: 4px; }

      /* Hide CSS grid timetable on mobile — replaced by table */
      .floor-header-bar { display: none !important; }
      .timetable { display: none !important; }

      /* Show mobile table */
      .tt-table-wrap { display: block !important; min-height: 300px; }

      /* Single scroll container for both axes */
      .tt-v-scroll { overflow: auto; scrollbar-width: none; -ms-overflow-style: none; overscroll-behavior: none; }
      .tt-v-scroll::-webkit-scrollbar { display: none; }

      .tt-table { border-collapse: separate; border-spacing: 0; table-layout: fixed; width: calc(40px + var(--num-floors) * 40vw); }
      .tt-table thead th { position: sticky; top: 0; z-index: 2; background: #fff; padding: 4px 2px 4px; text-align: center; vertical-align: top; }
      .tt-table thead th:first-child { left: 0; z-index: 3; background: #fff; width: 40px; min-width: 40px; }
      .tt-floor-th > span:first-child { display: block; padding: 6px 10px; border-radius: 999px; font-size: var(--font-xs); font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin: 0 3px; }
      .tt-floor-th .floor-curator { display: block; font-size: 10px; padding: 1px 0 2px; margin: 0; }
      .tt-floor-th.floor-eisbahn > span:first-child { background: var(--floor-eisbahn); }
      .tt-floor-th.floor-grand-hall > span:first-child { background: var(--floor-grand-hall); }
      .tt-floor-th.floor-koksofenbatterie > span:first-child { background: var(--floor-koksofenbatterie); }
      .tt-floor-th.floor-listening-floor > span:first-child { background: var(--floor-listening-floor); }
      .tt-floor-th.floor-mischanlage > span:first-child { background: var(--floor-mischanlage); }
      .tt-floor-th.floor-salzlager > span:first-child { background: var(--floor-salzlager); }
      .tt-floor-th.floor-werksschwimmbad > span:first-child { background: var(--floor-werksschwimmbad); }
      .tt-table tbody td.tt-time-td { position: sticky; left: 0; z-index: 1; background: #fff; font-size: var(--font-xs); color: var(--color-muted-icon); text-align: right; padding: 0 6px 0 0; vertical-align: top; width: 40px; min-width: 40px; line-height: var(--row-h); overflow: hidden; }
      .tt-table tbody td.tt-line-hour, .tt-table tbody td.tt-line-half { vertical-align: middle; }
      .tt-table tbody td { vertical-align: top; padding: 0; }
      .tt-table tbody td:not(.tt-time-td) { width: 40vw; min-width: 40vw; vertical-align: top; padding: 0; position: relative; }
      .tt-table tbody tr { height: var(--row-h); }
      .tt-table tbody tr:first-child { height: calc(var(--row-h) / 2); }
      .tt-table tbody tr:first-child td.tt-time-td { padding-bottom: calc(var(--row-h) / 2); }
      .tt-table tbody tr:last-child { height: calc(var(--row-h) / 2); }
      .tt-table tbody tr:nth-last-child(2) td.tt-time-td { padding-top: calc(var(--row-h) / 2); }
      .tt-table tbody {
        background-image:
          repeating-linear-gradient(to bottom, var(--color-line-hour) 0, var(--color-line-hour) 1px, transparent 1px, transparent calc(var(--row-h) * 12)),
          repeating-linear-gradient(to bottom, var(--color-line-half) 0, var(--color-line-half) 1px, transparent 1px, transparent calc(var(--row-h) * 6));
        background-position: 0 calc(var(--row-h) / 2 - 1px);
      }

      /* Artist blocks inside table cells */
      .tt-table .tt-block { position: absolute; top: 1.5px; left: 1px; right: 1px; bottom: 2.5px; }

      /* Filter bar compact */
      .filter-bar { padding: 6px 0; margin: 0 0 4px; top: 100px; }
      .day-tab, .period-tab { padding: 5px 10px; font-size: var(--font-xs); }

      /* Popup full width on mobile */
      .tt-popup { width: calc(100vw - 24px); max-width: none; left: 12px !important; }
      .tt-popup .popup-photo, .tt-popup .popup-photo-placeholder { width: 64px; height: 64px; }
    }
    """)
    parts.append("  </style>")
    parts.append("</head>")
    parts.append("<body>")
    heart_path = "M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"
    cal_inner = '<rect x="3" y="4" width="18" height="18" rx="2" fill="none" stroke="currentColor" stroke-width="2"/><line x1="16" y1="2" x2="16" y2="6" stroke="currentColor" stroke-width="2"/><line x1="8" y1="2" x2="8" y2="6" stroke="currentColor" stroke-width="2"/><line x1="3" y1="10" x2="21" y2="10" stroke="currentColor" stroke-width="2"/>'
    parts.append('  <svg style="display:none">')
    parts.append(
        f'    <symbol id="i-heart" viewBox="0 0 24 24"><path d="{heart_path}"/></symbol>'
    )
    parts.append(f'    <symbol id="i-cal" viewBox="0 0 24 24">{cal_inner}</symbol>')
    parts.append(f"    {_svg_to_symbol(SVG_IG, 'i-ig')}")
    parts.append(f"    {_svg_to_symbol(SVG_SC, 'i-sc')}")
    parts.append(f"    {_svg_to_symbol(SVG_SP, 'i-sp')}")
    parts.append(f"    {_svg_to_symbol(SVG_LT, 'i-lt')}")
    parts.append(f"    {_svg_to_symbol(SVG_YT, 'i-yt')}")
    parts.append(f"    {_svg_to_symbol(SVG_RA, 'i-ra')}")
    parts.append("  </svg>")
    parts.append('  <div class="cmd-bar" id="cmd-bar">')
    parts.append('    <span class="view-label" id="view-label">Line-up</span>')
    parts.append('    <div class="cmd-group">')
    if has_timetable:
        parts.append(
            '      <button onmousedown="this.blur()" onclick="switchView(\'list\', this)" id="btn-list" class="active view-btn">Line-up</button>'
        )
        parts.append(
            '      <button onmousedown="this.blur()" onclick="switchView(\'timetable\', this)" id="btn-timetable" class="view-btn">Timetable</button>'
        )
        parts.append('      <span class="cmd-sep"></span>')
    parts.append(
        '      <button onmousedown="this.blur()" onclick="toggleFilter(this)" id="btn-filter">Show My Picks</button>'
    )
    if has_timetable:
        parts.append(
            '      <button onmousedown="this.blur()" onclick="toggleScheduleFilter(this)" id="btn-schedule" style="display:none">Show My Schedule</button>'
        )
    parts.append("    </div>")
    parts.append('    <div class="cmd-group cmd-group-right">')
    parts.append(
        '      <button onmousedown="this.blur()" onclick="openShareModal()">Share</button>'
    )
    parts.append(
        '      <button onmousedown="this.blur()" onclick="openSyncModal()">Sync</button>'
    )
    parts.append(
        '      <button onmousedown="this.blur()" onclick="toggleNotifications()" id="btn-bell" '
        'aria-label="Notifications">'
        '<svg width="14" height="14" style="position:relative;top:1px" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg></button>'
        '<script>document.getElementById("btn-bell").className=localStorage.getItem("stc_push")==="1"?"active":""</script>'
    )
    parts.append("    </div>")
    parts.append(
        '    <button class="hamburger" onclick="toggleMenu()" aria-label="Menu"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="4" y1="6" x2="20" y2="6"/><line x1="4" y1="12" x2="20" y2="12"/><line x1="4" y1="18" x2="20" y2="18"/></svg></button>'
    )
    parts.append("  </div>")

    # Hamburger dropdown menu
    parts.append('  <div class="cmd-dropdown" id="cmd-dropdown">')
    if has_timetable:
        parts.append(
            "    <button onclick=\"switchView('list', document.getElementById('btn-list')); closeMenu()\" id=\"dd-list\">Line-up</button>"
        )
        parts.append(
            "    <button onclick=\"switchView('timetable', document.getElementById('btn-timetable')); closeMenu()\" id=\"dd-timetable\">Timetable</button>"
        )
    parts.append(
        '    <button onclick="toggleFilter(document.getElementById(\'btn-filter\')); closeMenu()" id="dd-filter">Show My Picks</button>'
    )
    if has_timetable:
        parts.append(
            '    <button onclick="toggleScheduleFilter(document.getElementById(\'btn-schedule\')); closeMenu()" id="dd-schedule" style="display:none">Show My Schedule</button>'
        )
    parts.append('    <button onclick="openShareModal(); closeMenu()">Share</button>')
    parts.append('    <button onclick="openSyncModal(); closeMenu()">Sync</button>')
    parts.append(
        '    <button onclick="toggleNotifications(); closeMenu()" id="dd-bell">Notifications</button>'
    )
    parts.append("  </div>")
    parts.append(
        '  <div class="menu-overlay" id="menu-overlay" onclick="closeMenu()"></div>'
    )

    parts.append(f"  <h1>{esc(title)}</h1>")

    # Share modal
    parts.append(
        '  <div class="modal-overlay" id="m-share" role="dialog" aria-modal="true" aria-labelledby="m-share-title">'
    )
    parts.append('    <div class="modal-box">')
    parts.append('      <h3 id="m-share-title">Share My Picks</h3>')
    parts.append(
        '      <p class="sub" style="color:inherit">Friends can view your picks and schedule. Click the link to copy it.</p>'
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
    parts.append("            <p>Click <strong>Sync</strong></p>")
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

    # iOS PWA instructions modal
    parts.append(
        '  <div class="modal-overlay" id="m-ios" role="dialog" aria-modal="true" aria-labelledby="m-ios-title">'
    )
    parts.append('    <div class="modal-box">')
    parts.append('      <h3 id="m-ios-title">Enable Notifications</h3>')
    parts.append(
        '      <p class="sub" style="color:inherit">On iOS, notifications require Safari and adding the app to your home screen.</p>'
    )
    parts.append(
        '      <button type="button" class="btn" style="margin:0 0 14px;width:100%" '
        "onclick=\"navigator.clipboard.writeText(location.origin).then(function(){this.textContent='Copied!';var b=this;setTimeout(function(){b.textContent='Copy link to open in Safari'},1500)}.bind(this))\">Copy link to open in Safari</button>"
    )
    parts.append(
        '      <p class="sub" style="color:inherit;margin:0 0 10px">Then in Safari:</p>'
    )
    parts.append('      <div class="steps">')
    parts.append(
        "        <p>Tap the <strong>Share</strong> button "
        '<svg style="display:inline;vertical-align:middle" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/><polyline points="16 6 12 2 8 6"/><line x1="12" y1="2" x2="12" y2="15"/></svg></p>'
    )
    parts.append("        <p>Tap <strong>Add to Home Screen</strong></p>")
    parts.append("        <p>Open the app from your home screen</p>")
    parts.append("        <p>Enable notifications</p>")
    parts.append("      </div>")
    parts.append("    </div>")
    parts.append("  </div>")

    # Non-Safari iOS: switch to Safari + sync instructions
    parts.append(
        '  <div class="modal-overlay" id="m-ios-switch" role="dialog" aria-modal="true" aria-labelledby="m-ios-switch-title">'
    )
    parts.append('    <div class="modal-box">')
    parts.append('      <h3 id="m-ios-switch-title">Enable Notifications</h3>')
    parts.append(
        '      <p class="sub" style="color:inherit">On iOS, notifications only work in Safari.</p>'
    )
    parts.append(
        '      <button type="button" class="btn" style="margin:0 0 14px;width:100%" '
        'onclick="navigator.clipboard.writeText(location.origin).then(function(){'
        "this.textContent='Copied!';var b=this;setTimeout(function(){"
        "b.textContent='Copy link to open in Safari'},1500)}.bind(this))\">"
        "Copy link to open in Safari</button>"
    )
    parts.append('      <div class="steps">')
    parts.append("        <p>Open <strong>Safari</strong> and paste the link</p>")
    parts.append(
        "        <p>Tap the <strong>Share</strong> button "
        '<svg style="display:inline;vertical-align:middle" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/><polyline points="16 6 12 2 8 6"/>'
        '<line x1="12" y1="2" x2="12" y2="15"/></svg> '
        "&rarr; <strong>Add to Home Screen</strong></p>"
    )
    parts.append(
        "        <p>Open the app from your home screen and enable notifications</p>"
    )
    parts.append("      </div>")
    parts.append(
        '      <p class="sub" style="color:inherit;margin:10px 0 0"><strong>Tip:</strong> '
        "use Sync to transfer your picks to Safari before switching.</p>"
    )
    parts.append("    </div>")
    parts.append("  </div>")

    # Brave push instructions modal
    parts.append(
        '  <div class="modal-overlay" id="m-brave" role="dialog" aria-modal="true" aria-labelledby="m-brave-title">'
    )
    parts.append('    <div class="modal-box">')
    parts.append('      <h3 id="m-brave-title">Enable Notifications in Brave</h3>')
    parts.append(
        '      <p class="sub" style="color:inherit">Brave blocks push notifications by default because they route through Google\'s servers.</p>'
    )
    parts.append('      <div class="steps">')
    parts.append("        <p>Open <strong>brave://settings/privacy</strong></p>")
    parts.append(
        "        <p>Enable <strong>Use Google services for push messaging</strong></p>"
    )
    parts.append("        <p>Come back and tap the notification bell again</p>")
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
        ra = a.get("ra")
        ig_f = format_followers(a.get("ig_followers"))
        sc_f = format_followers(a.get("sc_followers"))
        sp_l = format_followers(a.get("spotify_listeners"))
        ra_f = format_followers(a.get("ra_followers"))
        sched_main, sched_also = _format_artist_schedule(
            a.get("all_slots", []), cur_date, cur_period
        )

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
        if sched_main:
            parts.append(
                f'        <span class="artist-schedule">{esc(sched_main)}</span>'
            )
        parts.append('        <div class="links">')
        if ig:
            parts.append(
                f"          {_link(ig, _use_svg('i-ig', width='18', height='18'), ig_f or '')}"
            )
        if sc:
            parts.append(
                f"          {_link(sc, _use_svg('i-sc', width='18', height='18'), sc_f or '')}"
            )
        if sp:
            parts.append(
                f"          {_link(sp, _use_svg('i-sp', width='18', height='18'), sp_l or '')}"
            )
        if yt:
            parts.append(
                f"          {_link(yt, _use_svg('i-yt', width='18', height='18'))}"
            )
        if ra:
            parts.append(
                f"          {_link(ra, _use_svg('i-ra', width='18', height='18'), ra_f or '')}"
            )
        if lt:
            parts.append(
                f"          {_link(lt, _use_svg('i-lt', width='18', height='18'))}"
            )
        if not ig and not sc and not sp and not lt and not yt and not ra:
            parts.append('          <span class="missing">No links</span>')
        parts.append("        </div>")
        if sched_also:
            parts.append(f'        <span class="artist-also">{esc(sched_also)}</span>')
        parts.append("        </div>")
        parts.append(
            '        <button class="heart-btn" onclick="toggleHeart(this)" aria-label="Add to favorites" aria-pressed="false"><svg viewBox="0 0 24 24"><use href="#i-heart"/></svg></button>'
        )
        parts.append("      </li>")

    dates_seen: list[str] = []
    sections_by_date: dict[str, list[dict]] = {}
    for sec in ordered_sections:
        sections_by_date.setdefault(sec["date"], []).append(sec)
        if sec["date"] not in dates_seen:
            dates_seen.append(sec["date"])

    # --- List view ---
    if has_timetable:
        parts.append('  <div id="list-view">')

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

    if has_timetable:
        parts.append("  </div>")  # end #list-view

    # --- Timetable view ---
    if has_timetable:
        # Build timetable data
        canonical_floor_order = [
            "eisbahn",
            "grand-hall",
            "koksofenbatterie",
            "listening-floor",
            "mischanlage",
            "salzlager",
            "werksschwimmbad",
        ]
        timetable_data: list[dict] = []
        for date_str in dates_seen:
            for sec in sections_by_date[date_str]:
                artists = assignments.get(sec["key"], [])
                timed = [
                    a for a in artists if a.get("start_time") and a.get("end_time")
                ]
                if not timed:
                    continue

                by_floor: dict[str, list[dict]] = {}
                for a in timed:
                    fid = a.get("location_id") or "unknown"
                    by_floor.setdefault(fid, []).append(a)
                floor_ids = [f for f in canonical_floor_order if f in by_floor] + [
                    f for f in by_floor if f not in canonical_floor_order
                ]

                all_starts = [_parse_time(a["start_time"]) for a in timed]
                all_ends = [_parse_time(a["end_time"]) for a in timed]
                is_night = sec["period"] == "night"
                if is_night:
                    adjusted_ends = []
                    for e in all_ends:
                        adjusted_ends.append(e + 1440 if e < 12 * 60 else e)
                    adjusted_starts = []
                    for s in all_starts:
                        adjusted_starts.append(s + 1440 if s < 12 * 60 else s)
                    grid_start = min(adjusted_starts)
                    grid_end = max(adjusted_ends)
                else:
                    grid_start = min(all_starts)
                    grid_end = max(all_ends)

                grid_start = (grid_start // 60) * 60

                timetable_data.append(
                    {
                        "date": date_str,
                        "period": sec["period"],
                        "key": sec["key"],
                        "floor_ids": floor_ids,
                        "by_floor": by_floor,
                        "grid_start": grid_start,
                        "grid_end": grid_end,
                        "is_night": is_night,
                    }
                )

        needs_large_rows = False
        for td in timetable_data:
            for fid, floor_artists in td["by_floor"].items():
                slots: dict[tuple[str, str], list[dict]] = {}
                for a in floor_artists:
                    key = (a["start_time"], a["end_time"])
                    slots.setdefault(key, []).append(a)
                for (st, et), group in slots.items():
                    dur = _parse_time(et) - _parse_time(st)
                    if td["is_night"] and dur < 0:
                        dur += 1440
                    if dur < 30 * (len(group) + 1):
                        needs_large_rows = True
        row_h = 14 if needs_large_rows else 10

        artist_lookup: dict[str, list[dict]] = {}
        parts.append(f"  <style>.tt-table {{ --row-h: {row_h}px; }}</style>")
        parts.append('  <div id="timetable-view" style="display:none">')

        # Filter bar (day/period tabs)
        parts.append('  <div class="filter-bar">')
        parts.append('    <div class="day-tabs" id="day-tabs">')
        for i, date_str in enumerate(dates_seen):
            active = " active" if i == 0 else ""
            parts.append(
                f'      <button class="day-tab{active}" onclick="switchDay(\'{esc(date_str)}\', this)">'
                f"{esc(_format_date_tab(date_str))}</button>"
            )
        parts.append("    </div>")
        parts.append('    <div class="period-tabs" id="period-tabs"></div>')
        parts.append("  </div>")

        # Render timetable panels per section
        heart_svg = '<svg viewBox="0 0 24 24"><use href="#i-heart"/></svg>'

        for td in timetable_data:
            tt_date_str = td["date"]
            period = td["period"]
            panel_id = f"panel-{tt_date_str}-{period}"
            floor_ids = td["floor_ids"]
            by_floor = td["by_floor"]
            grid_start = td["grid_start"]
            grid_end = td["grid_end"]
            is_night = td["is_night"]
            num_floors = len(floor_ids)

            total_minutes = grid_end - grid_start
            px_per_min = 2

            parts.append(
                f'  <div class="timetable-panel" data-date="{esc(tt_date_str)}" data-period="{esc(period)}" '
                f'data-grid-start="{grid_start}" data-grid-end="{grid_end}" '
                f'data-is-night="{1 if is_night else 0}" id="{esc(panel_id)}">'
            )

            # Floor header bar (desktop — CSS sticky)
            parts.append(
                f'    <div class="floor-header-bar" '
                f'style="grid-template-columns: 40px repeat({num_floors}, var(--col-w, 1fr));">'
            )
            parts.append('      <div class="floor-header-gutter"></div>')
            for fid in floor_ids:
                loc_name = locations.get(fid, {}).get("name", fid)
                curator_key = f"{tt_date_str}.{fid}"
                curator_text = (floor_curators or {}).get(curator_key, "")
                curator_html = (
                    f'<span class="floor-curator">{esc(curator_text)}</span>'
                    if curator_text
                    else ""
                )
                parts.append(
                    f'      <div class="floor-header floor-{esc(fid)}">'
                    f"<span>{esc(loc_name)}</span>{curator_html}</div>"
                )
            parts.append("    </div>")

            parts.append(
                f'    <div class="timetable" data-num-floors="{num_floors}" style="grid-template-columns: 40px repeat({num_floors}, var(--col-w, 1fr)); grid-template-rows: auto repeat({total_minutes}, {px_per_min}px);">'
            )

            # Time labels and grid lines
            hour_start = grid_start // 60
            hour_end = (grid_end + 59) // 60
            for h in range(hour_start, hour_end):
                row = (h * 60 - grid_start) + 2
                display_h = h % 24
                parts.append(
                    f'      <div class="time-label" style="grid-column: 1; grid-row: {row};">{display_h:02d}:00</div>'
                )
                parts.append(
                    f'      <div class="grid-line hour" style="grid-row: {row};"></div>'
                )
                half_row = row + 30
                if half_row < (grid_end - grid_start) + 2:
                    parts.append(
                        f'      <div class="grid-line half" style="grid-row: {half_row};"></div>'
                    )

            # Now line placeholder
            parts.append(
                '      <div class="now-line" style="grid-row: 2; display: none;" data-now-line></div>'
            )

            # Artist blocks
            for col, fid in enumerate(floor_ids, 2):
                floor_artists = by_floor.get(fid, [])
                slots: dict[tuple[str, str], list[dict]] = {}
                for a in floor_artists:
                    key = (a["start_time"], a["end_time"])
                    slots.setdefault(key, []).append(a)

                for (st, et), group in slots.items():
                    start_min = _parse_time(st)
                    end_min = _parse_time(et)
                    if is_night:
                        if start_min < 12 * 60:
                            start_min += 1440
                        if end_min < 12 * 60:
                            end_min += 1440

                    row_start = (start_min - grid_start) + 2
                    row_end = (end_min - grid_start) + 2

                    s_display = _format_hhmm(start_min)
                    e_display = _format_hhmm(end_min)
                    loc_name = locations.get(fid, {}).get("name", fid)

                    card_key = ":".join(
                        [a.get("overlay_id", "") for a in group]
                        + [tt_date_str, period, fid]
                    )
                    artist_id = str(uuid.uuid5(uuid.NAMESPACE_URL, card_key))

                    names = " b2b ".join(a.get("name", "") for a in group)
                    artist_lookup[artist_id] = _json.loads(
                        _artists_json(group, photos_prefix)
                    )
                    data_attrs = (
                        f'data-artist-id="{esc(artist_id)}" '
                        f'data-name="{esc(names)}" '
                        f'data-time="{esc(s_display)} – {esc(e_display)}" '
                        f'data-floor="{esc(loc_name)}" '
                        f'data-ics-start="{esc(st)}" '
                        f'data-ics-end="{esc(et)}"'
                    )

                    cal_svg = '<svg viewBox="0 0 24 24"><use href="#i-cal"/></svg>'
                    is_b2b = len(group) > 1

                    cal_btn = (
                        f'<button class="tt-cal" onclick="event.stopPropagation(); toggleSchedule(this)" '
                        f'aria-label="Add to schedule" aria-pressed="false">{cal_svg}</button>'
                    )
                    parts.append(
                        f'      <div class="tt-block floor-{esc(fid)}" style="grid-column: {col}; grid-row: {row_start} / {row_end};" {data_attrs}>'
                        f'<div class="tt-text">'
                        f'<div class="tt-time-row"><span class="tt-time">{esc(s_display)}–{esc(e_display)}</span>{cal_btn}</div>'
                    )
                    for a in group:
                        photo_local = a.get("photo_local") or ""
                        name = a.get("name", "")
                        loc_for_id = fid if is_night else ""
                        a_card_key = f"{a.get('overlay_id', '')}:{tt_date_str}:{period}:{loc_for_id}"
                        a_artist_id = str(uuid.uuid5(uuid.NAMESPACE_URL, a_card_key))
                        if photo_local:
                            photo_el = f'<img class="tt-photo" src="{esc(photos_prefix + photo_local)}" alt="" loading="lazy">'
                        else:
                            photo_el = '<div class="tt-photo-placeholder"></div>'
                        heart_btn = (
                            f'<button class="tt-photo-heart" onclick="event.stopPropagation(); toggleHeart(this)" '
                            f'aria-label="Add to favorites" aria-pressed="false">{heart_svg}</button>'
                        )
                        parts.append(
                            f'<div class="tt-artist-row" data-artist-id="{esc(a_artist_id)}">'
                            f'<div class="tt-photo-wrap">{photo_el}{heart_btn}</div>'
                            f'<span class="tt-name">{esc(name)}</span></div>'
                        )
                    parts.append(
                        '<a class="tt-ics" onclick="event.stopPropagation(); downloadICS(this.closest(\'[data-ics-start]\'))">Add to calendar</a>'
                        "</div></div>"
                    )

            parts.append("    </div>")  # .timetable

            # Table uses 5-minute rows, starts 5 min early for label centering
            step = 5
            table_start = grid_start - step
            total_rows = (total_minutes + step) // step + 1

            # --- Mobile table (hidden on desktop, shown on mobile via CSS) ---
            parts.append('    <div class="tt-table-wrap">')
            parts.append('    <div class="tt-v-scroll">')
            # Main table
            parts.append(
                f'    <table class="tt-table" style="--num-floors:{num_floors}">'
            )
            parts.append('    <colgroup><col style="width:40px;min-width:40px;">')
            for fid in floor_ids:
                parts.append('<col style="width:40vw;min-width:40vw;">')
            parts.append("</colgroup>")

            # thead — floor name headers
            parts.append("    <thead><tr><th></th>")
            for fid in floor_ids:
                loc_name = locations.get(fid, {}).get("name", fid)
                curator_key = f"{tt_date_str}.{fid}"
                curator_text = (floor_curators or {}).get(curator_key, "")
                if curator_text:
                    parts.append(
                        f'<th class="tt-floor-th floor-{esc(fid)}"><span>{esc(loc_name)}</span>'
                        f'<span class="floor-curator">{esc(curator_text)}</span></th>'
                    )
                else:
                    parts.append(
                        f'<th class="tt-floor-th floor-{esc(fid)}"><span>{esc(loc_name)}</span></th>'
                    )
            parts.append("</tr></thead>")

            # tbody — one row per minute
            # Build a map of artist blocks per floor column:
            #   artist_at[(floor_index, minute_offset)] = (duration, block_html)
            # Also track which cells are covered by rowspan
            artist_at: dict[tuple[int, int], tuple[int, str]] = {}
            for fi, fid in enumerate(floor_ids):
                floor_artists = by_floor.get(fid, [])
                slots_table: dict[tuple[str, str], list[dict]] = {}
                for a in floor_artists:
                    key = (a["start_time"], a["end_time"])
                    slots_table.setdefault(key, []).append(a)

                for (st, et), group in slots_table.items():
                    start_min = _parse_time(st)
                    end_min = _parse_time(et)
                    if is_night:
                        if start_min < 12 * 60:
                            start_min += 1440
                        if end_min < 12 * 60:
                            end_min += 1440

                    offset = start_min - grid_start
                    duration = end_min - start_min
                    if duration <= 0 or offset < 0:
                        continue

                    s_display = _format_hhmm(start_min)
                    e_display = _format_hhmm(end_min)
                    loc_name = locations.get(fid, {}).get("name", fid)

                    card_key = ":".join(
                        [a.get("overlay_id", "") for a in group]
                        + [tt_date_str, period, fid]
                    )
                    artist_id = str(uuid.uuid5(uuid.NAMESPACE_URL, card_key))
                    names = " b2b ".join(a.get("name", "") for a in group)
                    if artist_id not in artist_lookup:
                        artist_lookup[artist_id] = _json.loads(
                            _artists_json(group, photos_prefix)
                        )
                    data_attrs = (
                        f'data-artist-id="{esc(artist_id)}" '
                        f'data-name="{esc(names)}" '
                        f'data-time="{esc(s_display)} – {esc(e_display)}" '
                        f'data-floor="{esc(loc_name)}" '
                        f'data-ics-start="{esc(st)}" '
                        f'data-ics-end="{esc(et)}"'
                    )

                    cal_svg = '<svg viewBox="0 0 24 24"><use href="#i-cal"/></svg>'
                    cal_btn = (
                        f'<button class="tt-cal" onclick="event.stopPropagation(); toggleSchedule(this)" '
                        f'aria-label="Add to schedule" aria-pressed="false">{cal_svg}</button>'
                    )

                    block_parts: list[str] = []
                    block_parts.append(
                        f'<div class="tt-block floor-{esc(fid)}" {data_attrs}>'
                        f'<div class="tt-text">'
                        f'<div class="tt-time-row"><span class="tt-time">{esc(s_display)}–{esc(e_display)}</span>{cal_btn}</div>'
                    )
                    for a in group:
                        photo_local = a.get("photo_local") or ""
                        name = a.get("name", "")
                        loc_for_id = fid if is_night else ""
                        a_card_key = f"{a.get('overlay_id', '')}:{tt_date_str}:{period}:{loc_for_id}"
                        a_artist_id = str(uuid.uuid5(uuid.NAMESPACE_URL, a_card_key))
                        if photo_local:
                            photo_el = f'<img class="tt-photo" src="{esc(photos_prefix + photo_local)}" alt="" loading="lazy">'
                        else:
                            photo_el = '<div class="tt-photo-placeholder"></div>'
                        heart_btn = (
                            f'<button class="tt-photo-heart" onclick="event.stopPropagation(); toggleHeart(this)" '
                            f'aria-label="Add to favorites" aria-pressed="false">{heart_svg}</button>'
                        )
                        block_parts.append(
                            f'<div class="tt-artist-row" data-artist-id="{esc(a_artist_id)}">'
                            f'<div class="tt-photo-wrap">{photo_el}{heart_btn}</div>'
                            f'<span class="tt-name">{esc(name)}</span></div>'
                        )
                    block_parts.append(
                        '<a class="tt-ics" onclick="event.stopPropagation(); downloadICS(this.closest(\'[data-ics-start]\'))">Add to calendar</a>'
                        "</div></div>"
                    )
                    block_html = "".join(block_parts)

                    artist_at[(fi, offset)] = (duration, block_html)

            # Remap artist_at to 5-minute row indices (offset by 1 for the early start)
            artist_at_5: dict[tuple[int, int], tuple[int, str]] = {}
            for (fi, offset), (duration, block_html) in artist_at.items():
                row_idx = (offset // step) + 1  # +1 for the early start row
                row_span = max(1, round(duration / step))
                artist_at_5[(fi, row_idx)] = (row_span, block_html)

            # Track covered floor cells
            covered: set[tuple[int, int]] = set()
            for (fi, row_idx), (row_span, _) in artist_at_5.items():
                for r in range(1, row_span):
                    covered.add((fi, row_idx + r))

            # Track time cells covered by label rowspan
            time_covered: set[int] = set()

            parts.append("    <tbody>")
            for row_idx in range(total_rows):
                actual_min = table_start + row_idx * step
                parts.append("    <tr>")

                # Time label cell — rowspan=2 centered on the boundary
                if row_idx in time_covered:
                    pass  # covered by previous label's rowspan
                else:
                    next_min = actual_min + step
                    if next_min % 60 == 0:
                        display_h = (next_min // 60) % 24
                        parts.append(
                            f'<td class="tt-time-td tt-line-hour" rowspan="2">{display_h:02d}:00</td>'
                        )
                        time_covered.add(row_idx + 1)
                    elif next_min % 30 == 0:
                        display_h = (next_min // 60) % 24
                        parts.append(
                            f'<td class="tt-time-td tt-line-half" rowspan="2">{display_h:02d}:30</td>'
                        )
                        time_covered.add(row_idx + 1)
                    else:
                        parts.append('<td class="tt-time-td"></td>')

                # Floor cells
                for fi in range(len(floor_ids)):
                    if (fi, row_idx) in covered:
                        continue
                    if (fi, row_idx) in artist_at_5:
                        row_span, block_html = artist_at_5[(fi, row_idx)]
                        parts.append(f'<td rowspan="{row_span}">{block_html}</td>')
                    else:
                        parts.append("<td></td>")

                parts.append("</tr>")

            parts.append(
                f'    <tr style="height:calc(var(--row-h) / 2)"><td></td>{"<td></td>" * len(floor_ids)}</tr>'
            )
            parts.append("    </tbody></table>")
            parts.append("    </div>")  # .tt-v-scroll
            parts.append("    </div>")  # .tt-table-wrap
            # --- End mobile table ---

            parts.append("  </div>")  # .timetable-panel

        # Artist detail popup
        parts.append('  <div class="tt-popup" id="tt-popup">')
        parts.append('    <div class="popup-meta" id="popup-meta"></div>')
        parts.append('    <div id="popup-artists"></div>')
        parts.append("  </div>")

        parts.append(
            f"  <script>var TT_ARTISTS={_json.dumps(artist_lookup, separators=(',', ':'))};</script>"
        )
        parts.append("  </div>")  # end #timetable-view

    qr_js = (ICONS_DIR.parent / "qrcode.min.js").read_text(encoding="utf-8")
    parts.append(f"  <script>{qr_js}</script>")
    parts.append("  <script>")
    if has_timetable:
        parts.append("""
    // Immediate view restore before anything renders
    (function() {
      var vp = new URLSearchParams(location.search).get('view');
      if (vp) history.replaceState(null, '', location.pathname);
      var v = vp || localStorage.getItem('stc_view');
      if (v === 'timetable') {
        var lv = document.getElementById('list-view');
        var tv = document.getElementById('timetable-view');
        if (lv) lv.style.display = 'none';
        if (tv) tv.style.display = '';
        var h1 = document.querySelector('h1');
        if (h1) h1.textContent = h1.textContent.replace('Line-up', 'Timetable');
        var bar = document.getElementById('cmd-bar');
        var vl = document.getElementById('view-label');
        if (vl) vl.textContent = 'Timetable';
        var bl = document.getElementById('btn-list');
        var bt = document.getElementById('btn-timetable');
        if (bl) bl.classList.remove('active');
        if (bt) bt.classList.add('active');
        var ddl = document.getElementById('dd-list');
        var ddt = document.getElementById('dd-timetable');
        if (ddl) ddl.style.display = '';
        if (ddt) ddt.style.display = 'none';
      }
    })();
    """)

    # Emit timetable section data for JS (when timetable is present)
    if has_timetable:
        sections_json = _json.dumps(
            [
                {"date": td["date"], "period": td["period"], "key": td["key"]}
                for td in timetable_data
            ]
        )
        parts.append(f"    const TT_SECTIONS = {sections_json};")
        parts.append(f"    const TT_DATES = {_json.dumps(dates_seen)};")
        parts.append("    const HAS_TIMETABLE = true;")
    else:
        parts.append("    const HAS_TIMETABLE = false;")

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
    let localSchedule; try { localSchedule = new Set(JSON.parse(localStorage.getItem('stc_schedule') || '[]')); } catch { localSchedule = new Set(); localStorage.removeItem('stc_schedule'); }
    let readOnly = false;
    let filterActive = false;
    let scheduleFilterActive = false;
    let currentView = localStorage.getItem('stc_view') || 'list';

    function saveLocal() {
      localStorage.setItem('stc_picks', JSON.stringify([...localPicks]));
      localStorage.setItem('stc_schedule', JSON.stringify([...localSchedule]));
      updateUI();
    }

    function updateUI() {
      document.querySelectorAll('[data-artist-id]').forEach(el => {
        el.classList.toggle('hearted', localPicks.has(el.dataset.artistId));
      });
      document.querySelectorAll('.tt-block[data-artist-id]').forEach(el => {
        el.classList.toggle('scheduled', localSchedule.has(el.dataset.artistId));
      });
    }

    function applyHearts() {
      document.querySelectorAll('.heart-btn').forEach(btn => {
        const id = btn.closest('[data-artist-id]').dataset.artistId;
        const active = localPicks.has(id);
        btn.classList.toggle('active', active);
        btn.setAttribute('aria-pressed', active);
      });
      document.querySelectorAll('.tt-heart, .tt-photo-heart').forEach(btn => {
        const id = btn.closest('[data-artist-id]').dataset.artistId;
        const active = localPicks.has(id);
        btn.classList.toggle('active', active);
        btn.setAttribute('aria-pressed', active);
      });
      document.querySelectorAll('.tt-cal').forEach(btn => {
        const id = btn.closest('[data-artist-id]').dataset.artistId;
        const active = localSchedule.has(id);
        btn.classList.toggle('active', active);
        btn.setAttribute('aria-pressed', active);
      });
      updateUI();
    }

    function toggleFilter(btn) {
      filterActive = !filterActive;
      track(filterActive ? 'filter-on' : 'filter-off');
      document.body.classList.toggle('filter-active', filterActive);
      btn.classList.toggle('active', filterActive);
      updateUI();
      updateGroupVisibility();
      syncDropdownState();
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
          for (const id of localSchedule) {
            fetch(API + '/session/' + sessionId + '/schedule/' + id, {method: 'POST'}).catch(() => {});
          }
        } catch {}
        finally { _sessionPromise = null; }
      })();
      return _sessionPromise;
    }

    function track(event, data) { if (typeof umami !== 'undefined') umami.track(event, data); }

    async function toggleHeart(btn) {
      if (readOnly) return;
      const el = btn.closest('[data-artist-id]');
      const id = el.dataset.artistId;
      const adding = !localPicks.has(id);
      const name = el.querySelector('.artist-name')?.textContent || el.dataset.name || id;
      track(adding ? 'heart' : 'unheart', {artist: name});

      if (adding) localPicks.add(id); else localPicks.delete(id);
      btn.classList.toggle('active', adding);
      btn.setAttribute('aria-pressed', adding);
      el.classList.toggle('hearted', adding);
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
          el.classList.toggle('hearted', !adding);
          saveLocal();
        }
      } catch {}
    }

    async function toggleSchedule(btn) {
      if (readOnly) return;
      const el = btn.closest('[data-artist-id]');
      const id = el.dataset.artistId;
      const adding = !localSchedule.has(id);
      const name = el.querySelector('.artist-name')?.textContent || el.dataset.name || id;
      track(adding ? 'schedule-add' : 'schedule-remove', {artist: name});

      if (adding) localSchedule.add(id); else localSchedule.delete(id);
      btn.classList.toggle('active', adding);
      btn.setAttribute('aria-pressed', adding);
      el.classList.toggle('scheduled', adding);
      saveLocal();

      await ensureSession();
      if (!sessionId) return;

      try {
        const method = adding ? 'POST' : 'DELETE';
        const res = await fetch(API + '/session/' + sessionId + '/schedule/' + id, {method});
        if (res.status === 404) {
          sessionId = null; shareToken = null;
          localStorage.removeItem('stc_session_id');
          localStorage.removeItem('stc_share_token');
          await ensureSession();
          return;
        }
        if (!res.ok && res.status !== 204) {
          if (adding) localSchedule.delete(id); else localSchedule.add(id);
          btn.classList.toggle('active', !adding);
          btn.setAttribute('aria-pressed', !adding);
          el.classList.toggle('scheduled', !adding);
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
        if (data.schedule) localSchedule = new Set(data.schedule);
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
          document.querySelectorAll('.tt-heart, .tt-photo-heart, .tt-cal').forEach(b => b.style.pointerEvents = 'none');
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
        const serverSchedule = new Set(data.schedule || []);
        const syncs = [];
        for (const id of localPicks) {
          if (!serverPicks.has(id)) syncs.push(fetch(API + '/session/' + sessionId + '/pick/' + id, {method: 'POST'}).catch(() => {}));
        }
        for (const id of localSchedule) {
          if (!serverSchedule.has(id)) syncs.push(fetch(API + '/session/' + sessionId + '/schedule/' + id, {method: 'POST'}).catch(() => {}));
        }
        await Promise.all(syncs);
        for (const id of serverPicks) localPicks.add(id);
        for (const id of serverSchedule) localSchedule.add(id);
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
            track('sync-complete');
            if (_syncTimer) { clearInterval(_syncTimer); _syncTimer = null; }
            document.getElementById('sync-pending').style.display = 'none';
            document.getElementById('sync-done').style.display = '';
          }
          if (data.picks) {
            localPicks = new Set(data.picks);
            if (data.schedule) localSchedule = new Set(data.schedule);
            saveLocal();
            applyHearts();
            if (data.readonly !== undefined) {
              readOnly = data.readonly;
              if (readOnly) {
                document.querySelectorAll('.heart-btn').forEach(b => b.style.pointerEvents = 'none');
                document.querySelectorAll('.tt-heart, .tt-photo-heart, .tt-cal').forEach(b => b.style.pointerEvents = 'none');
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
        track('share-copy');
        shareLink.classList.add('copied');
        shareLink.value = 'Copied!';
        setTimeout(() => { shareLink.value = url; shareLink.classList.remove('copied'); }, 1500);
      });
    });
    function openShareModal() {
      if (!shareToken) { alert('Heart an artist first.'); return; }
      track('share-open');
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
      track('sync-open');
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
        if (data.schedule) localSchedule = new Set(data.schedule);
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

    // Hamburger menu
    function toggleMenu() {
      document.getElementById('cmd-dropdown').classList.toggle('open');
      document.getElementById('menu-overlay').classList.toggle('open');
    }
    if (window.matchMedia('(max-width:768px)').matches) {
      document.getElementById('cmd-bar').addEventListener('click', function(e) {
        if (!e.target.closest('.hamburger')) toggleMenu();
      });
    }
    function closeMenu() {
      document.getElementById('cmd-dropdown').classList.remove('open');
      document.getElementById('menu-overlay').classList.remove('open');
    }

    // --- Push Notifications ---
    const _isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent);
    const _isStandalone = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
    const _needsSafariSwitch = _isIOS && !!navigator.brave;
    const _supportsPush = 'serviceWorker' in navigator && 'PushManager' in window;

    function _urlBase64ToUint8Array(base64String) {
      const padding = '='.repeat((4 - base64String.length % 4) % 4);
      const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
      const raw = atob(base64);
      const out = new Uint8Array(raw.length);
      for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
      return out;
    }

    function updateBellState() {
      const btn = document.getElementById('btn-bell');
      const dd = document.getElementById('dd-bell');
      const on = localStorage.getItem('stc_push') === '1';
      if (btn) { btn.style.display = _supportsPush ? '' : 'none'; btn.classList.toggle('active', on); }
      if (dd) { dd.style.display = (_supportsPush || _isIOS) ? '' : 'none'; dd.textContent = on ? 'Disable notifications' : 'Enable notifications'; }
    }

    async function enableNotifications() {
      if (_needsSafariSwitch) { openDialog('m-ios-switch'); return; }
      if (_isIOS && !_isStandalone) { openDialog('m-ios'); return; }
      if (!_supportsPush) return;
      const perm = await Notification.requestPermission();
      if (perm !== 'granted') return;
      try {
        const vapidRes = await fetch(API + '/push/vapid-key');
        if (!vapidRes.ok) return;
        const { public_key } = await vapidRes.json();
        const reg = await navigator.serviceWorker.ready;
        const keyBytes = _urlBase64ToUint8Array(public_key);
        var oldSub = await reg.pushManager.getSubscription();
        if (oldSub) await oldSub.unsubscribe();
        const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes });
        await ensureSession();
        if (!sessionId) return;
        await fetch(API + '/session/' + sessionId + '/push/subscribe', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(sub.toJSON()) });
        localStorage.setItem('stc_push', '1');
        track('push-enable');
      } catch (e) {
        if (navigator.brave && e.name === 'AbortError') { openDialog('m-brave'); return; }
        console.warn('Push subscribe failed', e);
      }
      updateBellState();
    }

    async function disableNotifications() {
      try {
        const reg = await navigator.serviceWorker.ready;
        const sub = await reg.pushManager.getSubscription();
        if (sub) {
          const endpoint = sub.endpoint;
          await sub.unsubscribe();
          if (sessionId) {
            await fetch(API + '/session/' + sessionId + '/push/subscribe', { method: 'DELETE', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({endpoint}) });
          }
        }
      } catch {}
      localStorage.removeItem('stc_push');
      track('push-disable');
      updateBellState();
    }

    async function toggleNotifications() {
      if (localStorage.getItem('stc_push') === '1') { await disableNotifications(); }
      else { await enableNotifications(); }
    }

    updateBellState();
    """)
    parts.append("""
    // Keep dropdown button states in sync with cmd-bar buttons
    function syncDropdownState() {
      const ddList = document.getElementById('dd-list');
      const btnList = document.getElementById('btn-list');
      const ddTT = document.getElementById('dd-timetable');
      const btnTT = document.getElementById('btn-timetable');
      const ddFilter = document.getElementById('dd-filter');
      const btnFilter = document.getElementById('btn-filter');
      const ddSched = document.getElementById('dd-schedule');
      const btnSched = document.getElementById('btn-schedule');
      if (ddList && btnList) {
        ddList.style.display = btnList.classList.contains('active') ? 'none' : '';
      }
      if (ddTT && btnTT) {
        ddTT.style.display = btnTT.classList.contains('active') ? 'none' : '';
      }
      if (ddFilter && btnFilter) ddFilter.classList.toggle('active', btnFilter.classList.contains('active'));
      if (ddSched && btnSched) {
        ddSched.style.display = btnSched.style.display;
        ddSched.classList.toggle('active', btnSched.classList.contains('active'));
      }
    }
    """)

    # --- Timetable-specific JS (only when has_timetable) ---
    if has_timetable:
        parts.append("""
    // View toggle
    function switchView(view, btn) {
      track('view-switch', {view});
      currentView = view;
      localStorage.setItem('stc_view', view);
      const listView = document.getElementById('list-view');
      const ttView = document.getElementById('timetable-view');
      const btnList = document.getElementById('btn-list');
      const btnTT = document.getElementById('btn-timetable');
      const btnSched = document.getElementById('btn-schedule');
      const h1 = document.querySelector('h1');
      if (view === 'timetable') {
        listView.style.display = 'none';
        ttView.style.display = '';
        btnList.classList.remove('active');
        btnTT.classList.add('active');
        if (btnSched) btnSched.style.display = '';
        h1.textContent = h1.textContent.replace('Line-up', 'Timetable');
        requestAnimationFrame(truncateNames);
        updateNowLine();
        requestAnimationFrame(() => { sizeMobileTable(); });
      } else {
        listView.style.display = '';
        ttView.style.display = 'none';
        btnList.classList.add('active');
        btnTT.classList.remove('active');
        if (btnSched) btnSched.style.display = 'none';
        h1.textContent = h1.textContent.replace('Timetable', 'Line-up');
        scheduleFilterActive = false;
        document.body.classList.remove('filter-schedule');
        if (btnSched) btnSched.classList.remove('active');
      }
      document.getElementById('view-label').textContent = view === 'timetable' ? 'Timetable' : 'Line-up';
      window.scrollTo(0, 0);
      syncDropdownState();
    }

    function toggleScheduleFilter(btn) {
      scheduleFilterActive = !scheduleFilterActive;
      track(scheduleFilterActive ? 'schedule-filter-on' : 'schedule-filter-off');
      document.body.classList.toggle('filter-schedule', scheduleFilterActive);
      btn.classList.toggle('active', scheduleFilterActive);
      syncDropdownState();
    }

    // Day/period switching
    let currentDate = TT_DATES[0];
    let currentPeriod = null;

    function getPeriodsForDate(date) {
      return TT_SECTIONS.filter(s => s.date === date).map(s => s.period);
    }

    let _savedScrollTop = {};
    let _carryScroll = null;
    function showPanel(date, period) {
      const prevPanel = document.querySelector('.timetable-panel.active');
      if (prevPanel) {
        const prevVScroll = prevPanel.querySelector('.tt-v-scroll');
        if (prevVScroll) _savedScrollTop[prevPanel.dataset.period] = prevVScroll.scrollTop;
      }
      document.querySelectorAll('.timetable-panel').forEach(p => p.classList.remove('active'));
      const id = 'panel-' + date + '-' + period;
      const panel = document.getElementById(id);
      if (panel) panel.classList.add('active');
      const scrollY = _carryScroll ? _carryScroll.top : (_savedScrollTop[period] || 0);
      const scrollX = _carryScroll ? _carryScroll.left : 0;
      _carryScroll = null;
      requestAnimationFrame(() => {
        truncateNames(); sizeMobileTable();
        const next = panel ? panel.querySelector('.tt-v-scroll') : null;
        if (next) { next.scrollTop = scrollY; next.scrollLeft = scrollX; }
      });
      updateNowLine();
    }

    // Sticky fade observers for floor headers (desktop only)
    document.querySelectorAll('.floor-header-bar').forEach(el => {
      if (window.innerWidth <= 480) return;
      const top = parseFloat(getComputedStyle(el).top) || 0;
      if (top === 0) return;
      const s = document.createElement('div');
      s.style.cssText = 'height:0;width:0;pointer-events:none;visibility:hidden;position:relative;top:-' + top + 'px';
      el.parentNode.insertBefore(s, el);
      new IntersectionObserver(([e]) => {
        el.classList.toggle('stuck', e.intersectionRatio === 0);
      }, {threshold: 0}).observe(s);
    });

    function renderPeriodTabs(date) {
      const periods = getPeriodsForDate(date);
      const div = document.getElementById('period-tabs');
      div.innerHTML = '';
      if (periods.length <= 1) {
        currentPeriod = periods[0] || 'day';
        showPanel(date, currentPeriod);
        return;
      }
      const keepPeriod = currentPeriod && periods.includes(currentPeriod) ? currentPeriod : periods[0];
      periods.forEach((p) => {
        const btn = document.createElement('button');
        btn.className = 'period-tab' + (p === keepPeriod ? ' active' : '');
        btn.textContent = p === 'day' ? 'Day' : 'Night';
        btn.onclick = function() {
          div.querySelectorAll('.period-tab').forEach(b => b.classList.remove('active'));
          btn.classList.add('active');
          currentPeriod = p;
          showPanel(date, p);
        };
        div.appendChild(btn);
      });
      currentPeriod = keepPeriod;
      showPanel(date, currentPeriod);
    }

    function switchDay(date, btn) {
      track('day-switch', {day: btn.textContent.trim()});
      const sameDay = date === currentDate;
      const prevVScroll = document.querySelector('.timetable-panel.active .tt-v-scroll');
      _carryScroll = sameDay ? {top: 0, left: 0} : (prevVScroll ? {top: prevVScroll.scrollTop, left: prevVScroll.scrollLeft} : null);
      currentDate = date;
      document.querySelectorAll('.day-tab').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      renderPeriodTabs(date);
    }

    // Init first day
    renderPeriodTabs(TT_DATES[0]);

    // Truncate names
    function truncateNames() {
      document.querySelectorAll('.tt-name').forEach(el => {
        if (el.clientWidth === 0) return;
        const full = el.dataset.full || el.textContent;
        el.dataset.full = full;
        el.textContent = full;
        if (el.scrollWidth > el.clientWidth) {
          let lo = 0, hi = full.length;
          while (hi - lo > 1) {
            const mid = (lo + hi) >> 1;
            el.textContent = full.slice(0, mid) + '\\u2026';
            if (el.scrollWidth > el.clientWidth) hi = mid; else lo = mid;
          }
          el.textContent = full.slice(0, lo) + '\\u2026';
        }
      });
    }
    truncateNames();
    new ResizeObserver(truncateNames).observe(document.body);

    // Artist popup
    const popup = document.getElementById('tt-popup');

    function _popupLink(href, svg, label) {
      return '<a href="' + href + '" target="_blank" rel="noopener noreferrer">' + svg + ' ' + (label || '') + '</a>';
    }
    """)
        parts.append("""
    const SVG_IG_JS = '<svg width="18" height="18"><use href="#i-ig"/></svg>';
    const SVG_SC_JS = '<svg width="18" height="18"><use href="#i-sc"/></svg>';
    const SVG_SP_JS = '<svg width="18" height="18"><use href="#i-sp"/></svg>';
    const SVG_LT_JS = '<svg width="18" height="18"><use href="#i-lt"/></svg>';
    const SVG_YT_JS = '<svg width="18" height="18"><use href="#i-yt"/></svg>';
    const SVG_RA_JS = '<svg width="18" height="18"><use href="#i-ra"/></svg>';""")
        parts.append("""
    let _popupJustOpened = false;
    document.querySelectorAll('.tt-block').forEach(block => {
      block.addEventListener('click', e => {
        if (e.target.closest('.tt-photo-heart') || e.target.closest('.tt-cal') || e.target.closest('.tt-ics')) return;
        if (popup.classList.contains('open')) {
          closePopup();
          _popupJustOpened = true;
          return;
        }
        openBlockPopup(block, e.clientX, e.clientY);
      });
    });

    function openBlockPopup(block, px, py) {
        _popupJustOpened = true;
        const d = block.dataset;
        const artists = TT_ARTISTS[d.artistId] || [];
        const timetable = block.closest('.timetable');
        const tr = timetable ? timetable.getBoundingClientRect() : {left:0, right:window.innerWidth, top:0, bottom:window.innerHeight};
        requestAnimationFrame(() => {
          document.getElementById('popup-meta').textContent = d.time + ' \\u00b7 ' + d.floor;
          let artistsHtml = '';
          artists.forEach(a => {
            const photo = a.photo
              ? '<img class="popup-photo" src="' + a.photo + '" alt="' + a.name + '">'
              : '<div class="popup-photo-placeholder"></div>';
            let links = '';
            if (a.ig) links += _popupLink(a.ig, SVG_IG_JS, a.igF);
            if (a.sc) links += _popupLink(a.sc, SVG_SC_JS, a.scF);
            if (a.sp) links += _popupLink(a.sp, SVG_SP_JS, a.spL);
            if (a.yt) links += _popupLink(a.yt, SVG_YT_JS, '');
            if (a.ra) links += _popupLink(a.ra, SVG_RA_JS, a.raF);
            if (a.lt) links += _popupLink(a.lt, SVG_LT_JS, '');
            artistsHtml += '<div class="popup-artist">' + photo + '<div><div class="popup-name">' + a.name + '</div><div class="links">' + links + '</div></div></div>';
          });
          document.getElementById('popup-artists').innerHTML = artistsHtml;
          popup.style.left = '-9999px';
          popup.style.top = '-9999px';
          popup.classList.add('open');
          const pw = popup.offsetWidth;
          const ph = popup.offsetHeight;
          const vw = window.innerWidth;
          const vh = window.innerHeight;
          let left = px + 12;
          let top = py + 12;
          if (left + pw > vw - 8) left = vw - pw - 8;
          if (left < 8) left = 8;
          if (top + ph > vh - 8) top = py - ph - 12;
          if (top < 8) top = 8;
          popup.style.left = left + 'px';
          popup.style.top = top + 'px';
        });
    }

    function downloadICS(block) {
      track('ics-export', {artist: block.dataset.name});
      window.location.href = '/ics/' + block.dataset.artistId;
    }

    function closePopup() {
      popup.classList.remove('open');
    }
    document.addEventListener('click', e => {
      if (_popupJustOpened) { _popupJustOpened = false; return; }
      if (popup.classList.contains('open') && !e.target.closest('.tt-popup')) closePopup();
    });
    document.addEventListener('scroll', () => closePopup(), {passive: true});
    document.querySelectorAll('.tt-v-scroll').forEach(w => w.addEventListener('scroll', () => closePopup(), {passive: true}));
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape' && !document.querySelector('.modal-overlay.open')) closePopup();
    });

    // Now line
    function updateNowLine() {
      document.querySelectorAll('[data-now-line]').forEach(el => el.style.display = 'none');
      const panel = document.querySelector('.timetable-panel.active');
      if (!panel) return;
      const date = panel.dataset.date;
      const gridStart = parseInt(panel.dataset.gridStart);
      const gridEnd = parseInt(panel.dataset.gridEnd);
      const isNight = panel.dataset.isNight === '1';
      const now = new Date();
      const yyyy = now.getFullYear();
      const mm = String(now.getMonth() + 1).padStart(2, '0');
      const dd = String(now.getDate()).padStart(2, '0');
      const today = yyyy + '-' + mm + '-' + dd;
      const yesterday = new Date(now.getTime() - 86400000);
      const yy = yesterday.getFullYear();
      const ym = String(yesterday.getMonth() + 1).padStart(2, '0');
      const yd = String(yesterday.getDate()).padStart(2, '0');
      const yesterdayStr = yy + '-' + ym + '-' + yd;
      let nowMin = now.getHours() * 60 + now.getMinutes();
      let match = false;
      if (isNight) {
        if (date === today && nowMin >= gridStart && nowMin < 1440) match = true;
        if (date === yesterdayStr && nowMin < 12 * 60) { nowMin += 1440; match = true; }
      } else {
        if (date === today && nowMin >= gridStart && nowMin <= gridEnd) match = true;
      }
      if (!match || nowMin < gridStart || nowMin > gridEnd) return;
      const row = (nowMin - gridStart) + 2;
      const line = panel.querySelector('[data-now-line]');
      if (line) { line.style.display = ''; line.style.gridRow = row + ''; }
    }
    setInterval(updateNowLine, 60000);



    function sizeMobileTable() {
      document.querySelectorAll('.tt-v-scroll').forEach(vscroll => {
        if (vscroll.offsetHeight === 0) return;
        const top = vscroll.getBoundingClientRect().top;
        vscroll.style.height = (window.innerHeight - top) + 'px';
      });
    }
    window.addEventListener('resize', sizeMobileTable);


    """)

    parts.append("""
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
            if (data.schedule) localSchedule = new Set(data.schedule);
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
      var viewParam = p.get('view');
      if (viewParam) { history.replaceState(null, '', location.pathname); currentView = viewParam; }
      // Check for push notification navigate flag (iOS workaround)
      if ('caches' in window) {
        try {
          var pushCache = await caches.open('stc-push');
          var navResp = await pushCache.match('/_push_navigate');
          if (navResp) {
            var navUrl = await navResp.text();
            await pushCache.delete('/_push_navigate');
            if (navUrl.includes('timetable')) currentView = 'timetable';
          }
        } catch {}
      }
      if (currentView === 'timetable' && document.getElementById('btn-timetable')) {
        switchView('timetable', document.getElementById('btn-timetable'));
      }
      syncDropdownState();
      setTimeout(syncDropdownState, 100);
      // Re-sync push subscription to server (handles purged DB, reinstalls, etc.)
      if (localStorage.getItem('stc_push') === '1' && 'serviceWorker' in navigator) {
        try {
          var swReg = await navigator.serviceWorker.ready;
          var existingSub = await swReg.pushManager.getSubscription();
          if (existingSub && sessionId) {
            fetch(API + '/session/' + sessionId + '/push/subscribe', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(existingSub.toJSON()) }).catch(function() {});
          } else if (!existingSub) {
            localStorage.removeItem('stc_push');
            updateBellState();
          }
        } catch {}
      }""")
    if has_timetable:
        parts.append("      updateNowLine();")
    parts.append("""
    })();

    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.register('/sw.js').catch(function() {});
    }
    """)
    parts.append("  </script>")
    parts.append("</body>")
    parts.append("</html>")
    return "\n".join(parts)
