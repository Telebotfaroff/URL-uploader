import re
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup


class JavtifulChannelExtractor:
    """Extract channel metadata and paginated video cards for batch downloads."""

    BASE_URL = "https://javtiful.com"
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/140.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ja;q=0.8",
    }

    def __init__(self, timeout=30, retries=2):
        self.timeout = timeout
        self.retries = max(0, int(retries))
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)

    @staticmethod
    def _clean(value):
        return re.sub(r"\s+", " ", str(value or "")).strip() or None

    @staticmethod
    def _normalize_title_code(title, code):
        """Remove repeated/leading occurrences of the detected code from a title."""
        title = JavtifulChannelExtractor._clean(title) or "Video"
        if not code:
            return title

        code_pattern = re.escape(code).replace(r"\-", r"[-_ ]?")
        pattern = rf"^(?:{code_pattern}\s*)+"
        cleaned = re.sub(pattern, "", title, flags=re.I).strip(" -_")
        return f"{code} {cleaned}".strip() if cleaned else code

    @staticmethod
    def page_url(base, site_page):
        if site_page <= 1:
            parsed = urlparse(base)
            query = parse_qs(parsed.query, keep_blank_values=True)
            query.pop("page", None)
            return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
        parsed = urlparse(base)
        query = parse_qs(parsed.query, keep_blank_values=True)
        query["page"] = [str(site_page)]
        return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))

    def _fetch(self, url):
        last_error = None
        for attempt in range(self.retries + 1):
            try:
                response = self.session.get(
                    url,
                    timeout=self.timeout,
                    allow_redirects=True,
                )
                response.raise_for_status()
                if len(response.text) < 100:
                    raise requests.RequestException("Empty or malformed response")
                return response.url, response.text
            except requests.RequestException as exc:
                last_error = exc
                if attempt < self.retries:
                    import time
                    time.sleep(1 * (2 ** attempt))
        raise last_error

    @staticmethod
    def _soup(html):
        return BeautifulSoup(html, "html.parser")

    @staticmethod
    def _absolute(base, value):
        return urljoin(base, value) if value else None

    @staticmethod
    def _slug_from_url(url):
        path = urlparse(url).path.rstrip("/")
        return path.rsplit("/", 1)[-1] if path else ""

    def _channel_name(self, soup, fallback):
        for selector in ("h1", ".front-channel-title", ".channel-title"):
            node = soup.select_one(selector)
            if node:
                value = self._clean(node.get_text(" ", strip=True))
                if value:
                    return re.sub(r"\s*\(?\d+\s+Videos?\)?\s*$", "", value, flags=re.I).strip()

        meta = soup.find("meta", property="og:title")
        if meta and meta.get("content"):
            value = self._clean(meta["content"])
            if value:
                return re.sub(r"\s*\(?\d+\s+Videos?\)?\s*$", "", value, flags=re.I).strip()

        slug = self._slug_from_url(fallback)
        return slug.replace("-", " ").strip().title()

    def _total_videos(self, soup, channel_name):
        candidates = []

        for node in soup.find_all(string=re.compile(r"\d+\s+Videos?", re.I)):
            text = self._clean(node)
            if not text:
                continue
            match = re.search(r"(\d+)\s+Videos?", text, re.I)
            if match:
                candidates.append(int(match.group(1)))

        text = self._clean(soup.get_text(" ", strip=True)) or ""
        for pattern in (
            rf"{re.escape(channel_name)}\s*\(?\s*(\d+)\s+Videos?\)?",
            r"(\d+)\s+Videos?",
        ):
            match = re.search(pattern, text, re.I)
            if match:
                candidates.append(int(match.group(1)))

        return max(candidates) if candidates else None

    @staticmethod
    def _parse_pagination(soup, current_page):
        max_page = current_page
        has_next = False
        for link in soup.select("nav.front-pagination a.front-pagination-link, .pagination a, nav a[href*='page=']"):
            text = (link.get_text(" ", strip=True) or "").strip()
            href = link.get("href") or ""
            match = re.search(r"[?&]page=(\d+)", href, re.I)
            if match:
                max_page = max(max_page, int(match.group(1)))
            if text.isdigit():
                max_page = max(max_page, int(text))
            if "next" in text.lower():
                has_next = True

        has_next = has_next or current_page < max_page
        return {
            "current_page": current_page,
            "total_pages": max_page,
            "has_next": has_next,
        }

    @staticmethod
    def _extract_code(title, post_url):
        slug_match = re.search(
            r"/video/\d+/([a-zA-Z0-9]+[\-_][a-zA-Z0-9]+(?:[\-_][a-zA-Z0-9]+)?)",
            post_url,
            re.I,
        )
        if slug_match:
            value = re.sub(r"[\s_]+", "-", slug_match.group(1).strip().upper())
            if re.fullmatch(r"[A-Z0-9]{2,8}-[A-Z0-9]{2,8}(?:-[A-Z0-9]{1,8})?", value):
                return value

        match = re.search(r"\b([A-Z]{2,8}[-_ ]?\d{2,7})\b", title or "", re.I)
        if match:
            return re.sub(r"[\s_]+", "-", match.group(1).upper())
        return None

    @classmethod
    def deduplicate_items(cls, items):
        """Remove duplicate cards by normalized video code, then canonical URL."""
        seen = set()
        unique = []
        duplicate_count = 0

        for item in items:
            post_url = str(item.get("post_url") or "").strip()
            title = str(item.get("title") or "")
            code = item.get("code") or cls._extract_code(title, post_url)
            normalized_code = re.sub(r"[-_ ]+", "-", str(code).strip()).upper() if code else ""
            parsed = urlparse(post_url)
            canonical_url = urlunparse((
                parsed.scheme.lower(),
                parsed.netloc.lower(),
                parsed.path.rstrip("/"),
                parsed.params,
                "",
                "",
            )) if parsed.scheme and parsed.netloc else post_url.rstrip("/")
            keys = {f"code:{normalized_code}"} if normalized_code else set()
            if canonical_url:
                keys.add(f"url:{canonical_url}")

            if keys.intersection(seen):
                duplicate_count += 1
                continue

            seen.update(keys)
            unique.append(item)

        return unique, duplicate_count

    def _parse_cards(self, html, base):
        soup = self._soup(html)
        items = []
        seen = set()
        cards = soup.select(".front-video-card, .video-card, .video-item")

        for card in cards:
            title_link = card.select_one(".front-video-title, .video-title, h2 a, h3 a, h4 a")
            anchor = title_link or card.select_one(".front-video-thumb, .thumb, a")
            if not anchor:
                continue

            href = anchor.get("href")
            post_url = self._absolute(base, href)
            if not post_url or "/video/" not in post_url or post_url in seen:
                continue

            title = self._clean(anchor.get_text(" ", strip=True))
            if not title:
                title = self._clean(anchor.get("title"))
            img = card.find("img")
            thumbnail = None
            if img:
                thumbnail = (
                    img.get("data-front-lazy-src")
                    or img.get("data-src")
                    or img.get("src")
                )

            code = self._extract_code(title, post_url)
            title = self._normalize_title_code(title, code)
            seen.add(post_url)
            items.append({
                "code": code,
                "title": title or "Video",
                "post_url": post_url,
                "thumbnail": self._absolute(base, thumbnail),
            })

        if not items:
            for anchor in soup.select('a[href*="/video/"]'):
                post_url = self._absolute(base, anchor.get("href"))
                if not post_url or post_url in seen:
                    continue
                title = self._clean(anchor.get_text(" ", strip=True)) or self._clean(anchor.get("title"))
                if not title or len(title) < 5:
                    continue
                img = anchor.find("img")
                thumbnail = img.get("data-front-lazy-src") if img else None
                thumbnail = thumbnail or (img.get("src") if img else None)
                code = self._extract_code(title, post_url)
                title = self._normalize_title_code(title, code)
                seen.add(post_url)
                items.append({
                    "code": code,
                    "title": title,
                    "post_url": post_url,
                    "thumbnail": self._absolute(base, thumbnail),
                })

        return soup, items

    def inspect(self, url):
        final_url, html = self._fetch(self.page_url(url, 1))
        soup, items = self._parse_cards(html, final_url)
        channel_name = self._channel_name(soup, final_url)
        total_pages = self._parse_pagination(soup, 1)["total_pages"]
        total_videos = self._total_videos(soup, channel_name)

        return {
            "url": url,
            "final_url": final_url,
            "channel_name": channel_name,
            "total_videos": total_videos,
            "total_pages": total_pages,
            "first_page_items": items,
        }

    def page(self, url, page):
        # Telegram uses zero-based page numbers:
        # 0 => website page 1, 1 => website page 2, etc.
        site_page = int(page) + 1
        final_url, html = self._fetch(self.page_url(url, site_page))
        soup, items = self._parse_cards(html, final_url)
        pagination = self._parse_pagination(soup, site_page)
        return {
            "page": int(page),
            "site_page": site_page,
            "url": final_url,
            "items": items,
            "total_pages": pagination["total_pages"],
        }
