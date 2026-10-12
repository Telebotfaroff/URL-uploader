import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import subprocess

import requests

from plugins.javdl_core.channel import JavtifulChannelExtractor
from plugins.javdl_core.downloader import Downloader


class ChannelExtractorTests(unittest.TestCase):
    def test_page_url_replaces_page_parameter(self):
        base = "https://javtiful.com/channel/example?page=9&sort=recent"
        first = JavtifulChannelExtractor.page_url(base, 1)
        third = JavtifulChannelExtractor.page_url(base, 3)
        self.assertNotIn("page=", first)
        self.assertIn("page=3", third)
        self.assertIn("sort=recent", third)

    def test_deduplicate_items_uses_video_code(self):
        items = [
            {"code": "ABC-123", "post_url": "https://javtiful.com/video/1/a", "title": "ABC-123 one"},
            {"code": "abc_123", "post_url": "https://javtiful.com/video/2/b", "title": "ABC-123 two"},
            {"post_url": "https://javtiful.com/video/3/c", "title": "A different video"},
        ]
        unique, duplicates = JavtifulChannelExtractor.deduplicate_items(items)
        self.assertEqual(duplicates, 1)
        self.assertEqual(len(unique), 2)


class DownloaderTests(unittest.TestCase):
    def test_filename_removes_path_separators(self):
        filename = Downloader._filename("../../folder\\video", "1080p")
        self.assertNotIn("/", filename)
        self.assertNotIn("\\", filename)

    def test_http_failure_removes_partial_file(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "partial.mp4"
            path.write_bytes(b"partial")
            downloader = Downloader(workdir=temp, retries=1)
            with patch("plugins.javdl_core.downloader.requests.get", side_effect=requests.ConnectionError("offline")):
                with self.assertRaises(RuntimeError):
                    downloader._download_http("https://example.invalid/video.mp4", path, {}, None)
            self.assertFalse(path.exists())

    def test_hls_timeout_removes_partial_file(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "partial.mp4"
            path.write_bytes(b"partial")
            timeout = subprocess.TimeoutExpired("ffmpeg", 900)
            with patch("plugins.javdl_core.downloader.subprocess.run", side_effect=timeout) as run:
                with self.assertRaisesRegex(RuntimeError, "timed out"):
                    Downloader._download_hls("https://example.invalid/video.m3u8", path, {}, None)
            self.assertEqual(run.call_args.kwargs["timeout"], 900)
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
