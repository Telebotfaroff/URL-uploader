import json
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .models import Video
from .extractor_base import BaseExtractor


class JavtifulExtractor(BaseExtractor):
    name = "javtiful"

    _QUALITY_RE = re.compile(r"(?<!\d)(2160|1440|1080|720|480|360)p", re.I)
    _MEDIA_RE = re.compile(
        r'''https?://[^\s"'<>\\]+(?:\.m3u8(?:\?[^\s"'<>\\]*)?|\.mp4(?:\?[^\s"'<>\\]*)?|\.webm(?:\?[^\s"'<>\\]*)?|/p/[A-Za-z0-9._~:/?#[\]@!$&()*+,;=%-]+)''',
        re.I,
    )
    _HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ja;q=0.8",
    }

    def extract(self, url: str) -> Video:
        response = requests.get(url, timeout=30, headers=self._HEADERS)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        title = self._title(soup)
        thumbnail = self._thumbnail(soup, url)
        duration = self._find_duration(soup)
        qualities = {}

        # Primary: Javtiful's player configuration.
        self._add_front_watch_config(soup, qualities)

        # Fallback 1: inline playerSources JSON.
        if not self._has_real_media_url(qualities):
            self._add_inline_player_sources(response.text, qualities)

        # Fallback 2: regular HTML5 video/source elements.
        if not self._has_real_media_url(qualities):
            self._add_dom_sources(soup, url, qualities)

        # Last static fallback: media URLs embedded elsewhere in HTML/JS.
        if not self._has_real_media_url(qualities):
            self._add_raw_media_urls(response.text, qualities)

        # Runtime fallback for pages that only create the final signed stream
        # after JavaScript initializes the player.
        if not self._has_real_media_url(qualities):
            self._add_runtime_media_urls(url, qualities)

        if not thumbnail:
            thumbnail = self._runtime_thumbnail(url)

        if not duration:
            duration = self._runtime_duration(url)

        return Video(
            source_url=url,
            title=title,
            thumbnail=thumbnail,
            duration=duration,
            qualities=qualities,
        )

    @staticmethod
    def _title(soup):
        node = soup.find("h1")
        if node:
            return node.get_text(" ", strip=True)

        node = soup.find("meta", property="og:title")
        return node.get("content", "").strip() if node else ""

    @staticmethod
    def _thumbnail(soup, page_url):
        selectors = [
            ("meta", {"property": "og:image"}),
            ("meta", {"property": "og:image:url"}),
            ("meta", {"name": "twitter:image"}),
            ("meta", {"name": "twitter:image:src"}),
        ]
        for tag_name, attrs in selectors:
            node = soup.find(tag_name, attrs=attrs)
            if node and node.get("content"):
                return urljoin(page_url, node["content"].strip())
        for tag in soup.find_all(["video", "source", "img"]):
            for attr in ("poster", "data-poster", "data-thumbnail", "data-thumb", "data-image"):
                value = tag.get(attr)
                if value:
                    return urljoin(page_url, str(value).strip())
        node = soup.find("link", rel=lambda value: value and "image_src" in value)
        if node and node.get("href"):
            return urljoin(page_url, node["href"].strip())
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or script.get_text())
            except (TypeError, ValueError):
                continue
            candidates = data if isinstance(data, list) else [data]
            for item in candidates:
                if not isinstance(item, dict):
                    continue
                image = item.get("image") or item.get("thumbnailUrl")
                if isinstance(image, list):
                    image = image[0] if image else None
                if isinstance(image, dict):
                    image = image.get("url")
                if image:
                    return urljoin(page_url, str(image).strip())
        return None

    def _add_front_watch_config(self, soup, qualities):
        config_tag = soup.find("script", id="frontWatchConfig")
        if not config_tag:
            return

        raw = config_tag.string or config_tag.get_text()
        if not raw:
            return

        try:
            config = json.loads(raw.strip())
        except (TypeError, ValueError):
            return

        sources = config.get("playerSources")
        if not isinstance(sources, list):
            return

        for source in sources:
            if not isinstance(source, dict):
                continue
            self._add_player_source(source, qualities)

    def _add_inline_player_sources(self, html, qualities):
        # The guide's fallback is based on an inline "playerSources" array.
        # Use a small balanced-array scanner so nested objects don't break
        # parsing when source metadata contains additional fields.
        marker = '"playerSources"'
        start = 0

        while True:
            marker_pos = html.find(marker, start)
            if marker_pos < 0:
                return

            array_start = html.find("[", marker_pos + len(marker))
            if array_start < 0:
                return

            array_end = self._balanced_json_end(html, array_start)
            if array_end < 0:
                return

            raw = html[array_start:array_end + 1]
            try:
                sources = json.loads(raw)
            except (TypeError, ValueError):
                start = array_start + 1
                continue

            if isinstance(sources, list):
                for source in sources:
                    if isinstance(source, dict):
                        self._add_player_source(source, qualities)

                if self._has_real_media_url(qualities):
                    return

            start = array_end + 1

    @staticmethod
    def _balanced_json_end(text, start):
        depth = 0
        in_string = False
        escaped = False

        for index in range(start, len(text)):
            char = text[index]

            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
            elif char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
                if depth == 0:
                    return index

        return -1

    def _add_player_source(self, source, qualities):
        src = source.get("src")
        if not src:
            return

        src = str(src).strip()
        if not self._looks_like_media(src):
            return

        quality = source.get("size") or source.get("quality") or source.get("label")
        if isinstance(quality, (int, float)):
            key = f"{int(quality)}p"
        elif quality:
            quality_text = str(quality).strip()
            match = self._QUALITY_RE.search(quality_text)
            key = f"{match.group(1)}p" if match else quality_text
        else:
            key = self._quality_from_url(src) or "source"

        qualities.setdefault(key, src)

    def _add_dom_sources(self, soup, page_url, qualities):
        for tag in soup.find_all(["video", "source"]):
            src = (
                tag.get("src")
                or tag.get("data-src")
                or tag.get("data-url")
                or tag.get("data-video")
            )
            if not src:
                continue

            absolute = urljoin(page_url, src)
            if not self._looks_like_media(absolute):
                continue

            quality = (
                tag.get("label")
                or tag.get("res")
                or tag.get("data-quality")
                or self._quality_from_url(absolute)
                or "source"
            )
            qualities.setdefault(str(quality), absolute)

    def _add_raw_media_urls(self, html, qualities):
        raw = (
            html.replace("\\/","/")
            .replace("&amp;", "&")
            .replace("\\u002F", "/")
            .replace("\\u003A", ":")
        )

        for match in self._MEDIA_RE.finditer(raw):
            candidate = match.group(0).rstrip("\\.,);]}'")
            if self._looks_like_media(candidate):
                qualities.setdefault(
                    self._quality_from_url(candidate) or "source",
                    candidate,
                )

    def _add_runtime_media_urls(self, page_url, qualities):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return

        media_urls = []

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
                page = browser.new_page(user_agent=self._HEADERS["User-Agent"])

                def on_response(response):
                    candidate = response.url
                    if self._looks_like_media(candidate):
                        media_urls.append(candidate)

                page.on("response", on_response)
                page.goto(page_url, wait_until="domcontentloaded", timeout=45_000)
                page.wait_for_timeout(6_000)

                resources = page.evaluate(
                    """() => performance.getEntriesByType('resource')
                    .map(x => x.name)
                    .filter(x => /\\.(m3u8|mp4|webm)(\\?|$)|\\/p\\//i.test(x))"""
                )
                media_urls.extend(resources)
                browser.close()
        except Exception:
            return

        for candidate in media_urls:
            candidate = str(candidate).strip()
            if self._looks_like_media(candidate):
                qualities.setdefault(
                    self._quality_from_url(candidate) or "source",
                    candidate,
                )

    def _runtime_thumbnail(self, page_url):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return None
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
                page = browser.new_page(user_agent=self._HEADERS["User-Agent"])
                page.goto(page_url, wait_until="domcontentloaded", timeout=45_000)
                page.wait_for_timeout(2_000)
                value = page.evaluate("""() => {
                    const v = document.querySelector("video");
                    if (v?.poster) return v.poster;
                    for (const selector of ['meta[property="og:image"]', 'meta[name="twitter:image"]', "img[data-poster]", "img[data-thumbnail]", "[data-poster]"]) {
                        const el = document.querySelector(selector);
                        const value = el?.content || el?.getAttribute("data-poster") || el?.getAttribute("data-thumbnail");
                        if (value) return value;
                    }
                    return null;
                }""")
                browser.close()
                return urljoin(page_url, str(value).strip()) if value else None
        except Exception:
            return None

    def _runtime_duration(self, page_url):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return None

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
                page = browser.new_page(user_agent=self._HEADERS["User-Agent"])
                page.goto(page_url, wait_until="domcontentloaded", timeout=45_000)
                page.wait_for_timeout(3_000)
                value = page.evaluate(
                    """() => {
                        const v = document.querySelector('video');
                        return v && Number.isFinite(v.duration) ? v.duration : null;
                    }"""
                )
                browser.close()
                return int(float(value)) if value else None
        except Exception:
            return None

    def _find_duration(self, soup):
        for tag in soup.find_all(["video", "source"]):
            value = tag.get("duration") or tag.get("data-duration")
            if value:
                parsed = self._duration(str(value))
                if parsed is not None:
                    return parsed

        for tag in soup.find_all(attrs={"class": re.compile("duration|time", re.I)}):
            parsed = self._duration(tag.get_text(" ", strip=True))
            if parsed is not None:
                return parsed

        for tag in soup.find_all(attrs={"data-duration": True}):
            parsed = self._duration(str(tag.get("data-duration")))
            if parsed is not None:
                return parsed

        return None

    @staticmethod
    def _quality_from_url(url):
        match = re.search(r"(?<!\d)(2160|1440|1080|720|480|360)p", url, re.I)
        return match.group(1) + "p" if match else None

    @classmethod
    def _looks_like_media(cls, url):
        lowered = str(url).lower()
        return (
            ".m3u8" in lowered
            or ".mp4" in lowered
            or ".webm" in lowered
            or "/p/" in lowered
        )

    @classmethod
    def _has_real_media_url(cls, qualities):
        return any(cls._looks_like_media(value) for value in qualities.values())

    @staticmethod
    def _duration(value):
        value = str(value).strip()

        if re.fullmatch(r"\d+(?:\.\d+)?", value):
            return int(float(value))

        match = re.fullmatch(
            r"(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*(?:(\d+)\s*s)?",
            value,
            re.I,
        )
        if match and any(match.groups()):
            return (
                int(match.group(1) or 0) * 3600
                + int(match.group(2) or 0) * 60
                + int(match.group(3) or 0)
            )

        match = re.fullmatch(r"(\d+):(\d{1,2})(?::(\d{1,2}))?", value)
        if match:
            if match.group(3) is None:
                return int(match.group(1)) * 60 + int(match.group(2))
            return (
                int(match.group(1)) * 3600
                + int(match.group(2)) * 60
                + int(match.group(3))
            )

        return None
