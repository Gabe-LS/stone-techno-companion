"""
Static enforcement of apps/web/DESIGN-STANDARDS.md against every CSS file
under apps/web. Pure file reading -- no browser, no dev server, no network
-- so it can run in CI (see .github/workflows/ci.yml) and as part of a local
pre-commit-style check (`tests/web/transport_nextjs_check.py` documents
running this alongside it; it is not itself a Playwright script and is not
named test_*.py so pytest never collects it).

Checks (see DESIGN-STANDARDS.md "Component inventory" for the token/
primitive system this enforces):

  (a) No raw hex color outside packages/design-tokens/tokens.css, unless
      the declaration sits within CSS_EXCEPTION_WINDOW lines of a comment
      containing the marker text "semantic-exception".
  (b) No raw px value on a small set of "design-scale" properties
      (font-size, border-radius, padding*, margin*, gap, top, right,
      bottom, left, inset) outside a small allowlist (0px, 1px, 2px --
      hairline borders and underline/accent-bar nudges), unless marked
      semantic-exception the same way as (a).
  (c) No `text-decoration: underline` outside components/ui/ExternalLink.module.css
      and components/Nav.module.css (DESIGN-STANDARDS.md #2: underline is
      reserved for links that leave the site, plus the Nav bar's own
      documented accent-underline convention).

Usage:
  python tests/web/css_standards_check.py
Exit code 0 = clean, 1 = violations found (each printed with file:line).
"""

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_ROOT = REPO_ROOT / "apps" / "web"

# Files allowed to declare `text-decoration: underline`.
UNDERLINE_ALLOWED = {
    WEB_ROOT / "components" / "ui" / "ExternalLink.module.css",
    WEB_ROOT / "components" / "Nav.module.css",
}

# How many lines above (and the line itself) to search for a
# "semantic-exception" marker comment before a flagged declaration. Wide
# enough to cover a documentation comment sitting once at the top of a
# multi-property rule block (the common pattern in this codebase) without
# being unbounded (a marker still can't "cover" an unrelated rule far below it).
EXCEPTION_WINDOW = 20

# px values allowed on the "design-scale" properties without a token or an
# exception marker: 0, hairline borders (1px), and underline/accent-bar
# nudges (2px). Anything else on these properties should be a token
# (var(--space-*), var(--radius-*), var(--font-*), var(--icon-size-*), ...)
# or a documented exception.
ALLOWED_PX = {"0", "1", "2"}

# Properties whose values are checked for raw px under rule (b). Matched at
# the start of a declaration (optionally prefixed by a vendor/subproperty
# hyphen segment, e.g. "padding-left", "border-radius" -- but NOT as a
# substring of an unrelated property like "border-top" matching "top").
DESIGN_SCALE_PROPS = (
    "font-size",
    "border-radius",
    "padding",
    "padding-top",
    "padding-right",
    "padding-bottom",
    "padding-left",
    "margin",
    "margin-top",
    "margin-right",
    "margin-bottom",
    "margin-left",
    "gap",
    "row-gap",
    "column-gap",
    "top",
    "right",
    "bottom",
    "left",
    "inset",
)
_PROP_ALT = "|".join(re.escape(p) for p in sorted(DESIGN_SCALE_PROPS, key=len, reverse=True))
# Anchored so "border-top" can never match the bare "top" alternative: the
# property must start right after a `{`, `;`, or newline (with only
# whitespace between), and must be followed by `:` (not another word char,
# so "top-level" style custom idents can't match either).
DECLARATION_RE = re.compile(
    r"(?:^|[{;])\s*(" + _PROP_ALT + r")\s*:\s*([^;{}]+);",
    re.MULTILINE,
)
PX_TOKEN_RE = re.compile(r"(\d+(?:\.\d+)?)px")

HEX_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")
UNDERLINE_RE = re.compile(r"text-decoration\s*:\s*[^;{}]*\bunderline\b")

COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def strip_comments_preserve_lines(text: str) -> str:
    """Blank out comment bodies (keep newlines) so line numbers stay aligned
    with the original file, and so a hex-looking substring inside a comment
    (e.g. a doc reference like "docs/parity/transport.md #216") never
    matches the code-scanning regexes."""

    def blank(m: re.Match) -> str:
        return "".join(c if c == "\n" else " " for c in m.group(0))

    return COMMENT_RE.sub(blank, text)


def line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def has_exception_marker(lines: list[str], line_no: int) -> bool:
    """True if any line from `line_no - EXCEPTION_WINDOW` through `line_no`
    (1-indexed, inclusive) contains the "semantic-exception" marker text."""
    start = max(1, line_no - EXCEPTION_WINDOW)
    for n in range(start, line_no + 1):
        if "semantic-exception" in lines[n - 1]:
            return True
    return False


def find_css_files() -> list[Path]:
    files = []
    for path in WEB_ROOT.rglob("*.css"):
        parts = path.parts
        if "node_modules" in parts or ".next" in parts:
            continue
        files.append(path)
    return sorted(files)


def check_file(path: Path) -> list[str]:
    violations = []
    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines()
    code = strip_comments_preserve_lines(raw)

    # --- (a) raw hex colors ---
    for m in HEX_RE.finditer(code):
        line_no = line_of(code, m.start())
        if has_exception_marker(lines, line_no):
            continue
        violations.append(
            f"{path.relative_to(REPO_ROOT)}:{line_no}: raw hex color {m.group(0)!r} "
            f"outside tokens.css and not within {EXCEPTION_WINDOW} lines of a "
            f"'semantic-exception' marker comment"
        )

    # --- (b) raw px on design-scale properties ---
    for m in DECLARATION_RE.finditer(code):
        prop, value = m.group(1), m.group(2)
        for px_m in PX_TOKEN_RE.finditer(value):
            num = px_m.group(1)
            # Normalize "2.0" -> "2" style so the allowlist compares cleanly;
            # any decimal (e.g. "1.5px") is never in ALLOWED_PX and is
            # correctly flagged.
            normalized = num[:-2] if num.endswith(".0") else num
            if normalized in ALLOWED_PX:
                continue
            decl_start = m.start(2)
            line_no = line_of(code, decl_start)
            if has_exception_marker(lines, line_no):
                continue
            violations.append(
                f"{path.relative_to(REPO_ROOT)}:{line_no}: raw px value '{px_m.group(0)}' "
                f"on '{prop}' is not a token and not in the allowlist "
                f"({sorted(ALLOWED_PX)}px) or exception-marked"
            )

    # --- (c) text-decoration: underline outside the allowed files ---
    if path not in UNDERLINE_ALLOWED:
        for m in UNDERLINE_RE.finditer(code):
            line_no = line_of(code, m.start())
            violations.append(
                f"{path.relative_to(REPO_ROOT)}:{line_no}: 'text-decoration: underline' "
                f"is only allowed in components/ui/ExternalLink.module.css and "
                f"components/Nav.module.css"
            )

    return violations


def main() -> int:
    files = find_css_files()
    if not files:
        print(f"No CSS files found under {WEB_ROOT}")
        return 1

    all_violations = []
    for f in files:
        all_violations.extend(check_file(f))

    print(f"Scanned {len(files)} CSS files under apps/web")
    if all_violations:
        print(f"\n{len(all_violations)} violation(s):\n")
        for v in all_violations:
            print(f"  FAIL  {v}")
        return 1

    print("OK  no hardcoded-value violations found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
