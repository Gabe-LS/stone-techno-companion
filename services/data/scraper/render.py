from __future__ import annotations

import html
import json as _json
import uuid
from datetime import datetime
from pathlib import Path

from .scrape import format_followers
from .timetable_json import slot_uuid

ICONS_DIR = Path(__file__).resolve().parent / "icons"


def _slot_group_times(slots: dict) -> dict:
    """Map each artist-group in a floor's slot dict to all its (start, end)
    times, so slot_uuid keeps the historical id for the canonical slot and only
    disambiguates a genuine same-artist repeat set (see timetable_json.slot_uuid)."""
    gt: dict[tuple, list[tuple[str, str]]] = {}
    for (st, et), grp in slots.items():
        gt.setdefault(tuple(x.get("id", "") for x in grp), []).append((st, et))
    return gt


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


PLATFORM_ICONS = {
    "instagram": "i-ig",
    "soundcloud": "i-sc",
    "spotify": "i-sp",
    "youtube": "i-yt",
    "ra": "i-ra",
    "linktree": "i-lt",
}


def _artists_json(group: list[dict], photos_prefix: str) -> str:
    return _json.dumps(
        [
            {
                "oid": a.get("id", ""),
                "name": a.get("name", ""),
                "photo": photos_prefix + a["photo_file"] if a.get("photo_file") else "",
                "links": [
                    {
                        "p": lnk["platform"],
                        "u": lnk["url"],
                        "f": format_followers(lnk.get("follower_count")) or "",
                    }
                    for lnk in a.get("links", [])
                    if lnk.get("url")
                ],
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


def _render_markdown(text: str) -> str:
    import markdown as _md
    import re as _re
    from html.parser import HTMLParser

    result = _md.markdown(text, extensions=["nl2br"])

    _ALLOWED_TAGS = frozenset(
        {"p", "br", "strong", "em", "b", "i", "a", "ul", "ol", "li",
         "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "code", "pre", "hr"}
    )
    _ALLOWED_ATTRS = {"a": frozenset({"href"})}
    _SAFE_HREF_RE = _re.compile(r"^https?://", _re.IGNORECASE)

    class _Sanitizer(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.out = []

        def handle_starttag(self, tag, attrs):
            if tag not in _ALLOWED_TAGS:
                return
            allowed = _ALLOWED_ATTRS.get(tag, frozenset())
            safe_attrs = []
            for k, v in attrs:
                if k not in allowed:
                    continue
                if k == "href" and not _SAFE_HREF_RE.match(v or ""):
                    continue
                safe_attrs.append(f'{k}="{_re.sub(r"[\"&]", lambda m: "&quot;" if m.group() == chr(34) else "&amp;", v or "")}"')
            attr_str = (" " + " ".join(safe_attrs)) if safe_attrs else ""
            self.out.append(f"<{tag}{attr_str}>")

        def handle_endtag(self, tag):
            if tag in _ALLOWED_TAGS:
                self.out.append(f"</{tag}>")

        def handle_data(self, data):
            self.out.append(data.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

        def handle_entityref(self, name):
            self.out.append(f"&{name};")

        def handle_charref(self, name):
            self.out.append(f"&#{name};")

    s = _Sanitizer()
    s.feed(result)
    return "".join(s.out).strip()


def _strip_booking(text: str) -> str:
    import re as _re

    paragraphs = text.split("\n\n")
    kept = []
    for p in paragraphs:
        lines = p.strip().splitlines()
        if any(
            _re.match(
                r"^(bookings?|management|press|promos?|agency|contact|licensing|selected performance)\b",
                line.strip(),
                _re.IGNORECASE,
            )
            or ("@" in line and (">" in line or ":" in line))
            or _re.match(r"^https?://\S+$", line.strip())
            or _re.match(r"^www\.\S+$", line.strip())
            for line in lines
        ):
            break
        kept.append(p)
    return "\n\n".join(kept).strip()


def render_output_html(
    title: str,
    ordered_sections: list[dict],
    assignments: dict[str, list[dict]],
    locations: dict[str, dict],
    has_timetable: bool = False,
    photos_prefix: str = "photos/",
    stage_curators: dict[str, str] | None = None,
    stage_colors: dict[str, str] | None = None,
    output_dir: str | None = None,
    videos: dict[str, list[dict]] | None = None,
    site_short: str = "ST26",
) -> str:
    def esc(text: str | None) -> str:
        return html.escape(text or "")

    def json_for_script(obj) -> str:
        return _json.dumps(obj, separators=(",", ":")).replace("<", "\\u003c")

    parts: list[str] = []
    parts.append("<!DOCTYPE html>")
    parts.append('<html lang="en">')
    parts.append("<head>")
    # Last-section resume, INSTALLED APP ONLY: a home-screen launch at
    # start_url '/' reopens whichever section the user was in last (chat
    # included). In a normal browser tab '/' never bounces to chat: it
    # always resolves to the last used lineup view via stc_view below
    # (owner decision, July 2026). The standalone checks mirror the
    # pwa-standalone detection in chat.html. Explicit lineup visits
    # reclaim the flag so the next launch returns here.
    parts.append(
        "  <script>(function(){try{"
        "if(location.pathname==='/'"
        "&&(window.navigator.standalone||window.matchMedia('(display-mode: standalone)').matches)"
        "&&localStorage.getItem('last_section')==='chat')"
        "{location.replace('/chat');return;}"
        "localStorage.setItem('last_section','lineup');"
        "}catch(e){}})()</script>"
    )
    if has_timetable:
        parts.append(
            "  <script>(function(){"
            "var v=location.pathname==='/timetable'?'timetable':"
            "location.pathname==='/line-up'?'list':"
            "(localStorage.getItem('stc_view')==='timetable'?'timetable':'list');"
            # stc_view means "last view SEEN", not "last toggled": an
            # explicit /line-up or /timetable visit updates it too, so the
            # chat calendar icon and the neutral / return to this view
            "try{localStorage.setItem('stc_view',v)}catch(e){}"
            "document.documentElement.className='view-'+v;"
            "if(location.pathname!==(v==='timetable'?'/timetable':'/line-up'))"
            "history.replaceState(null,'',v==='timetable'?'/timetable':'/line-up');"
            "})()</script>"
        )
    parts.append(
        "  <style>"
        ".view-list #timetable-view{display:none}"
        ".view-timetable #list-view{display:none}"
        ".view-timetable #btn-list{color:#aaa!important}"
        ".view-timetable #btn-timetable{color:#fff!important}"
        ".view-list #btn-timetable{color:#aaa}"
        ".view-timetable #view-label{font-size:0}"
        ".view-timetable #view-label::after{content:'Timetable';font-size:var(--font-sm)}"
        ".view-timetable #page-title{font-size:0}"
        ".view-timetable #page-title::after{content:'Timetable';font-size:var(--font-2xl)}"
        "@media (max-width:768px){.view-timetable #page-title::after{font-size:var(--font-xl)}}"
        ".view-timetable #btn-schedule{display:inline-block!important}"
        ".view-list #dd-list{background:var(--gray-700)}"
        ".view-timetable #dd-timetable{background:var(--gray-700)}"
        "body{opacity:0}"
        "</style>"
    )
    parts.append('  <meta charset="UTF-8">')
    parts.append(
        '  <meta name="viewport" content="width=device-width, initial-scale=1.0">'
    )
    parts.append(f"  <title>Line-up &middot; {esc(site_short)}</title>")
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
    parts.append('  <link rel="stylesheet" href="/shared.css">')
    parts.append('  <link rel="manifest" href="/manifest.json">')
    parts.append('  <meta name="mobile-web-app-capable" content="yes">')
    parts.append(
        '  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">'
    )
    parts.append(f'  <meta name="apple-mobile-web-app-title" content="{esc(title)}">')
    parts.append('  <meta name="theme-color" content="#111">')
    parts.append("  <style>")
    parts.append("""
    /* ===== PAGE-SPECIFIC TOKENS ===== */
    :root {
      --color-accent: #e53e3e;
      --color-schedule: #4a90d9;
      --color-line-hour: #ccc;
      --color-line-half: #e8e8e8;
      --radius-card: var(--radius-md);
      --radius-modal: 14px;
    }

    /* ===== BASE OVERRIDES ===== */
    html { scrollbar-width: none; }
    ::-webkit-scrollbar { display: none; }
    body { max-width: 960px; margin: 0 auto; padding: 0 var(--space-xl); }

    /* ===== COMPONENTS ===== */

    /* --- Command bar --- */

    /* --- Sticky headings --- */
    /* margin-top is explicit (not the em-based UA default) so the timetable
       view's font-size:0 title trick cannot collapse it. The ::before strip
       keeps that margin band opaque while the h1 travels to its pin, so
       scrolling content never shows through the header area. */
    h1::before { content: ''; position: absolute; left: 0; right: 0; bottom: 100%; height: 21px; background: var(--color-bg); }
    h1 { margin-top: 21px; margin-bottom: var(--space-xl); font-size: var(--font-2xl); position: sticky; top: var(--sticky-top-h1, 28px); background: var(--color-bg); z-index: 30; padding: var(--space-md) 0 var(--space-sm); border-bottom: 2px solid #222; }
    section.date-section { margin-bottom: 48px; }
    .date-section > h2 { position: sticky; top: var(--sticky-top-h2, 96px); background: var(--color-bg); z-index: 20; padding: 10px 0 var(--space-sm); margin-bottom: var(--space-sm); font-size: var(--font-xl); border-bottom: 1px solid var(--color-line-hour); }
    h3.period-heading { position: sticky; top: var(--sticky-top-h3, 150px); background: var(--color-bg); z-index: var(--z-sticky); padding: var(--space-sm) 0 6px; margin: var(--space-xl) 0 var(--space-md); font-size: var(--font-lg); color: #333; text-transform: uppercase; letter-spacing: 0.05em; }
    h4.location-heading { position: sticky; top: var(--sticky-top-h4, 190px); background: var(--color-bg); z-index: var(--z-sticky); font-size: var(--font-base); padding: 6px 0 var(--space-xs); margin: var(--space-lg) 0 var(--space-sm); color: #555; border-bottom: 1px solid var(--color-surface-hover); }
    h4.location-heading small { font-weight: normal; color: var(--color-muted); }

    /* --- Artist list --- */
    ul.artist-list { list-style: none; padding: 0; margin: 0; }
    li.artist-item { display: flex; align-items: center; gap: var(--space-lg); padding: var(--space-md); margin-bottom: var(--space-sm); background: var(--gray-50); border-radius: var(--radius-card); border: 1px solid var(--color-surface-hover); }
    .artist-photo { width: 120px; height: 120px; object-fit: cover; border-radius: 6px; flex-shrink: 0; box-shadow: 0 1px 3px rgba(0,0,0,0.1); cursor: pointer; }
    .photo-placeholder { width: 120px; height: 120px; flex-shrink: 0; background: var(--color-surface-hover); border-radius: 6px; }
    .artist-info { flex: 1; min-width: 0; }
    .artist-name { font-weight: 700; font-size: var(--font-lg); display: block; margin-bottom: 3px; cursor: pointer; }
    .artist-schedule { color: var(--color-muted); font-size: var(--font-sm); display: block; margin-bottom: 6px; }
    .artist-also { color: var(--color-muted); font-size: var(--font-xs); line-height: 1; margin-top: var(--space-xs); }

    /* --- Social links --- */
    .links { display: flex; flex-wrap: wrap; column-gap: 18px; row-gap: var(--space-xs); align-items: center; }
    .links a { display: inline-flex; align-items: center; gap: 5px; text-decoration: none; color: #555; font-size: var(--font-xs); padding: 3px 0; min-width: 72px; font-variant-numeric: tabular-nums; }
    .links a svg { flex-shrink: 0; }
    .missing { color: var(--color-muted); font-size: var(--font-xs); }
    @media (hover: hover) { .links a:hover { color: var(--color-text); } }

    /* --- Heart button --- */
    .heart-btn { background: none; border: none; padding: 6px; flex-shrink: 0; align-self: flex-start; margin-top: 2px; cursor: pointer; }
    .heart-btn svg { fill: none; stroke: var(--color-muted-icon); stroke-width: 2; transition: fill var(--transition-fast), stroke var(--transition-fast); width: 22px; height: 22px; }
    .heart-btn:focus:not(:focus-visible) { outline: none; }
    .heart-btn.active svg { fill: var(--color-accent); stroke: var(--color-accent); }

    /* --- Filters --- */
    .filter-active .artist-item:not(.hearted) { display: none; }
    .filter-active .tt-block:not(.hearted):not(:has(.tt-artist-row.hearted)) { opacity: 0.15; }

    /* --- Bio overlay --- */
    .modal-box.bio-box { background: var(--color-bg); border-radius: var(--radius-modal); padding: 0; width: 480px; max-width: 100%; color: var(--color-text); box-shadow: var(--shadow-modal); max-height: 80vh; max-height: 80dvh; overflow: hidden; text-align: left; }
    .bio-scroll { max-height: 80vh; max-height: 80dvh; overflow-y: auto; padding: var(--space-xl); -webkit-overflow-scrolling: touch; }
    .bio-header { display: flex; gap: var(--space-lg); align-items: flex-start; margin-bottom: var(--space-lg); }
    .bio-photo { width: 128px; height: 128px; border-radius: var(--radius-card); object-fit: cover; flex-shrink: 0; }
    .bio-photo-placeholder { width: 128px; height: 128px; border-radius: var(--radius-card); background: var(--color-surface-hover); flex-shrink: 0; }
    .bio-name { font-weight: 700; font-size: var(--font-xl); margin-top: var(--space-xs); }
    .bio-text { font-size: var(--font-sm); line-height: 1.6; color: #333; }
    .bio-text p { margin: 0 0 0.6em; }
    .bio-text p:last-child { margin-bottom: 0; }
    .bio-text ul, .bio-text ol { margin: 0.4em 0; padding-left: 1.4em; }
    .bio-text strong { font-weight: 600; }
    .bio-text a { color: inherit; text-decoration: underline; }
    .bio-text:empty { display: none; }
    .bio-empty { font-size: var(--font-sm); color: var(--color-muted); font-style: italic; }
    .bio-videos { margin-top: var(--space-lg); }
    .bio-videos-title { font-size: var(--font-sm); font-weight: 600; margin-bottom: 10px; color: var(--color-text); }
    .bio-video { display: flex; gap: var(--space-md); align-items: flex-start; margin-bottom: 10px; text-decoration: none; color: inherit; border-radius: 6px; transition: background var(--transition-fast); padding: var(--space-xs); margin-left: -4px; margin-right: -4px; }
    .bio-video:last-child { margin-bottom: 0; }
    .bio-video-thumb { width: 120px; height: 90px; border-radius: var(--radius-sm); object-fit: cover; flex-shrink: 0; background: var(--color-surface-hover); }
    .bio-video-info { flex: 1; min-width: 0; }
    .bio-video-title { font-size: var(--font-xs); font-weight: 600; line-height: 1.3; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
    .bio-video-meta { font-size: var(--font-xs); color: var(--color-muted); margin-top: 3px; }
    @media (hover: hover) { .bio-video:hover { background: var(--color-surface); } }

    /* --- Modals --- */
    .modal-overlay { display: none; position: fixed; inset: 0; z-index: var(--z-modal); background: rgba(0,0,0,.4); padding: var(--space-xl); }
    .modal-overlay.open { display: flex; justify-content: center; align-items: center; overflow-y: auto; overscroll-behavior: contain; }
    .modal-box { background: var(--color-bg); border-radius: var(--radius-modal); padding: var(--space-xl); width: 420px; max-width: 100%; text-align: center; color: var(--color-text); box-shadow: var(--shadow-modal); }
    .modal-box h3 { margin: 0 0 6px; font-size: var(--font-base); font-weight: 600; text-wrap: balance; }
    .modal-box .sub { font-size: var(--font-xs); color: var(--color-muted-icon); margin: 0 0 14px; text-wrap: balance; }
    .modal-link { display: block; width: 100%; background: var(--color-surface); padding: var(--space-md) 14px; border-radius: var(--radius-card); font-size: var(--font-sm); font-family: inherit; color: #333; transition: background var(--transition-fast); margin: 0; border: none; text-align: left; overflow: hidden; text-overflow: clip; white-space: nowrap; cursor: pointer; }
    .modal-link:focus:not(:focus-visible) { outline: none; }
    .modal-link:focus-visible { outline: 1px solid var(--color-text); outline-offset: 2px; }
    .modal-link.copied { background: #d4edda; text-align: center; }
    .modal-box canvas { display: block; margin: 10px auto; border-radius: 6px; }
    .modal-box .or-line { display: flex; align-items: center; gap: 10px; margin: 10px 0; }
    .modal-box .or-line hr { flex: 1; border: none; border-top: 1px solid var(--color-border); }
    .modal-box .or-line span { color: var(--color-muted); font-size: var(--font-xs); }
    .modal-box .tabs { display: flex; gap: 3px; margin-bottom: 14px; border-radius: var(--radius-card); border: 1px solid var(--color-border); padding: 3px; background: var(--color-surface); }
    .modal-box .tabs button { flex: 1; background: transparent; border: none; padding: 7px var(--space-xs); font-size: var(--font-xs); color: var(--color-muted-icon); border-radius: 5px; transition: color var(--transition-fast), background var(--transition-fast); cursor: pointer; }
    .modal-box .tabs button:focus:not(:focus-visible) { outline: none; }
    .modal-box .tabs button:focus-visible { outline: 1px solid var(--color-text); outline-offset: -2px; }
    .modal-box .tabs button.on { background: var(--color-text); color: #fff; }
    .modal-box .pane { display: none; }
    .modal-box .pane.on { display: block; }
    .modal-box .lbl { font-size: var(--font-sm); color: #333; text-align: left; margin: 0 0 var(--space-xs); }
    .modal-box .recv-lbl { font-size: var(--font-sm); color: #333; text-align: left; margin: 10px 0 var(--space-xs); }
    .modal-box .steps { counter-reset: s; }
    .modal-box .steps p { text-align: left; font-size: var(--font-xs); color: #333; margin: 5px 0; padding-left: 20px; position: relative; }
    .modal-box .steps p::before { content: counter(s) ". "; counter-increment: s; font-weight: 600; position: absolute; left: 0; }
    .modal-box .btn { background: var(--color-text); color: #fff; border: none; padding: 7px 18px; border-radius: 5px; font-size: var(--font-sm); margin-top: var(--space-sm); cursor: pointer; }
    .modal-box .btn:focus:not(:focus-visible) { outline: none; }
    .modal-box .btn:focus-visible { outline: 1px solid var(--color-text); outline-offset: 2px; }
    .qr-wrap { display: block; }
    @media (hover: hover) {
      .modal-link:hover { background: var(--color-surface-hover); }
      .modal-box .tabs button:hover:not(.on) { background: var(--color-surface-hover); color: #555; }
      .modal-box .btn:hover { background: #333; }
    }

    /* --- Pin / sync --- */
    .pin { display: flex; gap: 5px; justify-content: center; margin: 10px 0; }
    .pin span { width: 28px; height: 36px; font-size: var(--font-lg); font-weight: 700; border: 1px solid #ddd; border-radius: 5px; background: var(--color-surface); color: var(--color-text); display: flex; align-items: center; justify-content: center; line-height: 1; }
    .sync-expiry { font-size: var(--font-xs); color: var(--color-muted-icon); text-align: center; margin: var(--space-sm) 0 0; }
    .sync-expiry a { color: inherit; text-decoration: underline; }
    .pin-wrap { position: relative; cursor: text; margin: 10px 0; -webkit-tap-highlight-color: transparent; }
    .pin-wrap .pin { pointer-events: none; }
    .pin-wrap .pin span.active { border-color: var(--color-text); background: var(--color-bg); }
    .pin-wrap.focused .pin span.active:empty::after { content: ''; width: 2px; height: 1.2em; background: var(--color-text); border-radius: 1px; animation: blink 1s step-end infinite; }
    @keyframes blink { 0%,100% { opacity: 1; } 50% { opacity: 0; } }
    .pin-wrap .pin span.filled { color: var(--color-text); }
    .pin-real { position: absolute; inset: 0; opacity: 0; font-size: var(--font-base); width: 100%; height: 100%; border: none; padding: 0; margin: 0; -webkit-tap-highlight-color: transparent; }

    /* --- Desktop-hidden elements --- */

    /* --- Filter visibility via CSS :has() --- */
    .filter-active section.date-section:not(:has(.artist-item.hearted)) { display: none; }
    .filter-active ul.artist-list:not(:has(.artist-item.hearted)) { display: none; }
    /* location-heading visibility is JS-driven (updateGroupVisibility), not :has() --
       a :has(+ ul:not(:has(...))) selector re-evaluates broadly on every heart
       toggle; a plain class avoids that cost. */
    .filter-active h4.location-heading.no-hearted { display: none; }

    /* ===== MEDIA QUERIES ===== */
    @media (max-width: 480px) {
      body { padding: 0 var(--space-md); }
      h1 { margin-top: var(--space-lg); font-size: var(--font-xl); padding: var(--space-sm) 0 6px; }
      h1::before { height: var(--space-lg); }
      .date-section > h2 { font-size: var(--font-lg); padding: 6px 0; }
      h3.period-heading { font-size: var(--font-base); padding: 6px 0 var(--space-xs); margin: var(--space-lg) 0 var(--space-sm); }
      li.artist-item { gap: 10px; padding: 10px; align-items: flex-start; flex-wrap: wrap; }
      .artist-also { margin-left: calc(-72px - 10px); width: calc(100% + 72px + 10px); margin-top: 10px; display: block; }
      .artist-photo { width: 72px; height: 72px; border-radius: var(--radius-sm); margin-top: 2px; }
      .photo-placeholder { width: 72px; height: 72px; border-radius: var(--radius-sm); margin-top: 2px; }
      .artist-name { font-size: var(--font-base); }
      .artist-schedule { font-size: var(--font-xs); margin-bottom: var(--space-xs); }
      .links { column-gap: var(--space-sm); row-gap: 0; }
      .links a { font-size: var(--font-xs); min-width: 72px; gap: 3px; }
      .links a svg { width: 14px; height: 14px; }
      .heart-btn svg { width: 18px; height: 18px; }
      .qr-wrap { display: none; }
      .modal-box .tabs { flex-direction: column; }
      .modal-box.bio-box { border-radius: 10px; }
      .bio-photo, .bio-photo-placeholder { width: 96px; height: 96px; }
      .bio-name { font-size: var(--font-lg); }
      .bio-video-thumb { width: 96px; height: 72px; }
    }
    """)
    if has_timetable:
        parts.append("""
    /* --- Timetable view --- */

    /* Filter bar */
    .filter-bar::after { content: ''; position: absolute; left: 0; right: 0; top: 100%; height: var(--space-sm); background: var(--color-bg); }
    /* min-height mirrors the date h2 box (1.5 line-height x its font + same padding/border) so both sticky bars are equal-height */
    .filter-bar { position: sticky; top: var(--sticky-top-h2, 96px); z-index: 20; background: var(--color-bg); display: flex; align-items: center; justify-content: space-between; padding: 10px 0 var(--space-sm); margin: 0.83em 0 var(--space-sm); gap: var(--space-sm); border-bottom: 1px solid var(--color-line-hour); min-height: calc(1.5 * var(--font-xl) + 19px); }

    /* Floor headers */
    /* pins exactly below the filter bar: its top + its token-derived height */
    .floor-header-bar { display: grid; position: sticky; top: calc(var(--sticky-top-h2, 96px) + 1.5 * var(--font-xl) + 19px); z-index: var(--z-sticky); background: var(--color-bg); padding: var(--space-sm) 0 6px; margin: var(--space-xl) 0 var(--space-md); align-items: start; }
    .floor-header-bar::after { content: ''; position: absolute; left: 0; right: 0; top: 100%; height: 36px; background: var(--fade-gradient); pointer-events: none; opacity: 0; transition: opacity var(--transition-fast); }
    .floor-header-bar.stuck::after { opacity: 1; }
    .floor-header { text-align: center; margin: 0 3px; background: none !important; }
    .floor-header > span:first-child { display: block; font-weight: 700; font-size: var(--font-sm); padding: var(--space-sm) var(--space-md); border-radius: var(--radius-pill); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .floor-curator { font-style: italic; font-weight: normal; font-size: var(--font-xs); color: var(--color-muted); margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

    /* Timetable grid */
    .timetable-panel { display: none; }
    .timetable-panel.active { display: block; }
    .timetable { display: grid; position: relative; margin-bottom: var(--space-xs); }
    .time-label { font-size: var(--font-xs); color: var(--color-muted); text-align: right; padding-right: var(--space-sm); line-height: 1; position: relative; top: calc(-0.5em + 1px); }
    .grid-line { grid-column: 2 / -1; pointer-events: none; }
    .grid-line.hour { border-top: 1px solid var(--color-line-hour); }
    .grid-line.half { border-top: 1px dashed var(--color-line-half); }

    /* Artist blocks */
    .tt-block { border-radius: 6px; margin: 5px 3px var(--space-xs); padding: var(--space-sm) 10px; font-size: var(--font-sm); position: relative; display: flex; flex-direction: row; align-items: flex-start; border: 1px solid var(--color-border); transition: opacity var(--transition-fast); min-height: 0; cursor: pointer; }
    .tt-text { width: 0; flex-grow: 1; display: flex; flex-direction: column; }
    .tt-block .tt-time-row { display: flex; align-items: center; justify-content: space-between; margin-bottom: 5px; }
    .tt-block .tt-time { font-size: var(--font-xs); color: var(--color-muted); white-space: nowrap; line-height: 1; }
    .tt-artist-row { display: flex; align-items: center; gap: var(--space-sm); margin-top: 6px; min-width: 0; }
    .tt-photo-wrap { position: relative; flex-shrink: 0; width: 34px; height: 34px; }
    .tt-photo { width: 34px; height: 34px; border-radius: var(--radius-sm); object-fit: cover; display: block; }
    .tt-photo-placeholder { width: 34px; height: 34px; border-radius: var(--radius-sm); background: var(--color-surface-hover); }
    .tt-block .tt-name { font-weight: 700; font-size: var(--font-sm); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; line-height: 1.3; min-width: 0; flex: 1; }

    /* Per-artist heart */
    .tt-photo-heart { position: absolute; bottom: -5px; right: -5px; background: rgba(255,255,255,0.85); border: none; padding: 2px; line-height: 0; border-radius: 50%; width: 18px; height: 18px; display: flex; align-items: center; justify-content: center; z-index: 1; cursor: pointer; }
    .tt-photo-heart svg { width: 12px; height: 12px; fill: none; stroke: var(--color-muted-icon); stroke-width: 2; transition: fill var(--transition-fast), stroke var(--transition-fast); }
    .tt-photo-heart.active svg { fill: var(--color-accent); stroke: var(--color-accent); }

    /* Calendar icon */
    .tt-cal { background: none; border: none; padding: 0; line-height: 0; flex-shrink: 0; cursor: pointer; }
    .tt-cal svg { width: var(--space-lg); height: var(--space-lg); color: var(--color-muted-icon); transition: color var(--transition-fast); }
    .tt-cal.active svg { color: var(--color-schedule); }
    .tt-block.scheduled { box-shadow: inset 0 0 0 2px var(--color-schedule); }
    .tt-ics { position: absolute; bottom: var(--space-xs); left: var(--space-sm); color: var(--color-muted); background: none; border: none; font: inherit; font-size: var(--font-xs); padding: 0; cursor: pointer; }
    .filter-schedule .tt-block:not(.scheduled) { opacity: 0.15; }
    @media (hover: hover) { .tt-ics:hover { color: #555; } }

    /* Now line */
    .now-line { grid-column: 2 / -1; border-top: 2px solid var(--color-accent); pointer-events: none; z-index: 8; position: relative; }
    .now-line::before { content: 'NOW'; position: absolute; left: -48px; top: -8px; font-size: var(--font-xs); font-weight: 700; color: var(--color-accent); letter-spacing: 0.05em; }

    /* Floor colors — generated dynamically */
    .floor-unknown { background: rgba(243, 244, 246, 0.88); }

    /* Mobile table — hidden on desktop */
    .tt-table-wrap { display: none; }

    /* Artist detail popup */
    .tt-popup { position: fixed; z-index: var(--z-popup); background: var(--color-bg); border-radius: 10px; box-shadow: 0 8px 24px rgba(0,0,0,0.18); padding: var(--space-lg); width: 320px; max-width: 90vw; visibility: hidden; opacity: 0; pointer-events: none; }
    .tt-popup.open { visibility: visible; opacity: 1; pointer-events: auto; }
    .tt-popup .popup-meta { font-size: var(--font-xs); color: var(--color-muted); margin-bottom: 10px; }
    .tt-popup .popup-artist { display: flex; gap: 14px; align-items: flex-start; margin-bottom: 10px; }
    .tt-popup .popup-artist:last-child { margin-bottom: 0; }
    .tt-popup .popup-photo { width: 80px; height: 80px; border-radius: 6px; object-fit: cover; flex-shrink: 0; margin-top: 2px; cursor: pointer; }
    .tt-popup .popup-photo-placeholder { width: 80px; height: 80px; border-radius: 6px; background: var(--color-surface-hover); flex-shrink: 0; margin-top: 2px; }
    .tt-popup .popup-name { font-weight: 700; font-size: var(--font-base); cursor: pointer; }
    .tt-popup .links { column-gap: var(--space-xl); row-gap: 0; margin-top: var(--space-sm); }
    .tt-popup .links a { gap: var(--space-xs); }

    /* Tablet (768px) */
    @media (max-width: 768px) {
      html, body { overscroll-behavior: none; }
      .floor-header > span:first-child { font-size: var(--font-xs); padding: 6px 2px; }
      .tt-block { font-size: var(--font-xs); padding: 6px 7px; margin: 2px; gap: 5px; }
    }

    /* Mobile timetable (480px) */
    @media (max-width: 480px) {
      h1 { margin-bottom: 0; }

      .cmd-bar { margin-left: -12px; margin-right: -12px; }



      .tt-photo, .tt-photo-placeholder { width: 30px; height: 30px; border-radius: var(--radius-sm); }

      .floor-header-bar { display: none !important; }
      .timetable { display: none !important; }
      .tt-table-wrap { display: block !important; min-height: 300px; }

      /* Trimmed layout, CSS only: the page is a fixed flex column of
         100dvh, and dvh tracks Safari's collapsing toolbar, so the page
         always ends exactly at the toolbar's top and nothing ever paints
         behind it. Document scrolling is impossible (overflow: hidden),
         so the chrome physically cannot move and nothing can unpin. The
         only scrolling surface is the per-panel .tt-v-scroll (both
         axes); the floor row and time column stick against it, which is
         the device-proven original design. The 12px side inset comes
         from the body's own padding: the scroller's box is inset by it,
         and an overflow box clips its content at its edge, so the inset
         persists while panning with no gutter overlays. */
      /* overflow: clip on the ROOT element (not just body): clip on the
         root makes the viewport non-scrollable even programmatically,
         while body's clip propagated to the viewport still allows
         script scrolling (a stray 4px of overflow would let the page
         shift and misalign the trimmed layout). */
      .view-timetable { overflow: hidden; overflow: clip; }
      .view-timetable body { height: 100vh; height: 100dvh; overflow: hidden; overflow: clip; display: flex; flex-direction: column; }
      .view-timetable main { flex: 1 1 auto; min-height: 0; display: flex; flex-direction: column; }
      .view-timetable #timetable-view { flex: 1 1 auto; min-height: 0; display: flex; flex-direction: column; }
      .view-timetable .timetable-panel.active { flex: 1 1 auto; min-height: 0; display: flex; flex-direction: column; }
      .view-timetable .tt-table-wrap { flex: 1 1 auto; min-height: 0; }
      /* Chrome rows must never be squeezed: without flex: none they are
         shrinkable flex items, and the panel's huge intrinsic content
         height makes the shrink distribution compress them (the title
         rendered 25px tall against 50px of content). Only the panel and
         the containers above it may flex. */
      .view-timetable body > :not(main), .view-timetable main > :not(#timetable-view), .view-timetable #timetable-view > :not(.timetable-panel), .view-timetable .timetable-panel.active > :not(.tt-table-wrap) { flex: none; }
      /* Document-sticky chrome is meaningless in a non-scrolling page and
         actively harmful: body { overflow: hidden } makes body the
         scrollport, and the sticky top offsets (--sticky-top-*) push the
         tabs DOWN once the collapsing title raises their flow position
         above the threshold, opening a gap that overlaps the panel. */
      .view-timetable .cmd-bar, .view-timetable #page-title, .view-timetable .filter-bar { position: static; }
      /* The tabs' scroll-fade is for the sticky list-view bar; with the
         filter-bar static its absolutely positioned fade escapes body's
         clip (containing block = the root) and adds 4px of document
         overflow, enough for a programmatic scroll to shift the page. */
      .view-timetable .filter-bar::after { display: none; }
      .tt-v-scroll { height: 100%; overflow: auto; scrollbar-width: none; -ms-overflow-style: none; overscroll-behavior: none; }
      .tt-v-scroll::-webkit-scrollbar { display: none; }

      /* Title compaction, CSS only, mirroring the list view: there the
         headings never disappear, they scroll up until they pin under
         the bar (only the whitespace above them compacts). Here a named
         scroll timeline on the active panel's scroller slides the title
         snug against the bar by compacting its top margin over the same
         number of scrolled pixels (1:1, so it feels like real page
         scroll), then it stays pinned, full size, rule and all.
         timeline-scope on body makes the scroller's timeline reachable
         from the title, which sits outside the scroller; only the ACTIVE
         panel declares the timeline, so the name never has conflicting
         owners. Safari without scroll-driven animations keeps a static
         title. */
      @supports (animation-timeline: scroll()) {
        .view-timetable body { timeline-scope: --tt-vscroll; }
        .view-timetable .timetable-panel.active .tt-v-scroll { scroll-timeline: --tt-vscroll block; }
        .view-timetable #page-title { animation: tt-title-compact linear both; animation-timeline: --tt-vscroll; animation-range: 0px 16px; }
      }
      @keyframes tt-title-compact {
        to { margin-top: 0; }
      }

      .tt-table { border-collapse: separate; border-spacing: 0; table-layout: fixed; width: calc(40px + var(--num-floors) * 40vw); }
      .tt-table thead th { position: sticky; top: 0; z-index: 2; background: var(--color-bg); padding: var(--space-xs) 2px; text-align: center; vertical-align: top; }
      .tt-table thead th:first-child { left: 0; z-index: 3; background: var(--color-bg); width: 40px; min-width: 40px; }
      .tt-floor-th > span:first-child { display: block; padding: 6px 10px; border-radius: var(--radius-pill); font-size: var(--font-xs); font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin: 0 3px; }
      .tt-floor-th .floor-curator { display: block; font-size: var(--font-xs); padding: 1px 0 2px; margin: 0; }
      /* Floor colors for mobile table — generated dynamically */
      .tt-table tbody td.tt-time-td { position: sticky; left: 0; z-index: 1; background: var(--color-bg); font-size: var(--font-xs); color: var(--color-muted-icon); text-align: right; padding: 0 6px 0 0; vertical-align: top; width: 40px; min-width: 40px; line-height: var(--row-h); overflow: hidden; }
      .tt-table tbody td.tt-line-hour, .tt-table tbody td.tt-line-half { vertical-align: middle; }
      .tt-table tbody td { vertical-align: top; padding: 0; }
      .tt-table tbody td:not(.tt-time-td) { width: 40vw; min-width: 40vw; position: relative; }
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

      .tt-table .tt-block { position: absolute; top: 1.5px; left: 1px; right: 1px; bottom: 2.5px; }

      .filter-bar { padding: 6px 0; margin: 0 0 var(--space-xs); min-height: calc(1.5 * var(--font-lg) + 13px); }
      .filter-bar::after { height: var(--space-xs); }

      .tt-popup { width: calc(100vw - var(--space-xl)); max-width: none; left: var(--space-md) !important; }
      .tt-popup .popup-photo, .tt-popup .popup-photo-placeholder { width: 64px; height: 64px; }
    }
    """)
    _fc = stage_colors or {}
    if _fc:
        color_css: list[str] = []
        color_css.append("    /* Floor colors (from DB) */")
        for fid, rgb in _fc.items():
            color_css.append(
                f"    .floor-{esc(fid)} {{ background: rgba({rgb}, 0.88); }}"
            )
            color_css.append(
                f"    .floor-header.floor-{esc(fid)} > span:first-child {{ background: rgb({rgb}); }}"
            )
            color_css.append(
                f"    .tt-floor-th.floor-{esc(fid)} > span:first-child {{ background: rgb({rgb}); }}"
            )
        parts.append("\n".join(color_css))
    parts.append("  </style>")
    parts.append("""  <script>
  (function(){
    var _navigating=false;
    function plog(step,detail){
      try{fetch('/chat/api/swlog',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({src:'lineup',step:step,detail:detail||null})}).catch(function(){});}catch(e){}
    }
    function nav(u){
      if(_navigating||!u||u.indexOf('line-up')>=0||u===location.pathname){plog('nav-blocked',u);return;}
      _navigating=true;
      setTimeout(function(){_navigating=false;},3000);
      plog('nav-go',u);
      window.location.href=u;
    }
    function chkCache(){
      if(_navigating||!('caches'in window))return;
      caches.open('stc-push').then(function(c){
        c.match('/_push_navigate').then(function(r){
          if(r)r.text().then(function(u){plog('cache-hit',u);c.delete('/_push_navigate').then(function(){nav(u);});});
        });
      }).catch(function(){});
    }
    [0,300,800,1500,3000,5000].forEach(function(d){setTimeout(chkCache,d);});
    if('serviceWorker'in navigator){
      navigator.serviceWorker.register('/sw.js').catch(function(){});
      navigator.serviceWorker.addEventListener('message',function(e){
        if(e.data&&e.data.type==='navigate'){plog('postmessage-received',e.data.url);nav(e.data.url);}
      });
    }
  })();
  </script>""")
    parts.append("</head>")
    parts.append("<body>")
    heart_path = "M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"
    cal_inner = '<rect x="3" y="4" width="18" height="18" rx="2" fill="none" stroke="currentColor" stroke-width="2"/><line x1="16" y1="2" x2="16" y2="6" stroke="currentColor" stroke-width="2"/><line x1="8" y1="2" x2="8" y2="6" stroke="currentColor" stroke-width="2"/><line x1="3" y1="10" x2="21" y2="10" stroke="currentColor" stroke-width="2"/>'
    parts.append('  <svg aria-hidden="true" style="display:none">')
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
    parts.append('  <nav class="cmd-bar" id="cmd-bar" aria-label="Main navigation">')
    parts.append(
        '    <a href="/chat" class="nav-icon chat-nav-icon" aria-label="Chat"></a>'
    )
    parts.append('    <span class="view-label" id="view-label">Line-up</span>')
    parts.append('    <div class="cmd-group">')
    if has_timetable:
        parts.append(
            '      <button type="button" onmousedown="this.blur()" onclick="switchView(\'list\', this)" id="btn-list" class="active view-btn">Line-up</button>'
        )
        parts.append(
            '      <button type="button" onmousedown="this.blur()" onclick="switchView(\'timetable\', this)" id="btn-timetable" class="view-btn">Timetable</button>'
        )
    parts.append(
        '      <button type="button" onmousedown="this.blur()" onclick="window.open(\'/chat\',\'_self\')">Chat</button>'
    )
    parts.append(
        '      <button type="button" onmousedown="this.blur()" onclick="dbg(\'[NAV] transport (cmd-bar)\'); window.open(\'/transport\',\'_self\')">Transport</button>'
    )
    if has_timetable:
        parts.append('      <span class="cmd-sep"></span>')
    parts.append(
        '      <button type="button" onmousedown="this.blur()" onclick="toggleFilter(this)" id="btn-filter">Show My Picks</button>'
    )
    if has_timetable:
        parts.append(
            '      <button type="button" onmousedown="this.blur()" onclick="toggleScheduleFilter(this)" id="btn-schedule" style="display:none">Show My Schedule</button>'
        )
    parts.append("    </div>")
    parts.append('    <div class="cmd-group cmd-group-right">')
    parts.append(
        '      <button type="button" onmousedown="this.blur()" onclick="openShareModal()">Share</button>'
    )
    parts.append(
        '      <button type="button" onmousedown="this.blur()" onclick="openSyncModal()">Sync</button>'
    )
    parts.append(
        '      <button type="button" onmousedown="this.blur()" onclick="toggleNotifications()" id="btn-bell" '
        'aria-label="Notifications">'
        '<svg width="14" height="14" style="position:relative;top:1px" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg></button>'
    )
    parts.append("    </div>")
    parts.append(
        '    <button type="button" class="hamburger" onclick="toggleMenu()" aria-label="Menu"></button>'
    )
    parts.append("  </nav>")

    # Hamburger dropdown menu (fixed-positioned, outside nav)
    parts.append('  <div class="cmd-dropdown" id="cmd-dropdown">')
    if has_timetable:
        parts.append(
            '    <button type="button" onclick="switchView(\'list\', document.getElementById(\'btn-list\')); closeMenu()" id="dd-list">Line-up</button>'
        )
        parts.append(
            '    <button type="button" onclick="switchView(\'timetable\', document.getElementById(\'btn-timetable\')); closeMenu()" id="dd-timetable">Timetable</button>'
        )
    parts.append('    <div id="dd-view-options">')
    parts.append(
        '      <button type="button" class="dd-option dd-toggle" role="switch" aria-checked="false" onclick="toggleFilter(document.getElementById(\'btn-filter\'))" id="dd-filter">Show My Picks<span class="dd-switch" aria-hidden="true"></span></button>'
    )
    if has_timetable:
        parts.append(
            '      <button type="button" class="dd-option dd-toggle" role="switch" aria-checked="false" onclick="toggleScheduleFilter(document.getElementById(\'btn-schedule\'))" id="dd-schedule" style="display:none">Show My Schedule<span class="dd-switch" aria-hidden="true"></span></button>'
        )
    parts.append("    </div>")
    parts.append(
        '    <button type="button" onclick="dbg(\'[NAV] chat (menu)\'); window.open(\'/chat\',\'_self\')">Chat</button>'
    )
    parts.append(
        '    <button type="button" onclick="dbg(\'[NAV] transport (menu)\'); window.open(\'/transport\',\'_self\')">Transport</button>'
    )
    parts.append('    <div class="dd-divider"></div>')
    parts.append(
        '    <button type="button" class="dd-option" onclick="openShareModal(); closeMenu()">Share</button>'
    )
    parts.append(
        '    <button type="button" class="dd-option" onclick="openSyncModal(); closeMenu()">Sync</button>'
    )
    parts.append(
        '    <button type="button" class="dd-option dd-toggle" role="switch" aria-checked="false" onclick="toggleNotifications()" id="dd-bell">Notifications<span class="dd-switch" aria-hidden="true"></span></button>'
    )
    parts.append("  </div>")
    parts.append(
        '  <div class="menu-overlay" id="menu-overlay" onclick="closeMenu()"></div>'
    )

    parts.append("  <main>")
    parts.append('  <h1 id="page-title">Line-up</h1>')

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
        '      <input type="text" readonly class="modal-link" id="share-link" aria-label="Share link">'
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
        f'<input class="pin-real" id="pin-input" type="text" inputmode="numeric" maxlength="6" autocomplete="off" aria-label="Enter sync PIN">'
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

    # Bio overlay
    parts.append(
        '  <div class="modal-overlay" id="m-bio" role="dialog" aria-modal="true" aria-labelledby="bio-name">'
    )
    parts.append('    <div class="modal-box bio-box">')
    parts.append('      <div class="bio-scroll">')
    parts.append('        <div class="bio-header">')
    parts.append('          <div id="bio-photo"></div>')
    parts.append('          <div class="bio-name" id="bio-name"></div>')
    parts.append("        </div>")
    parts.append('        <div class="bio-text" id="bio-text"></div>')
    parts.append('        <div class="bio-videos" id="bio-videos"></div>')
    parts.append("      </div>")
    parts.append("    </div>")
    parts.append("  </div>")

    def _safe_href(href: str) -> str:
        h = (href or "").strip()
        return h if h.lower().startswith(("http://", "https://", "mailto:")) else "#"

    def _link(href: str, svg: str, label: str = "") -> str:
        txt = f"{svg} {esc(label)}" if label else svg
        return f'<a href="{esc(_safe_href(href))}" target="_blank" rel="noopener noreferrer" title="{esc(label)}">{txt}</a>'

    def render_artist_card(
        a: dict, cur_date: str, cur_period: str, loc_id: str | None = None
    ) -> None:
        name = a.get("name") or ""
        photo_file = a.get("photo_file")
        links = a.get("links", [])
        sched_main, sched_also = _format_artist_schedule(
            a.get("all_slots", []), cur_date, cur_period
        )

        oid = a.get("id", "")
        card_key = f"{oid}:{cur_date}:{cur_period}:{loc_id or ''}"
        artist_id = str(uuid.uuid5(uuid.NAMESPACE_URL, card_key))
        parts.append(
            f'      <li class="artist-item" data-artist-id="{esc(artist_id)}" data-oid="{esc(oid)}">'
        )
        if photo_file:
            parts.append(
                f'        <img class="artist-photo" src="photos/{esc(photo_file)}" alt="{esc(name)}" width="120" height="120" loading="lazy" tabindex="0" role="button">'
            )
        else:
            parts.append('        <div class="photo-placeholder"></div>')
        parts.append('        <div class="artist-info">')
        parts.append(
            f'        <span class="artist-name" tabindex="0" role="button">{esc(name)}</span>'
        )
        if sched_main:
            parts.append(
                f'        <span class="artist-schedule">{esc(sched_main)}</span>'
            )
        parts.append('        <div class="links">')
        for lnk in links:
            icon_id = PLATFORM_ICONS.get(lnk["platform"])
            if icon_id:
                fc = format_followers(lnk.get("follower_count")) or ""
                parts.append(
                    f"          {_link(lnk['url'], _use_svg(icon_id, width='18', height='18'), fc)}"
                )
        if not links:
            parts.append('          <span class="missing">No links</span>')
        parts.append("        </div>")
        if sched_also:
            parts.append(f'        <span class="artist-also">{esc(sched_also)}</span>')
        parts.append("        </div>")
        parts.append(
            '        <button type="button" class="heart-btn" aria-label="Add to favorites" aria-pressed="false"><svg viewBox="0 0 24 24"><use href="#i-heart"/></svg></button>'
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

    # Bio lookup (deduped by artist id)
    artist_videos = videos or {}

    bio_lookup: dict[str, dict] = {}
    for artists_list in assignments.values():
        for a in artists_list:
            oid = a.get("id", "")
            if oid and oid not in bio_lookup:
                raw_bio = a.get("bio") or ""
                entry: dict = {
                    "name": a.get("name", ""),
                    "photo": f"photos/{a['photo_file']}" if a.get("photo_file") else "",
                    "bio": _render_markdown(_strip_booking(raw_bio)) if raw_bio else "",
                }
                if oid in artist_videos:
                    entry["videos"] = artist_videos[oid]
                bio_lookup[oid] = entry
    if output_dir:
        bios_path = Path(output_dir) / "bios.json"
        bios_path.write_text(
            _json.dumps(bio_lookup, separators=(",", ":")), encoding="utf-8"
        )

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
        style_idx = next(i for i, p in enumerate(parts) if p.strip() == "</style>")
        parts.insert(style_idx, f"    .tt-table {{ --row-h: {row_h}px; }}")
        parts.append('  <div id="timetable-view" style="display:none">')

        # Filter bar (day/period tabs)
        parts.append('  <div class="filter-bar">')
        parts.append('    <div class="day-tabs" id="day-tabs">')
        for i, date_str in enumerate(dates_seen):
            active = " active" if i == 0 else ""
            parts.append(
                f'      <button type="button" class="day-tab{active}" onclick="switchDay(\'{esc(date_str)}\', this)">'
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
                curator_text = (stage_curators or {}).get(curator_key, "")
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

                    artist_id = slot_uuid(
                        [a.get("id", "") for a in group],
                        tt_date_str,
                        period,
                        fid,
                        st,
                        et,
                        _slot_group_times(slots)[tuple(a.get("id", "") for a in group)],
                    )

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
                        f'<button type="button" class="tt-cal" '
                        f'aria-label="Add to schedule" aria-pressed="false">{cal_svg}</button>'
                    )
                    parts.append(
                        f'      <div class="tt-block floor-{esc(fid)}" tabindex="0" role="button" style="grid-column: {col}; grid-row: {row_start} / {row_end};" {data_attrs}>'
                        f'<div class="tt-text">'
                        f'<div class="tt-time-row"><span class="tt-time">{esc(s_display)}–{esc(e_display)}</span>{cal_btn}</div>'
                    )
                    for a in group:
                        photo_file = a.get("photo_file") or ""
                        name = a.get("name", "")
                        loc_for_id = fid if is_night else ""
                        a_card_key = (
                            f"{a.get('id', '')}:{tt_date_str}:{period}:{loc_for_id}"
                        )
                        a_artist_id = str(uuid.uuid5(uuid.NAMESPACE_URL, a_card_key))
                        if photo_file:
                            photo_el = f'<img class="tt-photo" src="{esc(photos_prefix + photo_file)}" alt="{esc(name)}" loading="lazy">'
                        else:
                            photo_el = '<div class="tt-photo-placeholder"></div>'
                        heart_btn = (
                            f'<button type="button" class="tt-photo-heart" '
                            f'aria-label="Add to favorites" aria-pressed="false">{heart_svg}</button>'
                        )
                        parts.append(
                            f'<div class="tt-artist-row" data-artist-id="{esc(a_artist_id)}">'
                            f'<div class="tt-photo-wrap">{photo_el}{heart_btn}</div>'
                            f'<span class="tt-name">{esc(name)}</span></div>'
                        )
                    parts.append(
                        '<button type="button" class="tt-ics">Add to calendar</button>'
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
                curator_text = (stage_curators or {}).get(curator_key, "")
                if curator_text:
                    parts.append(
                        f'<th class="tt-floor-th floor-{esc(fid)}" scope="col"><span>{esc(loc_name)}</span>'
                        f'<span class="floor-curator">{esc(curator_text)}</span></th>'
                    )
                else:
                    parts.append(
                        f'<th class="tt-floor-th floor-{esc(fid)}" scope="col"><span>{esc(loc_name)}</span></th>'
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

                    artist_id = slot_uuid(
                        [a.get("id", "") for a in group],
                        tt_date_str,
                        period,
                        fid,
                        st,
                        et,
                        _slot_group_times(slots_table)[
                            tuple(a.get("id", "") for a in group)
                        ],
                    )
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
                        f'<button type="button" class="tt-cal" '
                        f'aria-label="Add to schedule" aria-pressed="false">{cal_svg}</button>'
                    )

                    block_parts: list[str] = []
                    block_parts.append(
                        f'<div class="tt-block floor-{esc(fid)}" tabindex="0" role="button" {data_attrs}>'
                        f'<div class="tt-text">'
                        f'<div class="tt-time-row"><span class="tt-time">{esc(s_display)}–{esc(e_display)}</span>{cal_btn}</div>'
                    )
                    for a in group:
                        photo_file = a.get("photo_file") or ""
                        name = a.get("name", "")
                        loc_for_id = fid if is_night else ""
                        a_card_key = (
                            f"{a.get('id', '')}:{tt_date_str}:{period}:{loc_for_id}"
                        )
                        a_artist_id = str(uuid.uuid5(uuid.NAMESPACE_URL, a_card_key))
                        if photo_file:
                            photo_el = f'<img class="tt-photo" src="{esc(photos_prefix + photo_file)}" alt="{esc(name)}" loading="lazy">'
                        else:
                            photo_el = '<div class="tt-photo-placeholder"></div>'
                        heart_btn = (
                            f'<button type="button" class="tt-photo-heart" '
                            f'aria-label="Add to favorites" aria-pressed="false">{heart_svg}</button>'
                        )
                        block_parts.append(
                            f'<div class="tt-artist-row" data-artist-id="{esc(a_artist_id)}">'
                            f'<div class="tt-photo-wrap">{photo_el}{heart_btn}</div>'
                            f'<span class="tt-name">{esc(name)}</span></div>'
                        )
                    block_parts.append(
                        '<button type="button" class="tt-ics">Add to calendar</button>'
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
        parts.append(
            '  <div class="tt-popup" id="tt-popup" role="dialog" aria-label="Artist details">'
        )
        parts.append('    <div class="popup-meta" id="popup-meta"></div>')
        parts.append('    <div id="popup-artists"></div>')
        parts.append("  </div>")

        parts.append(
            f"  <script>var TT_ARTISTS={json_for_script(artist_lookup)};</script>"
        )
        parts.append("  </div>")  # end #timetable-view

    parts.append('  <script src="/shared.js"></script>')
    qr_js = (ICONS_DIR.parent / "qrcode.min.js").read_text(encoding="utf-8")
    parts.append(f"  <script>{qr_js}</script>")
    parts.append("  <script>")
    parts.append("    setDbgTag('lineup');")
    parts.append("    document.querySelector('.hamburger').innerHTML = ICON_HAMBURGER;")
    parts.append("    document.querySelector('.chat-nav-icon').innerHTML = ICON_CHAT;")
    parts.append(f"    var siteShort = {json_for_script(site_short)};")
    if has_timetable:
        parts.append("""
    // Immediate view restore before anything renders
    (function() {
      var pathView = location.pathname === '/timetable' ? 'timetable' : location.pathname === '/line-up' ? 'list' : null;
      // Captured before the view router strips the query string below
      window.__deepAction = new URLSearchParams(location.search).get('action');
      var vp = pathView || new URLSearchParams(location.search).get('view');
      var v = vp || storageGet('stc_view');
      history.replaceState(null, '', v === 'timetable' ? '/timetable' : '/line-up');
      document.title = (v === 'timetable' ? 'Timetable' : 'Line-up') + ' · ' + siteShort;
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
        if (ddl) ddl.classList.remove('active');
        if (ddt) ddt.classList.add('active');
        var ddo = document.getElementById('dd-view-options');
        if (ddo && ddt) ddt.after(ddo);
      }
    })();
    """)

    # Emit timetable section data for JS (when timetable is present)
    if has_timetable:
        sections_json = json_for_script(
            [
                {"date": td["date"], "period": td["period"], "key": td["key"]}
                for td in timetable_data
            ]
        )
        parts.append(f"    const TT_SECTIONS = {sections_json};")
        parts.append(f"    const TT_DATES = {json_for_script(dates_seen)};")
        parts.append("    const HAS_TIMETABLE = true;")
    else:
        parts.append("    const HAS_TIMETABLE = false;")

    parts.append("""
    // Sticky gradient observer. Sentinel offsets encode the element's sticky
    // top, which changes across the mobile/desktop breakpoint, so they are
    // re-derived on every resize (see the shared resize handler below).
    const _fadePairs = [];
    document.querySelectorAll('.fade-after').forEach(el => {
      const s = document.createElement('div');
      s.style.cssText = 'height:0;width:0;pointer-events:none;visibility:hidden;position:relative;';
      el.parentNode.insertBefore(s, el);
      _fadePairs.push([el, s]);
      new IntersectionObserver(([e]) => {
        el.classList.toggle('stuck', e.intersectionRatio === 0);
      }, {threshold: 0}).observe(s);
    });
    function placeFadeSentinels() {
      const tops = _fadePairs.map(([el]) => parseFloat(getComputedStyle(el).top) || 0);
      _fadePairs.forEach(([, s], i) => {
        s.style.top = '-' + tops[i] + 'px';
      });
    }
    placeFadeSentinels();

    // Deep-linked actions from the identical command bars on other pages.
    // The action param is captured before the view router strips the query.
    (function () {
      const run = {
        picks: () => toggleFilter(document.getElementById('btn-filter')),
        schedule: () => { const b = document.getElementById('btn-schedule'); if (b) toggleScheduleFilter(b); },
        share: () => openShareModal(),
        sync: () => openSyncModal(),
        notifications: () => toggleNotifications(),
      }[window.__deepAction];
      if (!run) return;
      // Wait for init to finish (it reveals the body and loads session state
      // like shareToken) so the action sees the same state as a manual click.
      // The giveup cap must exceed the 5s init reveal timeout, or the action
      // could fire against a still-hidden body with unloaded session state.
      let tries = 0;
      const t = setInterval(() => {
        if (document.body.style.opacity === '1' || ++tries > 60) {
          clearInterval(t);
          setTimeout(run, 100);
        }
      }, 100);
    })();

    // Hearts
    const API = '/api';
    // Migrate old localStorage keys
    if (storageGet('stc_edit_code') && !storageGet('stc_session_id')) {
      storageSet('stc_session_id', storageGet('stc_edit_code'));
      storageRemove('stc_edit_code');
    }
    if (storageGet('stc_share_code') && !storageGet('stc_share_token')) {
      storageSet('stc_share_token', storageGet('stc_share_code'));
      storageRemove('stc_share_code');
    }
    let sessionId = storageGet('stc_session_id');
    let shareToken = storageGet('stc_share_token');
    let localPicks; try { localPicks = new Set(JSON.parse(storageGet('stc_picks') || '[]')); } catch { localPicks = new Set(); storageRemove('stc_picks'); }
    let localSchedule; try { localSchedule = new Set(JSON.parse(storageGet('stc_schedule') || '[]')); } catch { localSchedule = new Set(); storageRemove('stc_schedule'); }
    // syncedPicks/syncedSchedule mirror the last known server-confirmed state.
    // reconcile() uses them to tell "server deleted this since we last synced"
    // apart from "never told the server about this yet" -- without that, a
    // stale local id would be re-POSTed after another device deleted it there.
    let syncedPicks; try { syncedPicks = new Set(JSON.parse(storageGet('stc_synced_picks') || '[]')); } catch { syncedPicks = new Set(); storageRemove('stc_synced_picks'); }
    let syncedSchedule; try { syncedSchedule = new Set(JSON.parse(storageGet('stc_synced_schedule') || '[]')); } catch { syncedSchedule = new Set(); storageRemove('stc_synced_schedule'); }
    let readOnly = false;
    let filterActive = false;
    let scheduleFilterActive = false;
    // Path-aware, matching the head restore: an explicit /line-up or
    // /timetable always wins over the saved preference
    let currentView = location.pathname === '/timetable' ? 'timetable' : location.pathname === '/line-up' ? 'list' : (storageGet('stc_view') || 'list');

    function saveLocal() {
      storageSet('stc_picks', JSON.stringify([...localPicks]));
      storageSet('stc_schedule', JSON.stringify([...localSchedule]));
      storageSet('stc_synced_picks', JSON.stringify([...syncedPicks]));
      storageSet('stc_synced_schedule', JSON.stringify([...syncedSchedule]));
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
      // Driven by JS + a plain class rather than a nested :has() selector
      // (that :has() re-evaluated on every heart toggle across the whole
      // page, even with the filter off) -- location-heading is always
      // immediately followed by its ul.artist-list, so a direct sibling
      // check is all that's needed.
      document.querySelectorAll('h4.location-heading').forEach(h4 => {
        if (!filterActive) { h4.classList.remove('no-hearted'); return; }
        const ul = h4.nextElementSibling;
        const hasHearted = !!(ul && ul.matches('ul.artist-list') && ul.querySelector('.artist-item.hearted'));
        h4.classList.toggle('no-hearted', !hasHearted);
      });
    }

    // Delegated event listeners for list view
    var listView = document.getElementById('list-view') || document.querySelector('main');
    listView.addEventListener('click', function(e) {
      var heartBtn = e.target.closest('.heart-btn');
      if (heartBtn) { toggleHeart(heartBtn); return; }
      var bio = e.target.closest('.artist-photo, .artist-name');
      if (bio) { openBio(bio); return; }
    });
    listView.addEventListener('keydown', function(e) {
      if (e.key !== 'Enter') return;
      var bio = e.target.closest('.artist-photo, .artist-name');
      if (bio) openBio(bio);
    });

    // Delegated event listeners for timetable blocks
    document.addEventListener('click', function(e) {
      var cal = e.target.closest('.tt-cal');
      if (cal) { e.stopPropagation(); toggleSchedule(cal); return; }
      var heart = e.target.closest('.tt-photo-heart');
      if (heart) { e.stopPropagation(); toggleHeart(heart); return; }
      var ics = e.target.closest('.tt-ics');
      if (ics) { e.stopPropagation(); downloadICS(ics.closest('[data-ics-start]')); return; }
    });

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
          storageSet('stc_session_id', sessionId);
          storageSet('stc_share_token', shareToken);
          // syncedPicks/syncedSchedule describe the PREVIOUS session; a fresh
          // session has no other-device deletions, so carrying them over could
          // make reconcile() drop a local pick whose re-push below failed.
          syncedPicks = new Set();
          syncedSchedule = new Set();
          saveLocal();
          connectWS(sessionId);
          for (const id of localPicks) {
            fetch(API + '/session/' + sessionId + '/pick/' + id, {method: 'POST'}).catch(() => {});
          }
          for (const id of localSchedule) {
            fetch(API + '/session/' + sessionId + '/schedule/' + id, {method: 'POST'}).catch(() => {});
          }
        } catch (e) { dbg('ensureSession failed', e.message); }
        finally { _sessionPromise = null; }
      })();
      return _sessionPromise;
    }

    function track(event, data) { if (typeof umami !== 'undefined') umami.track(event, data); }

    // Per-id promise chains for toggleHeart/toggleSchedule: a rapid double-tap
    // on the same artist fires two overlapping network requests (POST then
    // DELETE, or vice versa) with no ordering guarantee between them, which
    // can leave the server out of sync with the (instant, optimistic) UI
    // until the next reconcile(). Chaining by id keeps each id's requests
    // strictly sequential without delaying the optimistic UI update at all.
    const _pickSyncChain = new Map();
    const _scheduleSyncChain = new Map();

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
      if (filterActive) updateGroupVisibility();

      const run = async () => {
        await ensureSession();
        if (!sessionId) return;

        try {
          const method = adding ? 'POST' : 'DELETE';
          const res = await fetch(API + '/session/' + sessionId + '/pick/' + id, {method});
          if (res.status === 404) {
            sessionId = null; shareToken = null;
            storageRemove('stc_session_id');
            storageRemove('stc_share_token');
            await ensureSession();
            return;
          }
          if (!res.ok && res.status !== 204) {
            if (adding) localPicks.delete(id); else localPicks.add(id);
            btn.classList.toggle('active', !adding);
            btn.setAttribute('aria-pressed', !adding);
            el.classList.toggle('hearted', !adding);
            saveLocal();
            if (filterActive) updateGroupVisibility();
          } else {
            if (adding) syncedPicks.add(id); else syncedPicks.delete(id);
            saveLocal();
          }
        } catch (e) { dbg('toggleHeart sync failed', e.message); }
      };
      const prev = _pickSyncChain.get(id) || Promise.resolve();
      const chained = prev.then(run);
      _pickSyncChain.set(id, chained);
      await chained;
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

      const run = async () => {
        await ensureSession();
        if (!sessionId) return;

        try {
          const method = adding ? 'POST' : 'DELETE';
          const res = await fetch(API + '/session/' + sessionId + '/schedule/' + id, {method});
          if (res.status === 404) {
            sessionId = null; shareToken = null;
            storageRemove('stc_session_id');
            storageRemove('stc_share_token');
            await ensureSession();
            return;
          }
          if (!res.ok && res.status !== 204) {
            if (adding) localSchedule.delete(id); else localSchedule.add(id);
            btn.classList.toggle('active', !adding);
            btn.setAttribute('aria-pressed', !adding);
            el.classList.toggle('scheduled', !adding);
            saveLocal();
          } else {
            if (adding) syncedSchedule.add(id); else syncedSchedule.delete(id);
            saveLocal();
          }
        } catch (e) { dbg('toggleSchedule sync failed', e.message); }
      };
      const prev = _scheduleSyncChain.get(id) || Promise.resolve();
      const chained = prev.then(run);
      _scheduleSyncChain.set(id, chained);
      await chained;
    }

    async function loadFromServer(code) {
      try {
        const res = await fetch(API + '/session/' + code);
        if (!res.ok) return;
        const data = await res.json();
        localPicks = new Set(data.picks);
        syncedPicks = new Set(data.picks);
        if (data.schedule) localSchedule = new Set(data.schedule);
        if (data.schedule) syncedSchedule = new Set(data.schedule);
        readOnly = data.readonly;
        if (!readOnly) {
          sessionId = data.session_id || null;
          shareToken = data.share_token || null;
          if (sessionId) storageSet('stc_session_id', sessionId); else storageRemove('stc_session_id');
          if (shareToken) storageSet('stc_share_token', shareToken); else storageRemove('stc_share_token');
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
      } catch (e) { dbg('loadFromServer failed', e.message); }
    }

    async function reconcile() {
      if (!sessionId || readOnly) return;
      try {
        const res = await fetch(API + '/session/' + sessionId);
        if (res.status === 404) {
          sessionId = null; shareToken = null;
          storageRemove('stc_session_id');
          storageRemove('stc_share_token');
          await ensureSession();
          return;
        }
        if (!res.ok) return;
        const data = await res.json();
        const serverPicks = new Set(data.picks);
        const serverSchedule = new Set(data.schedule || []);
        const syncs = [];
        // A local id absent from the server is either a genuinely new offline
        // pick (never synced -- push it) or one this device previously synced
        // that another device has since deleted server-side (missed WS
        // broadcast -- drop it locally instead of resurrecting it with a
        // re-POST). syncedPicks/syncedSchedule (the last known server-
        // confirmed state) is what tells the two cases apart.
        const pushedPicks = [];
        for (const id of localPicks) {
          if (serverPicks.has(id)) continue;
          if (syncedPicks.has(id)) { localPicks.delete(id); continue; }
          pushedPicks.push(id);
          syncs.push(fetch(API + '/session/' + sessionId + '/pick/' + id, {method: 'POST'}).catch(() => {}));
        }
        const pushedSchedule = [];
        for (const id of localSchedule) {
          if (serverSchedule.has(id)) continue;
          if (syncedSchedule.has(id)) { localSchedule.delete(id); continue; }
          pushedSchedule.push(id);
          syncs.push(fetch(API + '/session/' + sessionId + '/schedule/' + id, {method: 'POST'}).catch(() => {}));
        }
        await Promise.all(syncs);
        for (const id of serverPicks) localPicks.add(id);
        for (const id of serverSchedule) localSchedule.add(id);
        syncedPicks = new Set([...serverPicks, ...pushedPicks]);
        syncedSchedule = new Set([...serverSchedule, ...pushedSchedule]);
        saveLocal();
        applyHearts();
      } catch (e) { dbg('reconcile failed', e.message); }
    }

    // WebSocket real-time sync
    let _ws = null;
    let _wsDelay = 2000;
    function connectWS(code) {
      if (_ws) { try { _ws.close(); } catch (e) { /* already closed */ } }
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
            syncedPicks = new Set(data.picks);
            if (data.schedule) localSchedule = new Set(data.schedule);
            if (data.schedule) syncedSchedule = new Set(data.schedule);
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
        } catch (e) { dbg('ws message handler error', e); }
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
    var _savedScrollY = 0;
    function openDialog(id) {
      _modalTrigger = document.activeElement;
      _savedScrollY = window.scrollY;
      document.body.style.position = 'fixed';
      document.body.style.top = '-' + _savedScrollY + 'px';
      document.body.style.left = '0';
      document.body.style.right = '0';
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
      document.body.style.position = '';
      document.body.style.top = '';
      document.body.style.left = '';
      document.body.style.right = '';
      window.scrollTo(0, _savedScrollY);
      if (_syncTimer) { clearInterval(_syncTimer); _syncTimer = null; }
      pinField.value = '';
      syncPinDisplay();
      if (_modalTrigger) { _modalTrigger.focus(); _modalTrigger = null; }
    }
    document.querySelectorAll('.modal-overlay').forEach(ov => {
      // Close only when the press STARTED on the backdrop too: a text
      // selection or drag that begins inside the box and is released over
      // the overlay fires the click on the overlay (common ancestor of
      // press and release) and must not close the dialog.
      ov.addEventListener('pointerdown', e => { ov._downOnBackdrop = (e.target === ov); });
      ov.addEventListener('click', e => { if (e.target === ov && ov._downOnBackdrop) closeDialog(ov.id); });
      ov.addEventListener('wheel', e => { if (!e.target.closest('.modal-box')) e.preventDefault(); }, { passive: false });
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
      }).catch((e) => {
        dbg('clipboard copy failed', e && e.message);
        showToast('Copy failed, the link is selected, copy it manually');
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
      } catch (e) { dbg('generateSyncPin failed', e.message); }
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
        syncedPicks = new Set(data.picks);
        if (data.schedule) localSchedule = new Set(data.schedule);
        if (data.schedule) syncedSchedule = new Set(data.schedule);
        readOnly = data.readonly;
        if (!readOnly) {
          sessionId = data.session_id || null;
          shareToken = data.share_token || null;
          if (sessionId) storageSet('stc_session_id', sessionId); else storageRemove('stc_session_id');
          if (shareToken) storageSet('stc_share_token', shareToken); else storageRemove('stc_share_token');
          saveLocal();
        }
        applyHearts();
        if (sessionId) connectWS(sessionId);
      } catch (e) { dbg('exchangeSyncPin failed', e.message); }
    }

    // Bio overlay
    function _formatViews(n) {
      if (n >= 1000000) return (n / 1000000).toFixed(1).replace(/\\.0$/, '') + 'M';
      if (n >= 1000) return (n / 1000).toFixed(1).replace(/\\.0$/, '') + 'K';
      return String(n);
    }
    var _biosCache = null;
    async function _loadBios() {
      if (_biosCache) return _biosCache;
      try {
        const res = await fetch('bios.json');
        _biosCache = await res.json();
      } catch { _biosCache = {}; }
      return _biosCache;
    }
    async function openBioById(oid, fallbackName) {
      const bios = await _loadBios();
      const data = bios[oid] || { name: fallbackName || '', photo: '', bio: '' };
      const photoEl = document.getElementById('bio-photo');
      if (data.photo) {
        photoEl.outerHTML = '<img class="bio-photo" id="bio-photo" src="' + esc(data.photo) + '" alt="' + esc(data.name) + '">';
      } else {
        photoEl.outerHTML = '<div class="bio-photo-placeholder" id="bio-photo"></div>';
      }
      document.getElementById('bio-name').textContent = data.name;
      const bioText = document.getElementById('bio-text');
      if (data.bio) {
        bioText.innerHTML = data.bio;
      } else {
        bioText.innerHTML = '<span class="bio-empty">No biography available</span>';
      }
      const videosEl = document.getElementById('bio-videos');
      if (data.videos && data.videos.length) {
        let html = '<div class="bio-videos-title">Sets</div>';
        data.videos.forEach(function(v) {
          html += '<a class="bio-video" href="' + esc(/^https?:\\/\\//i.test(v.url || '') ? v.url : '#') + '" target="_blank" rel="noopener noreferrer">';
          html += '<img class="bio-video-thumb" src="thumbs/' + esc(v.id) + '.avif" alt="" loading="lazy">';
          html += '<div class="bio-video-info">';
          html += '<div class="bio-video-title">' + esc(v.title) + '</div>';
          var dateStr = '';
          if (v.date) { var ds = String(v.date); dateStr = ' \\u00b7 ' + ds.slice(0,4) + '-' + ds.slice(4,6) + '-' + ds.slice(6); }
          html += '<div class="bio-video-meta">' + _formatViews(v.views) + ' views \\u00b7 ' + esc(v.duration) + ' min' + dateStr + '</div>';
          html += '</div></a>';
        });
        videosEl.innerHTML = html;
      } else {
        videosEl.innerHTML = '';
      }
      openDialog('m-bio');
      document.querySelector('.bio-scroll').scrollTop = 0;
    }
    async function openBio(el) {
      const li = el.closest('.artist-item');
      if (!li) return;
      openBioById(li.dataset.oid, li.querySelector('.artist-name,.tt-name')?.textContent);
    }

    // Hamburger menu
    function toggleMenu() {
      document.getElementById('cmd-dropdown').classList.toggle('open');
      document.getElementById('menu-overlay').classList.toggle('open');
    }
    document.getElementById('cmd-bar').addEventListener('click', function(e) {
      if (!window.matchMedia('(max-width:768px)').matches) return;
      if (!e.target.closest('.hamburger') && !e.target.closest('.nav-icon')) toggleMenu();
    });
    // The dropdown is mobile-only UI: crossing the breakpoint resets it,
    // otherwise its overlay survives into the desktop layout
    window.matchMedia('(max-width:768px)').addEventListener('change', closeMenu);
    function closeMenu() {
      document.getElementById('cmd-dropdown').classList.remove('open');
      document.getElementById('menu-overlay').classList.remove('open');
    }

    // --- Push Notifications ---
    const _isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent);
    const _isStandalone = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
    const _needsSafariSwitch = _isIOS && !!navigator.brave;
    const _supportsPush = 'serviceWorker' in navigator && 'PushManager' in window;



    function updateBellState() {
      const btn = document.getElementById('btn-bell');
      const dd = document.getElementById('dd-bell');
      const on = storageGet('stc_push') === '1';
      if (btn) { btn.style.display = _supportsPush ? '' : 'none'; btn.classList.toggle('active', on); }
      if (dd) { dd.style.display = (_supportsPush || _isIOS) ? '' : 'none'; dd.setAttribute('aria-checked', on ? 'true' : 'false'); }
    }

    async function enableNotifications() {
      if (_needsSafariSwitch) { openDialog('m-ios-switch'); return; }
      if (_isIOS && !_isStandalone) { openDialog('m-ios'); return; }
      if (!_supportsPush) { showToast('This browser does not support notifications.'); return; }
      const perm = await Notification.requestPermission();
      if (perm !== 'granted') {
        // Blocked or dismissed: give feedback instead of failing silently, so
        // the switch never looks inert. requestPermission returns immediately
        // with 'denied' when the site is blocked in browser settings.
        dbg('notifications permission not granted:', perm);
        showToast('Notifications are blocked. Enable them for this site in your browser settings, then try again.');
        return;
      }
      try {
        const vapidRes = await fetch(API + '/push/vapid-key');
        if (!vapidRes.ok) { showToast('Could not enable notifications. Please try again.'); return; }
        const { public_key } = await vapidRes.json();
        const reg = await navigator.serviceWorker.ready;
        const keyBytes = _urlBase64ToUint8Array(public_key);
        var sub = await reg.pushManager.getSubscription();
        if (!sub) {
          sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes });
        }
        await ensureSession();
        if (!sessionId) { showToast('Could not enable notifications. Please try again.'); return; }
        const subRes = await fetch(API + '/session/' + sessionId + '/push/subscribe', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(sub.toJSON()) });
        if (!subRes.ok) { dbg('push subscribe POST failed', subRes.status); showToast('Could not enable notifications. Please try again.'); return; }
        storageSet('stc_push', '1');
        storageSet('stc_push_endpoint', sub.endpoint);
        track('push-enable');
      } catch (e) {
        if (navigator.brave && e.name === 'AbortError') { openDialog('m-brave'); return; }
        dbg('Push subscribe failed', e);
        // Push permission can be denied at the subscribe step even after
        // requestPermission resolved (blocked site / OS-level block on Chrome),
        // surfacing as NotAllowedError. Tell the user rather than swallowing it.
        if (e && e.name === 'NotAllowedError') {
          showToast('Notifications are blocked. Enable them for this site in your browser settings, then try again.');
        } else {
          showToast('Could not enable notifications. Please try again.');
        }
        return;
      }
      updateBellState();
    }

    async function disableNotifications() {
      try {
        const reg = await navigator.serviceWorker.ready;
        const sub = await reg.pushManager.getSubscription();
        if (sub) {
          const endpoint = sub.endpoint;
          if (sessionId) {
            await fetch(API + '/session/' + sessionId + '/push/subscribe', { method: 'DELETE', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({endpoint}) });
          }
          if (storageGet('push_enabled') === '1') {
            dbg('chat still enabled, keeping shared push subscription');
          } else {
            await sub.unsubscribe();
          }
        }
      } catch (e) { dbg('disableNotifications failed', e.message); }
      storageRemove('stc_push');
      track('push-disable');
      updateBellState();
    }

    async function toggleNotifications() {
      if (storageGet('stc_push') === '1') { await disableNotifications(); }
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
        ddList.classList.toggle('active', btnList.classList.contains('active'));
      }
      if (ddTT && btnTT) {
        ddTT.classList.toggle('active', btnTT.classList.contains('active'));
      }
      // The view options dock directly under the current view's row
      const ddOpts = document.getElementById('dd-view-options');
      const ddCur = (ddTT && btnTT && btnTT.classList.contains('active')) ? ddTT : ddList;
      if (ddOpts && ddCur && ddCur.nextElementSibling !== ddOpts) ddCur.after(ddOpts);
      if (ddFilter && btnFilter) {
        ddFilter.setAttribute('aria-checked', btnFilter.classList.contains('active') ? 'true' : 'false');
      }
      if (ddSched && btnSched) {
        ddSched.style.display = btnSched.style.display;
        ddSched.setAttribute('aria-checked', btnSched.classList.contains('active') ? 'true' : 'false');
      }
    }
    """)

    # --- Timetable-specific JS (only when has_timetable) ---
    if has_timetable:
        parts.append("""
    // View toggle
    var _viewScrollPos = { list: 0, timetable: 0 };
    function switchView(view, btn) {
      track('view-switch', {view});
      _viewScrollPos[currentView] = window.scrollY;
      currentView = view;
      storageSet('stc_view', view);
      document.documentElement.className = 'view-' + view;
      history.replaceState(null, '', view === 'timetable' ? '/timetable' : '/line-up');
      document.title = (view === 'timetable' ? 'Timetable' : 'Line-up') + ' · ' + siteShort;
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
      window.scrollTo(0, _viewScrollPos[view] || 0);
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
    // On phones the panel's .tt-v-scroll is the scroller (the trimmed
    // layout never scrolls the document); tablets scroll the document
    // under the desktop grid. _ttScroller resolves the right target.
    function _ttScroller(panel) {
      if (!window.matchMedia('(max-width: 480px)').matches) return null;
      return panel ? panel.querySelector('.tt-v-scroll') : null;
    }
    function showPanel(date, period) {
      const prevPanel = document.querySelector('.timetable-panel.active');
      const mobileTT = window.matchMedia('(max-width: 768px)').matches;
      if (prevPanel && mobileTT) {
        const pv = _ttScroller(prevPanel);
        _savedScrollTop[prevPanel.dataset.date + '|' + prevPanel.dataset.period] = pv
          ? { top: pv.scrollTop, left: pv.scrollLeft }
          : { top: window.scrollY, left: window.scrollX };
      }
      document.querySelectorAll('.timetable-panel').forEach(p => p.classList.remove('active'));
      const id = 'panel-' + date + '-' + period;
      const panel = document.getElementById(id);
      if (panel) panel.classList.add('active');
      const saved = _carryScroll || _savedScrollTop[date + '|' + period] || { top: 0, left: 0 };
      _carryScroll = null;
      requestAnimationFrame(() => {
        truncateNames();
        if (mobileTT) {
          const nv = _ttScroller(panel);
          if (nv) { nv.scrollTop = saved.top; nv.scrollLeft = saved.left; }
          else window.scrollTo(saved.left, saved.top);
        }
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
        btn.type = 'button';
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
      const pv = _ttScroller(document.querySelector('.timetable-panel.active'));
      _carryScroll = sameDay ? {top: 0, left: 0}
        : pv ? {top: pv.scrollTop, left: pv.scrollLeft}
        : {top: window.scrollY, left: window.scrollX};
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
    var _truncateRaf = 0;
    new ResizeObserver(function() {
      cancelAnimationFrame(_truncateRaf);
      _truncateRaf = requestAnimationFrame(truncateNames);
    }).observe(document.body);

    // Artist popup
    const popup = document.getElementById('tt-popup');

    function _safeHref(u) {
      return /^(https?:\\/\\/|mailto:)/i.test(u || '') ? u : '#';
    }
    function _popupLink(href, svg, label) {
      return '<a href="' + esc(_safeHref(href)) + '" target="_blank" rel="noopener noreferrer">' + svg + ' ' + esc(label || '') + '</a>';
    }
    """)
        parts.append("""
    const PLATFORM_SVG = {
      instagram: '<svg width="18" height="18"><use href="#i-ig"/></svg>',
      soundcloud: '<svg width="18" height="18"><use href="#i-sc"/></svg>',
      spotify: '<svg width="18" height="18"><use href="#i-sp"/></svg>',
      linktree: '<svg width="18" height="18"><use href="#i-lt"/></svg>',
      youtube: '<svg width="18" height="18"><use href="#i-yt"/></svg>',
      ra: '<svg width="18" height="18"><use href="#i-ra"/></svg>'
    };""")
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

    document.getElementById('popup-artists').addEventListener('click', function(e) {
      const t = e.target.closest('[data-bioclick]');
      if (!t) return;
      e.stopPropagation();
      closePopup();
      openBioById(t.dataset.oid, t.dataset.name);
    });

    function openBlockPopup(block, px, py) {
        _popupJustOpened = true;
        const d = block.dataset;
        const artists = TT_ARTISTS[d.artistId] || [];
        requestAnimationFrame(() => {
          document.getElementById('popup-meta').textContent = d.time + ' \\u00b7 ' + d.floor;
          let artistsHtml = '';
          artists.forEach(a => {
            const bioAttrs = ' data-bioclick="1" data-oid="' + esc(a.oid) + '" data-name="' + esc(a.name) + '"';
            const photo = a.photo
              ? '<img class="popup-photo" src="' + esc(a.photo) + '" alt="' + esc(a.name) + '"' + bioAttrs + '>'
              : '<div class="popup-photo-placeholder"></div>';
            let links = '';
            (a.links || []).forEach(function(l) {
              var svg = PLATFORM_SVG[l.p] || '';
              if (svg) links += _popupLink(l.u, svg, l.f || '');
            });
            artistsHtml += '<div class="popup-artist">' + photo + '<div><div class="popup-name"' + bioAttrs + '>' + esc(a.name) + '</div><div class="links">' + links + '</div></div></div>';
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
    window.addEventListener('scroll', () => closePopup(), {passive: true});
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





    """)

    parts.append("""
    // Init
    const _initP = (async () => {
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
            syncedPicks = new Set(data.picks);
            if (data.schedule) localSchedule = new Set(data.schedule);
            if (data.schedule) syncedSchedule = new Set(data.schedule);
            sessionId = data.session_id;
            shareToken = data.share_token;
            storageSet('stc_session_id', sessionId);
            storageSet('stc_share_token', shareToken);
            saveLocal();
            connectWS(sessionId);
          }
        } catch (e) { dbg('fetch /api/me failed', e.message); }
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
            if (navUrl && navUrl !== location.pathname && !navUrl.includes('line-up')) {
              window.location.href = navUrl;
              return;
            }
            if (navUrl.includes('timetable')) currentView = 'timetable';
          }
        } catch (e) { /* cache API unavailable */ }
      }
      if (currentView === 'timetable' && document.getElementById('btn-timetable')) {
        switchView('timetable', document.getElementById('btn-timetable'));
      }
      syncDropdownState();
      setTimeout(syncDropdownState, 100);
      // Re-sync push subscription to server (handles purged DB, reinstalls, etc.)
      if (storageGet('stc_push') === '1' && 'serviceWorker' in navigator) {
        try {
          var swReg = await navigator.serviceWorker.ready;
          var existingSub = await swReg.pushManager.getSubscription();
          if (existingSub && sessionId) {
            storageSet('stc_push_endpoint', existingSub.endpoint);
            fetch(API + '/session/' + sessionId + '/push/subscribe', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(existingSub.toJSON()) }).catch(function() {});
          } else if (!existingSub) {
            storageRemove('stc_push');
            updateBellState();
          }
        } catch (e) { dbg('push re-sync failed', e.message); }
      }""")
    if has_timetable:
        parts.append("      updateNowLine();")
    parts.append("""
    })();

    var _pushNavigating = false;
    function _checkPushNavigate() {
      if (_pushNavigating || !('caches' in window)) return;
      caches.open('stc-push').then(function(c) {
        return c.match('/_push_navigate').then(function(r) {
          if (!r) return;
          return r.text().then(function(url) {
            return c.delete('/_push_navigate').then(function() {
              if (url && url !== location.pathname && !url.includes('line-up')) {
                _pushNavigating = true;
                setTimeout(function() { _pushNavigating = false; }, 3000);
                window.location.href = url;
              }
            });
          });
        });
      }).catch(function() {});
    }
    function _pushNavRetry() {
      _checkPushNavigate();
      setTimeout(_checkPushNavigate, 300);
      setTimeout(_checkPushNavigate, 1000);
    }
    document.addEventListener('visibilitychange', function() {
      if (!document.hidden) _pushNavRetry();
    });
    window.addEventListener('focus', _pushNavRetry);
    window.addEventListener('pageshow', _pushNavRetry);

    function setStickyTops() {
      var bar = document.getElementById('cmd-bar');
      if (!bar) return;
      var barH = bar.offsetHeight;
      var h1 = document.querySelector('h1');
      var h1H = h1 ? h1.offsetHeight : 0;
      var h2 = document.querySelector('.date-section > h2');
      var h2H = h2 ? h2.offsetHeight : 0;
      var h3 = document.querySelector('h3.period-heading');
      var h3H = h3 ? h3.offsetHeight : 0;
      // Custom properties on :root, not inline styles on every heading
      var root = document.documentElement.style;
      root.setProperty('--sticky-top-h1', barH + 'px');
      root.setProperty('--sticky-top-h2', (barH + h1H) + 'px');
      root.setProperty('--sticky-top-h3', (barH + h1H + h2H) + 'px');
      root.setProperty('--sticky-top-h4', (barH + h1H + h2H + h3H) + 'px');
    }
    setStickyTops();
    var _stickyResizeRaf = 0;
    window.addEventListener('resize', function() {
      cancelAnimationFrame(_stickyResizeRaf);
      _stickyResizeRaf = requestAnimationFrame(function() { setStickyTops(); placeFadeSentinels(); });
    });
    // Reveal only once init settles (success or failure) so the deep-link
    // gate (which polls this opacity flag) never fires before sessionId/
    // shareToken are loaded. A timeout race is the safety net: fetch() has
    // no built-in timeout, so a hung request could otherwise leave the page
    // invisible forever.
    var _initTimeout = new Promise(function(resolve) { setTimeout(resolve, 5000); });
    Promise.race([_initP.catch(function(e) { dbg('init failed', e && e.message); }), _initTimeout]).then(function() {
      document.body.style.opacity = '1';
    });
    """)
    parts.append("  </script>")
    parts.append("  </main>")
    parts.append("</body>")
    parts.append("</html>")
    return "\n".join(parts)
