"""Tests for match file attachments (tusd hooks + MCP tools)."""

import uuid

from sqlalchemy import func, select

import db
import tusd_hooks
from models import Match, MatchFile
from tests.factories import create_test_match


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pre_create_body(match_id: str, auth_key: str = "test-key"):
    """Build a tusd pre-create hook request body."""
    return {
        "HTTPRequest": {
            "Header": {
                "Authorization": [f"Bearer {auth_key}"],
            },
        },
        "Upload": {
            "MetaData": {
                "match_id": match_id,
                "filename": "recording.mp4",
            },
        },
    }


def _make_post_finish_body(match_id: str, tus_id: str | None = None, filename: str = "recording.mp4", size: int = 1024):
    """Build a tusd post-finish hook request body."""
    return {
        "Upload": {
            "ID": tus_id or str(uuid.uuid4()),
            "Size": size,
            "MetaData": {
                "match_id": match_id,
                "filename": filename,
            },
        },
    }


async def _call_post_finish(match_id: str, **kwargs):  # type: ignore[no-untyped-def]
    """Shortcut to call _post_finish and return the JSONResponse."""
    body = _make_post_finish_body(match_id, **kwargs)
    return await tusd_hooks._post_finish(body)


# ---------------------------------------------------------------------------
# pre-create hook
# ---------------------------------------------------------------------------


class TestPreCreate:
    async def test_rejects_when_no_auth_key_configured(self):
        original = tusd_hooks.TUSD_AUTH_KEY
        tusd_hooks.TUSD_AUTH_KEY = ""
        try:
            body = _make_pre_create_body("anything")
            resp = await tusd_hooks._pre_create(None, body)
            assert resp.status_code == 403
        finally:
            tusd_hooks.TUSD_AUTH_KEY = original

    async def test_rejects_invalid_auth(self):
        original = tusd_hooks.TUSD_AUTH_KEY
        tusd_hooks.TUSD_AUTH_KEY = "correct-key"
        try:
            body = _make_pre_create_body("anything", auth_key="wrong-key")
            resp = await tusd_hooks._pre_create(None, body)
            assert resp.status_code == 403
        finally:
            tusd_hooks.TUSD_AUTH_KEY = original

    async def test_accepts_valid_auth(self):
        original = tusd_hooks.TUSD_AUTH_KEY
        tusd_hooks.TUSD_AUTH_KEY = "test-key"
        try:
            match_id = str(uuid.uuid4())
            body = _make_pre_create_body(match_id, auth_key="test-key")
            resp = await tusd_hooks._pre_create(None, body)
            assert resp.status_code == 200
        finally:
            tusd_hooks.TUSD_AUTH_KEY = original

    async def test_rejects_missing_match_id(self):
        original = tusd_hooks.TUSD_AUTH_KEY
        tusd_hooks.TUSD_AUTH_KEY = "test-key"
        try:
            body = {
                "HTTPRequest": {"Header": {"Authorization": ["Bearer test-key"]}},
                "Upload": {"MetaData": {}},
            }
            resp = await tusd_hooks._pre_create(None, body)
            assert resp.status_code == 400
        finally:
            tusd_hooks.TUSD_AUTH_KEY = original

    async def test_rejects_invalid_uuid(self):
        original = tusd_hooks.TUSD_AUTH_KEY
        tusd_hooks.TUSD_AUTH_KEY = "test-key"
        try:
            body = _make_pre_create_body("not-a-uuid", auth_key="test-key")
            resp = await tusd_hooks._pre_create(None, body)
            assert resp.status_code == 400
        finally:
            tusd_hooks.TUSD_AUTH_KEY = original


# ---------------------------------------------------------------------------
# post-finish hook
# ---------------------------------------------------------------------------


class TestPostFinish:
    async def test_creates_match_file(self):
        match_id = await create_test_match()
        tus_id = str(uuid.uuid4())
        resp = await _call_post_finish(match_id, tus_id=tus_id, filename="vid.mp4", size=8000)
        assert resp.status_code == 200

        async with db.async_session() as session:
            mf = (
                await session.execute(
                    select(MatchFile).where(MatchFile.tus_id == tus_id)
                )
            ).scalar_one()
            assert mf.filename == "vid.mp4"
            assert mf.size == 8000
            assert str(mf.match_id) == match_id

    async def test_sets_has_attachments(self):
        match_id = await create_test_match()
        await _call_post_finish(match_id)

        async with db.async_session() as session:
            match = (
                await session.execute(
                    select(Match).where(Match.id == uuid.UUID(match_id))
                )
            ).scalar_one()
            assert match.has_attachments is True

    async def test_rejects_nonexistent_match(self):
        fake_id = str(uuid.uuid4())
        resp = await _call_post_finish(fake_id)
        assert resp.status_code == 404

    async def test_default_filename(self):
        match_id = await create_test_match()
        body = {
            "Upload": {
                "ID": str(uuid.uuid4()),
                "Size": 100,
                "MetaData": {"match_id": match_id},
            },
        }
        resp = await tusd_hooks._post_finish(body)
        assert resp.status_code == 200

        async with db.async_session() as session:
            mf = (
                await session.execute(
                    select(MatchFile).where(MatchFile.match_id == uuid.UUID(match_id))
                )
            ).scalar_one()
            assert mf.filename == "unknown"


# ---------------------------------------------------------------------------
# MCP tools: list_match_files, delete_match_file
# ---------------------------------------------------------------------------


class TestListMatchFiles:
    async def test_empty(self):
        from main import list_match_files

        match_id = await create_test_match()
        result = await list_match_files(match_id)
        assert result["files"] == []

    async def test_lists_files(self):
        from main import list_match_files

        match_id = await create_test_match()
        await _call_post_finish(match_id, filename="a.mp4", size=100)
        await _call_post_finish(match_id, filename="b.json", size=50)

        result = await list_match_files(match_id)
        assert len(result["files"]) == 2
        filenames = [f["filename"] for f in result["files"]]
        assert "a.mp4" in filenames
        assert "b.json" in filenames


class TestDeleteMatchFile:
    async def test_delete_existing(self):
        from main import delete_match_file, list_match_files

        match_id = await create_test_match()
        tus_id = str(uuid.uuid4())
        await _call_post_finish(match_id, tus_id=tus_id, filename="x.mp4")

        files = (await list_match_files(match_id))["files"]
        assert len(files) == 1

        result = await delete_match_file(files[0]["id"])
        assert result["deleted"] is True

        files_after = (await list_match_files(match_id))["files"]
        assert len(files_after) == 0

    async def test_delete_not_found(self):
        from main import delete_match_file

        result = await delete_match_file(str(uuid.uuid4()))
        assert result == {"error": "File not found"}

    async def test_clears_has_attachments_on_last_file(self):
        from main import delete_match_file, list_match_files

        match_id = await create_test_match()
        await _call_post_finish(match_id, filename="only.mp4")

        async with db.async_session() as session:
            match = (await session.execute(
                select(Match).where(Match.id == uuid.UUID(match_id))
            )).scalar_one()
            assert match.has_attachments is True

        files = (await list_match_files(match_id))["files"]
        await delete_match_file(files[0]["id"])

        async with db.async_session() as session:
            match = (await session.execute(
                select(Match).where(Match.id == uuid.UUID(match_id))
            )).scalar_one()
            assert match.has_attachments is False

    async def test_keeps_has_attachments_when_files_remain(self):
        from main import delete_match_file, list_match_files

        match_id = await create_test_match()
        await _call_post_finish(match_id, filename="a.mp4")
        await _call_post_finish(match_id, filename="b.mp4")

        files = (await list_match_files(match_id))["files"]
        await delete_match_file(files[0]["id"])

        async with db.async_session() as session:
            match = (await session.execute(
                select(Match).where(Match.id == uuid.UUID(match_id))
            )).scalar_one()
            assert match.has_attachments is True


# ---------------------------------------------------------------------------
# delete_match cascades to match files
# ---------------------------------------------------------------------------


class TestDeleteMatchCascadesFiles:
    async def test_cascade_deletes_match_files(self):
        from main import delete_match

        match_id = await create_test_match()
        await _call_post_finish(match_id, filename="vid.mp4")

        await delete_match(match_id)

        async with db.async_session() as session:
            count = (
                await session.execute(
                    select(func.count()).select_from(MatchFile)
                )
            ).scalar_one()
        assert count == 0


# ---------------------------------------------------------------------------
# has_attachments defaults to False
# ---------------------------------------------------------------------------


class TestHasAttachmentsDefault:
    async def test_defaults_to_false(self):
        match_id = await create_test_match()
        async with db.async_session() as session:
            match = (await session.execute(
                select(Match).where(Match.id == uuid.UUID(match_id))
            )).scalar_one()
            assert match.has_attachments is False


# ---------------------------------------------------------------------------
# Storage limit enforcement
# ---------------------------------------------------------------------------


class TestStorageLimit:
    async def test_enforces_limit(self):
        original = tusd_hooks.MAX_STORED_MATCHES
        tusd_hooks.MAX_STORED_MATCHES = 1
        try:
            # Create two matches with files; oldest should be purged
            match1 = await create_test_match(played_at="2026-01-01T00:00:00")
            match2 = await create_test_match(played_at="2026-01-02T00:00:00")

            await _call_post_finish(match1, filename="old.mp4")
            # This should trigger enforcement and purge match1's files
            await _call_post_finish(match2, filename="new.mp4")

            async with db.async_session() as session:
                m1_files = (
                    await session.execute(
                        select(func.count()).select_from(MatchFile).where(
                            MatchFile.match_id == uuid.UUID(match1)
                        )
                    )
                ).scalar_one()
                m2_files = (
                    await session.execute(
                        select(func.count()).select_from(MatchFile).where(
                            MatchFile.match_id == uuid.UUID(match2)
                        )
                    )
                ).scalar_one()

            assert m1_files == 0
            assert m2_files == 1
        finally:
            tusd_hooks.MAX_STORED_MATCHES = original

    async def test_clears_has_attachments_on_purge(self):
        original = tusd_hooks.MAX_STORED_MATCHES
        tusd_hooks.MAX_STORED_MATCHES = 1
        try:
            match1 = await create_test_match(played_at="2026-01-01T00:00:00")
            match2 = await create_test_match(played_at="2026-01-02T00:00:00")

            await _call_post_finish(match1, filename="old.mp4")
            await _call_post_finish(match2, filename="new.mp4")

            async with db.async_session() as session:
                m1 = (await session.execute(
                    select(Match).where(Match.id == uuid.UUID(match1))
                )).scalar_one()
                m2 = (await session.execute(
                    select(Match).where(Match.id == uuid.UUID(match2))
                )).scalar_one()
                assert m1.has_attachments is False
                assert m2.has_attachments is True
        finally:
            tusd_hooks.MAX_STORED_MATCHES = original

    async def test_no_enforcement_when_disabled(self):
        original = tusd_hooks.MAX_STORED_MATCHES
        tusd_hooks.MAX_STORED_MATCHES = 0
        try:
            match1 = await create_test_match(played_at="2026-01-01T00:00:00")
            match2 = await create_test_match(played_at="2026-01-02T00:00:00")

            await _call_post_finish(match1, filename="a.mp4")
            await _call_post_finish(match2, filename="b.mp4")

            async with db.async_session() as session:
                count = (
                    await session.execute(
                        select(func.count()).select_from(MatchFile)
                    )
                ).scalar_one()
            assert count == 2
        finally:
            tusd_hooks.MAX_STORED_MATCHES = original
