"""Tests that every tool correctly isolates data between users.

Each test creates data as user A (the default test user), then verifies that
user B cannot see, modify, or delete it.
"""

import uuid
from contextlib import contextmanager

import pytest

import db
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from tests.factories import create_test_match
from main import (
    delete_match,
    delete_match_file,
    edit_match,
    get_duration_stats,
    get_hero_detail_stats,
    get_hero_stat_series,
    get_match,
    get_match_player_history,
    get_match_rankings,
    get_player_history,
    get_player_note,
    get_stats_summary,
    get_teammate_stats,
    list_match_files,
    list_matches,
    list_player_notes,
    set_player_note,
    upload_screenshot,
)
from models import Match, MatchFile, User


class _FakeAccessToken(AccessToken):
    user_id: uuid.UUID
    is_admin: bool = False


@contextmanager
def set_current_user(user):
    token = _FakeAccessToken(
        token="test-token",
        client_id="test",
        scopes=[],
        user_id=user.id,
        is_admin=user.is_admin,
    )
    auth_user = AuthenticatedUser(token)
    t = auth_context_var.set(auth_user)
    try:
        yield
    finally:
        auth_context_var.reset(t)


@pytest.fixture
async def user_b():
    """Get or create a second test user."""
    from sqlalchemy import select

    async with db.async_session() as session:
        existing = (
            await session.execute(
                select(User).where(User.google_sub == "test-google-sub-user-b")
            )
        ).scalar_one_or_none()
        if existing:
            return existing

    async with db.async_session() as session:
        async with session.begin():
            user = User(
                google_sub="test-google-sub-user-b",
                email="userb@test.com",
                display_name="User B",
                is_admin=False,
            )
            session.add(user)
        await session.refresh(user)
    return user


class TestSubmitAndOwnership:
    async def test_submit_match_owned_by_current_user(self, default_user):
        match_id = await create_test_match()
        async with db.async_session() as session:
            from sqlalchemy import select

            m = (
                await session.execute(select(Match).where(Match.id == uuid.UUID(match_id)))
            ).scalar_one()
            assert m.user_id == default_user.id


class TestGetMatchIsolated:
    async def test_user_b_cannot_get_user_a_match(self, user_b):
        match_id = await create_test_match()

        # User A can see it
        result_a = await get_match(match_id)
        assert "error" not in result_a

        # User B cannot
        with set_current_user(user_b):
            result_b = await get_match(match_id)
        assert result_b == {"error": "Match not found"}


class TestListMatchesIsolated:
    async def test_list_only_own_matches(self, user_b):
        # User A creates a match
        await create_test_match()

        # User B creates a match
        with set_current_user(user_b):
            match_b = await create_test_match(map_name="Hanamura")

        # User A sees only their match
        result_a = await list_matches()
        assert result_a["total"] == 1

        # User B sees only their match
        with set_current_user(user_b):
            result_b = await list_matches()
        assert result_b["total"] == 1
        assert result_b["matches"][0]["id"] == match_b


class TestEditMatchIsolated:
    async def test_user_b_cannot_edit_user_a_match(self, user_b):
        match_id = await create_test_match()

        with set_current_user(user_b):
            result = await edit_match(match_id, notes="hacked")
        assert result == {"error": "Match not found"}

        # Verify notes weren't changed
        original = await get_match(match_id)
        assert original.get("notes") != "hacked"


class TestDeleteMatchIsolated:
    async def test_user_b_cannot_delete_user_a_match(self, user_b):
        match_id = await create_test_match()

        with set_current_user(user_b):
            result = await delete_match(match_id)
        assert result == {"deleted": False}

        # Still exists for user A
        result_a = await get_match(match_id)
        assert "error" not in result_a


class TestUploadScreenshotIsolated:
    async def test_user_b_cannot_upload_to_user_a_match(self, user_b):
        import base64

        match_id = await create_test_match()
        # Minimal valid PNG
        png = base64.b64encode(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
            b"\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
            b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
        ).decode()

        with set_current_user(user_b):
            result = await upload_screenshot(match_id, png)
        assert result == {"error": "Match not found"}


class TestStatsSummaryIsolated:
    async def test_stats_only_from_own_matches(self, user_b):
        await create_test_match(result="VICTORY")
        await create_test_match(result="DEFEAT", played_at="2026-01-16T20:00:00")

        # User B sees no data
        with set_current_user(user_b):
            result_b = await get_stats_summary()
        assert result_b["groups"] == [] or result_b["groups"][0]["matches"] == 0

        # User A sees their matches
        result_a = await get_stats_summary()
        assert result_a["groups"][0]["matches"] == 2


class TestHeroDetailStatsIsolated:
    async def test_hero_stats_only_from_own_matches(self, user_b):
        await create_test_match()

        with set_current_user(user_b):
            result_b = await get_hero_detail_stats(hero_name="Ana")
        assert result_b["stats"] == []


class TestHeroStatSeriesIsolated:
    async def test_hero_series_only_from_own_matches(self, user_b):
        await create_test_match()

        with set_current_user(user_b):
            result_b = await get_hero_stat_series("Ana", "Nano Boost Assists")
        assert result_b["count"] == 0
        assert result_b["points"] == []


class TestDurationStatsIsolated:
    async def test_duration_stats_only_from_own_matches(self, user_b):
        await create_test_match()

        with set_current_user(user_b):
            result_b = await get_duration_stats()
        assert result_b["buckets"] == []


class TestMatchRankingsIsolated:
    async def test_rankings_only_from_own_matches(self, user_b):
        await create_test_match()

        with set_current_user(user_b):
            result_b = await get_match_rankings()
        assert result_b["matches_analyzed"] == 0


class TestTeammateStatsIsolated:
    async def test_teammate_stats_only_from_own_matches(self, user_b):
        await create_test_match()

        with set_current_user(user_b):
            result_b = await get_teammate_stats()
        assert result_b["teammates"] == []


class TestMatchPlayerHistoryIsolated:
    async def test_player_history_scoped_to_user(self, user_b):
        # User A creates two matches with overlapping players
        await create_test_match(played_at="2026-01-15T20:00:00")
        m2 = await create_test_match(played_at="2026-01-16T20:00:00")

        # User B cannot query history for user A's match
        with set_current_user(user_b):
            result_b = await get_match_player_history(m2)
        assert result_b == {"error": "Match not found"}


class TestGetPlayerHistoryIsolated:
    async def test_player_history_by_name_scoped(self, user_b):
        await create_test_match()

        with set_current_user(user_b):
            result_b = await get_player_history(["Enemy1"])
        # User B has no matches so Enemy1 has no history
        assert result_b["players_with_history"] == []


class TestPlayerNotesIsolated:
    async def test_notes_are_per_user(self, user_b):
        # User A sets a note
        await set_player_note("SomePlayer", "toxic")

        # User B cannot see it
        with set_current_user(user_b):
            result_b = await get_player_note("SomePlayer")
        assert result_b["note"] is None

        # User B can set their own note for the same player
        with set_current_user(user_b):
            await set_player_note("SomePlayer", "friendly")
            result_b = await get_player_note("SomePlayer")
        assert result_b["note"] == "friendly"

        # User A's note is unchanged
        result_a = await get_player_note("SomePlayer")
        assert result_a["note"] == "toxic"

    async def test_list_notes_scoped(self, user_b):
        await set_player_note("Player1", "note1")
        await set_player_note("Player2", "note2")

        with set_current_user(user_b):
            await set_player_note("Player3", "note3")

        # User A sees 2 notes
        result_a = await list_player_notes()
        assert len(result_a["notes"]) == 2

        # User B sees 1 note
        with set_current_user(user_b):
            result_b = await list_player_notes()
        assert len(result_b["notes"]) == 1


class TestMatchFilesIsolated:
    async def test_list_files_scoped(self, user_b):
        match_id = await create_test_match()

        # Create a file directly in DB for user A's match
        async with db.async_session() as session:
            async with session.begin():
                session.add(MatchFile(
                    match_id=uuid.UUID(match_id),
                    filename="test.txt",
                    size=100,
                    tus_id="test-tus-id",
                ))

        # User A can see it
        result_a = await list_match_files(match_id)
        assert len(result_a["files"]) == 1

        # User B cannot
        with set_current_user(user_b):
            result_b = await list_match_files(match_id)
        assert len(result_b["files"]) == 0

    async def test_delete_file_scoped(self, user_b):
        match_id = await create_test_match()

        async with db.async_session() as session:
            async with session.begin():
                mf = MatchFile(
                    match_id=uuid.UUID(match_id),
                    filename="test.txt",
                    size=100,
                    tus_id="test-tus-id-del",
                )
                session.add(mf)
            await session.refresh(mf)
            file_id = str(mf.id)

        # User B cannot delete it
        with set_current_user(user_b):
            result = await delete_match_file(file_id)
        assert result == {"error": "File not found"}

        # File still exists for user A
        result_a = await list_match_files(match_id)
        assert len(result_a["files"]) == 1
