"""Tests for screenshot upload functionality."""

import base64

import pytest
import main
from starlette.staticfiles import StaticFiles
from starlette.testclient import TestClient
from tests.factories import create_test_match

# A minimal valid 1x1 PNG image
_TINY_PNG = base64.b64encode(
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
    b"\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
    b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
).decode()


class TestSubmitMatchWithUpload:
    async def test_upload_creates_file(self):
        from main import get_match

        match_id = await create_test_match(
            screenshot_uploads=[{"data": _TINY_PNG, "filename": "screen.png"}]
        )
        match = await get_match(match_id)
        assert len(match["screenshots"]) == 1

        url = match["screenshots"][0]
        assert url.startswith("/uploads/")
        assert url.endswith(".png")

        # File actually exists on disk
        file_path = main.UPLOADS_DIR / url.split("/uploads/")[1]
        assert file_path.exists()

    async def test_upload_with_jpg_extension(self):
        from main import get_match

        match_id = await create_test_match(
            screenshot_uploads=[{"data": _TINY_PNG, "filename": "photo.jpg"}]
        )
        match = await get_match(match_id)
        assert match["screenshots"][0].endswith(".jpg")

    async def test_upload_defaults_to_png(self):
        from main import get_match

        match_id = await create_test_match(
            screenshot_uploads=[{"data": _TINY_PNG}]
        )
        match = await get_match(match_id)
        assert match["screenshots"][0].endswith(".png")

    async def test_mixed_urls_and_uploads(self):
        from main import get_match

        match_id = await create_test_match(
            screenshots=["https://example.com/existing.png"],
            screenshot_uploads=[{"data": _TINY_PNG, "filename": "new.png"}],
        )
        match = await get_match(match_id)
        assert len(match["screenshots"]) == 2

        urls = match["screenshots"]
        assert any(u == "https://example.com/existing.png" for u in urls)
        assert any(u.startswith("/uploads/") for u in urls)


class TestEditMatchWithUpload:
    async def test_upload_via_edit(self):
        from main import edit_match, get_match

        match_id = await create_test_match()
        await edit_match(
            match_id,
            screenshot_uploads=[{"data": _TINY_PNG, "filename": "added.png"}],
        )
        match = await get_match(match_id)
        assert len(match["screenshots"]) == 1
        assert match["screenshots"][0].startswith("/uploads/")


class TestUploadScreenshot:
    async def test_standalone_upload(self):
        from main import get_match, upload_screenshot

        match_id = await create_test_match()
        result = await upload_screenshot(
            match_id=match_id, data=_TINY_PNG, filename="standalone.png"
        )
        assert "url" in result
        assert result["url"].startswith("/uploads/")

        match = await get_match(match_id)
        assert result["url"] in match["screenshots"]

    async def test_upload_to_nonexistent_match(self):
        from main import upload_screenshot

        result = await upload_screenshot(
            match_id="00000000-0000-0000-0000-000000000000",
            data=_TINY_PNG,
        )
        assert result == {"error": "Match not found"}

    async def test_upload_multiple_to_same_match(self):
        from main import get_match, upload_screenshot

        match_id = await create_test_match()
        r1 = await upload_screenshot(match_id=match_id, data=_TINY_PNG)
        r2 = await upload_screenshot(match_id=match_id, data=_TINY_PNG)

        match = await get_match(match_id)
        assert len(match["screenshots"]) == 2
        assert r1["url"] != r2["url"]  # unique filenames


class TestUploadsServing:
    """Test that uploaded files are served via HTTP."""

    def _client(self) -> TestClient:
        app = StaticFiles(directory=str(main.UPLOADS_DIR))
        return TestClient(app)

    async def test_uploaded_file_is_downloadable(self):
        from main import upload_screenshot

        match_id = await create_test_match()
        result = await upload_screenshot(
            match_id=match_id, data=_TINY_PNG, filename="serve_test.png"
        )
        # URL is like "/uploads/<uuid>.png", strip the "/uploads/" prefix
        filename = result["url"].split("/uploads/")[1]

        client = self._client()
        response = client.get(f"/{filename}")
        assert response.status_code == 200
        assert response.headers["content-type"] in (
            "image/png",
            "application/octet-stream",
        )
        # Verify we get the actual image bytes back
        original_bytes = base64.b64decode(_TINY_PNG)
        assert response.content == original_bytes

    async def test_nonexistent_file_returns_404(self):
        from starlette.exceptions import HTTPException

        client = self._client()
        with pytest.raises(HTTPException) as exc_info:
            client.get("/nonexistent.png")
        assert exc_info.value.status_code == 404

    async def test_serves_correct_content_type_for_jpg(self):
        from main import upload_screenshot

        match_id = await create_test_match()
        result = await upload_screenshot(
            match_id=match_id, data=_TINY_PNG, filename="photo.jpg"
        )
        filename = result["url"].split("/uploads/")[1]

        client = self._client()
        response = client.get(f"/{filename}")
        assert response.status_code == 200
        assert "image/jpeg" in response.headers["content-type"]
