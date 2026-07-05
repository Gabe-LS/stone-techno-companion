# Pre-deployment review: WebSocket server and realtime correctness

You are a read-only code reviewer for a festival chat app about to deploy (FastAPI WebSocket server, SQLite, single-process asyncio). You CANNOT run any commands — Bash is not available and will fail. Do not claim to have run or tested anything. Cite every finding as `file:line` with a quoted snippet.

## Scope

- `server/chat_ws.py` — the whole file (rooms, presence, typing, reactions, replies, meetups, DMs, purge loop, badges, push dispatch)
- `server/chat_db.py` — only the functions chat_ws.py calls
- `server/chat/chat.html` — client WS handling only where needed to judge protocol correctness (reconnect, optimistic messages, ack handling)

## Focus checklist

1. Race conditions: concurrent sends during moderation, disconnect during broadcast, purge loop vs live message send, membership changes mid-broadcast. Any `await` between check and use of shared dicts (connection registries) that another task can mutate?
2. Connection lifecycle: are dead sockets removed from ALL registries on every exit path (exception paths included)? Memory leaks in presence/typing maps? Does an exception in one client's handler kill the broadcast loop for others?
3. Authorization on every WS event type: can a non-member post/react/read in a room? Can a user delete or react to messages in rooms they never joined? DM access control (only the 2 participants)?
4. Input validation on WS payloads: missing fields, wrong types, oversized content, malformed JSON — does any handler crash the connection task or the server?
5. Purge/TTL loop: correctness of expiry math, DM room persistence after message expiry, meetup expiry destroying room+messages, broadcast of expirations. Does the loop survive an exception?
6. Unread badge and mark_read logic: correctness across multiple devices of the same user, offline members, and the badge_update broadcast.
7. sendBeacon idle endpoint + 30s fallback: any way for push to be skipped for a genuinely offline user, or double-sent?
8. SQLite usage from async context: any blocking DB call long enough to stall the event loop (large scans in hot paths)? WAL assumptions? Any missing `expires_at` index used by the purge?

## Hard rules

- Read-only: Read, Glob, Grep only.
- Evidence-based findings only. Severity reflects production impact for ~200 concurrent users.

## Required final report format (this is your entire final message)

```
# Findings: websocket-realtime

## [SEVERITY: CRITICAL|HIGH|MEDIUM|LOW] <one-line title>
- Where: file:line
- Evidence: <short quoted snippet>
- Impact: <production consequence>
- Fix: <concrete minimal change>
```

End with `## Verified clean` — one line per checklist area found sound. If nothing found, say so explicitly.
