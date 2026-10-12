import unittest
import tempfile
from pathlib import Path

from gofile_media import _safe_name, extract_screenshots
from gofile_search import _parse_page
from gofile_bot import (
    _screenshot_quantity_keyboard, _combined_quantity_keyboard, _bulk_quantity_keyboard,
    _parallel_keyboard, PARALLEL_LIMITS, _bulk_success_message,
)


class GofileSearchTests(unittest.TestCase):
    def test_bulk_successes_are_combined_into_one_compact_message(self):
        self.assertEqual(
            _bulk_success_message(["START-618", "START-627", "IPZZ-950"]),
            "START-618✅ START-627✅ IPZZ-950✅",
        )

    def test_parallel_keyboard_offers_custom_worker_counts(self):
        keyboard = _parallel_keyboard()
        callback_data = [
            button.callback_data
            for row in keyboard.inline_keyboard
            for button in row
        ]
        for key, (low, high) in PARALLEL_LIMITS.items():
            for value in range(low, high + 1):
                self.assertIn(f"par:{key}:{value}", callback_data)

    def test_screenshot_keyboards_offer_private_channel_destination(self):
        for keyboard in (
            _screenshot_quantity_keyboard("abc12345", 0),
            _combined_quantity_keyboard("abc12345", 0),
            _bulk_quantity_keyboard("abc12345", "ss"),
        ):
            callback_data = [
                button.callback_data
                for row in keyboard.inline_keyboard
                for button in row
            ]
            self.assertTrue(any(value.endswith(":channel") for value in callback_data))
            self.assertTrue(any(value.endswith(":chat") for value in callback_data))
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
