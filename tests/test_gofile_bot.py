import unittest
import tempfile
from pathlib import Path

from gofile_media import _safe_name, extract_screenshots
from gofile_search import _parse_page


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




if __name__ == "__main__":
    unittest.main()
