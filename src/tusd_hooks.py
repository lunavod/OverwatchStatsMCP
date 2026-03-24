"""tusd webhook handlers for file upload lifecycle.

tusd sends HTTP POST requests for upload events. We handle:
- pre-create: validate the auth key and match_id metadata
- post-finish: create a MatchFile row and enforce MAX_STORED_MATCHES
"""

import logging
import os
import uuid
from pathlib import Path

from sqlalchemy import func, select
from starlette.requests import Request
from starlette.responses import JSONResponse

import db
from models import Match, MatchFile

logger = logging.getLogger(__name__)

TUSD_AUTH_KEY = os.getenv("TUSD_AUTH_KEY", "")
TUSD_DATA_DIR = Path(os.getenv("TUSD_DATA_DIR", "/srv/tusd-data"))
MAX_STORED_MATCHES = int(os.getenv("MAX_STORED_MATCHES", "0"))


async def tusd_hook(request: Request) -> JSONResponse:
    """Single endpoint that dispatches based on the Hook-Name header."""
    hook_name = request.headers.get("Hook-Name", "")
    body = await request.json()

    # tusd v2 sends the event type in the JSON body as "Type", not as a header
    hook_type = body.get("Type", "")

    # tusd v2 nests data under "Event"; v1 puts it at the top level
    event_data = body.get("Event", body)

    event = hook_name or hook_type

    if event == "pre-create":
        return await _pre_create(request, event_data)
    elif event == "post-finish":
        return await _post_finish(event_data)
    else:
        logger.warning("Unknown hook event: %r — returning ok", event)
        return JSONResponse({"ok": True})


async def _pre_create(request: Request, event_data: dict) -> JSONResponse:
    """Validate auth key and required metadata before accepting an upload."""
    if not TUSD_AUTH_KEY:
        logger.warning("TUSD_AUTH_KEY not set — rejecting all uploads")
        return JSONResponse(
            {"ok": False, "error": "Upload auth not configured"},
            status_code=403,
        )

    # tusd forwards the original client headers in HTTPRequest.Header
    http_req = event_data.get("HTTPRequest", {})
    headers = http_req.get("Header", {})
    # Headers are lists of values
    auth_values = headers.get("Authorization", [])
    auth_header = auth_values[0] if auth_values else ""

    expected = f"Bearer {TUSD_AUTH_KEY}"
    if auth_header != expected:
        return JSONResponse(
            {"ok": False, "error": "Invalid auth key"},
            status_code=403,
        )

    # Require match_id in metadata
    upload = event_data.get("Upload", {})
    metadata = upload.get("MetaData", {})
    match_id = metadata.get("match_id", "")
    if not match_id:
        return JSONResponse(
            {"ok": False, "error": "match_id metadata is required"},
            status_code=400,
        )

    # Validate match_id is a real UUID
    try:
        uuid.UUID(match_id)
    except ValueError:
        return JSONResponse(
            {"ok": False, "error": "match_id must be a valid UUID"},
            status_code=400,
        )

    return JSONResponse({"ok": True})


async def _post_finish(event_data: dict) -> JSONResponse:
    """Create a MatchFile row after a successful upload, then enforce storage limit."""
    upload = event_data.get("Upload", {})
    metadata = upload.get("MetaData", {})
    match_id_str = metadata.get("match_id", "")
    filename = metadata.get("filename", "unknown")
    size = upload.get("Size", 0)
    tus_id = upload.get("ID", "")

    if not match_id_str or not tus_id:
        return JSONResponse(
            {"ok": False, "error": "Missing match_id or upload ID"},
            status_code=400,
        )

    match_uuid = uuid.UUID(match_id_str)

    try:
        async with db.async_session() as session:
            async with session.begin():
                # Verify the match exists
                match = (
                    await session.execute(
                        select(Match).where(Match.id == match_uuid)
                    )
                ).scalar_one_or_none()

                if not match:
                    return JSONResponse(
                        {"ok": False, "error": "Match not found"},
                        status_code=404,
                    )

                # Replace existing file with same name for this match
                old_file = (
                    await session.execute(
                        select(MatchFile).where(
                            MatchFile.match_id == match_uuid,
                            MatchFile.filename == filename,
                        )
                    )
                ).scalar_one_or_none()

                if old_file:
                    old_tus_id = old_file.tus_id
                    await session.delete(old_file)
                    await session.flush()
                    # Clean up old file from disk
                    for path in [TUSD_DATA_DIR / old_tus_id, TUSD_DATA_DIR / f"{old_tus_id}.info"]:
                        try:
                            path.unlink(missing_ok=True)
                        except OSError as exc:
                            logger.warning("Failed to delete old file %s: %s", path, exc)
                    logger.info("Replaced existing file %s (old tus_id=%s) for match %s", filename, old_tus_id, match_id_str)

                match.has_attachments = True
                session.add(
                    MatchFile(
                        match_id=match_uuid,
                        filename=filename,
                        size=size,
                        tus_id=tus_id,
                    )
                )
    except Exception:
        logger.exception("Failed to register file %s for match %s", tus_id, match_id_str)
        return JSONResponse(
            {"ok": False, "error": "Internal error saving file record"},
            status_code=500,
        )

    logger.info("Registered file %s (%s) for match %s", filename, tus_id, match_id_str)

    # Enforce storage limit
    await _enforce_storage_limit()

    return JSONResponse({"ok": True})


async def _enforce_storage_limit() -> None:
    """If MAX_STORED_MATCHES is set and exceeded, delete files from the oldest match."""
    if MAX_STORED_MATCHES <= 0:
        return

    # Read phase: figure out what to delete
    async with db.async_session() as session:
        count = (
            await session.execute(
                select(func.count(func.distinct(MatchFile.match_id)))
            )
        ).scalar_one()

        if count <= MAX_STORED_MATCHES:
            return

        oldest_match_id = (
            await session.execute(
                select(Match.id)
                .join(MatchFile, MatchFile.match_id == Match.id)
                .order_by(Match.played_at.asc().nulls_first(), Match.created_at.asc())
                .limit(1)
            )
        ).scalar_one_or_none()

        if oldest_match_id is None:
            return

        tus_ids = [
            row[0]
            for row in (
                await session.execute(
                    select(MatchFile.tus_id).where(MatchFile.match_id == oldest_match_id)
                )
            ).all()
        ]

    # Write phase: delete rows and clear the flag
    async with db.async_session() as session:
        async with session.begin():
            match = (
                await session.execute(
                    select(Match).where(Match.id == oldest_match_id)
                )
            ).scalar_one_or_none()
            if match:
                match.has_attachments = False
            await session.execute(
                MatchFile.__table__.delete().where(MatchFile.match_id == oldest_match_id)
            )

    # Delete from disk (tusd stores files as <tus_id> and <tus_id>.info)
    for tus_id in tus_ids:
        for path in [TUSD_DATA_DIR / tus_id, TUSD_DATA_DIR / f"{tus_id}.info"]:
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("Failed to delete %s: %s", path, exc)

    logger.info(
        "Storage limit enforced: deleted %d file(s) from match %s",
        len(tus_ids),
        oldest_match_id,
    )
