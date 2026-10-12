import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from gofile_media import _safe_name, extract_screenshots
from gofile_search import _parse_page
from gofile_image_host import upload_images


class GofileSearchTests(unittest.TestCase):
    def test_parse_video_card(self):
        html = """
        <html><body>
          <div class="front-video-card">
            <a class="front-video-title" href="/video/123/star-765">STAR-765 Example Video</a>
            <img data-src="/thumbs/star765.jpg">
          </div>
        </body></html>
        """
        items, max_page = _parse_page(html, "https://javtiful.com/search?q=STAR-765")
        self.assertEqual(max_page, 1)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["code"], "STAR-765")
        self.assertEqual(items[0]["url"], "https://javtiful.com/video/123/star-765")
        self.assertEqual(items[0]["thumbnail"], "https://javtiful.com/thumbs/star765.jpg")

    def test_parse_pagination(self):
        html = '<a href="?q=STAR-765&page=3">3</a>'
        _, max_page = _parse_page(html, "https://javtiful.com/search?q=STAR-765")
        self.assertEqual(max_page, 3)

    def test_screenshot_quantity_is_limited_to_supported_choices(self):
        with self.assertRaises(ValueError):
            extract_screenshots("missing.mp4", ".", count=7)

    def test_safe_filename(self):
        self.assertNotIn("/", _safe_name("../folder/video:one?.mp4"))
        self.assertNotIn(":", _safe_name("video:one?.mp4"))

    def test_empty_html_returns_no_results(self):
        items, max_page = _parse_page("<html><body>Nothing</body></html>", "https://javtiful.com/")
        self.assertEqual(items, [])
        self.assertEqual(max_page, 1)


    @patch("gofile_image_host.requests.post")
    def test_freeimage_upload_returns_gallery_links(self, mock_post):
        mock_post.return_value = Mock(
            status_code=200,
            json=lambda: {
                "status_code": 200,
                "success": {"code": 200, "message": "image uploaded"},
                "image": {
                    "url": "https://freeimage.host/images/test.jpg",
                    "url_viewer": "https://freeimage.host/image/test",
                },
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "test.jpg"
            image.write_bytes(b"fake-jpeg")
            result = upload_images([image], api_key="test-key")
        self.assertEqual(result[0]["url"], "https://freeimage.host/images/test.jpg")
        self.assertEqual(result[0]["viewer_url"], "https://freeimage.host/image/test")
        self.assertEqual(mock_post.call_args.kwargs["data"]["action"], "upload")

    def test_freeimage_upload_requires_api_key(self):
        with patch.dict("os.environ", {"FREEIMAGE_API_KEY": ""}):
            with self.assertRaisesRegex(RuntimeError, "FREEIMAGE_API_KEY"):
                upload_images([])


if __name__ == "__main__":
    unittest.main()
