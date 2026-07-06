I reviewed `server/chat/chat.html` (4677 lines) and `server/static/shared.js` across the full file, using parallel sub-agent passes then manually verifying the highest-severity claims against the actual source. Full report saved to `review_reports/chat-frontend-xss.md`.

**Key result: no exploitable XSS.** Several sub-agent findings looked scary at first pass but turned out to be false positives once I read the actual helper implementations:
- `jss()` (chat.html:4161) HTML-attribute-escapes on top of JS-string-escaping — the `onclick="...('${jss(x)}')"` pattern used throughout is safe.
- `_linkify()` (chat.html:4162) runs `escapeHtml()` before linkifying — message text rendering is safe.
- Link-preview `href`/`img src` can't carry `javascript:`/`data:` — verified server-side in `chat_ws.py`, which hard-requires `https?://` and re-validates scheme.

**Real findings (all MEDIUM or LOW, none deploy-blocking):**
- A cluster of shared-global race conditions on rapid room switching: `window._roomHistoryResolve` (1931), `_lastSeenTime`/`_markReadRoom` (2247-2297), and `_routing` getting reset by the resize handler (4519) or bypassed by an early-return in `route()` (4457).
- Missing guards: `appendMessage()` has no dedup check (2101-2113, can double-render a message), `toggleReaction` (2463) and `submitProfile` (990) have no in-flight lock, `_subscribePush` (3852) can run concurrently from two callers.
- `_doBlock` (3298) updates local block state optimistically with no ack/reconciliation if the WS send is dropped.
- Two minor unescaped-but-low-risk spots (4019, 4021) inconsistent with the rest of the file's escaping discipline.
- Accessibility gaps on two custom widgets: avatar zoom/pan cropper and country picker (mouse/touch-only, no ARIA semantics).

Worth prioritizing the `_roomHistoryResolve`/`_routing` race fixes since they affect correctness under normal rapid-tapping usage, not just edge cases.
