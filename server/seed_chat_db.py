#!/usr/bin/env python3
"""Build a production-ready chat.db seed from the local development database.

Takes the local chat.db (which holds the curated room setup and app settings
configured via the admin panel) and produces a copy with all development/test
data stripped: every message, user, session, DM/meetup room, upload reference,
report, strike, ban, and push subscription is removed. Group rooms and
chat_settings survive. A single pre-created user is inserted so the owner's
first login (email magic link) lands on an account that already has the
username/display name/country filled in — the profile prompt then only asks
for the avatar and a confirmation of the prefilled details (the prompt
triggers on missing avatar, see chat.html renderProfilePrompt).

Used by deploy.sh to seed the VPS on the first chat deploy (only when the VPS
has no chat.db yet). Safe to re-run locally: the source db is opened read-only.

Usage:
    python3 seed_chat_db.py --out /tmp/chat-seed.db
    python3 seed_chat_db.py --source data/chat.db --out /tmp/chat-seed.db \
        --email gabrielelosurdo@gmail.com --username gabriele \
        --display-name Gabriele --country IT
"""

import argparse
import sqlite3
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent

# Everything that is per-user, per-message, or otherwise runtime state.
# rooms and chat_settings are intentionally NOT here.
WIPE_TABLES = [
    "message_reactions",
    "messages",
    "room_memberships",
    "meetup_attendees",
    "meetups",
    "dm_participants",
    "blocks",
    "reports",
    "strikes",
    "sessions",
    "email_tokens",
    "avatars",
    "chat_push_subscriptions",
    "e2ee_device_keys",
    "admin_actions",
    "admins",
    "bans",
    "user_providers",
    "users",
]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", default=str(SERVER_DIR / "data" / "chat.db"))
    p.add_argument("--out", required=True)
    p.add_argument("--email", default="gabrielelosurdo@gmail.com")
    p.add_argument("--username", default="gabriele")
    p.add_argument("--display-name", default="Gabriele")
    p.add_argument("--country", default="IT")
    args = p.parse_args()

    source = Path(args.source)
    out = Path(args.out)
    if not source.is_file():
        print(f"ERROR: source db not found: {source}")
        return 1
    if out.exists():
        out.unlink()

    # VACUUM INTO gives a transactionally consistent copy even if the local
    # dev server happens to be running against the source db.
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    src.execute("VACUUM INTO ?", (str(out),))
    src.close()

    db = sqlite3.connect(out)
    db.row_factory = sqlite3.Row

    for table in WIPE_TABLES:
        db.execute(f"DELETE FROM {table}")
    db.execute("DELETE FROM rooms WHERE type IN ('dm', 'meetup')")
    db.execute("UPDATE rooms SET last_message_at = NULL")

    # Pre-create the owner account. create_user from chat_db keeps the row
    # shape in lockstep with the app (id/color/user_providers handling).
    sys.path.insert(0, str(SERVER_DIR))
    import chat_db

    email = args.email.strip().lower()
    user = chat_db.create_user(db, "email", email, args.display_name)
    db.execute(
        "UPDATE users SET username = ?, username_lower = ?, country = ? WHERE id = ?",
        (args.username, args.username.lower(), args.country, user["id"]),
    )
    # No avatar on purpose: the profile prompt fires on missing avatar and
    # shows all other fields prefilled for confirmation.

    db.commit()
    db.execute("VACUUM")

    rooms = db.execute(
        "SELECT name, type, is_main FROM rooms ORDER BY position"
    ).fetchall()
    settings = db.execute("SELECT COUNT(*) FROM chat_settings").fetchone()[0]
    check = db.execute("PRAGMA quick_check").fetchone()[0]
    db.close()

    print(f"Seed written: {out} (integrity: {check})")
    print(f"  settings rows kept: {settings}")
    print(f"  rooms kept ({len(rooms)}):")
    for r in rooms:
        flag = " [main]" if r["is_main"] else ""
        print(f"    - {r['name']} ({r['type']}){flag}")
    print(
        f"  owner user: {args.display_name} (@{args.username}, {email}, "
        f"country {args.country}, no avatar -> profile prompt will ask for it)"
    )
    return 0 if check == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
