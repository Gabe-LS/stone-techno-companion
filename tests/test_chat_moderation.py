"""Tests for chat moderation pipeline: word filter, strike system, full pipeline."""

import sqlite3
import pytest
from unittest.mock import AsyncMock, patch

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "companion"))

from chat_db import (
    init_chat_db,
    create_user,
    get_user,
    is_muted,
    get_strike_count,
    is_banned,
)
from chat_moderation import (
    WordFilter,
    _normalize,
    process_strike,
    moderate_message,
    check_openai_moderation,
    OPENAI_THRESHOLDS,
)


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_chat_db(conn)
    yield conn
    conn.close()


@pytest.fixture
def user(db):
    return create_user(db, "google", "google-123", "TestUser", "fp-abc")


@pytest.fixture
def wf():
    return WordFilter()


# --- Normalization ---


class TestNormalization:
    def test_lowercase(self):
        assert _normalize("HELLO") == "hello"

    def test_strip_diacritics(self):
        assert _normalize("café") == "cafe"

    def test_substitutions(self):
        assert _normalize("m0lly") == "molly"
        assert _normalize("@cid") == "acid"
        assert _normalize("k3t") == "ket"
        assert _normalize("$peed") == "speed"

    def test_strip_punctuation(self):
        assert _normalize("hey what's up???") == "hey whats up"

    def test_collapse_whitespace(self):
        assert _normalize("too   many    spaces") == "too many spaces"


# --- Word Filter ---


class TestWordFilter:
    def test_catches_drug_terms(self, wf):
        result = wf.check("anyone got molly?")
        assert result is not None
        assert result["is_drug"] is True
        assert result["matched"] == "molly"

    def test_catches_mdma(self, wf):
        result = wf.check("I took MDMA last night")
        assert result is not None
        assert result["is_drug"] is True

    def test_catches_ketamine(self, wf):
        assert wf.check("anyone selling ket?")["is_drug"] is True
        assert wf.check("ketamine is wild")["is_drug"] is True

    def test_catches_substitutions(self, wf):
        result = wf.check("got any m0lly?")
        assert result is not None
        assert result["is_drug"] is True

    def test_catches_dealer(self, wf):
        assert wf.check("looking for a dealer")["is_drug"] is True

    def test_catches_multi_word(self, wf):
        assert wf.check("need a half g") is not None

    def test_clean_message_passes(self, wf):
        assert wf.check("this set is incredible") is None
        assert wf.check("love the music here") is None
        assert wf.check("where's the bar?") is None

    def test_empty_message(self, wf):
        assert wf.check("") is None

    def test_emoji_only(self, wf):
        assert wf.check("🔥🔥🔥") is None

    def test_case_insensitive(self, wf):
        assert wf.check("MOLLY")["is_drug"] is True
        assert wf.check("Ketamine")["is_drug"] is True

    def test_term_count(self, wf):
        assert wf.term_count > 30

    def test_custom_blocklist(self, tmp_path):
        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text("badword\n# comment\nanotherbad\n")
        wf = WordFilter(blocklist)
        assert wf.check("this is a badword")["is_drug"] is False
        assert wf.check("anotherbad here") is not None
        assert wf.check("clean message") is None

    def test_blocklist_comments_ignored(self, tmp_path):
        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text("# this is a comment\nactualterm\n")
        wf = WordFilter(blocklist)
        assert wf.check("# this is a comment") is None
        assert wf.check("actualterm") is not None


# --- Strike System ---


class TestStrikeSystem:
    def test_first_strike_warning(self, db, user):
        result = process_strike(db, user["id"], "word_filter", "bad word")
        assert result["action"] == "strike"
        assert result["strike_count"] == 1
        assert "flagged" in result["message"].lower()

    def test_second_strike_warning(self, db, user):
        process_strike(db, user["id"], "word_filter", "first")
        result = process_strike(db, user["id"], "word_filter", "second")
        assert result["action"] == "strike"
        assert result["strike_count"] == 2

    def test_third_strike_mute(self, db, user):
        process_strike(db, user["id"], "word_filter", "1")
        process_strike(db, user["id"], "word_filter", "2")
        result = process_strike(db, user["id"], "word_filter", "3")
        assert result["action"] == "mute"
        assert result["strike_count"] == 3
        assert is_muted(db, user["id"])

    def test_fourth_strike_ban(self, db, user):
        process_strike(db, user["id"], "word_filter", "1")
        process_strike(db, user["id"], "word_filter", "2")
        process_strike(db, user["id"], "word_filter", "3")
        result = process_strike(db, user["id"], "word_filter", "4")
        assert result["action"] == "ban"
        assert is_banned(db, "google", "google-123") is not None

    def test_drug_first_strike(self, db, user):
        result = process_strike(db, user["id"], "word_filter", "molly", is_drug=True)
        assert result["action"] == "strike"
        assert result["strike_count"] == 1

    def test_drug_follows_normal_escalation(self, db, user):
        process_strike(db, user["id"], "word_filter", "molly", is_drug=True)
        process_strike(db, user["id"], "word_filter", "ket", is_drug=True)
        result = process_strike(db, user["id"], "word_filter", "mdma", is_drug=True)
        assert result["action"] == "mute"
        assert result["strike_count"] == 3
        result = process_strike(db, user["id"], "word_filter", "speed", is_drug=True)
        assert result["action"] == "ban"
        assert is_banned(db, "google", "google-123") is not None

    def test_ban_includes_fingerprint(self, db, user):
        process_strike(db, user["id"], "word_filter", "1")
        process_strike(db, user["id"], "word_filter", "2")
        process_strike(db, user["id"], "word_filter", "3")
        process_strike(db, user["id"], "word_filter", "4")
        ban = is_banned(db, "google", "google-123")
        assert ban["device_fingerprint"] == "fp-abc"

    def test_repeated_mutes_trigger_ban(self, db, user):
        for cycle in range(3):
            process_strike(db, user["id"], "word_filter", f"a{cycle}")
            process_strike(db, user["id"], "word_filter", f"b{cycle}")
            result = process_strike(db, user["id"], "word_filter", f"c{cycle}")
            if result["action"] == "ban":
                break
            db.execute("DELETE FROM strikes WHERE user_id = ?", (user["id"],))
            db.execute(
                "UPDATE users SET muted_until = NULL WHERE id = ?", (user["id"],)
            )
            db.commit()
        assert result["action"] == "ban"
        assert is_banned(db, "google", "google-123") is not None

    def test_expired_strikes_dont_count(self, db, user):
        from chat_db import add_strike, STRIKE_TTL_HOURS
        from datetime import datetime, timedelta, timezone

        expired = (
            datetime.now(timezone.utc) - timedelta(hours=STRIKE_TTL_HOURS + 1)
        ).isoformat()
        db.execute(
            "INSERT INTO strikes (id, user_id, reason, detail, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("old-1", user["id"], "word_filter", "old", expired, expired),
        )
        db.execute(
            "INSERT INTO strikes (id, user_id, reason, detail, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("old-2", user["id"], "word_filter", "old", expired, expired),
        )
        db.commit()
        result = process_strike(db, user["id"], "word_filter", "new")
        assert result["action"] == "strike"
        assert result["strike_count"] == 1

    def test_new_strike_resets_expiry(self, db, user):
        from chat_db import add_strike, STRIKE_TTL_HOURS
        from datetime import datetime, timedelta, timezone

        soon = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        db.execute(
            "INSERT INTO strikes (id, user_id, reason, detail, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("about-to-expire", user["id"], "word_filter", "old", soon, soon),
        )
        db.commit()
        process_strike(db, user["id"], "word_filter", "new")
        row = db.execute(
            "SELECT expires_at FROM strikes WHERE id = 'about-to-expire'"
        ).fetchone()
        new_expiry = datetime.fromisoformat(row[0])
        assert new_expiry > datetime.fromisoformat(soon)

    def test_fourth_strike_after_mute_ends(self, db, user):
        process_strike(db, user["id"], "word_filter", "1")
        process_strike(db, user["id"], "word_filter", "2")
        result = process_strike(db, user["id"], "word_filter", "3")
        assert result["action"] == "mute"
        db.execute("UPDATE users SET muted_until = NULL WHERE id = ?", (user["id"],))
        db.commit()
        result = process_strike(db, user["id"], "word_filter", "4")
        assert result["action"] == "ban"
        assert is_banned(db, "google", "google-123") is not None

    def test_mute_count_persists_after_strike_expiry(self, db, user):

        process_strike(db, user["id"], "word_filter", "1")
        process_strike(db, user["id"], "word_filter", "2")
        process_strike(db, user["id"], "word_filter", "3")
        db.execute("DELETE FROM strikes WHERE user_id = ?", (user["id"],))
        db.execute("UPDATE users SET muted_until = NULL WHERE id = ?", (user["id"],))
        db.commit()
        u = get_user(db, user["id"])
        assert u["mute_count"] == 1
        assert get_strike_count(db, user["id"]) == 0


# --- Full Pipeline ---


class TestModeratePipeline:
    @pytest.mark.asyncio
    async def test_clean_message_allowed(self, db, user):
        with patch(
            "chat_moderation.check_openai_moderation",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await moderate_message(db, user["id"], "great set!")
            assert result["allowed"] is True

    @pytest.mark.asyncio
    async def test_word_filter_blocks(self, db, user):
        with patch(
            "chat_moderation.check_openai_moderation",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await moderate_message(db, user["id"], "anyone got molly?")
            assert result["allowed"] is False
            assert result["action"] == "strike"

    @pytest.mark.asyncio
    async def test_muted_user_blocked(self, db, user):
        from chat_db import mute_user

        mute_user(db, user["id"], minutes=30)
        result = await moderate_message(db, user["id"], "hello")
        assert result["allowed"] is False
        assert result["action"] == "mute"

    @pytest.mark.asyncio
    async def test_ai_moderation_blocks(self, db, user):
        ai_result = {"category": "harassment", "score": 0.95, "instant_ban": False}
        with patch(
            "chat_moderation.check_openai_moderation",
            new_callable=AsyncMock,
            return_value=ai_result,
        ):
            result = await moderate_message(db, user["id"], "some hateful message")
            assert result["allowed"] is False
            assert result["action"] == "strike"
            assert get_strike_count(db, user["id"]) == 1

    @pytest.mark.asyncio
    async def test_ai_instant_ban(self, db, user):
        ai_result = {"category": "sexual/minors", "score": 0.90, "instant_ban": True}
        with patch(
            "chat_moderation.check_openai_moderation",
            new_callable=AsyncMock,
            return_value=ai_result,
        ):
            result = await moderate_message(db, user["id"], "terrible content")
            assert result["allowed"] is False
            assert result["action"] == "ban"
            assert is_banned(db, "google", "google-123") is not None

    @pytest.mark.asyncio
    async def test_word_filter_runs_before_ai(self, db, user):
        mock_ai = AsyncMock(return_value=None)
        with patch("chat_moderation.check_openai_moderation", mock_ai):
            await moderate_message(db, user["id"], "got any molly?")
            mock_ai.assert_not_called()

    @pytest.mark.asyncio
    async def test_ai_failure_allows_message(self, db, user):
        with patch(
            "chat_moderation.check_openai_moderation",
            new_callable=AsyncMock,
            side_effect=Exception("API down"),
        ):
            result = await moderate_message(db, user["id"], "normal message")
            assert result["allowed"] is True

    @pytest.mark.asyncio
    async def test_drug_escalation_through_pipeline(self, db, user):
        with patch(
            "chat_moderation.check_openai_moderation",
            new_callable=AsyncMock,
            return_value=None,
        ):
            r1 = await moderate_message(db, user["id"], "got molly?")
            assert r1["allowed"] is False
            assert r1["strike_count"] == 1

            r2 = await moderate_message(db, user["id"], "selling ket")
            assert r2["allowed"] is False
            assert r2["action"] == "strike"

            r3 = await moderate_message(db, user["id"], "got mdma")
            assert r3["allowed"] is False
            assert r3["action"] == "mute"

            r4 = await moderate_message(db, user["id"], "more drugs")
            assert r4["allowed"] is False
            assert r4["action"] == "mute"

    @pytest.mark.asyncio
    async def test_three_strike_escalation(self, db, user):
        with patch(
            "chat_moderation.check_openai_moderation",
            new_callable=AsyncMock,
            return_value=None,
        ):
            wf_blocklist = Path(__file__).parent / "_test_blocklist.txt"
            wf_blocklist.write_text("badword1\nbadword2\nbadword3\n")
            try:
                from chat_moderation import reload_word_filter, _word_filter
                import chat_moderation

                chat_moderation._word_filter = WordFilter(wf_blocklist)

                r1 = await moderate_message(db, user["id"], "badword1")
                assert r1["action"] == "strike"

                r2 = await moderate_message(db, user["id"], "badword2")
                assert r2["action"] == "strike"

                r3 = await moderate_message(db, user["id"], "badword3")
                assert r3["action"] == "mute"

                r4 = await moderate_message(db, user["id"], "badword3")
                assert r4["action"] == "mute"

                db.execute(
                    "UPDATE users SET muted_until = NULL WHERE id = ?", (user["id"],)
                )
                db.commit()

                r5 = await moderate_message(db, user["id"], "badword3")
                assert r5["action"] == "ban"
            finally:
                wf_blocklist.unlink(missing_ok=True)
                chat_moderation._word_filter = None
